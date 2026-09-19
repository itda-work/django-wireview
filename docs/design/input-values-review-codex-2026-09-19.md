# #92 구현 리뷰 — Codex 원문

> Herdr pane에서 Codex(gpt-6-astra, medium)가 2026-09-19에 쓴 적대적 구현 리뷰 원문이다. 대상은 #92의 첫 구현(7153822)이고,
> 반영 결과는 [input-values.md](./input-values.md)의 2판 머리말과 §1~§3에 있다. 손대지 않은 근거 문서로 두고, 재현 스크립트의
> 출력은 부록에 옮겼다.

---

# #92 구현 리뷰 — 7153822

## 판정: 수정 필요

ref 왕복과 버전 협상의 방향은 맞다. 그러나 응답 수신 시 여는 전역 `answered`와 나중 프레임에서 실행하는 morph가 서로 묶이지 않았다. 정상 응답 두 개만으로 확정 표시가 사라지고, `myself`에서는 완료한 액션의 표시가 다른 컴포넌트에 남는다. 전송 후 입력 보존에도 예외가 있다. 되돌리기보다는 응답·morph·필드 정리의 소유권을 고쳐야 한다.

검토 시 HEAD는 정확히 `71538226b8114c7fc41bdc98e28a148dbdb15871`이었다. 저장소 파일은 수정하지 않았다. 아래 실행 재현은 production `valueGuard`, render 분기, `scheduleMorph`를 추출해 실행하되 DOM·Idiomorph·프레임 큐를 모형으로 대체한다. 실제 브라우저 재현으로 주장하지 않는다.

## 발견 사항

차단 등급으로 확인한 사항은 없다. 다음은 중요도 순이다.

### 중요 1. 앞선 null 응답의 settle이 뒤 액션의 응답 표시를 지운다

**근거:** `wireview/static/wireview/wireview.js:231–248,954–971`, `wireview/static/wireview/wireview-boost.js:86–105`.

`answer(ref, ...)`는 즉시 실행하지만, null diff에 대한 `settle`은 requestAnimationFrame으로 미룬다. `settle`은 자기 ref가 아니라 해당 root 안의 모든 answered를 지운다.

**결정론적 재현:**

1. 같은 컴포넌트의 입력값 `abc`로 확정 액션 A와 B를 보낸다.
2. A의 `{diff:null, ref:1}`이 와서 정리 콜백을 예약한다.
3. 같은 프레임 전에 B의 `{diff:..., ref:2}`가 와서 입력의 answered를 열고 빈 입력으로 morph를 예약한다.
4. A의 정리가 먼저 B의 표시를 지운다. B의 morph는 일반 렌더로 취급해 `abc`를 남긴다. B의 응답은 입력을 비워야 한다.

`review3_values.mjs`의 `NULL_BEFORE_MORPH`가 실제 render 분기와 scheduleMorph로 이 결과를 확인한다. 서버에서 응답 순서를 뒤집을 필요가 없다. A는 보조 이벤트여도 같다. 자식-only 응답의 새 정리 분기도 같은 위험을 가진다.

**같은 원인의 반대 방향:** B의 확정 응답이 `ABC`를 렌더하고 다음 무관한 렌더가 `broadcast`를 보낸 뒤 프레임을 실행하면, 첫 morph도 `currentHtml()`에서 최신 `broadcast`를 읽는다. B의 표시로 무관한 서버 값을 포커스된 입력에 넣는다(`COALESCED_RENDER`). 각각 프레임을 실행했다면 B가 `ABC`를 넣은 뒤 무관한 렌더에서는 포커스된 편집값을 보존해야 한다. 이는 단순한 DOM 갱신 합치기가 아니라 정책 결과가 달라지는 문제다.

**제안:** ref 및 해당 응답의 렌더 상태를 morph 작업과 함께 관리한다. 렌더를 합칠 경우에도 필드별 승인 출처와 서버 값의 출처를 보존한다. null 응답이 나중에 열린 다른 응답의 표시를 정리하지 않게 한다. 동일 컴포넌트 null→diff, diff→무관한 diff, 부모·자식 교차를 프레임 장벽으로 고정한 회귀 테스트가 필요하다.

### 중요 2. myself·조상 폼에서 응답 대상 밖의 answered가 계속 남는다

**근거:** `wireview/static/wireview/wireview-boost.js:56–66,86–105`, `wireview/static/wireview/wireview.js:1131–1144,3074–3111,1147–1166`.

`fieldsOf`는 이벤트 요소의 조상 폼 전체를 기록한다. 반면 `send`의 formScope와 `serialize`는 대상 컴포넌트 경계로 좁힌다. 표시한 필드가 실제 전송된 필드와 다를 수 있다. 응답 뒤 settle도 대상 컴포넌트 root만 정리한다.

**재현:** 부모 폼 안에 부모 입력과 LiveComponent를 두고, 자식 안의 Enter 바인딩에서 자식(`myself`)을 호출한다. 조상 폼의 부모 입력까지 pending에 들어간다. 자식 diff 응답은 모든 필드를 answered로 옮기지만 자식 morph/settle은 부모 입력을 닫지 않는다. 이후 부모의 무관한 렌더가 포커스된 부모 입력을 지울 수 있다. 외부 요소가 다른 컴포넌트로 target을 지정하는 경우에도 같은 모양이다.

`MYSELF_SCOPE`는 필드가 대상 root 밖인 상태를 모형에 주어 자식 완료 뒤 answered가 남고, 무관한 keep가 false인 것을 확인한다. 마크업→fieldsOf→serialize 전체 브라우저 경로는 소스상 확인이다.

**제안:** 실제 직렬화 대상 및 확정 정책에 맞는 필드 집합을 기록하고, 응답 완료 시 해당 ref가 소유한 필드를 위치와 관계없이 정리한다. 단순히 부모까지 settle 범위를 넓히면 발견 1처럼 다른 응답까지 닫는다.

### 중요 3. “전송 이후 값이 다르면 보존”은 focused·edited 분기에서 다시 무효화된다

**근거:** `wireview/static/wireview/wireview-boost.js:121–130`, `wireview/static/wireview/values.mjs:32–35`; `docs/design/input-values.md` §2.

보낸 값과 현재 값이 다르면 `committing=false`일 뿐, 별도 보존 사유가 되지는 않는다.

**재현 A:** 서버 기본값 `old`, 보낸 값 `abc`, 추가 입력 `abcz`에서 다른 칸으로 포커스를 옮긴다. 응답이 서버 정규화 값 `ABC`를 보내면 `serverChanged=true`, `focused=false`라서 `abcz`를 덮는다(`POST_SEND_BLURRED`).

**재현 B:** 기본값이 빈 칸에서 `abc`를 보내고 응답 전에 모두 지운다. 포커스가 그대로 있어도 현재값이 defaultValue와 같아 `edited=false`다. 응답이 `ABC`이면 사용자의 삭제를 되돌린다(`POST_SEND_DEFAULT`).

두 경우 모두 production guard의 실행 결과다. 단순히 `abc`에 `z`를 붙이고 계속 포커스한 현재 E2E는 이 예외를 못 잡는다. §0의 기존 서버 갱신 규칙과 §2의 “어떤 경우에도 사라지지 않는다”는 설명이 충돌하며, 전송 이후 편집을 보호한다는 #92의 의도대로라면 구현 보완이 필요하다.

**제안:** 답변 중인 필드가 전송 이후 달라졌다는 상태를 보존 판단에 별도로 전달해 우선순위를 정한다. 입력 세대를 쓸지 문자열 비교만 쓸지도 명시한다. 보낸 값과 같게 지웠다 다시 친 경우는 현재 설계의 문자열 기준상 구별하지 못하고 덮는다. 이것은 현 문서의 명시적인 `응답 시점 값 ≠ 보낸 값` 기준에서는 별도 구현 위반으로 세지 않았다.

### 중요 4. 답이 오지 않은 pending을 연결 종료·컴포넌트 제거가 해제하지 않는다

**근거:** `wireview/static/wireview/wireview-boost.js:43–45,74–89,98–105`, `wireview/static/wireview/wireview.js:138–157,181–196`; `wireview/repository.py:436–468`, `wireview/consumer.py:506–515`.

pending은 `answer(ref)`에서만 삭제된다. close는 serverVsn과 components를 초기화할 뿐 guard를 비우지 않는다. 제거한 요소를 대상으로 settle을 해도 pending에는 접근하지 않는다. 서버에 대상이 없으면 dispatch_event가 None을 반환하므로 render/ref 응답도 없다. 핸들러 예외 역시 정상 ref 응답을 만들지 않는다.

**실행:** `PENDING`은 연결이 끊긴 요소를 pending에 넣고 settle을 호출해도 Map이 남음을 확인한다. 연결 종료 처리는 소스로 확인했다. 반복 실패/재접속으로 DOM 요소와 입력 문자열을 계속 강하게 참조하는 수명 누수다. ref가 즉시 재사용돼 다른 응답과 충돌한다는 주장은 아니다. lastRef는 같은 connection 인스턴스에서 계속 증가한다.

**제안:** 연결 세대와 컴포넌트 소유자를 pending에 기록하고 close/leave/제거/전송 실패 시 정리한다. 없는 대상·실패한 핸들러도 가능한 범위에서 명시적 완료/실패 응답을 주도록 계약을 정한다. 실제 페이지 reload는 JS 문맥이 없어지므로 이 누수를 해소한다.

### 경미 5. 확정 규칙·문서·테스트에 남는 경계

- **click:** 현재 `isCommitAction`은 폼 안/밖 모두 false다. 일반 submit 버튼이 폼 submit 바인딩을 일으키면 정상 확정한다. 반면 버튼의 `click.prevent` 핸들러로 저장하고 submit은 막는 흔한 폼에서는 확정되지 않아 같은 빈 서버 값으로 입력을 비우지 못한다. 설계 §3은 “폼 밖의 click”만 보조로 적어 폼 안 규칙이 불명확하다. 지원하려면 submit 의도 또는 명시적 commit 옵션으로 구별하고 문서와 테스트를 맞춰야 한다. 이번 커밋의 의도된 범위 축소인지 확인이 필요하다.
- **직접 send:** `send(..., 'submit')`와 `{commit:true}`는 지원하지만 `send(..., 'keypress')`는 여전히 보조다. 키 정보를 모른다는 설계상 결정이며 이전 리뷰 5의 예시를 그대로 해결한 것은 아니다. 공개 `types.d.ts`에도 새 options 계약을 반영할지 검토해야 한다.
- **Enter/IME:** `keyup.enter`와 textarea의 Enter 필터도 확정한다. `runSteps`는 `isComposing`을 검사하지 않으므로 `{key:'Enter',isComposing:true}`도 fire/commit한다(순수 실행 확인). 실제 OS/브라우저에서 어떤 이벤트가 나오는지 검증하지 않았으며 기존 이벤트 처리에도 있던 위험이다. textarea의 일반 Enter는 줄바꿈이고, Enter 핸들러가 명시된 경우만 이 정책을 탄다.
- **key_code:** 기존 수정자 `key_code.13`은 fire될 수 있지만 isCommitAction은 `key:enter`만 본다. Enter 단축 표기와 같은 확정 의미를 원하는 경우 누락이다(소스상 확인).
- `values.mjs:11`의 “any event but input” 주석은 새 규칙과 어긋난다.

## 직전 리뷰 3~6 해소 여부

| 이전 발견 | 판정 | 근거 |
|---|---|---|
| 3. 먼저 온 렌더가 표시 소비 | 부분 해소 | 자기 응답 전 pending은 일반 morph에 노출되지 않는다. 다만 응답 도착 후 프레임 전 경합은 중요 1, 전송 후 입력 예외는 중요 3에 남는다. |
| 4. 자식-only 표시 잔류 | 기본 사례 해소, 범위 변형 미해소 | answer(ref,false)가 pending을 즉시 지워 부모 표시를 남기지 않는다. 스트림 건너뛰기도 같은 root의 settle로 닫힌다. myself/조상 폼은 중요 2에 남는다. |
| 5. JS push·send 경로 불일치 | 대부분 해소, 직접 keypress는 명시적 제외 | 바인딩 commit이 exec→push→dispatch로 전달된다. send는 submit 등의 이벤트와 options.commit을 받는다. 직접 keypress는 기본 false다. |
| 6. 검색 방향키 확정 오판 | 해소 | ArrowDown/Up/Escape는 보조이고 Enter 필터만 확정한다. examples/search 바인딩과 일치한다. 실제 search 브라우저 실행은 하지 않았다. |

## 옳다고 확인한 것

- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_event_refs.py -p no:cacheprovider -o 'addopts=--nomigrations --strict-markers' -q`: **8 passed**. join의 vsn, 변경·무변경 응답 ref, bool/string/float/None 제외, ref 없는 이벤트의 기존 응답을 확인한다.
- `node --test tests/js/values.test.mjs tests/js/rendered.test.mjs`: **30 passed**. 값 보존 순수 판단, 확정 이벤트 규칙, diff 조립과 버전 관련 기대가 통과한다. DOM 수명 테스트는 아니다.
- `review3_values.mjs`의 BASELINE: 응답 전에는 입력을 보존하고 해당 응답이 오면 확정하며, 같은 root settle이 표시를 지운다. 자식-only 응답은 pending을 즉시 지운다.
- **새 클라이언트 + 옛 서버:** serverVsn=0이면 ref를 추가하지 않아 옛 command_user_event의 알 수 없는 인자 오류를 피한다. unpaired가 처음 morph에서 사용되는 #91 폴백은 유지된다. 따라서 #92의 짝짓기 보장은 옛 서버에 적용되지 않는다. 새 확정 이벤트 분류와 child-only settle까지 #91과 완전히 동일하다는 뜻은 아니다.
- **옛 클라이언트 + 새 서버:** ref 없이 호출 가능하고 vsn은 기존 render payload의 추가 필드라 무시할 수 있다. Python 테스트의 ref 없는 요청은 이를 일부 뒷받침하나 옛 번들 자체를 실행하지 않았다.
- **join 응답 전 이벤트:** ref 없는 폴백을 사용한다. 같은 연결에서 다른 컴포넌트 join이 이미 vsn을 알렸다면 ref를 보낼 수 있다. capability가 연결 단위인 점은 타당하다. boost 내비게이션의 새 join도 같은 소켓이면 같은 서버 능력을 사용한다.
- **재연결·다중 워커:** close가 serverVsn을 0으로 되돌리고 lastRef를 유지하므로 재협상 전 ref 전송을 피하고 같은 인스턴스 안의 재사용도 피한다. ref는 연결 안에서 왕복하는 값이므로 정상 WebSocket 연결끼리 전역 카운터/워커 간 합의가 필요하지 않다. 여러 워커 통합 실험은 하지 않았다.
- **여러 render:** 정상 command_user_event의 직접 답은 send_render 한 번이고 children은 그 메시지에 묶인다. 후속 채널 작업의 render는 ref 없는 별도 갱신이다. 한 액션에서 유래한 모든 후속 갱신에 승인 권한이 전파되는 설계는 아니다. 이 구분 자체는 타당하나 같은 프레임의 실제 적용은 중요 1을 고쳐야 한다.
- **예제 다섯:** todo의 Enter/blur, search의 방향키와 Enter는 분류에 맞는다. chat은 명시적 `push_js(set_value(...,''))`로도 비우므로 폼 밖 Send를 새 결함으로 보지 않는다. notifications는 input으로 값을 서버에 반영하고 quiz는 시작 후 화면을 교체한다. 이 검토는 소스 수준이다. chat의 명시적 set_value는 guard 밖이므로 전송 후 타이핑 보존을 보장하지 않는다.
- **로딩 정리:** 자식-only 응답에서도 부모 clearLoadingClasses를 예약하는 변경은 disabled 상태가 계속 남던 기본 사례를 해결한다. 다만 정리는 여전히 ref별이 아니라 컴포넌트 전체다. 먼저 끝난 액션이 다른 pending 액션의 버튼까지 풀 수 있고 부모 정리가 자식 로딩까지 건드릴 수 있다. 기존 diff/null 처리에도 있던 제약이 새 children-only 분기로 확대됐다. root 자신은 querySelectorAll 대상이 아니며 keypress-loading을 제거 목록에 넣지 않는 기존 제약도 이번 변경으로 해결되지 않는다. 이를 별도 신규 차단 결함으로 세지는 않았다.

## 확인하지 못한 것

- 브라우저 E2E 10개는 실행하지 않았다. 별도 브라우저 세션을 열지 않았고 저장소 JS 빌드·collectstatic도 실행하지 않았다. 전체 pytest, 실제 옛 서버/옛 클라이언트, reconnect·boost·다중 워커 E2E는 미실행이다.
- 모형은 production guard/render/scheduleMorph를 그대로 실행하지만 실제 Idiomorph의 요소 교체·textarea·선택 영역·IME·브라우저 프레임 스케줄을 증명하지 않는다. 중요 1의 콜백 순서는 결정론적으로 구성했으며 실환경 발생 빈도는 측정하지 않았다.
- 새 E2E의 earlier-answer 테스트는 `wait_for_timeout(150)`으로 debounce가 전송됐다고 가정한다. 느린 실행 환경에서는 Enter 전송 시점과 핸들러 sleep의 관계가 달라질 수 있다. 또 전송 후 `z`를 붙이므로, ref 짝짓기를 잘못 구현해도 값 비교만으로 보존해 통과할 여지가 있다. ref 자체의 효과를 분리한 테스트가 필요하다.
- child-only E2E는 완료 뒤 `!`를 덧붙인다. stale answered가 남아도 보낸 값 `hello`와 현재값 `hello!`가 달라 보호될 수 있어, 새 모델에서 표시 누수를 직접 검증하지 못한다. 완료 뒤 값을 그대로 둔 무관한 morph도 검사해야 한다.
- ArrowDown 테스트는 실제 debounce 완료 전에 방향키가 갔다는 장벽이 없다. 최종 query가 맞아도 위험한 순서를 실행하지 않았을 수 있다. 서버 수신/응답을 테스트 훅으로 멈추고 순서를 통제하는 편이 낫다.
- 기존 `slow_set_from_server` 테스트는 600ms sleep 뒤 이미 `mine`인 값만 검사하므로 서버 응답이 아직 안 와도 통과할 수 있다. 응답 수신 또는 렌더 완료 표식을 기다려야 한다.
- 두 확정 액션, null→diff 같은 프레임, 부모/자식 교차, 외부 form 연결 버튼, 에러/제거/재접속, 포커스 이동 후 서버 정규화와 사용자 삭제는 현재 10개 E2E에 없다.
- `button form="id"`는 fieldsOf와 send가 closest('form')을 사용해 연결된 폼을 발견하지 못한다. serialize도 form.elements 대신 DOM 후손을 읽어 폼 외부 연결 필드를 놓친다. 이는 이전부터 있던 제약이며 이번 커밋의 새 회귀로 분류하지 않았다.

## 재현 산출물

같은 디렉터리의 `review3_values.mjs`, `review3_values.log`에 결정론적 재현과 출력이 있다. 실행: `node <scratchpad>/review3_values.mjs`. assertion은 현재 결함이 실제로 나타나는 결과를 검증하므로 이 스크립트가 종료 코드 0이라고 제품 정책이 올바르다는 뜻은 아니다.

---

## 부록 A. review3_values.log

```text
NULL_BEFORE_MORPH: answered ref 2 lost; expected empty, actual abc
COALESCED_RENDER: commit permission used by later unpaired broadcast, actual broadcast
MYSELF_SCOPE: child settled, outer field still authorized for unrelated morph
PENDING: disconnected element retained even after settle
POST_SEND_BLURRED: abcz overwritten by ABC despite differing from sent abc
POST_SEND_DEFAULT: user deletion overwritten even while focused
BASELINE: ordinary pairing and child-only release pass
POLICY: composing Enter fires/commits; keyup.enter commits; click never commits
```
