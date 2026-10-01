# LiveComponent 소유권과 렌더 분리 — 결정

> 2026-09-09. [live-component-lifecycle.md](./live-component-lifecycle.md)가 남긴 여덟 질문에 대한 답과,
> #78·#79·#80을 한 원인으로 묶어 고치는 계획. **구현됨** (같은 날, 아래 §6의 순서대로). 실측 전후는 §5.

## 1. 결정

**자식은 부모가 소유한다. 부모 템플릿에는 자식의 참조만 남고, 자식의 초기화와 렌더는 부모 렌더 뒤
비동기 단계에서 처리해 같은 `render` 메시지에 묶어 보낸다.** Phoenix의 CID 모델을 Django 동기 템플릿
위에 옮긴 것이고, lifecycle 메모 §3의 선택지 **B**다.

지금 결함의 뿌리는 하나다. `{% live_component %}`가 동기 템플릿 평가 중에 자식을 만들고 **그 자리에서
렌더까지** 한다. 거기서 네 가지가 동시에 파생된다.

| 파생 결함 | 메모 절 | 이슈 |
|---|---|---|
| `joined()` 전에 자식 HTML이 나간다. 초기화는 렌더 뒤 별도 flush에 의존하고, 진입점마다 flush를 기억해야 한다 | 1-1, 1-2 | #79 |
| 자식이 자기 join도 보내므로 초기화 경로가 둘이다. 같은 인스턴스에서 `joined()`가 두 번 돈다 | 1-3 | #78 |
| 자식 HTML이 부모 diff 안에 통째로 중복된다. 부모 재렌더가 자식 상태를 prop으로 덮어쓴다 | 1-4 | — |
| 클라이언트는 부모의 RAF 뒤에야 자식을 등록하므로 그 사이 도착한 자식 diff를 버릴 수 있다 | 1-8 | — |

"발견"과 "렌더"를 떼어 놓으면 넷이 같이 사라진다. #80(`leave`가 `leaving()`을 안 부름)은 독립 버그지만
소유권 규칙(부모 제거 시 자식 cascade)이 여기서 정해지므로 같은 묶음으로 다룬다.

## 2. 기각한 대안

- **A. 태그 안에서 `async_to_sync`로 `joined()`를 끝내고 인라인 렌더.** 리뷰어가 중첩 전환이 동작함을
  확인했지만, 자식 수만큼 이벤트 루프를 왕복한다. 이 라이브러리가 `_get_context_async`를 만든 이유
  (`core/meta.py:246`)와 반대 방향이고, 부모 diff에 자식 HTML이 중복되는 문제와 RAF race는 그대로 남는다.
- **C. 안전한 placeholder를 먼저 보내고 초기화 뒤 본문을 보낸다.** 두 번 그리기와 메시지 2개가 남고,
  자식 등록보다 diff가 먼저 도착하는 race도 따로 풀어야 한다. B가 되면 C는 필요 없다.
- **자식의 독립 join을 유지하고 서버를 멱등하게만 만든다.** `joined()` 중복은 막을 수 있지만 상태의
  진실 소스가 두 개(부모 children 맵, 자식 data-state)로 남는다. 경로를 하나로 줄이는 쪽이 맞다.

## 3. 규칙

### 3-1. 소유권

- 클라이언트는 `wireview-live` 요소에 `join`을 보내지 않는다. 상태는 이미 부모 join의 `children`으로
  실려 간다. 훅·viewport 초기화는 그대로 한다.
- 서버는 등록된 LiveComponent id로 `join`이 오면 무시하고 debug 로그만 남긴다. 캐시된 구형 JS를 위한
  방어다.
- `joined()`는 **인스턴스당 한 번**이다. 재연결은 새 연결이므로 새 인스턴스가 생기고 다시 한 번 돈다.
- 재연결 복원은 `build_live_component`가 `children` 맵을 보고 **`복원값 | 부모 prop`** 으로 만든다.
  부모가 넘기는 필드는 부모가 진실이고, 넘기지 않는 필드는 자식 것이다. Phoenix와 같은 우선순위이며
  일반 중첩 Component의 `build()`와도 같다.
- 맵의 항목은 **그것을 실은 join의 루트**와 수명을 같이한다. 루트가 떠나면(`repo.remove`, join 실패 포함)
  남은 항목도 버린다. 첫 render가 그리지 않은 자식도 있기 때문이다 — `joined()`가 다시 시작한
  `assign_async`의 결과 안에 있는 LiveComponent는 작업이 끝난 뒤의 render에서야 만들어지고, 그때 항목을
  꺼내 재연결 전 상태로 돌아온다. join이 끝날 때 맵을 통째로 비우던 때는 이것이 기본값으로 시작했다.
  핸들러가 던져 루트를 버릴 때(`_crashed`)는 항목을 남긴다(`remove(keep_carried=True)`). 롤백 join은 같은 id로
  곧 오고, 페이지의 요소에는 첫 render가 지운 자식이 없어 다시 실을 수 없기 때문이다.
- 그 결과 안에 **일반 Component**가 있고 그 안에 LiveComponent가 있으면, 재연결한 페이지는 루트의 join 바로
  뒤에 그 일반 Component의 join도 보낸다 — 루트가 live로 표시됐고 첫 render를 아직 패치하지 않아 요소가
  남아 있다. 루트가 실은 항목이 있는데 루트의 패스가 아직 만들지 않은 id(`repo.undrawn`)의 join은 서버가
  답하지 않고 버린다. 페이지는 곧 그 요소를 떠나보낸다. 받아들이면 그 join이 루트가 실은 자기 항목과 안의
  LiveComponent 항목을 꺼내 쓰고 떠나면서 가져가, 작업이 끝난 뒤의 render가 둘 다 기본값으로 그렸다.
- 이미 만들어진 id의 항목은 싣지 않는다. 중첩된 일반 Component의 join은 안의 컴포넌트 상태를 다시
  싣는데, 그것들은 바깥 join의 패스가 이미 만들었으므로 아무도 꺼내지 않는다. 남겨 두면 `{% if %}`로
  숨겼다 다시 보인 **새** 인스턴스가 그 옛 상태로 시작했다.

### 3-2. 발견 단계 — 동기, 템플릿 안

- live 렌더에서 `{% live_component %}`는 자식을 등록하고 이번에 넘긴 props를 기록한 뒤 **참조 dynamic**
  하나만 출력한다. HTML 상으로는 `<!--$n--><!--@wv:c1--><!--/$n-->`, 파싱 결과는 `ComponentRef("c1")`,
  페이로드는 `{"c": "c1"}`이다. 값이 id뿐이라 부모가 몇 번 재렌더돼도 이 슬롯은 바뀌지 않는다.
- HTTP 최초 렌더(`is_live=False`)는 지금처럼 인라인으로 그린다. dead render이고 `joined()`는 없다.
  Component도 HTTP에서 `joined()`를 받지 않으므로 계약이 같다.
- 대조: 일반 `{% component %}`는 live 렌더에서도 부모의 패스 안에서 build·mount되어 인라인으로 그려지고,
  이 수명주기 단계를 타지 않는다. 그 인스턴스를 완성하는 것은 **페이지의 join**이다. HTTP 페이지의
  중첩 컴포넌트는 페이지가 연결할 때 join하고, 연결된 페이지에서 렌더가 새로 그린 것(`{% if %}`가 다시
  그림)은 `data-is-live="true"`로 오므로 페이지가 그 요소를 처음 볼 때 join한다(2026-10-01). 서버는 패스가
  만들고 아직 join되지 않은 인스턴스를 그 join에 넘겨 `joined()`를 한 번 부르고, 답하는 render 뒤에
  `joined`를 보낸다. 그 전에는 아무도 그것을 join하지 않아 `joined()`가 돌지 않았고 무한 스크롤도
  시작되지 않았다. 서버가 패스 뒤에 `joined()`를 부르는 안(LiveComponent처럼)은 고르지 않았다 — 인라인
  HTML이 이미 계산된 뒤라 `joined()`가 바꾼 상태가 그려지지 않고, 연결할 때의 join 렌더에서는 페이지도
  그 요소를 join하므로 `joined()`가 두 번 돈다.

### 3-3. 수명주기 단계 — 비동기, `send_render` 안

부모 diff를 계산한 뒤, 템플릿이 실제로 평가됐을 때만 다음을 한다.

1. **사라진 자식.** 부모 아래 등록됐지만 이번 렌더에서 발견되지 않은 자식은 `leaving()` 뒤 제거한다.
   서버가 처음으로 고아를 스스로 판정한다. 조건부 렌더로 사라진 자식은 상태를 잃는다. Phoenix가 CID를
   지우는 것과 같은 의미론이다.
2. **새 자식.** pending 모드에 넣고 `joined()`를 한 번 부른다.
3. **기존 자식.** 이번 props가 **직전에 넘긴 props와 값이 다를 때만** `update()`를 부른다. 비교 대상은
   자식의 현재 상태가 아니라 직전 props다. 그래야 자식이 스스로 바꾼 값이 부모 재렌더에 덮이지 않는다.
4. 2·3의 자식을 async 경로(`render_diff`)로 렌더한다. 그 렌더가 손자식을 발견하면 같은 절차를 재귀한다.
5. 예외는 자식 단위로 격리해 로깅하고, pending 모드 해제는 `finally`로 보장한다.
6. 부수효과 순서: 번들 전송 → 구독 동기화 → 자식·부모의 pending 큐 flush. 구독 준비 전에 초기
   broadcast가 나가는 리뷰 G 항목이 여기서 닫힌다.

컨슈머의 `_flush_pending_live_components()` 호출 여섯 곳은 전부 삭제된다. 렌더를 보내는 곳이 한 곳이면
잊을 수 없다.

### 3-4. 전송

- `render` 페이로드에 `children: {"<id>": diff | null}`을 더한다. 이번 렌더 트리에서 렌더된 자식만 들어간다.
- 클라이언트는 수신 즉시(RAF 전에) 자식 상태 객체를 등록하고 diff를 적용한다. 부모 HTML을 합성할 때
  참조를 자식의 현재 HTML로 치환하고 morph 한 번으로 그린다.
- diff 데이터 적용은 수신 시점에 동기로, DOM morph만 RAF로 미룬다. 지금은 둘 다 RAF 안에 있어서 번들
  경로와 단독 경로가 섞이면 순서가 뒤집힐 수 있다.
- 자식 단독 이벤트는 지금처럼 자식 `render`만 나간다.
- 등록한 자식의 요소는 그 render를 받은 컴포넌트의 패치가 다음 프레임에 넣는다. 그 사이에 다른 컴포넌트의
  패치가 먼저 돌면 그 뒤의 `joinAllComponents`가 요소 없는 자식을 본다. 그 패치(또는 그것을 감싼 컴포넌트의
  패치)가 아직 남아 있으면 떠난 것으로 보지 않는다(`drawnBy`). 지웠던 때는 부모 HTML이 참조를 풀 컴포넌트를
  찾지 못해 자식이 그려지지 않았고, 서버는 render를 이미 보냈으므로 다시 보내지 않았다.

### 3-5. leave와 id 재사용

- `command_leave`: `leaving()` await → `_parent_id`가 그 id인 자식에 cascade → 제거 → 구독 재계산.
  예외는 disconnect 경로처럼 로깅하고 삼킨다.
- 클라이언트는 `wireview-live` 요소가 사라져도 `leave`를 보내지 않고 로컬 정리(훅·업로드)만 한다.
  부모 렌더가 이미 그 자식을 떠나보냈다. 보내면 빠른 토글에서 늦게 도착한 `leave`가 다시 보인 **새**
  인스턴스를 지운다. 서버도 LiveComponent id의 `leave`를 무시한다(join과 같은 옛 번들 방어, #140).
  떠난 루트에 늦게 도착한 render는 자식을 등록하지 않고 버린다.
- 클라이언트가 LiveComponent의 루트(그것을 만든 join)를 정할 때는 요소를 감싼 컴포넌트가 아니라
  `data-parent` 사슬을 따라 처음 만나는 일반 Component를 본다. 슬롯은 부르는 쪽의 패스에서 렌더되므로, 다른
  컴포넌트의 슬롯에 놓인 LiveComponent의 부모는 슬롯을 채운 쪽이다. 이 루트로 LiveComponent 자기 render를
  어느 join의 것으로 볼지(#146), join을 다시 보낼 때와 join이 실패했을 때 누구의 업로드·무한 스크롤을 끝낼지
  (#137, #112)를 정한다.
- join이 실패한 컴포넌트는 **서버가** 막는다(`repo.refused`). 연결이 그 id를 기억하고, 부모의 패스가 그 id로
  다시 만든 인스턴스와 그것이 소유한 LiveComponent(`_parent_id` 사슬의 루트가 그 id인 것)에는 이벤트·훅·업로드가
  닿지 않는다. 그 id로 join이 오면 다시 시도한다. 클라이언트가 소유를 추정해 막던 때는 슬롯의 LiveComponent까지
  막고, boost 이동 뒤 다시 join하지 않았다.
- 이동 뒤 같은 id가 LiveComponent에서 루트로(또는 반대로) 바뀔 수 있다. 클라이언트는 join할 때마다 요소의
  `wireview-live`로 소유를 다시 정하고, 떠난 것의 `leave`를 새 것의 join보다 **먼저** 보낸다. join을 먼저
  보내면 서버에는 아직 옛 부모의 LiveComponent가 그 id로 남아 있어 루트의 join을 무시했다(#146).
- 같은 id로 다른 클래스가 오면 이전 인스턴스를 `leaving()` 대기 큐에 넣고 새로 만든다. 다른 부모 아래
  나타나면 `_parent_id`를 바꾸고 debug 로그를 남긴다.

## 4. lifecycle 메모 §2의 질문에 대한 답

| # | 질문 | 답 |
|---|---|---|
| 1 | 첫 렌더가 `joined()`를 기다려야 하는가 | live 렌더는 기다린다(§3-3). HTTP는 dead render로 예외이며 문서화한다. 인가 경계는 #75의 on_mount가 맡는다 |
| 2 | `joined()`는 인스턴스당인가 메시지당인가 | 인스턴스당 한 번 |
| 3 | 자식은 부모 소유인가 독립 개체인가 | 부모 소유. 독립 join 없음 |
| 4 | 진입점마다 다른 순서를 누가 보장하나 | `send_render` 한 곳 |
| 5 | `update()`의 의미 | 부모가 넘긴 props가 직전과 달라졌을 때. 최초 생성은 생성자, `send_update`는 명시 호출이라 항상 |
| 6 | leave·reparent·id 재사용 정리 | §3-5 |
| 7 | 초기화 실패 시 롤백 | 자식 단위 격리와 로깅. 이미 보낸 것은 되돌리지 않는다 |
| 8 | 자식 join에 부모 증거를 요구할까 | 자식 join 자체를 없앤다 |

## 5. 실측

### 5-1. 착수 전 관찰

`examples/livecomp`의 Dashboard(자식 Counter 3개)를 저장소 수준에서 돌린 값이다. WebSocket 없이
`repo.join` → `_render_diff` → flush 순서를 그대로 밟았다.

| 시나리오 | 착수 전 |
|---|---|
| 첫 join의 부모 diff | 4,913B. 자식 마크업과 자식 `data-state` 3개가 안에 있다 |
| 첫 join의 자식 render | 1,273B × 3. 부모 diff와 **이중 전송** |
| 자식 하나 reset 뒤 무관한 부모 재렌더 | 부모 diff 3,834B, 자식 3개 모두 `update()` 큐, reset한 자식이 10으로 되돌아감 |
| 부모만 바뀐 재렌더 | 244B. 이 경우는 이미 좋다 |
| 부모 이벤트당 render 호출 | 1 + N |

셋째 줄은 예제 자체의 버그다. counter-2를 reset해 0으로 만든 뒤 다른 카운터를 누르면 counter-2가
10으로 돌아간다. E2E에 이 시나리오가 없어 잡히지 않았다. 이제
`examples/livecomp/tests.py::test_a_reset_counter_survives_an_unrelated_parent_rerender`가 지킨다.

### 5-2. 전후 비교

`make bench-compare BASE=790dab7 ARGS="--skip-ws"` (2026-09-09, macOS arm64, Python 3.12, Django 6.0).
790dab7은 이 작업 직전의 main이다. 시나리오는 `bench/payload.py`의 `_live_component_scenarios`로,
부모 `BenchBoard` 아래 `BenchCard` 3개를 컨슈머의 `send_render` 경로로 돌려 **브라우저가 받는 render
프레임의 바이트와 개수**를 잰다. 구 코드에서는 별도 flush 단계까지 포함한 값이다.

| 지표 | 790dab7 | 이 작업 | 변화 |
|---|--:|--:|--:|
| 첫 join 바이트 | 4,062 | 2,409 | −41% |
| 첫 join 프레임 | 4 | 1 | −75% |
| 부모만 바뀐 재렌더 바이트 | 232 | 232 | 0% |
| 자식 reset 뒤 무관한 부모 재렌더 바이트 | 2,095 | 233 | −89% |
| 같은 시나리오 프레임 | 2 | 1 | −50% |
| 같은 시나리오 뒤 자식 count (reset했으니 0이어야) | **2** | 0 | 버그 수정 |
| list.event_ms (LiveComponent 없는 기존 벤치) | 0.608 | 0.555 | −9% |

기존 `flat.*`·`list.*` 페이로드는 ±1%(2~4B) 안에서 같다. 이 작업이 LiveComponent가 없는 컴포넌트의
diff를 건드리지 않았다는 확인이다.

같은 실행에서 `bench/compare.sh`의 버그도 하나 잡았다. `cp -R "$ROOT/bench" "$WT/bench"`는 대상
디렉터리가 이미 있으면 그 **안에** 복사하므로, 과거 커밋은 자기 벤치 코드로 돌고 있었다. 새 시나리오가
비교표에 나타나지 않아 발견했다. 이전 비교(`997ee59` 대 GAP-024)는 양쪽 벤치 코드가 같아 결과에는
영향이 없었다.

## 6. 착수 순서

독립 배포 가능한 단위로 넷이다. 각각 테스트를 동반한다.

1. **#80** leave. `leaving()`, cascade, 구독 재계산.
2. **#78** 소유권. 클라이언트 자식 join 제외, 서버 무시, `children` 복원, 직전 props 값 비교.
3. **#79** 렌더 분리. `ComponentRef`, `send_render` 안의 수명주기 루프, `render.children`, flush 삭제,
   livecomp E2E에 reset 시나리오, bench-compare.
4. 문서. 기능 문서의 틀린 서술(리뷰 §5) 정정, [live-component.md](./live-component.md) §4.1을 이 계약으로
   개정, [wire-protocol.md](../implementation/wire-protocol.md), [html-diff.md](../features/html-diff.md), CHANGELOG.

## 7. 범위 밖

- `{% live_component_block %}`와 슬롯 전달. 기능 문서의 모달 예제가 전제하는 API인데 없다. 별도 이슈.
- 인가 경계. #75가 `_build` 지점에 on_mount를 꽂으면 HTTP와 WS 양쪽을 덮는다. 이 안은 자식 생성을
  저장소 한 곳으로 모아 그 자리를 마련할 뿐이다.
- 일반 중첩 Component의 재join 시 `joined()` 중복. 같은 문제가 있지만 LiveComponent와 원인이 달라
  따로 본다.
