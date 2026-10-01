# 서버 오류 처리

컴포넌트 코드가 예외를 던지면 그 컴포넌트 하나만 대가를 치른다. 연결과 같은 페이지의 다른
컴포넌트는 그대로 동작한다(#94).

전에는 예외가 컨슈머 밖으로 나가 WebSocket이 닫혔다. 클라이언트는 `wireview-disconnected`를
붙였다가 재연결하고 페이지의 **모든** 컴포넌트를 다시 join했다. 무엇이 잘못됐는지는 서버 로그에만 남았다.

## 무엇이 일어나는가

| 무엇이 던졌나 | 서버 | 브라우저 |
|---|---|---|
| 이벤트 핸들러, 브로드캐스트 수신(`notification`·`mutation`), `params_changed`, 훅 이벤트, 업로드 콜백, `start_async`의 `handle_async`, LiveComponent `update()`, 렌더 | 로그(`log.exception`)를 남기고 인스턴스를 버린다. `leaving()`을 부른다 | 그 컴포넌트를 요소의 `data-state`로 **다시 join**한다 |
| `mount`, `on_mount` 훅, `joined()`, join의 첫 렌더 | 로그를 남기고 저장소에서 지운다. `leaving()`을 부른다 | 요소를 그대로 두고 `wireview-error` 클래스를 붙인다. 다시 시도하지 않는다 |
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
그것이 곧 루프다. 그래서 요소는 HTTP 응답이 그린 그대로 남고, 이벤트는 서버로 가지 않는다. 다음
**연결**(재연결이나 새로고침)에서 다시 join한다.

부모의 렌더가 그 요소를 다시 그려도 같다. 부모의 템플릿 패스는 그 id로 새 인스턴스를 만들지만 아무도
join하지 않는다 — `joined()`가 돌지 않은 인스턴스다. 페이지는 그 연결 동안 그 요소를 받아들이지 않는다.
클릭도 훅의 `pushEvent`도 보내지 않고, 다시 그려져도 `wireview-error`를 유지한다. 전에는 서버의 HTML이
클래스를 지우고 페이지가 그 요소를 다시 등록해, 이벤트가 join되지 않은 인스턴스에 닿았다.

`on_mount` 훅의 예외는 여전히 **거절과 같다**. 컴포넌트는 저장소에 남지 않고, 새 렌더도 서명 상태도
나가지 않는다. 요소가 페이지에 남는 것은 HTTP 렌더가 이미 보낸 것이 남는 것뿐이다
([lifecycle-hooks](./lifecycle-hooks.md#실행-순서)).

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
