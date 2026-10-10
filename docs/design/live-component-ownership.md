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
- 재연결 복원은 `build_live_component`가 `_take_restored`로 루트별 맵을 보고 **`복원값 | 부모 prop`** 으로 만든다.
  부모가 넘기는 필드는 부모가 진실이고, 넘기지 않는 필드는 자식 것이다. Phoenix와 같은 우선순위이며
  일반 중첩 Component의 `build()`와도 같다.
- 맵은 **join의 루트마다 따로** 있고, 그 루트와 수명을 같이한다. 루트가 떠나면(`repo.remove`, join 실패 포함)
  남은 항목도 버린다. 첫 render가 그리지 않은 자식도 있기 때문이다 — `joined()`가 다시 시작한
  `assign_async`의 결과 안에 있는 LiveComponent는 작업이 끝난 뒤의 render에서야 만들어지고, 그때 항목을
  꺼내 재연결 전 상태로 돌아온다. join이 끝날 때 맵을 통째로 비우던 때는 이것이 기본값으로 시작했다.
  핸들러가 던져 루트를 버릴 때(`_crashed`)는 항목을 남긴다(`remove(keep_carried=True)`). 롤백 join은 같은 id로
  곧 오고, 페이지의 요소에는 첫 render가 지운 자식이 없어 다시 실을 수 없기 때문이다.
- 항목을 꺼내는 것은 **그 id를 그리는 패스의 루트들**뿐이다(`repo._take_restored`). 패스를 돌리는 컴포넌트에서
  시작해 LiveComponent는 부모로, `{% component %}`로 그려진 컴포넌트는 그것을 그린 패스의 컴포넌트로
  (`repo._built_by`) 거슬러 가며 처음 만나는 항목을 쓴다. `_built_by`는 그 id를 **마지막으로 그린 패스**를
  가리키고, 그 패스가 그 id를 그리지 않으면 지운다(`repo.end_pass`). 다른 컴포넌트의 패스 안에서 인라인으로
  그려지는 `{% component %}`의 패스도 그 컴포넌트의 패스다 — 부모가 prop으로 중첩 컴포넌트에게 하위
  컴포넌트를 숨기게 하면 그 인라인 패스가 `_built_by`를 지운다. 그러지 않던 때는 중첩 컴포넌트가 자기
  render를 할 때까지 옛 기록이 남아, 숨긴 하위 컴포넌트의 `leave`를 다시 그린 것의 늦은 `leave`로 보고 그
  안 LiveComponent의 상태를 남겼고, 다시 보인 **새** 인스턴스가 그것으로 시작했다. 인스턴스보다 오래
  산다 — 페이지가 join하는 것은 그 패스가 그린 요소이기 때문이다. 그린 쪽이 떠나면(`remove`) 그 기록도
  지운다. 그 쪽의 패스가 더는 끝나지 않으니, 남겨 두면 피드의 행처럼 바뀌어 가는 그린 쪽마다 연결이
  사는 동안 쌓였다. 크래시 롤백(`keep_carried`)만 남긴다 — 남긴 맵으로 가는 길이다. 이미 있는
  인스턴스를 다시 그리면 그
  패스의 루트가 실은 그 id의 항목은 쓴 것으로 친다.
- 같은 id를 두 루트가 실을 수 있다. 재연결한 페이지는 루트의 join 바로 뒤에, 그 루트의 결과 안에 있던
  **일반 Component**의 join도 보낸다 — 루트가 live로 표시됐고 첫 render를 아직 패치하지 않아 요소가 남아 있다.
  sticky 루트가 실은 id를 boost로 간 다음 페이지가 자기 루트로 그리기도 한다. 그 join은 페이지가 보낸
  상태로 진행하고, 그 답 render들(URL params가 있을 때의 `params_changed()` render 포함)과 그 컴포넌트의
  이후 render는 **자기 join이 실은 항목**만 쓴다. 다른 루트의 항목은 그 루트의 이후 render 몫으로 남는다.
  전에는 그 join이 항목을 꺼내 쓰고 떠나면서 가져가 둘 다 기본값으로 그려졌고, 그 뒤 한때는 그런 join을
  답 없이 버렸다 — 그러면 sticky 루트가 실은 id를 다음 페이지가 **자기 루트**로 그릴 때 그 join도 버려져
  컴포넌트가 죽었다. 그다음에는 다른 루트가 실은 항목을 그 join 동안만 막았는데, 그러면 답 render가
  LiveComponent를 페이지의 상태가 아니라 props로 그렸고, join이 끝난 뒤 그 id를 그리는 패스(떠나기 전
  그 컴포넌트 자신의 render, 다음 페이지에서 다시 보인 LiveComponent)가 남의 항목을 꺼냈다.
- 그 일반 Component의 요소는 루트의 첫 render를 패치하면 떠나고 페이지는 `leave`를 보낸다. 그 전에 루트의
  작업이 끝나면 루트의 render가 그 id를 이미 다시 그렸고, 늦게 온 `leave`는 그 render가 그린 인스턴스를
  지운다. 페이지는 그 render가 그린 새 요소를 join하는데, 그 안의 LiveComponent는 옛 요소와 함께 이미
  놓아 버려 상태를 싣지 못한다. 그래서 **아직 있는 컴포넌트의 마지막 패스가 그린** 컴포넌트에 `leave`가
  오면 서버는 그것을 지우되 그 안 LiveComponent의 상태를 그린 쪽의 맵에 남기고(`repo.let_go`), 새 요소의
  join이 그것을 꺼낸다.
- 부모가 `{% if %}`로 일반 Component를 숨기면 그 패스가 끝날 때 은퇴한다(§3-6). 그래서 **페이지의 `leave`가
  닿기 전에** 다시 보여도 다시 보인 render는 새 인스턴스를 그리고, 그 안 LiveComponent도 새로 시작한다. 그 뒤에
  온 늦은 `leave`는 새 인스턴스(아직 join하지 않은 것)를 지우고, 페이지는 그 render가 그린 요소를 join한다.
  전에는 숨긴 인스턴스가 `leave`가 올 때까지 남아 있어, 다시 보인 render가 그것을 그려 옛 상태로 돌아왔고
  `leave`가 두 render 사이에 오면 새로 시작했다 — 결과가 메시지 순서에 따라 갈렸다(`tests/test_joined.py`의
  `test_a_box_hidden_and_shown_again_starts_anew_wherever_its_leave_lands`가 두 순서를 본다, #204).
- 이미 만들어진 id의 항목은 싣지 않는다. 중첩된 일반 Component의 join은 안의 컴포넌트 상태를 다시
  싣는데, 그것들은 바깥 join의 패스가 이미 만들었으므로 아무도 꺼내지 않는다. 남겨 두면 `{% if %}`로
  숨겼다 다시 보인 **새** 인스턴스가 그 옛 상태로 시작했다.

### 3-2. 발견 단계 — 동기, 템플릿 안

- live 렌더에서 `{% live_component %}`는 자식을 등록하고 이번에 넘긴 props를 기록한 뒤 **참조 dynamic**
  하나만 출력한다. HTML 상으로는 `<!--$n--><!--@wv:c1--><!--/$n-->`, 파싱 결과는 `ComponentRef("c1")`,
  페이로드는 `{"c": "c1"}`이다. 값이 id뿐이라 부모가 몇 번 재렌더돼도 이 슬롯은 바뀌지 않는다.
- `let:` 없는 fill 안의 `{% live_component %}`도 fill을 쓴 컴포넌트(`this`)의 패스에서 위와 같이
  발견되고, 그 컴포넌트가 부모다. 슬롯을 그리는 컴포넌트(`{% component_block %}`나
  `{% live_component_block %}`)가 기억한 슬롯으로 스스로 렌더할 때는 그 자리에 참조만 다시 번호를 매겨
  내보낸다(`wireview/slots.py`의 `_ComponentRefNode`). 자식을 등록하지 않으므로 소유도, 수명주기도
  바뀌지 않는다.
- 그래서 어떤 렌더가 참조하는 LiveComponent가 그 렌더의 소유가 아닐 수 있다. 슬롯을 그리는 컴포넌트가
  슬롯을 숨겼다 다시 보이면, 페이지는 숨길 때 그 LiveComponent를 버렸는데 부모는 여전히 그것을 그리므로
  어떤 수명주기도 그것을 다시 등록하지 않는다. 그래서 렌더가 직전 자기 렌더에 없던 참조를 새로 내면
  `send_render`가 그 LiveComponent(와 그것이 참조하는 것)의 지금 전체 렌더를 같은 `render`의
  `children`에 싣는다(`WireviewSession._send_shown_again`). 훅은 부르지 않는다 — 소유는 그대로다.
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
  그 컴포넌트가 그리는 LiveComponent는 다르다 — 페이지가 그 컴포넌트를 join한 뒤에는 그린 쪽의 수명주기
  단계를 탄다(§3-3).

### 3-3. 수명주기 단계 — 비동기, `send_render` 안

부모 diff를 계산한 뒤, 템플릿이 실제로 평가됐을 때만 다음을 한다.

1. **사라진 자식.** 부모 아래 등록됐지만 이번 렌더에서 발견되지 않은 자식은 `leaving()` 뒤 제거한다.
   서버가 처음으로 고아를 스스로 판정한다. 조건부 렌더로 사라진 자식은 상태를 잃는다. Phoenix가 CID를
   지우는 것과 같은 의미론이다.
2. **새 자식.** pending 모드에 넣고 `joined()`를 한 번 부른다. 패스가 만든 뒤 이 단계 전에 저장소에서
   지워진 자식(부모의 join이 실패했거나 페이지가 부모를 놓았다)은 이 목록에서도 빠진다(`repo.remove`). 남아
   있던 때는 그 id로 다시 만든 부모의 첫 render가 지워진 인스턴스의 `joined()`까지 불렀다.
3. **기존 자식.** 이번 props가 **직전에 넘긴 props와 값이 다를 때만** `update()`를 부른다. 비교 대상은
   자식의 현재 상태가 아니라 직전 props다. 그래야 자식이 스스로 바꾼 값이 부모 재렌더에 덮이지 않는다.
4. 2·3의 자식을 async 경로(`render_diff`)로 렌더한다. 그 렌더가 손자식을 발견하면 같은 절차를 재귀한다.
5. 예외는 자식 단위로 격리해 로깅하고, pending 모드 해제는 `finally`로 보장한다.
6. 부수효과 순서: 번들 전송 → 구독 동기화 → 자식·부모의 pending 큐 flush. 구독 준비 전에 초기
   broadcast가 나가는 리뷰 G 항목이 여기서 닫힌다.

**이 패스 안에서 그려진 컴포넌트의 자식도 같은 배치다.** 부모 R의 패스가 `{% component %}`로 중첩 컴포넌트 N을
그리고 N이 LiveComponent L을 그릴 때, R이 넘긴 prop으로 N이 L을 숨기거나 처음 보이거나 L에 새 props를 넘기는 것은
R의 패스 안에서만 드러난다. 그 패스가 끝나면 N이 무엇을 그렸는지 R 앞으로 기록하고(`repo.end_inline_pass`),
R의 `take_lifecycle`이 N의 자식까지 1~3을 한 배치에 담는다(N 안에서 다시 그려진 것도 재귀로). L의 렌더는
R의 `render`의 `children`에 실린다. 그러지 않던 때는 N 자신의 render가 있어야 L이 정리됐다. 숨긴 L은
`leaving()` 없이 남아 다시 보이면 옛 상태로 돌아왔고(N이 그 사이 자기 render를 했으면 새로 시작했다 —
결과가 메시지 순서에 달렸다), 처음 보인 L은 `joined()`도 렌더도 없어 그려지지 않았고, 바뀐 props는
`update()`에 닿지 않았다.

- 기록은 페이지가 N을 join한 뒤에만 한다. 아직 join하지 않은 N(패스가 방금 만든 것, 재연결에서 join이 오는
  중인 것)은 예전처럼 그 join의 render가 정리한다 — 그 join이 인스턴스를 완성하고(§3-2 대조), 실패하면 N이 소유한
  것은 거절된다(§3-5). join이 실패한 뒤 R의 패스가 다시 만든 N도 새 인스턴스라 여기 든다.
- R의 첫 렌더(아직 페이지에 렌더가 나가지 않은 인스턴스, `instance_announced`가 거짓)도 기록하지 않는다. 그 렌더가
  그린 것은 전부 페이지가 스스로 join할 새 DOM이다. "N이 join된 적이 있다"(`has_joined`)는 "페이지가 지금 그 요소를
  든다"가 아니다 — boost 이동으로 같은 id의 R·N을 가진 페이지에 가면 페이지는 둘을 새 요소로 받아 R, N 순서로 다시
  join하고, R의 join 렌더는 연결이 이미 든 N을 그린다. 그것을 기록하면 R의 배치가 L에 `joined()`를 돌리고, 바로 뒤의
  N의 join이 그 L을 은퇴시켜 `leaving()`을 돌린 뒤 새 L로 `joined()`를 또 돌렸다. 다른 id의 루트 R2가 boost 뒤 같은
  id의 N을 그려도 마찬가지다. 예외 뒤의 다시 join도 첫 렌더라 그 한 번은 N 자신의 render에 맡겨지고, 그 뒤 R의
  렌더부터 다시 기록한다.
- 은퇴는 기록할 때가 아니라 R의 배치에서 한다. 패스 도중에 지우면 같은 패스의 뒤에서 L을 다른 부모가 그릴 때
  (부모가 바뀌는 것) 상태가 사라진다. 배치에서는 `_parent_id`가 이미 바뀌어 N의 자식으로 보이지 않는다.
- N이 R의 배치 전에 자기 패스를 다시 시작하면(`begin_render`) 그 기록은 버린다. 새 패스가 정리한다. R의
  `after_render` 훅이 await하는 동안 N의 백그라운드 작업(`start_async`의 결과)이 N을 렌더할 수 있다 — 옛 기록으로
  정리하면 N의 새 패스가 다시 그린 L을 R의 배치가 은퇴시킨다.
- R이 배치 전에 사라지면(렌더나 `after_render` 훅이 던져 롤백될 때) R 앞으로 남긴 기록도 함께 지운다. N의 자식은 N
  자신의 다음 render가 정리한다.
- 슬롯의 LiveComponent는 슬롯을 채운 쪽의 자식이므로(§3-2) 슬롯을 그리는 N의 패스가 건드리지 않는다.
- **슬롯 주인의 자기 렌더가 다시 그린 N도 같은 모양이다.** R이 슬롯 주인 S의 fill에 N을 그리면, S가 자기 render로
  슬롯을 그릴 때 기억한 fill에서 N을 지금 상태로 다시 그린다(`wireview/slots.py`의 `_NestedComponentNode`). 그 패스는
  R의 것이 아니라 S의 render 안에서 돈다. 그래서 S 앞으로 기록하고(`end_inline_pass(N, S)`) S의 배치가 N의 자식을
  정리한다. N이 상태 밖의 값(쿼리 같은 것)을 읽어 그 패스에서 L을 숨기거나 처음 보이면, 예전에는 기록이 없어 숨긴 L이
  N 자신의 다음 render까지 `leaving()` 없이 남았고, 처음 보인 L은 `joined()`도 렌더도 없이 참조만 나갔다. 그 패스
  안에서 그려진 컴포넌트 M의 기록(`_inline[N]`)도 S의 배치가 재귀로 소비한다. 위의 조건(N이 join됐고 S의 렌더가
  첫 렌더가 아님)은 그대로다. 슬롯에 직접 놓인 LiveComponent(R 소유)는 S의 배치가 은퇴시키지 않는다 — S의 자식이
  아니라 R의 자식이고, S의 패스는 그것을 참조로만 그린다(`_ComponentRefNode`).

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
- `leaving()`은 `joined()`와 짝이다. 지워지는 인스턴스 가운데 `joined()`가 돈 적 없는 것(`has_joined`가 거짓)은
  `leaving()`을 받지 않고 비동기 작업만 취소된다(`WireviewSession._call_leaving`). 은퇴·leave·연결 종료·예외 뒤의
  롤백, 어느 경로든 같다. 패스가 만든 pending 자식이 `joined()` 전에 은퇴하는 일은 실제로 있다 — 같은 id의 페이지로
  boost 이동하면 루트의 join 렌더가 연결이 든 N을 다시 그리며 새 L을 만들고, 바로 뒤의 N의 join이 그 L을 은퇴시킨다.
  그 L이 `leaving()`부터 받던 때는 `joined()`에서 등록하고 `leaving()`에서 해제하는 코드가 하지 않은 일을 되돌렸다.
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
  다시 만든 인스턴스와 그것이 소유한 LiveComponent(`_parent_id` 사슬의 루트가 그 id인 것)에는 이벤트·훅·업로드·
  브로드캐스트·`params_changed`·`wire.defer`·`update_live_component`가 닿지 않는다(`repo.reachable`). 그 인스턴스는
  따로 렌더되지 않으므로 그 LiveComponent의 `joined()`·`update()`도 돌지 않는다. 그 id로 join이 오면 다시 시도한다. 클라이언트가 소유를 추정해 막던 때는 슬롯의 LiveComponent까지
  막고, boost 이동 뒤 다시 join하지 않았다.
- 이동 뒤 같은 id가 LiveComponent에서 루트로(또는 반대로) 바뀔 수 있다. 클라이언트는 join할 때마다 요소의
  `wireview-live`로 소유를 다시 정하고, 떠난 것의 `leave`를 새 것의 join보다 **먼저** 보낸다. join을 먼저
  보내면 서버에는 아직 옛 부모의 LiveComponent가 그 id로 남아 있어 루트의 join을 무시했다(#146).
- 같은 id로 다른 클래스가 오면 이전 인스턴스를 `leaving()` 대기 큐에 넣고 새로 만든다. 다른 부모 아래
  나타나면 `_parent_id`를 바꾸고 debug 로그를 남긴다.

### 3-6. 그린 쪽이 더는 그리지 않는 중첩 컴포넌트 (#204)

`{% component %}`로 그린 일반 Component는 LiveComponent처럼 부모가 소유하지 않는다 — 페이지가 따로 join하는
루트다(§3-2 대조). 그래서 오래 동안 그것이 페이지에서 사라졌다는 것은 페이지의 `leave`만 알렸다. 그 사이의 창이
문제였다.

- **id 없이 그린 것은 그린 쪽의 패스마다 새 인스턴스다.** `repo.build`가 새 `rx-<uuid>`를 붙인다. 그린 쪽이 다시
  렌더하면 새 것이 그려지고 페이지가 그것을 join하는데, 옛 것은 페이지의 `leave`가 올 때까지 저장소에 남았다 —
  구독이 이어져 브로드캐스트에 `notification()`을 돌리고 그 render를 보내려 했으며, Broadcast 패치를 받고,
  `start_async` 작업이 돌았다. `leave`가 오지 않으면(소켓을 직접 쓰는 클라이언트, 연결과 함께 사라진 메시지)
  연결이 끝날 때까지 그랬고, 그린 쪽의 렌더 횟수만큼 쌓였다.
- **그 render가 끝날 때 은퇴시킨다.** 그린 쪽의 패스가 끝나면(`ComponentRepository.end_pass`) 그 쪽의 직전
  패스가 그렸고 이번에 그리지 않은 컴포넌트를 후보로 적는다(`_undrawn`). 정하는 것은 render 전체가 끝난 뒤다
  (`take_lifecycle`의 `_retire_undrawn`): 그 사이 같은 render의 다른 패스가 그 id를 그렸으면 그대로 둔다. 템플릿이
  id를 중첩 컴포넌트에서 그것을 그리는 쪽으로 옮기면, 패스 순서에 따라 안쪽 패스가 먼저 끝나 은퇴시키고 바깥
  패스가 새 인스턴스를 만들었다 — 페이지는 같은 id의 요소를 옮겨 보존하고 join하지 않으므로 `joined()` 없는
  인스턴스가 남았다. 아무도 다시 그리지 않은 것은 저장소에서 지우고(`_retire_drawn` — 그것이 소유한
  LiveComponent와, 그것과 그 LiveComponent들의 패스가 그린 중첩 컴포넌트까지: 그 요소들은 그것의 요소 안에
  있다). 부모가 LiveComponent를 빼는 `_settle`도 같은 함수로 그 LiveComponent가 그린 것을 데려간다. 그 render 뒤
  구독을 다시 계산한다. 그때부터 브로드캐스트·Broadcast 패치·작업의 결과·`wire.defer`는 그것에 닿지 않는다
  (`repo.reachable`). id 없이 그린 것도 그린 쪽 앞으로 기록한다(`_drawn`) — 전에는 id가 있을 때만 기록했다.
- **render가 예외로 끝나면 후보를 되돌린다**(`render_failed`, `_render_tree`가 부른다). 그 render는 페이지에 닿지
  않았으므로 후보는 페이지에 남은 요소다. 후보마다 그것을 적은 그린 쪽을 함께 적어 두고(`_undrawn[id] = 그린 쪽`),
  `_built_by`·`_drew`를 그 패스가 돌지 않은 것처럼 되돌린다. 처음에는 후보를 남겨 두었는데, 그러면 크래시 뒤
  페이지의 다시 join이 오기 전에 **다른 컴포넌트의** render가 끝나며 그것을 은퇴시켰다 — 화면에 있는 컴포넌트가
  `leaving()`을 받고, 이어 온 다시 join의 패스는 그 id로 새 인스턴스를 만들었다. 페이지는 같은 id의 요소를 morph로
  보존해 join하지 않으므로 `joined()` 없는 인스턴스가 그 요소의 이벤트를 받았다. render는 한 연결 안에서 하나씩
  돌므로(컨슈머가 메시지를 차례로 처리하고 템플릿 패스는 한 스레드다) 마지막 `take_lifecycle` 뒤에 적힌 후보는
  지금 render의 것이다.
- **되돌리는 단위는 render 트리 하나다**(`begin_tree`·`tree_failed`, #204 3라운드). 한 render는 컴포넌트와 그것이
  정산하는 LiveComponent들의 render를 한 프레임으로 보낸다. 각 단계는 아래 단계를 그리기 전에 자식을 정산하므로,
  부모 단계의 `take_lifecycle`이 은퇴를 확정하고 묘비를 만든 **뒤에** 자식 LiveComponent의 render가 던질 수 있다.
  그 render도 페이지에 닿지 않았다. 그래서 가장 바깥 `_render_tree`가 트리를 열고(`ContextVar` — 다른 태스크의
  render와 섞이지 않는다), 트리 어디서든 던지면 그 트리가 만든 묘비와 `_settle`이 뺀 LiveComponent를 저장소로,
  `_built_by`·`_drew`를 트리가 시작할 때로 되돌린다. 그래서 `_settle`이 뺀 LiveComponent의 `leaving()`은 트리가
  끝날 때(`end_tree`) 돈다 — 같은 render의 새 자식 `joined()`보다 뒤지만, 같은 id의 둘이 한 패스에서 빠지고
  생기는 일은 없다. 이 레벨만 감쌀 때는 루트가 숨긴 id 있는 컴포넌트가 묘비로 남아, 크래시 뒤 다시 join의 패스가
  그 id로 새 인스턴스를 만들었다(위와 같은 `joined()` 없는 인스턴스). 크래시 복구가 루트와 그 LiveComponent를
  모두 은퇴시키므로 되돌린 LiveComponent는 그때 `leaving()`을 한 번 받는다.
- **페이지가 join한 것은 묘비로 남는다**(`Tombstone`, #204 2라운드). 은퇴는 서버가 render를 끝낸 순간이고, 페이지는
  그 render를 한 왕복 뒤에 본다. 그 사이 페이지가 옛 요소에서 보낸 이벤트 — 채팅처럼 브로드캐스트마다 루트가 다시
  그리는 목록에서 id 없는 행의 "삭제" 클릭, `{% if %}`로 숨겨지는 render와 겹친 클릭 — 는 인스턴스 없는 id로 왔고
  핸들러 없이 버려졌다. 그래서 은퇴한 인스턴스 가운데 페이지가 join한 것(소유한 LiveComponent와 함께)은 저장소에서
  빠지되 묘비로 남아 **그 id의 `user_event`만** 받는다 — 그리고 그 핸들러가 부른 묘비 안 LiveComponent의
  `send_to_parent`(함께 묘비에 있는 부모에 간다, 3라운드). 핸들러가 돌고, 컴포넌트 밖으로 가는 것(DB 쓰기·브로드캐스트·
  `push_event`·이동)은 나간다. 그 render는 보내지 않는다 — 페이지는 그 요소를 놓았거나 곧 놓고, `wire-update="ignore"`
  안에 남은 요소라면 다른 무엇도 닿지 않는 인스턴스를 살아 있는 것처럼 보이게 한다. 답은 `gone`이다(아래). 핸들러가
  던지면 로그를 남기고 묻는다(다시 join할 것이 없다). join하지 않은 것(페이지가 아직 받지 못한 것)은 페이지가 보낸 것이
  없으므로 그 render의 배치에서 바로 `leaving()` 대상이 된다(`leaving()`은 join한 것에만 돈다).
- **묘비는 이럴 때 묻고 그때 `leaving()`이 돈다.** 페이지의 `leave`(그 요소가 사라졌다는 확인), 그 id로 인스턴스가
  다시 만들어질 때(패스든 join이든 — join이면 새 인스턴스의 `joined()`보다 먼저, `command_join`), 연결의 끝(`stop`),
  그리고 **상한**: 은퇴하고 `TOMBSTONE_SECONDS`(10초)가 지난 뒤 끝나는 첫 render. 페이지가 `leave`를 보내지 않는
  경우(`wire-update="ignore"`, 잃은 메시지)를 위한 것이다. 묘비는 render만 만들고 render 끝에 나이를 보므로, render가
  이어지는 한 묘비의 수는 상한 시간 동안 은퇴한 "페이지가 join한 요소"의 수를 넘지 않는다 — 페이지가 받지 못한 새
  인스턴스(ignore 안의 그린 쪽의 새 것)는 묘비가 되지 않으므로 #204가 막으려던 렌더 횟수만큼의 누적은 돌아오지
  않는다. render가 없으면 묘비도 늘지 않고, 연결의 끝이 묻는다. 시간을 상한으로 고른 것은 그 창이 왕복 시간이기
  때문이다 — render 횟수로 세면 브로드캐스트가 잦은 페이지에서 왕복 하나 안에 상한을 넘겨 경합을 다시 잃는다.
- **`leaving()`이 묻힐 때 도는 이유.** 은퇴 때 돌리면 묘비의 핸들러가 `leaving()` 뒤에 돈다 — `leaving()`에서
  정리한 것(Presence의 `presence_leave`, 해제한 자원)을 핸들러가 다시 쓰는 순서가 된다. 그래서 `leaving()`과 작업
  취소, 업로드 해제를 묻을 때로 미뤘다. 대가: Presence는 은퇴한 컴포넌트를 페이지의 `leave`(한 왕복)나 상한까지
  남은 것으로 센다 — 전(origin/main)에는 언제나 `leave`까지였다. 업로드는 묘비에 닿지 않는다(`upload_op`은
  `reachable`로 찾는다). 묘비의 핸들러는 자기 업로드 항목을 읽을 수 있다(묻힐 때까지 해제하지 않는다). 작업은
  묻힐 때까지 돌지만 그 결과의 메일은 id로 인스턴스를 찾으므로 아무것에도 닿지 않는다 — 묘비가 있는 동안 그 id에는
  인스턴스가 없다(그 id로 만들면 먼저 묻는다).
- 페이지의 `leave`는 그대로 온다. 묘비면 묻는다. 묘비도 없는 은퇴한 id면 아무 일도 하지 않는다. 같은 id가 그 사이
  다시 그려졌으면(묘비는 그때 묻혔다) 그 새 인스턴스(join 전)를 지우고 페이지는 그 render가 그린 요소를 join한다 —
  §3-1의 늦은 `leave`와 같다.
- **id를 준 것도 같다.** `{% if %}`로 숨기면 그 패스가 은퇴시키고, 다시 보이면 새로 시작한다. 같은 id로 매번
  그리면 같은 인스턴스가 이어지는 것은 그대로다(튜토리얼 03).
- **남는 것.** 패스가 그리지 않았는데 페이지에 남는 것은 은퇴시키지 않는다. 초기화된 temporary assign 때문에
  유지된 부분(#111)이 그린 컴포넌트는 그 부분의 기록(`WireviewMeta.drawn`)에서 읽어
  `WireviewMeta.kept_components`로 남긴다. 자기 render만이 아니라 다른 컴포넌트의 패스 안에서 그려질 때도
  (`render()` → `_render_keeping`, 슬롯 주인이 fill의 컴포넌트를 다시 그릴 때 포함) 유지한 부분이 모으고, 패스마다
  새로 센다 — 처음에는 자기 render에서만 모아, 그린 쪽의 render가 유지 부분의 컴포넌트를 화면에 둔 채 은퇴시켰고
  앞의 render가 남긴 값이 정말 사라진 것을 붙들었다. 슬롯을 그리는 쪽이 자기 render로 슬롯을 다시 그릴 때는 슬롯을
  채운 쪽의 패스가 돌지 않으므로 은퇴도 없다. 스트림 항목 템플릿이 그린 것은 연결의 저장소가 아니라 따로 그려지므로
  기록에 없다.
- **`wire-update="ignore"` 안에 id 없이 그린 것.** 페이지가 옛 요소를 붙들지만 서버는 그것을 모르고 은퇴시킨다.
  그 요소의 이벤트는 묘비가 있으면 묘비에서 핸들러가 돌고, 상한이 지났으면 인스턴스 없는 id로 온다. 어느 쪽이든
  서버는 `gone`으로 답하고(`vsn` 9, 옛 번들에는 `diff: null`인 render), 페이지는 로딩 표시를 풀고 그 요소가 아직
  있으면 그 상태로 다시 join한다 — 묘비의 핸들러가 바꾼 상태는 화면에 오지 않고(그 render는 보내지 않는다),
  다시 join한 인스턴스는 요소의 `data-state`에서 시작한다. 묘비가 없었으면 그 이벤트의 핸들러는 돌지 않는다.
  ref 없는 이벤트(`wire-viewport-*`)의 `gone`은 그 id로 답을 기다리는 join이 있으면 다시 join하지 않는다 — 보낸 때를
  모르므로, 앞선 `gone`이 보낸 join을 그 join으로 본다. 전에는 알 수 없는 id의 이벤트에 아무 답도 없어 로딩 표시가
  남았다. 같은 인스턴스로 이어지게 하려면 id를 준다.
- **남은 창.** 그린 쪽의 렌더는 워커 스레드에서 돈다(#138). 그 동안 루프가 처리한 브로드캐스트는 패스가 아직
  끝나지 않은 옛 것에 닿는다. 렌더보다 먼저 발행된 브로드캐스트이니 순서상 맞다.

Phoenix와 비교하면: Phoenix의 함수 컴포넌트는 상태가 없어 서버에 인스턴스가 없고, 상태를 가진
`live_component`는 id가 필수라 패스마다 새 것이 생기는 일이 없다. 그 CID는 클라이언트가 DOM에서 사라진 것을
알리는 왕복으로 지운다: 클라이언트가 `cids_will_destroy`를 보내면 서버는 그 CID를 지울 예정으로 표시하되 그 CID로
오는 이벤트는 여전히 처리하고(핸들러가 돌고 diff도 만들지만 클라이언트가 DOM에 없는 CID의 diff를 버린다),
`cids_destroyed`에서 — 그 사이 다시 그려지지 않았으면 — 지운다. 날아오는 이벤트를 받으려는 두 단계다.
wireview는 은퇴를 서버의 render가 정하고(id 없는 상태 컴포넌트는 Phoenix에 대응물이 없고, 그 id는 다시 그려질 일이
없다), 그 뒤를 Phoenix의 두 번째 단계처럼 둔다: 묘비가 "지울 예정"이고 페이지의 `leave`가 `cids_destroyed`다.
다른 점은 셋이다. 묘비는 그 순간부터 브로드캐스트·패치·작업에서 빠진다(Phoenix의 CID는 지울 때까지 `update`를
받는다). 핸들러의 render는 보내지 않고 `gone`으로 답한다(Phoenix는 보내고 클라이언트가 버린다 — wireview의
클라이언트는 `wire-update="ignore"` 안에 요소를 붙들 수 있어, 버리지 않고 적용할 수 있다). 그리고 `leave`가 오지
않는 경우의 시간 상한이 있다(Phoenix는 `cids_destroyed`가 올 때까지, 곧 연결이 끝날 때까지 기다린다).
LiveComponent는 그대로 부모의 render가 바로 정한다(§3-3 1번).

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
