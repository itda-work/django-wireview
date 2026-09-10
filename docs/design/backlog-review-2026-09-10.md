# 0.3.0 이후 잔여 이슈 재검토 지침

> 2026-09-10, `v0.3.0` 직후에 씀. **이슈가 정본이다** — 이 문서는 각 이슈에 무엇을 *다시* 물어야
> 하는지만 적는다. 진행 상태나 인수 조건은 이슈에 있고, 이 문서를 갱신하는 것보다 이슈를 갱신하는
> 것이 먼저다.

## 왜 다시 보는가

`v0.3.0`(live_session, GAP-009)이 컴포넌트가 **생기는 방식**을 바꿨다. 마운트가 거절될 수 있게 됐고,
HTTP 렌더가 훅을 돌게 됐고, boost 내비게이션이 어떤 경우 morph를 포기하게 됐고, 연결에 경계와 인증
세대가 붙었다. 남은 이슈 여럿이 그 위에 서 있는데, **이슈 본문은 그 이전에 쓰였다.**

아래는 "이 이슈가 틀렸다"가 아니라 "**이 이슈를 지금 읽으면 빠져 있는 사실**"의 목록이다.

## 이슈별로 다시 물을 것

### [#72](https://github.com/itda-work/django-wireview/issues/72) GAP-033 Sticky 컴포넌트 — 전제가 바뀌었다

이슈는 "boost로 페이지가 바뀌어도 살아남는 컴포넌트"를 말한다. 그런데 0.3.0부터 **경계를 넘는 이동은
morph가 아니라 전체 페이지 로드**다. 전체 로드는 JavaScript 문맥을 통째로 버리므로 sticky 컴포넌트는
그 이동에서 **원리적으로 살아남을 수 없다.** 그것은 버그가 아니라 경계가 존재하는 이유다.

- 다시 물을 것: sticky의 범위를 "**하나의 live_session 안에서**"로 좁히는 것이 맞는가. 그렇다면 AC에
  "경계를 넘으면 살아남지 않는다"를 **명시적으로** 넣어야 한다 — 적지 않으면 나중에 결함으로 신고된다.
- morph 보존 판단은 이제 `isStreamContainer` 옆이 아니라 `NavigationGate`(예약된 작업을 취소한다)와
  경계 검사 옆에 나란히 선다. `wireview/static/wireview/live-session.mjs`와 `wireview-boost.js`를 함께 읽는다.

### [#73](https://github.com/itda-work/django-wireview/issues/73) GAP-034 Dead view — "첫 렌더는 공짜다"가 조건부가 됐다

이슈의 배경은 "첫 렌더는 이미 서버가 완전한 HTML을 만든다 — 이 부분은 공짜다"로 시작한다. 0.3.0부터
그렇지 않은 경우가 생겼다.

- 마운트 훅이 halt하거나 예외를 던지면 그 컴포넌트는 **HTTP 렌더에서 아무것도 그리지 않는다.**
- `_live_sessions`를 선언한 컴포넌트는 경계 밖 페이지에서 아예 렌더되지 않는다.
- `django.template.context_processors.request`가 없으면 경계가 조용히 꺼진다(`wireview.W010`).

즉 dead view가 약속할 수 있는 것은 "**정책을 통과한** 컴포넌트의 첫 HTML"이다. 다시 물을 것: 그 약속을
어떻게 적을 것인가, 그리고 거절된 컴포넌트 자리를 무엇으로 채울 것인가(빈 자리인가, 설명인가).

### [#60](https://github.com/itda-work/django-wireview/issues/60) GAP-027 세션 분리 — 옮겨야 할 상태가 늘었다

착수 기준은 그대로 미달이다(열어만 둔다). 다만 **세션 상태 목록이 바뀌었다.**
`docs/implementation/wire-protocol.md` §6에 페이지 경계·인증 세대·인증 토픽 구독·재확인 여부가 더해졌다.

- 다시 물을 것은 없고, **놓치면 안 되는 것**이 있다: 세션을 프로세스 밖으로 옮기면서 이 넷을
  빠뜨리면 **경계가 조용히 사라진다.** 새 워커가 경계 없는 연결로 세션을 이어받고, 정책도 인증
  세대도 로그아웃 구독도 붙지 않는다. §6에 그렇게 적어 뒀다.

### [#59](https://github.com/itda-work/django-wireview/issues/59) GAP-012 WebSocket 폴백 — 닫혔다

**만들지 않기로 결정했다(2026-09-10).** WebSocket 이 필수 전제이고, GAP-012 는 미구현이 아니라
설계상 제외다. 근거와 버린 길 셋은 [설계 메모](./longpolling-fallback.md) §5 에 있다.
다시 열 조건도 거기 적혀 있다 — 추정이 아니라 "막힌 배포에서 쓸 수 없다"는 구체적인 보고다.

### [#71](https://github.com/itda-work/django-wireview/issues/71) GAP-032 Colocated hooks — 헤더 태그가 문맥을 읽는다

후보 셋 중 "`{% wireview_header %}`가 수집해 로드"가 있다. 그 태그는 이제 `takes_context=True`이고
**`context["request"]`에 의존한다**(경계 이름을 거기서 읽는다). 훅 수집을 그 태그에 얹는다면 같은
의존을 하나 더 만드는 것이고, 그 의존이 없을 때 조용히 꺼지는 것이 어떤 모습인지는 `wireview.W010`이
이미 보여 준다. 다시 물을 것: 훅 수집도 요청에 의존해야 하는가, 아니면 요청과 무관한 경로가 있는가.

### [#70](https://github.com/itda-work/django-wireview/issues/70) GAP-031 내비게이션 테스트 헬퍼 — 재료가 늘었다

0.3.0이 `wireview.testing.mount()`에 `live_session=`을 더했고, 거절된 마운트를 freeze하게 바꿨다
(그 전에는 `mount()`만 서버와 다른 답을 냈다). AC3이 "스트림 검사 헬퍼도 함께 검토한다"이므로,
`tests/test_live_session_contract.py`가 쓰려고 만든 것들 중 사용자에게 줄 만한 것이 있는지 함께 본다.

### [#69](https://github.com/itda-work/django-wireview/issues/69) GAP-030 키 기반 comprehension diff — 거의 그대로

diff 기전은 바뀌지 않았다. 하나만: `data-state`는 dynamic 파트이고 봉투가 v2로 커졌으므로, 목록
항목마다 컴포넌트가 있는 템플릿에서는 재전송 비용이 이전보다 크다. AC1의 "그 항목만의 페이로드"가
주는 이득도 그만큼 커졌다는 뜻이다. `tests/test_diff_stability.py`가 두 가지를 다 지킨다.

### [#74](https://github.com/itda-work/django-wireview/issues/74) GAP-035 배치 업데이트 · [#41](https://github.com/itda-work/django-wireview/issues/41) 알림 예제 — 상호작용 없음

읽은 범위에서 0.3.0과 겹치는 곳을 찾지 못했다. 이슈 그대로 읽으면 된다.

## 어떻게 검토할 것인가

GAP-009에서 실제로 작동한 순서다. **비용이 크므로 크기에 맞춰 쓴다** — 아래 전부를 P3 이슈 하나에
적용하는 것은 낭비다.

1. **착수 전 설계 메모**(`docs/design/`)를 쓰고 **결정할 것을 이슈에 올린다.** #58과 #59가 그 모양이다.
   메모의 값은 결론이 아니라 "왜 다른 길을 버렸는가"에 있다.
2. 구현.
3. **계약 테스트** — 회귀가 아니라 불변식을 진술한다. `tests/test_live_session_contract.py`가 예다.
   경로 × 실패 사유를 parametrize로 돌리고, **거절마다 같은 경로의 허용 대조군을 짝짓는다**
   ("전부 거절"과 "옳게 거절"은 한쪽에서 보면 같다).
4. **변이 검사** — 구현을 실제로 망가뜨려 스위트가 잡는지 본다. GAP-009에서 이것이 테스트 결함
   열하나와 구현 버그 다섯을 찾았다. **"고쳤다"의 근거는 되돌려 넣으면 실패하는 테스트다.**
5. 적대적 리뷰. GAP-009는 여섯 라운드가 걸렸고 라운드마다 **직전 수정이 다음 결함을 만들었다.**
   원문은 `docs/design/live-session-*review*.md`에 있다.

## 세션을 시작할 때

`wireview-dev` 스킬 §1이 정본이다(`gh issue list --label wip`). 이 문서는 그 뒤에 읽는다.
읽고 나서 이슈를 고쳤다면 **여기가 아니라 이슈를 고친다.**
