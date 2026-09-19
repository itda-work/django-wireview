# GAP-030 설계 메모 리뷰 — Codex 원문

> Herdr pane에서 Codex(gpt-6-astra, medium)가 2026-09-19에 쓴 적대적 리뷰 원문이다. 대상은
> [keyed-comprehension.md](./keyed-comprehension.md)의 첫 판(커밋 4adfd93)과 round-trip 테스트(커밋 7593fc3)다.
> 반영 결과는 그 메모의 머리말과 각 절에 있고, 이 파일은 손대지 않은 근거 문서로 둔다.
> 본문의 `test_proto_payload.py`·`review_probe.py`·`review_probe.out`은 저장소 밖 scratch 파일이었다. 재현에 필요한
> `review_probe.py`와 그 출력은 부록에 그대로 옮겼다(시제품 `test_proto_payload.py`의 핵심은 메모 §3-1 절차다).

---

판정: 수정 후 진행

## 발견 사항

검토 기준은 `4adfd93`의 설계 메모와 `7593fc3`의 안전망이다. 이슈는 제공된 `issue-69.md`의 본문·댓글을 읽었다. 저장소는 수정하지 않았다. 아래 실행 결과는 같은 디렉터리의 `review_probe.py`와 `review_probe.out`에 남겼다. 차단 등급의 정확성 반례는 발견하지 못했지만, 선택 규칙·성능 근거·검증 범위는 구현 전에 고쳐야 한다.

### 중요 1. “다시 보내는 항목 수”는 전송 바이트의 대리 지표로 충분하지 않다

**근거:** 메모 `docs/design/keyed-comprehension.md:77`은 새 항목이 적으면 무조건 `k`를 고른다. 그러나 `k`는 변경하지 않은 앞뒤 구간까지 표현해야 하고, `u`는 그 부분을 생략한다. 실행에서 1,002개 단일 문자열 항목의 500·501번만 서로 바꾸면 다음과 같다(실제 Channels 기본값과 같은 `json.dumps`):

```json
{"u": {"500": ["c"], "501": ["b"]}, "n": 1002}
{"k": [[0, 500], [501, 1], [500, 1], [502, 500]]}
```

위치 형태는 **46B**, 제안 형태는 **49B**다. 제안 규칙은 다시 보내는 항목이 2개에서 0개가 되므로 더 큰 쪽을 선택한다. HTML 정확성 문제는 아니지만, 대역폭 최적화의 선택 조건이 목적과 어긋난다.

**제안:** 구간 메타데이터와 새 항목 dynamics를 포함한 직렬화 비용으로 선택하고, 같으면 위치 형태를 유지한다. 두 후보를 항상 완전히 JSON 직렬화하는 비용이 부담이면 길이를 누적 추정하거나 확실히 이득인 삽입·삭제 fast path만 우선 적용한다. 이 작은 항목 반례와 대부분 불변인 큰 목록의 소수 위치 변경을 벤치에 넣는다. “항목 수 기준의 휴리스틱이며 바이트 감소는 보장하지 않음”으로 계약을 낮추는 선택도 가능하지만, 그 경우 문서에 명시해야 한다.

### 중요 2. 중복 내용의 앞에서 꺼내기가 시제품에서는 O(n²)이고, 단일 이동도 트리밍만으로 끝나지 않는다

**근거:** `test_proto_payload.py:52`는 동일 식별자의 위치들을 list에 쌓고 `:57`에서 `pop(0)`한다. 앞 원소 제거마다 뒤의 인덱스들이 이동한다. `old = [a] * n + [b]`, `new = [b] + [a] * n`은 앞뒤 트리밍이 모두 0이며 같은 버킷을 n번 앞에서 제거한다. 시제품 함수를 그대로 추출하여 5회 중앙값을 재면 다음과 같다.

| 중복 a 개수 | 시제품 diff 시간 |
|---:|---:|
| 4,000 | 2.643ms |
| 8,000 | 6.264ms |
| 16,000 | 19.791ms |
| 32,000 | 73.238ms |

또한 메모 `:87`의 “한 항목 이동은 1단계에서 거의 다 끝나므로 해시를 거의 하지 않는다”는 일반화가 틀렸다. 고유 항목 목록의 마지막을 맨 앞으로 옮겨도 공통 접두·접미가 모두 없으므로 전 항목을 해시한다. 시제품 `:38`은 같은 길이면 우선 기존 positional diff를 만들어 바뀐 항목까지 직렬화하고, 그 결과를 버린 뒤 매칭한다.

**제안:** 버킷을 `deque.popleft()` 또는 위치 배열+소비 커서로 구현한다. 비용을 항목 개수만이 아니라 재귀 dynamics의 전체 크기에 대한 예상 선형 비용으로 설명하고, tuple 변환의 임시 메모리도 측정한다. 동일 길이 사전 판정에서는 변경 개수만 세고 payload 생성은 미룬다. 중복 회전, 고유 항목 회전, 깊고 큰 중첩 항목, 대부분 변경된 목록을 포함한다. 현재 수치는 시제품의 결함이며, deque를 쓰는 최종 설계까지 O(n²)라는 주장은 아니다.

### 중요 3. §6의 `_temporary_assigns` 조언은 이번 렌더의 서명 토큰을 줄이지 않는다

**근거:** `wireview/core/state.py:197`은 `_exclude_fields`만 제외하고 현재 필드를 서명한다. `wireview/consumer.py:1056`에서 diff를 만든 **뒤** `:1059`에서 temporary assigns를 초기화한다. 실행한 `ReviewTemp`에 `_temporary_assigns = {"items"}`를 지정해도 토큰 복원 결과에 1,000자 item이 그대로 있었다. 초기화 후 필드는 `[]`가 됐지만 `wire._state_token`의 캐시 JSON에도 원본이 남았다. 마지막 Rendered 역시 이전 화면 dynamics를 보관한다(`wireview/core/meta.py:356`).

따라서 메모 `:145`의 “남은 토큰 바이트를 줄이려면 `_temporary_assigns`”는 성립하지 않는다. 다음 이벤트마다 목록을 다시 조회해 채우면 매번 큰 상태를 서명하며, 채우지 않으면 일반 comprehension은 빈 목록을 렌더한다. `k`가 화면에 기존 항목을 계속 보존해 주는 기능은 아니다.

**제안:** 토큰 절감 조언은 조회 기반 재구성 또는 `_exclude_fields`로 바꾸고, 재연결 시 제외한 항목을 다시 로드해야 한다는 조건을 적는다. `_temporary_assigns`는 모델 필드의 렌더 후 초기화라는 별도 효과로 설명한다. 항목 안에 상태 토큰이 들어 있는 경우도 계약 표에 추가한다. 그 토큰이 바뀌면 항목 전체가 다른 내용이므로 재사용할 수 없으며, 많은 항목의 토큰이 갱신되는 렌더에서는 최적화 이득이 사라진다. 서명을 식별자에서 빼서 해결하면 오래된 토큰을 복원하게 되므로 금지해야 한다.

### 중요 4. 제공된 시제품은 최종 wire·선택 규칙과 다르고, 측정표는 그 시제품의 값이다

**근거:** 메모 `:70`은 `n`을 없앴지만 `test_proto_payload.py:75`는 여전히 `{"k": ..., "n": len(new)}`를 반환한다. BenchList와 같은 항목 dynamics 및 슬롯 봉투로 재구성한 결과, 메모 표의 수치가 모두 **n 포함** 결과와 일치했다.

| 변경 | 50개: 메모/시제품 → n 제거 | 500개: 메모/시제품 → n 제거 |
|---|---:|---:|
| 앞 삽입 | 68 → 59 | 70 → 60 |
| 중간 삽입 | 78 → 69 | 82 → 72 |
| 첫 삭제 | 32 → 23 | 34 → 24 |
| 마지막을 앞으로 | 41 → 32 | 44 → 34 |
| 뒤집기 | 463 → 454 | 4,914 → 4,904 |

기존 위치 형태 값도 각각 메모와 일치했다. 즉 절감 방향은 맞지만 제안 wire의 실측치로는 부정확하다.

선택 규칙도 다르다. `old=[a,b,c] → new=[a,b]`에서 시제품은 `{"k":[[0,2]],"n":2}`를 내보낸다. 메모는 끝 삭제를 기존 `{"u":{},"n":2}`와 바이트까지 같게 유지한다고 했다. 모든 항목을 교체하는 `[a,b] → [c,d]` 역시 시제품은 k를 고르지만, 문서의 항목 수 동률 규칙은 u여야 한다. 구현해야 할 선택 규칙의 비용이 시제품 측정에 온전히 반영되어 있지 않다.

또한 시제품 `:119`의 total은 `json.dumps(diff)` 길이다. 메모 `:144`는 이를 “전체 프레임”이라고 부르지만 실제 전송에는 `consumer.py:1035`의 id/diff 봉투와 `core/transport.py:116`의 command/payload 봉투가 추가된다. `:116`~`:119`는 각 경우 render_diff 시간을 한 번만 재므로, 이 스크립트만으로 전체 렌더 차이가 “측정 잡음 안”이라는 `:88`의 주장을 검증할 수도 없다.

**제안:** 최종 wire와 선택 조건을 반영한 구현으로 다시 측정한다. 단위를 “서명 포함 diff 객체 JSON”, “render 메시지 JSON”, “WebSocket 프레이밍 포함 바이트”로 구분한다. 표 자체는 제안의 효과를 보수적으로 보였지만, 98% 같은 비율도 같은 측정 경계를 사용해 다시 산출한다. 앞 삽입의 6.2→2.5µs는 시제품의 평평한 comprehension microbenchmark임을 명시하고 전체 렌더 벤치와 분리한다.

### 중요 5. 현 round-trip은 좋은 기반이지만, 새 형태의 실행 여부와 연결·자식 상태 계약을 검증하지 않는다

**근거:** `tests/test_diff_roundtrip.py:179`의 coverage guard는 u/p/r/s 등을 확인하지만 k를 요구하지 않는다. 새 기능이 항상 위치 형태로 폴백하거나 버전 인자가 누락돼 한 번도 켜지지 않아도 현재 테스트는 통과한다. 이 리뷰에서 세 테스트를 직접 호출하여 전부 통과했다. 이는 현 위치 프로토콜의 안전망 확인이며 아직 없는 k 구현의 검증은 아니다.

`tests/js/roundtrip.mjs:34`는 resolver 없이 `buildHtml`을 호출한다. 이 경로는 ComponentRef를 빈 문자열로 만든다(`rendered.mjs`의 `renderDynamic`). 테스트 템플릿에 참조·data-state·wire-stream·hook·input은 없다. 실제 브라우저는 `wireview.js:200`에서 children을 먼저 적용하고 `:905`에서 resolver로 자식의 현재 HTML을 결합한다. 버전 협상, 재연결 초기화, boost 재join도 이 드라이버 밖이다.

**제안:** 난수 테스트를 유지하면서 다음의 결정적 계약 검증을 추가한다.

- k가 실제 발생하는 앞 삽입·삭제·순환 이동·중복 이동, 같은 변경의 legacy 버전에서 k가 재귀적으로 전혀 없는지. 변이 결과의 HTML과 형태를 함께 확인한다.
- k 적용 다음에 u 적용, 그 반대, 블록 p 아래 k, 바깥 항목 이동과 안쪽 목록 변경, full 교체 이후의 delta. 현재 중첩 목록도 다루지만 바깥 항목 전체 교체로만 지나갈 수 있으므로 **블록 p 아래 k**는 따로 강제한다.
- 실제 자식 resolver를 둔 부모 재정렬+자식 동시 변경, 부모 불변+자식 변경, 자식 교체/제거. children 먼저 적용하는 순서를 검증한다.
- JSON을 거친 `to_dict/from_dict` 스냅샷 이후 delta, 재연결/boost의 첫 렌더가 full인 것, no-change None, 토큰 갱신.
- 구간 복원이 원본 배열의 길이·순서를 바꾸지 않는지 및 여러 diff를 연속 적용했을 때의 오염 여부.

DOM에서는 같은 초기 상태·같은 최종 HTML이면 새 wire 형태가 추가적인 정체성 정보를 주지 않는다. 따라서 이 설계 자체가 focus나 hook을 깨뜨린다는 반례는 발견하지 못했다. 다만 “키가 하던 일”을 DOM 상태 보존까지 넓혀 해석하면 틀린다. 안정된 id가 없는 동일 마크업 항목의 로컬 입력 상태는 내용 매칭으로 식별되지 않는다. id 있는 입력·hook·stream 목록의 이동을 기존 u와 새 k로 비교하는 좁은 E2E 검증을 완료 조건에 추가하면 이 경계를 확인할 수 있다.

### 경미 6. 폴백 표의 `cycle` 설명과 빈 목록 설명은 실제 파서 분기를 정확히 구분하지 않는다

**근거:** 메모 `:99`는 `forloop.counter`, `cycle`을 묶어 위치 형태로 폴백한다고 한다. 실행에서 `{{ forloop.counter }}`는 dynamic이라 위치 갱신이었지만 `{% cycle 'a' 'b' %}`는 변수 노드가 아니므로 마커가 붙지 않았고, 항목 static이 달라져 **루프 전체 문자열**이었다. 근거는 `wireview/template_engine.py:233`의 VariableNode 처리와 `wireview/core/rendered.py:343`의 uniform 검사다. `{% empty %}`도 구문 존재 자체가 아니라 실제 렌더된 내용이 uniform인지에 따라 판정된다.

**제안:** 표를 “내용은 달라도 uniform static을 유지함 → u/k 비용 선택”, “uniform 아님 → 문자열”, “이전 슬롯 타입/static이 다름 → 새 전체 값” 순서로 적는다. 위치 의존 값이 있다고 반드시 모든 항목이 변하거나 반드시 u가 선택되는 것은 아니며, 반복 패턴이 같으면 내용 재사용은 여전히 정확하다. 기본적인 full/string 전환 계약은 현 파서를 그대로 두면 유지된다.

### 경미 7. 버전과 구간 참조의 수명을 구현 계약으로 더 명시해야 한다

**근거:** `?vsn=2`를 connect의 ASGI scope에서 읽겠다는 메모 `:131`은 올바르다. 다만 기존 `consumer.query_string`은 페이지 검색 조건이며 `command_query_string`에서 바뀐다(`consumer.py:492`). 브라우저는 boost의 `newLocation`에서도 페이지 쿼리를 다시 보낸다(`wireview.js:149`). 둘을 재사용하면 페이지 이동이 프로토콜 capability를 바꾸게 된다.

현재 렌더 경로는 `consumer._render_tree → component._render_diff → wire.render_diff → _compute_rendered_diff → get_diff → changes → _diff_value → Rendered.diff/Comprehension.diff`다. 최상위 comprehension뿐 아니라 블록 안쪽과 LiveComponent 렌더에도 같은 capability가 전달돼야 한다. 기본 최신인 mount 하네스만 검사하면 실제 consumer에서 누락된 인자를 찾기 어렵다.

**제안:** capability는 연결별 별도 필드로 두고 페이지 query_string·join state·signed envelope의 v와 분리한다. missing/잘못된 값/중복 값의 보수적 처리와 앞으로의 버전 해석 규칙을 적는다. 재연결은 같은 JS의 WebSocket URL로 다시 협상하고, 같은 소켓의 boost 이동은 capability를 유지하되 새 컴포넌트 렌더 기준은 full로 시작한다. 향후 연결 외부화에는 capability도 포함한다.

구간은 직전 **완성된 comprehension**을 기준으로 한다고 명시한다. 서버 스냅샷은 계속 s/d 전체 구조만 저장하며 k delta를 `from_dict`에 넣지 않는다. j≥0, len>0, j+len≤old.length, 빈 결과는 k=[] 같은 범위 계약을 정하고, 생성 알고리즘은 한 old 위치를 중복 소비하지 않는다고 적는다. 이 조건이면 새 배열의 항목을 기존 dynamics에서 참조해도 현 항목 전체 교체 방식과 맞는다. 미래 항목 내부 diff나 중복 참조를 허용할 때는 객체 공유에 따른 상호 오염을 다시 검토해야 한다.

## 메모에서 옳다고 확인한 것

- **이 작업을 렌더 결과 전송의 최적화로 한정하면 템플릿 키는 필수가 아니다.** 같은 comprehension static 아래에서 타입과 nested static까지 포함한 dynamics의 정확한 동등성은 같은 렌더 구조를 뜻한다. 문자열·Rendered·Comprehension·ComponentRef를 구분하는 시제품 `_h`는 그 정보를 보존한다. Python dict는 tuple hash 충돌만으로 동등하다고 취급하지 않으므로 단순 해시 충돌이 잘못된 재사용으로 이어지지 않는다.
- **중복 내용의 임의 짝짓기는 복원 결과에 영향을 주지 않는다.** 단, DOM 개체 정체성을 보장하는 명제가 아니라 렌더 구조의 명제다. 키를 지금 도입하지 않고 항목 내부 부분 diff와 함께 나중에 검토하는 선택은 합리적이다.
- **ComponentRef는 예외를 명확히 설명하면 안전하다.** 같은 id가 시간에 걸쳐 같은 HTML을 뜻하지는 않는다. 같은 현재 resolver를 사용하면 같은 참조는 같은 자식 HTML로 해석된다. 실제 클라이언트가 자식 diff를 먼저 적용하므로 참조 내용을 그대로 재사용하는 것은 기존 계약과 맞는다.
- **k 구간의 old 읽기/new 배열 생성과 n 생략은 타당하다.** 새 길이는 구간 길이와 literal 항목 수로 결정된다. 왼쪽부터 기존 배열을 덮어쓰지만 않으면 회전·뒤집기에서 읽기 소스가 훼손되지 않는다.
- **기존 DOM 경로를 그대로 두는 범위는 타당하다.** `wireview.js:917` 이후는 전체 currentHtml로 기존 morph·hook 콜백을 실행하고, `wireview-boost.js:37`은 stream container의 morph를 막는다. k가 DOM 이동 명령으로 해석되지 않으므로 LIS는 이 계층에서 필요 없다. 동일 HTML 복원에 더해 메시지 발생 여부와 자식 적용 순서도 유지해야 한다.
- **새 클라이언트/옛 서버, 옛 클라이언트/새 서버에 대한 협상 방향은 맞다.** 옛 서버는 URL query의 vsn을 렌더 capability로 읽지 않으므로 기존 diff만 보내며, 옛 JS의 applyPartial은 k 객체를 일반 값으로 저장해 `[object Object]`가 된다. 그래서 새 서버의 보수적 기본값이 필요하다.
- **전체 렌더와 스냅샷에 k를 도입할 이유는 없다.** 현재 `get_diff(None)`은 s/d/f이고 `to_dict/from_dict`는 전체 구조를 저장·복원한다. 블록·중첩 comprehension·ComponentRef를 포함한 JSON 스냅샷 왕복에서 구조 동등성과 내용 식별자 동등성을 실행으로 확인했다.
- **앞 삽입의 바이트 절감은 실재한다.** 기존 표의 positional 값과 n 포함 시제품 값 전부를 재현했다. 잘못된 단위·n 존재·선택 조건을 바로잡으면 핵심 최적화 방향은 유지된다. 서명 상태가 큰 목록에서 지배적이라는 관찰도 방향은 맞다.
- **round-trip을 먼저 추가한 순서는 좋다.** 40개 시드 × 30단계의 실제 JS 복원, coverage guard, 의도적으로 갱신을 누락하는 세 테스트 모두 직접 실행해 통과했다. 구현 전 안전망으로 가치가 있다.

## 확인하지 못한 것

- k를 지원하는 최종 Python/JS 구현이 아직 없으므로 그 구현의 end-to-end 정확성·버전 혼재 E2E·make bench-compare 결과는 확인하지 않았다. 시제품 함수만 추출하여 반례와 비용을 실행했다.
- aside 브라우저를 이용한 DOM E2E는 실행하지 않았다. focus·입력값·hook 인스턴스·stream 노드 생존은 소스에 따른 영향 분석이며, 실제 브라우저에서 새 형태를 검증했다는 뜻이 아니다.
- Phoenix `1357afe` 원문은 재조회하지 않았다. 메모 §2의 wire 비교와 변경 추적에 대한 해석은 독립 검증하지 않았으며, 판정은 로컬 wireview 구현을 기준으로 했다.
- 정확한 실제 render 메시지 바이트와 전체 렌더 CPU/메모리 전후 비교는 재측정하지 않았다. 표 재현은 BenchList의 세 dynamic 값과 슬롯 봉투를 재구성한 것이고, signed-state 실험은 별도 최소 컴포넌트다. 서버·브라우저 전체 벤치로 확대해서 해석하면 안 된다.
- 현재 이슈 AC2와 제안 AC2는 다른 계약이다. 제공된 마지막 댓글까지는 메인테이너 결정 대기였으므로, 이 리뷰가 AC 변경 승인이나 이슈 완료를 대신하지 않는다.

---

## 부록 A. review_probe.py

```python
import ast, json, sys, time, statistics, runpy
from pathlib import Path
ROOT=Path('/Users/allieus/Apps/itda-work/django-wireview')
OUT=Path(__file__).parent
sys.path.insert(0,str(ROOT))
from django.conf import settings
settings.configure(SECRET_KEY='review-only', INSTALLED_APPS=['django.contrib.auth','django.contrib.contenttypes','wireview'], TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates','APP_DIRS':True}], WIREVIEW={'AUTO_GENERATE_STUBS':False}, USE_TZ=True)
import django
django.setup()
from wireview.core import rendered as R
from wireview.template_engine import TemplateMarker
from django.template import Template
ns={'R':R,'POSITIONAL':R.Comprehension.diff}
a=ast.parse((OUT/'test_proto_payload.py').read_text())
exec(compile(ast.Module(body=[n for n in a.body if isinstance(n,ast.FunctionDef) and n.name in ('_h','_ident','matched_diff')],type_ignores=[]),'prototype-functions','exec'),ns)
matched=ns['matched_diff']
def c(vals): return R.Comprehension(['<b>','</b>'],[[v] for v in vals])
def dump(v): return json.dumps(v)
def k(v): return {key:value for key,value in v.items() if key!='n'}
old=c(['a']*500+['b','c']+['a']*500); new=c(['a']*500+['c','b']+['a']*500)
u=new.diff(old); m=k(matched(new,old))
print('COUNT_RULE_BYTES',len(dump(u)),len(dump(m)),dump(u),dump(m))
for name,old,new in [('tail-delete',c(['a','b','c']),c(['a','b'])),('all-edit',c(['a','b']),c(['c','d']))]:
 print('PROTOTYPE_RULE',name,dump(new.diff(old)),dump(matched(new,old)))
for n in [4000,8000,16000,32000]:
 old=c(['a']*n+['b']);new=c(['b']+['a']*n)
 times=[]
 for _ in range(5):
  t=time.perf_counter();matched(new,old);times.append((time.perf_counter()-t)*1000)
 print('DUPLICATE_MS',n,round(statistics.median(times),3))
for src in ['{% for x in items %}<b>{{ forloop.counter }} {{ x }}</b>{% endfor %}',"{% for x in items %}<b class='{% cycle 'a' 'b' %}'>{{ x }}</b>{% endfor %}"]:
 marker=TemplateMarker();temp=Template(src)
 o=R.Rendered.from_marked_html(marker.render_marked(temp,{'items':['x','y','z']}))
 n=R.Rendered.from_marked_html(marker.render_marked(temp,{'items':['w','x','y','z']}))
 print('POSITION_DEPENDENT',type(o.dynamic[0]).__name__,dump(n.get_diff(o).to_payload()))
# Existing tests, without pytest's cache or repository fixture writes.
tests=runpy.run_path(str(ROOT/'tests/test_diff_roundtrip.py'))
for name in ['test_the_client_rebuilds_every_render_from_the_diffs','test_the_walk_exercises_partial_diffs','test_the_driver_catches_a_client_that_drops_an_update']:
 tests[name]();print('PASS',name)
# Snapshot identity includes blocks, nested lists and refs, but never a k delta.
r=R.Rendered(['',''],[R.Comprehension(['',''],[[R.Rendered(['<i>','</i>'],[R.ComponentRef('child')])],[R.Comprehension(['',''],[['x']])]])])
restored=R.Rendered.from_dict(json.loads(json.dumps(r.to_dict())))
print('SNAPSHOT',r==restored,ns['_ident'](r.dynamic[0].dynamics[0])==ns['_ident'](restored.dynamic[0].dynamics[0]))
from wireview import Component
from wireview.core.state import sign_state,unsign_state
class ReviewTemp(Component):
 _temporary_assigns={'items'}
 items:list[str]=[]
from wireview.core.meta import WireviewMeta
from django.contrib.auth.models import AnonymousUser
v=ReviewTemp(id='review-temp',items=['x'*1000], user=AnonymousUser(), wire=WireviewMeta({}))
token=sign_state(v)
print('TEMP_SIGNED_ITEMS_BEFORE_CLEAR',len(unsign_state(token,v._name)['items'][0]))
v._clear_temporary_assigns()
print('TEMP_FIELD_AFTER_CLEAR',v.items,'CACHED_JSON_HAS_ITEMS', 'x'*1000 in v.wire._state_token[0])

for count in [50,500]:
 base=[["done" if i%3==0 else "", f"item {i}",str(i)] for i in range(count)]
 old=R.Comprehension(["<li>"," "," ","</li>"],base)
 for name,vals in [("front",[["done","item 999","999"]]+base),("middle",base[:count//2]+[["done","item 999","999"]]+base[count//2:]),("remove",base[1:]),("move",base[-1:]+base[:-1]),("reverse",base[::-1])]:
  new=R.Comprehension(old.static,vals)
  u=new.diff(old);m=matched(new,old)
  print("TABLE_BYTES",count,name,len(dump({"4":u})),len(dump({"4":m})),len(dump({"4":k(m)})))
```

## 부록 B. review_probe.out

```text
COUNT_RULE_BYTES 46 49 {"u": {"500": ["c"], "501": ["b"]}, "n": 1002} {"k": [[0, 500], [501, 1], [500, 1], [502, 500]]}
PROTOTYPE_RULE tail-delete {"u": {}, "n": 2} {"k": [[0, 2]], "n": 2}
PROTOTYPE_RULE all-edit {"u": {"0": ["c"], "1": ["d"]}, "n": 2} {"k": [{"d": ["c"]}, {"d": ["d"]}], "n": 2}
DUPLICATE_MS 4000 2.643
DUPLICATE_MS 8000 6.264
DUPLICATE_MS 16000 19.791
DUPLICATE_MS 32000 73.238
POSITION_DEPENDENT Comprehension {"0": {"u": {"0": ["1", "w"], "1": ["2", "x"], "2": ["3", "y"], "3": ["4", "z"]}, "n": 4}}
POSITION_DEPENDENT str {"0": "<b class='a'>w</b><b class='b'>x</b><b class='a'>y</b><b class='b'>z</b>"}
PASS test_the_client_rebuilds_every_render_from_the_diffs
PASS test_the_walk_exercises_partial_diffs
PASS test_the_driver_catches_a_client_that_drops_an_update
SNAPSHOT True True
TEMP_SIGNED_ITEMS_BEFORE_CLEAR 1000
TEMP_FIELD_AFTER_CLEAR [] CACHED_JSON_HAS_ITEMS True
TABLE_BYTES 50 front 1546 68 59
TABLE_BYTES 50 middle 815 78 69
TABLE_BYTES 50 remove 1480 32 23
TABLE_BYTES 50 move 1511 41 32
TABLE_BYTES 50 reverse 1511 463 454
TABLE_BYTES 500 front 16398 70 60
TABLE_BYTES 500 middle 8392 82 72
TABLE_BYTES 500 remove 16330 34 24
TABLE_BYTES 500 move 16362 44 34
TABLE_BYTES 500 reverse 16362 4914 4904
```
