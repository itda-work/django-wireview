# LiveComponent 수명주기 — 지금 실제로 일어나는 일

> 2026-09-09. 코드를 읽고 재현해 확인한 사실과, 그로부터 나오는 설계 질문.
> 계기: `docs/design/live-session.md` 초안이 등록 시점을 반대로 적었다. 고치려고 전체 경로를
> 읽었고, Codex(gpt-6-astra) 3라운드 리뷰가 **이 문서의 초판도 여러 곳에서 뒤집었다.**
> 초판은 "재연결하면 상태가 사라진다"고 적었는데 틀렸다 — 사라지는 게 아니라 **두 번 초기화된다.**

## 1. 확인된 동작

### 1-1. 자식은 부모 렌더 **도중** 만들어지고 그 자리에서 렌더된다

`{% live_component %}`(`templatetags/wireview.py:787-795`)는 부모 템플릿을 평가하는 중에
`repo.build_live_component()`로 자식을 만들어 `repo.components`에 등록하고, 즉시 `_render()`해서
그 HTML을 부모 출력에 끼워 넣는다. `joined()`는 나중이다.

**HTTP 최초 렌더도 같다.** `is_live=False` 저장소로 같은 태그가 돌고, 그 경로에는 자식 flush가
아예 없다. 그러므로 "`joined()` 이전 HTML이 나간다"는 WebSocket 첫 응답만의 문제가 아니라
**최초 HTTP 응답에도** 해당한다. `joined()`를 인가 경계로 삼으면 두 번 샌다.

### 1-2. `joined()`가 도는 시점은 진입점마다 다르고, 아예 안 도는 경로가 있다

`_flush_pending_live_components()`를 부르는 곳은 여섯 군데뿐이다(`consumer.py:104,111,160,194,355,366`).

| 진입점 | 자식 flush |
|---|---|
| `command_join` | 있다 (부모 렌더 뒤) |
| `command_user_event` | 있다 |
| `command_hook_event` | 있다 |
| `component_dispatch_event`, `component_send_render` | 있다 |
| **`command_params_changed` / `query_string`** | **없다** |
| **notification / model mutation (브로드캐스트 수신)** | **없다** |
| `component_update_live_component`, 업로드 경로 | 없다 |

**브로드캐스트나 URL 변경으로 처음 화면에 등장한 자식은 `joined()`가 실행되지 않는다.** HTML과
서버 등록은 생기고 초기화만 빠진다. 그 큐는 나중에 무관한 이벤트가 flush할 때 뒤늦게 돈다.

### 1-3. 재연결은 상태를 잃지 않는다. 대신 **두 번 초기화한다** (재현 확인)

초판은 "상태가 사라진다"고 적었다. 저장소 수준에서 `build_live_component`가 `repo.children`을
보지 않는 것은 사실이지만, 브라우저는 거기서 끝나지 않는다.

클라이언트는 재연결 시 DOM을 훑어 부모를 join하고, **부모가 live로 표시되는 순간 자식도 자기
`data-state`로 별도의 join을 보낸다**(`wireview.js:938-959`). LiveComponent를 제외하는 분기가 없다.
서버의 `repo.join()`은 이미 등록된 인스턴스여도 **항상 `joined()`를 부른다**(`repository.py:227-229`).

```python
child = repo.build_live_component("ProbeCounter2", {"id": "c1", "count": 0}, parent_id="p1")
await repo.flush_pending_live_components()                 # joined() #1  (count=0, 템플릿 prop)
same  = await repo.join("ProbeCounter2", {"id": "c1", "count": 7})   # joined() #2  (count=7, 복원)
assert same is child and CALLS == [("c1", 0)]
# AssertionError: joined() ran more than once: [('c1', 0), ('c1', 7)]
```

그래서 정확한 결함은 이것이다.

- 상태는 **자식 자신의 join 경로로 복원된다.** `children` 맵은 무시되고, 복원은 초기화 **뒤**에 온다.
- 같은 인스턴스에서 **`joined()`가 두 번 돈다.** 구독·DB 쓰기·외부 호출을 거기 둔 컴포넌트는 두 번 한다.
- 첫 `joined()`는 템플릿 prop을 보고, 두 번째는 복원된 상태를 본다. 훅이 값을 초기화하면 결과가 또 달라진다.

### 1-4. 부모 재렌더는 자식 상태를 prop으로 덮어쓴다

`changed_props`는 이름과 달리 **값을 비교하지 않는다.** id를 뺀, 전달된 모든 모델 필드다
(`repository.py:135-137`). 부모가 `count=0`을 계속 넘기면 자식이 이벤트로 7까지 올려도 다음 부모
렌더에서 0으로 돌아간다. "초기 prop이 한 번만 적용된다"가 아니다.

일반 중첩 컴포넌트의 복원 병합도 비슷하다. `build()`의 `child_state | state`는 **템플릿이 넘긴
prop이 복원된 값을 이긴다.** 템플릿이 넘기지 않는 필드만 복원된다.

| 필드 | 복원 | 템플릿 prop | 결과 |
|---|---|---|---|
| `count` | 7 | 0 | **0** |
| `note` | `"kept"` | 없음 | **`"kept"`** |

### 1-5. 새 자식과 기존 자식의 처리가 비대칭이다

새 자식만 `enter_pending_mode()`에 들어가고, 기존 자식의 `update()` 경로는 들어가지 않는다
(`repository.py:199` vs `:208`).

**다만 이것이 "전송 순서가 뒤집힌다"는 뜻은 아니다.** `stream()`·`push_js()`는 자기 세션에도
채널 레이어를 한 번 거치고(`core/meta.py:446` → `transport.py` → `consumer.component_*`), Channels가
dispatch를 순차로 await하므로 현재 핸들러의 렌더가 끝난 뒤 처리된다. 실제로 순서가 갈리는 것은
**브로드캐스트**다 — pending이 아니면 즉시 publish되어 다른 세션이 먼저 본다(`core/meta.py:149`).
pending은 임의의 외부 I/O도 막아 주지 않는다.

### 1-6. `leave`는 `leaving()`을 부르지 않는다

`command_leave`는 업로드 레지스트리 해제와 `repo.remove()`(dict pop)만 한다(`consumer.py:118-122`).
그래서 **정상적으로 DOM에서 사라져 leave된 컴포넌트는 `leaving()`을 받지 못한다.** 이미 pop되었으므로
나중 disconnect의 `leaving()` 대상에서도 빠진다. 정리 코드를 `leaving()`에 둔 사용자는 그 기회를 잃는다.

leave는 구독 재계산도 하지 않는다. 마지막 구독자가 빠진 그룹이 다음 chores나 disconnect까지 남는다.

### 1-7. 고아 정리는 클라이언트 통지에 달려 있다 — 다만 정상 경로는 대체로 통지한다

서버에는 "렌더에서 사라졌으니 지운다"는 판정이 없다. 정리는 클라이언트가 DOM 스캔에서 사라진 id를
발견해 `leave`를 보낼 때 일어난다(`wireview.js:158-172`).

초판은 스트림 교체·JS 명령을 통지 유실 예로 들었는데 **틀렸다.** morph, append/prepend/replace/remove,
스트림 reset/insert/delete/limit trim은 모두 끝나고 `sendNewContent`로 스캔을 돌린다. 실제로 통지가
없거나 늦는 경로는 다음이다.

1. 사용자 훅이나 외부 JS가 `element.remove()`·`innerHTML`로 지우고 새 콘텐츠 이벤트를 안 낼 때.
2. `exec_js`가 부른 사용자 리스너가 DOM을 지우거나, `remove_attr`로 `wireview-component` 표지를 뗄 때.
3. 렌더가 아직 `requestAnimationFrame` 대기 중이거나 대상 element가 없어 그 렌더에서 스캔이 안 돌 때.
4. **서버에는 있는데 클라이언트가 한 번도 등록하지 않은 id.** 스캔은 "클라이언트가 아는 id"와 현재
   DOM만 비교하므로 이런 고아는 발견 자체가 불가능하다.

연결 종료는 다르다. `disconnect`는 등록된 컴포넌트마다 `leaving()`을 부르고 구독을 정리하며,
저장소는 연결 단위라 재연결 때 새로 생긴다. "끊긴 leave 때문에 영원히 남는다"는 초판 서술은 틀렸다.

### 1-8. 클라이언트에서 자식의 두 번째 렌더가 버려질 수 있다

렌더 수신은 `this.components[id]?.applyDiff(diff)`이고(`wireview.js:184`), 부모의 patch와 새 자식
등록은 `requestAnimationFrame`까지 미뤄진다(`:786`). 그래서

1. 부모 render 수신 → RAF 예약
2. 부모 RAF 실행 전에 자식의 `joined()` 후 render 도착
3. `components[childId]`가 아직 없어 **자식 diff를 버림**
4. 부모 RAF가 `joined()` 이전 자식 HTML을 붙이고 객체를 등록. `data-is-live=true`라 join도 안 보냄

즉 서버가 두 번 렌더해도 최종 상태가 화면에 반영된다는 보장이 없다. 코드상의 race이고
**브라우저 E2E로 재현한 것은 아니다.**

### 1-9. id 재사용에 클래스·부모 검증이 없다

기존 객체 경로는 `isinstance(LiveComponent)`만 본다(`repository.py:133`). 같은 id로 다른 자식
클래스를 렌더하거나 boost 뒤 다른 부모 아래 두면 이전 클래스와 이전 `_parent_id`를 재사용한다.

## 2. 설계 질문

`docs/design/live-component.md` §4.1은 이미 **joined → render**를 설계 목표로 적고 있다. 그러므로
질문 1은 새 결정이 아니라 **"원래 설계를 구현할 것인가, 계약을 개정할 것인가"** 다.

1. **첫 렌더가 `joined()`를 기다려야 하는가.** "정책 통과 전 무출력"과 "모든 비동기 초기 데이터를
   기다림"은 다른 요구다. 전자만 필요하면 선택지가 넓다(3절).
2. **`joined()`는 인스턴스당 한 번인가, join 메시지당 한 번인가.** 지금은 후자라 재연결에서 두 번 돈다.
   HTTP→WS 전환과 재연결을 각각 어떤 수명주기로 볼지 정해야 한다.
3. **자식은 부모 소유인가, 독립 join도 허용하는 개체인가.** 지금은 둘 다다 — 부모가 만들고, 자식도
   스스로 join한다. 복원 값·부모 prop·`joined()`의 우선순위를 정하려면 이것부터 정해야 한다.
4. **모든 렌더 진입점에서 초기화·prop 반영·구독·부수효과의 순서를 누가 보장하는가.** 지금은 진입점마다 다르다.
5. **`update()`의 의미.** 최초 생성에는 안 불리고, 값이 같아도 매번 불리며, 템플릿 경로와
   `send_update` 경로의 필드 필터가 다르다.
6. **leave·reparent·id 재사용에서 `leaving()`·구독·업로드·실행 중 작업을 누가 정리하는가.**
7. **초기화 실패나 halt에서 무엇을 롤백하는가.** 등록·pending·이미 보낸 렌더·부수효과 중 무엇을.
8. **자식의 직접 join에 부모 연결과 정책의 증거를 요구할 것인가.** live_session의 경계와 직결된다.

## 3. 질문 1의 선택지

동기 템플릿이라 async 훅이 **불가능한 것은 아니다.** 템플릿 렌더 자체가 이미
`database_sync_to_async`로 돈다(`core/meta.py:270`). 제약은 전환 비용과 DB 스레드 친화성이다.

- **A. 태그 안에서 초기화까지 끝내고 렌더한다.** worker 스레드에서 `async_to_sync`로 훅을 완료한 뒤
  HTML을 만든다. 리뷰어가 `async → sync_to_async(template) → async_to_sync(hook) → sync_to_async(db)`
  중첩이 실제로 도는 것을 확인했다. 다만 전환 비용과 자식 수만큼의 반복이 남는다.
- **B. 발견과 렌더를 분리한다.** 동기 태그는 자식 **명세**만 기록하고, 바깥 async 단계가 생성·인가·
  초기화한 뒤 최종 HTML을 합성한다. `send_render` 앞으로 flush를 옮기는 것만으로는 안 된다 —
  그 시점에 자식이 아직 없다. 중간 표현이나 준비 패스가 필요하다.
- **C. 안전한 placeholder를 먼저 보낸다.** 등록·정책 검사 뒤 loading shell만 내보내고, 초기화가
  끝나면 실제 내용을 보낸다. 민감한 내용과 `data-state`를 먼저 보내지 않는 것이 조건이다.
  CSS로 숨기는 것은 답이 아니다.

## 4. 이 문서의 위치

현황과 질문만 담는다. 답은 각 이슈에서 정한다. 1-2·1-3·1-6은 설계 이전에 **버그**이므로 별도 이슈다.
정책·인증 경계에서의 요구는 [live-session.md](./live-session.md) §3-5.
