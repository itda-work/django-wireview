# 서버 오류 처리

컴포넌트 코드가 예외를 던지면 그 컴포넌트 하나만 대가를 치른다. 연결과 같은 페이지의 다른
컴포넌트는 그대로 동작한다(#94).

전에는 예외가 컨슈머 밖으로 나가 WebSocket이 닫혔다. 클라이언트는 `wireview-disconnected`를
붙였다가 재연결하고 페이지의 **모든** 컴포넌트를 다시 join했다. 무엇이 잘못됐는지는 서버 로그에만 남았다.

## 무엇이 일어나는가

| 무엇이 던졌나 | 서버 | 브라우저 |
|---|---|---|
| 이벤트 핸들러, 브로드캐스트 수신(`notification`·`mutation`), `params_changed`, 훅 이벤트, 업로드 콜백, `start_async`의 `handle_async`, LiveComponent `update()`, 렌더 | 로그(`log.exception`)를 남기고 인스턴스를 버린다. `joined()`가 돌았던 인스턴스면 `leaving()`을 부른다 | 그 컴포넌트를 요소의 `data-state`로 **다시 join**한다 |
| `mount`, `on_mount` 훅, `joined()`, join의 첫 렌더 | 로그를 남기고 저장소에서 지운다. `leaving()`은 `joined()`가 돌았을 때만 부른다 — `joined()`가 던졌으면 부르고, `mount`·`on_mount` 훅이 던졌으면 부르지 않는다 | 요소를 그대로 두고 `wireview-error` 클래스를 붙인다. 다시 시도하지 않는다 |
| 아무도 기다리지 않는 태스크의 나머지: `start_async`·`assign_async`가 결과를 받은 뒤의 렌더 요청, `assign_async`의 `on_error`, `allow_upload()`의 config 전송 | 끝나는 즉시 `wireview` 로거에 ERROR와 트레이스백을 남긴다. 취소는 남기지 않는다. 컴포넌트는 그대로다(#151) | 없음. config가 가지 않으면 업로드 입력은 그려지지만 아무 동작도 하지 않는다 |
| 클라이언트가 보내지 않는 메시지(모르는 command, 인자가 맞지 않는 payload) | WARNING 로그를 남기고 버린다 | 없음 |
| 핸들러가 아닌 이름으로 온 이벤트(`_private`, 없는 이름, `joined` 같은 프레임워크 메서드) | WARNING 로그를 남긴다. 아무것도 호출하지 않는다 | 이벤트가 끝났다는 빈 응답을 받아 로딩 상태가 풀린다 |

### 다시 join하면 이벤트 전으로 돌아간다

요소의 `data-state`는 마지막으로 받은 렌더의 서명 상태다. 곧 **예외를 던진 이벤트가 오기 전의 상태**다.
그래서 다시 join하면 핸들러가 던지기 전에 바꾼 필드는 사라진다.

```python
async def transfer(self, amount: int, **_):
    self.balance -= amount       # 이 변경은 남지 않는다
    await self._charge(amount)   # 여기서 던지면
```

되돌아가는 것은 **컴포넌트 상태뿐**이다. 던지기 전에 DB에 쓴 것, 이미 보낸 브로드캐스트, 이미
나간 스트림 조작, 외부 API 호출은 그대로 남는다. 여러 쓰기가 함께 성공하거나 함께 실패해야 하면
`transaction.atomic`으로 묶는다.

다시 join하는 동안 사용자가 입력하던 값은 지켜진다. 던진 이벤트가 Enter나 submit이었어도 입력칸을
비우지 않는다. 그 이벤트의 답은 오지 않았기 때문이다.

같은 이벤트가 매번 던지면 누를 때마다 다시 join할 뿐 반복되지는 않는다. 다시 join하는 것은 이벤트가
아니라 join이기 때문이다.

LiveComponent가 던지면 그것을 소유한 **루트 컴포넌트가 트리째** 다시 join한다. LiveComponent는
자기 join이 없기 때문이다([live-component](./live-component.md)).

### join 실패는 다시 시도하지 않는다

mount나 `joined()`가 던지면 다시 해도 또 던질 가능성이 높다. 그리고 페이지의 렌더마다 다시 시도하면
그것이 곧 루프다. 그래서 요소는 HTTP 응답이 그린 그대로 남고 `wireview-error` 클래스가 붙는다. 서버는 그
연결 동안 그 컴포넌트의 코드를 돌리지 않는다 — 이벤트를 보내면 핸들러를 돌리지 않고 빈 render로
답해(버튼의 로딩 표시는 그것으로 끝난다), 훅의 `pushEvent`와 업로드는 받지 않는다. 브로드캐스트
(`mutation()`·`notification()`)와 `params_changed()`, 다른 컴포넌트의 `wire.defer()`, 부모의
`update_live_component`도 닿지 않고, 그 컴포넌트를 따로 렌더하지도 않는다. 따로 렌더하면 그 패스가 만든
LiveComponent의 `joined()`가 돌기 때문이다. 컴포넌트 코드를 부르는 경로는 저장소의 `reachable()`로
인스턴스를 찾고, 이벤트의 빈 답과 따로 하는 렌더의 거절은 `refused()`를 직접 본다.

다시 시도하는 것은 페이지가 그 id로 join을 다시 보낼 때다. 다음 **연결**(재연결이나 새로고침), boost 이동이
가져온 서버의 새 HTML, 부모의 렌더가 새로 그린 요소(`{% if %}`로 숨겼다 다시 보인 것)가 그렇다. 서버는 그
join을 새 시도로 받아 실패 기억을 지우고 정상적으로 join한다. 부모의 렌더가 **같은 요소를** 고쳐 그릴 때는
페이지가 join을 다시 보내지 않으므로 루프가 생기지 않는다.

부모의 템플릿 패스는 그 id로 새 인스턴스를 만들지만 아무도 join하지 않는다 — `joined()`가 돌지 않은
인스턴스다. 서버는 그것도 위와 같이 막고, 그 요소에 `wire-join-failed` 속성을 그려 보낸다. 부모의 렌더가
그것을 제 HTML 안에 그리는 것과, 그 패스가 그리기 전에 도는 `on_mount` 훅(경계 검사)만은 돈다.
`joined()`가 돌지 않았으므로 떠날 때 `leaving()`도 받지 않는다(남은 비동기 작업은 취소된다). 페이지는 패치 뒤 그 속성을 `wireview-error`로 보여 준다. 클래스 대신
속성인 것은 템플릿이 태그 뒤에 쓴 자기 `class`를 덮지 않기 위해서다. CSS로 고르려면 `.wireview-error`나
`[wire-join-failed]`를 쓴다.

그 컴포넌트가 **소유한** LiveComponent도 같다. 소유는 서버가 안다 — 그 LiveComponent를 만든 템플릿 패스가
누구의 것인가다. 실패한 컴포넌트의 **슬롯**에 놓인 LiveComponent는 슬롯을 채운 쪽의 것이라 살아 있고, 그대로
동작한다. 실패한 요소 안에 중첩된 일반 Component는 자기 join이 따로 있으므로 그 join대로 동작한다.

페이지는 실패한 컴포넌트와 그 LiveComponent를 등록에서 빼지 않는다. 그래서 요소가 페이지를 떠나면(부모의
`{% if %}`) 그 훅은 `destroyed()`를 받는다.

`on_mount` 훅의 예외는 여전히 **거절과 같다**. 컴포넌트는 저장소에 남지 않고, 새 렌더도 서명 상태도
나가지 않는다. 요소가 페이지에 남는 것은 HTTP 렌더가 이미 보낸 것이 남는 것뿐이다
([lifecycle-hooks](./lifecycle-hooks.md#실행-순서)).

## 개발 중에 템플릿을 고치면

join 실패는 다시 시도하지 않으므로, 템플릿을 깨뜨린 채 저장하면 그 컴포넌트는 그 연결에서 막힌다. 파일을
고쳐도 페이지가 그 id로 join을 다시 보내기 전까지는 그대로다. 그래서 개발 서버에서는 템플릿이 바뀔 때마다
열린 페이지가 컴포넌트를 다시 join한다(#180).

- Django의 자동 리로더(`runserver`, daphne의 `runserver` 포함)는 템플릿 디렉터리의 파일이 바뀌면 프로세스를
  다시 시작하지 않고 템플릿 로더만 비운다. 같은 신호(`django.utils.autoreload.file_changed`)를 받아 그
  프로세스의 열린 연결마다 `rejoin`을 보낸다([wire-protocol](../implementation/wire-protocol.md) §3).
- 페이지는 join한 컴포넌트마다 요소의 `data-state`, 곧 지금 상태로 다시 join한다 — 예외 뒤의 다시 join과 같은
  길이다. 멀쩡하던 컴포넌트는 저장 직후 새 템플릿으로 다시 그려지고 상태는 그대로다. join이 실패했던
  컴포넌트는 그 join에서 다시 시도된다. 고친 뒤라면 돌아오고, 아직 깨져 있으면 다시 `wireview-error`가 된다.
- 저장하는 순간 처리 중이던 클릭도 되돌리지 않는다. 서버는 처리 중인 핸들러나 join의 답을 보낸 뒤에 알리고,
  페이지는 그때까지 보낸 이벤트의 답을 모두 받은 뒤(`sync`) 그 render가 담은 상태로 join한다. 기다리는 동안의
  클릭, 그리고 다시 join하며 도는 DOM 콜백·훅이 보내는 것은 붙잡았다가 다시 join한 컴포넌트에 보낸다. 답이 10초
  안에 오지 않으면 다시 join하지 않고 붙잡은 것을 보낸다 — 저장 전과 같은 화면이다. 지켜지는 것은 서명 상태(`data-state`)에 실린 필드다 —
  다시 join은 `joined()`·`leaving()`을 다시 부르고, 끝나지 않은 `start_async()`·`assign_async()` 작업과 업로드는
  예전 인스턴스와 함께 끝나며, temporary assign과 `joined()`가 불러온 것은 새로 채운다.
- `wireview:error`는 나지 않는다. 다시 join이 실패한 경우에만 그 실패의 `error`가 온다.
- `.py` 파일과 템플릿 디렉터리 밖의 파일은 Django가 프로세스를 다시 시작한다. 그러면 페이지는 끊겼다 다시
  연결하고, 새 연결은 실패를 기억하지 않으므로 모든 컴포넌트가 지금 상태로 join한다. `uvicorn --reload`처럼
  바뀔 때마다 프로세스를 다시 시작하는 서버도 같은 길이다 — 재연결 대기(`RECONNECT_*`)만큼 늦을 뿐이다.

`REJOIN_ON_TEMPLATE_CHANGE`(기본 `None` = `DEBUG`)로 끄고 켠다([설정](./settings.md#개발-도구)). 자동 리로더가
없는 운영 서버에서는 신호가 오지 않고, 꺼져 있으면 연결이 등록되지도 않는다. 알리는 범위는 리로더가 돈 그
프로세스의 연결이다 — 각 연결은 알림을 자기 채널로 한 번 보내 처리 순서에 넣을 뿐, 다른 프로세스에 퍼뜨리지
않는다. Redis·NATS 레이어에서도 같다. 깨진 동안 무엇이 틀렸는지는 서버 로그에 있다(`Could not join …`의 traceback).

## 브라우저에서 알리기

두 경우 모두 요소에서 `wireview:error` 이벤트가 버블링된다.

```js
document.addEventListener("wireview:error", (e) => {
  const { id, during } = e.detail; // during: "event" | "join"
  showToast(during === "join" ? "이 영역을 불러오지 못했습니다" : "요청을 처리하지 못했습니다");
});
```

join에 실패한 요소에는 `wireview-error` 클래스가 붙는다. 다음 연결의 join을 보낼 때 떨어진다.

같은 id로 join을 다시 보낸 뒤(boost 이동으로 같은 id의 새 DOM, 예외 뒤 재join) 도착한 **이전 join의**
`error`는 새 요소에 붙지 않는다. 페이지는 join마다 번호(`ref`)를 싣고, 그 번호가 돌아온 응답만 지금 join의
것으로 받는다(#139). 그 전에는 이전 join의 `error`가 새 요소에 `wireview-error`를 붙이고 컴포넌트를 등록에서
빼, 새 join의 render가 적용될 곳이 없었다 — 다음 연결까지 죽은 채였다. join의 다른 응답도 같다(#146).
`on_mount`가 halt한 이전 join의 `remove`는 새 요소를 지우지 않고, id를 알 수 있는 이전 join의 `reload`(상태가
다른 클래스의 것, live_session 거절)는 페이지를 다시 불러오지 않으며, 이전 join의 `joined`는 새 요소의 무한
스크롤을 시작하지 않는다. 만료·서명 실패의 `reload`는 id가 없어 짝지을 수 없으므로 어느 join의 것이든 받는다 —
페이지를 다시 불러올 뿐이다. 이전 부모 인스턴스의 LiveComponent가 보낸 자기 render와 `remove`도 부모의 join으로
가려, 새 요소에 그리거나 새 요소에서 지우지 않는다. 연결의 첫 join은 서버가 버전을 알리기 전이라 번호가 없고,
번호 없는 join의 응답은 예전처럼 오는 대로 받는다.

```css
.wireview-error { opacity: 0.6; pointer-events: none; }
```

## 옛 번들

`error`는 프로토콜 버전 4에서 생긴 명령이다([wire-protocol](../implementation/wire-protocol.md) §3, §7).
배포 중에 옛 번들로 열린 페이지에는 이전과 같이 동작한다. 이벤트 중 예외면 소켓을 닫고(코드 1011),
join 실패면 요소를 지운다.

## 테스트에서

`mount()`로 만든 컴포넌트의 `call()`은 핸들러를 직접 부르므로 **예외가 그대로 올라온다**. 위의 복구는
WebSocket 연결의 동작이다. 복구 자체를 검증하려면 `tests/test_errors.py`처럼
`WebsocketCommunicator`로 연결한다.

## Phoenix LiveView 대응

| Phoenix | wireview |
|---|---|
| LiveView 프로세스가 죽고 클라이언트가 다시 마운트 | 그 컴포넌트만 이벤트 전 상태로 다시 join. 연결은 유지 |
| mount가 계속 실패하면 클라이언트가 재시도 후 오류 표시 | 재시도 없이 `wireview-error` |
| `phx-error` 클래스 | `wireview-error` 클래스, `wireview:error` 이벤트 |
| 개발 중 템플릿 변경을 live reload가 페이지 새로고침으로 반영 | 같은 소켓에서 컴포넌트만 지금 상태로 다시 join(`rejoin`) |
