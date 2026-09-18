# djust의 Rust VDOM — 무엇을 차용하고 무엇을 두는가

> 2026-09-18. **조사 메모 — 결정 아님.** djust(`djust-org/djust`, 커밋 `ed7e92a`, 2026-09-18 클론)의 Rust
> VDOM 구현을 읽고 wireview에 가져올 것을 가렸다. 소스 정독은 Herdr pane의 Codex(gpt-6-astra)에 맡기고
> 원문을 [djust-vdom-review-codex-2026-09-18.md](./djust-vdom-review-codex-2026-09-18.md)에 그대로 뒀다.
> 여기에는 내가 소스로 다시 확인한 것, 직접 잰 것, 그리고 판정만 적는다. 두 저장소 모두 코드는 손대지 않았다.

---

## 0. 판정

**Rust도, VDOM도, opcode 프로토콜도 가져오지 않는다. 가져올 것은 세 가지 설계 아이디어와 한 가지 테스트
전략이다.**

| 가져온다 | 무엇에서 | 어디에 |
|---|---|---|
| **키 기반 comprehension** (stable key → 항목 상태, 순서는 따로) | `reconcile_keyed` + LIS | GAP-030 (#69). Rendered 모델을 유지한 채 wire 형식만 확장한다 |
| **위젯 보존의 명시적 계약** (children만 무시 / 요소 통째 skip / 속성 단위) | `dj-update="ignore"`와 그 실패 이력(#1252) | GAP-033 어휘 설계와 `onBeforeElUpdated` |
| **sticky의 두 수명을 분리** (같은 소켓 안의 내비게이션 생존 ≠ 재연결 뒤 복원) | ADR-011 / ADR-014 / ADR-018 | GAP-033 (#72) |
| **delta round-trip 속성 테스트** (서버 diff → 실제 wire → 실제 JS 적용 → 복원 HTML == 새 렌더, 연속 변이) | `proptest_round_trip_with_sync.rs`, `fuzz_test.rs` | `tests/js/` + Python 픽스처. **djust가 스스로 못 한 부분**(§4)까지 하는 것이 목표 |

**두지 않는다**: HTML→VDOM→opcode 교체, `dj-id` 발급과 `sync_ids`, loop parse cache, Rust 템플릿 엔진,
MessagePack 전환(djust 자신도 기본은 JSON이다), `<100µs`·`7~11×` 같은 수치의 전재.

이유는 하나로 줄어든다. **djust의 Rust는 wireview에 없는 비용을 지우는 데 쓰인다.** djust는 렌더된 HTML을
html5ever로 파싱해 트리를 만들고 diff하므로, 파싱을 피하는 fast path·캐시·ID 동기화가 필요하다. wireview는
템플릿 컴파일 시점에 static/dynamic을 갈라 두어 그 비용이 애초에 없다. 남는 병목은 Django 템플릿 렌더
(이벤트당 CPU의 60% 이상, `transport-abstraction.md` §1)이고, 그것을 줄이는 djust의 답은 diff가 아니라
**Django 템플릿을 Rust로 다시 쓴 4만 줄짜리 엔진**이다. 그 길은 가지 않는다(§6).

---

## 1. djust가 실제로 하는 일 — 소스로 확인한 것

이벤트 하나의 경로(`python/djust/runtime.py:ViewRuntime` → `crates/djust_live/src/lib.rs:RustLiveViewBackend::render_with_diff`):

1. **Rust 템플릿 엔진이 렌더한다.** `crates/djust_templates`(41,131줄)는 Django 템플릿 문법의 독립 재구현이다.
   Django의 Python 렌더러는 기본 경로에 없다. README는 Django 템플릿 스위트 98.57% 통과(1,047셀 중 1,032)를
   말하면서 "admin and contrib templates still need Django's own backend"라고 적고, 이 클론 당일의 마지막
   커밋도 호환성 수정이다("spell a dict subclass as itself — `{{ v }}` is `str(v)`, as Django renders").
2. **HTML을 html5ever로 파싱해 `VNode` 트리를 만든다**(`djust_vdom/src/parser.rs`). 단, 항상은 아니다 —
   텍스트 하나만 바뀐 경우 old/new HTML 문자열의 공통 접두·접미를 비교해 파싱을 건너뛰는 fast path
   (`try_text_region_fast_path`), 바뀐 의존성이 없는 fragment의 HTML 재사용, 루프 항목의 파싱 결과 캐시
   (`LoopRenderCache.parsed`, #1970)가 있다.
3. **이전 트리와 diff한다**(`diff.rs:diff_nodes`). 결과는 14종의 `Patch`(`Replace`·`SetText`·`SetAttr`·
   `RemoveAttr`·`InsertChild`·`RemoveChild`·`MoveChild`, `Insert/Remove/MoveSubtree`, `Virtual*` 4종).
   주소는 루트부터의 자식 인덱스 경로 `path`와, 요소마다 서버가 발급한 base62 `dj-id`(`d`)다.
4. **diff 뒤 `sync_ids`**로 살아남은 노드의 옛 id를 새 트리에 옮긴다. 이것이 없으면 두 번째 이벤트부터
   클라이언트에 없는 id를 가리킨다(#1408, #1417의 회귀).
5. **JSON으로 보낸다.** `websocket.py:633`에 `self.use_binary = False  # Use JSON for now (MessagePack support TODO)`.
   README의 아키텍처 그림은 "Binary serialization (MessagePack)"이라고 적혀 있다. msgpack은 상태 저장
   (`SerializableViewState`)과 선택적 API에만 쓰인다.
6. **클라이언트**(`python/djust/static/djust/src/12-vdom-patch.js`, 클라이언트 전체는 19,286줄)가 패치를
   종류별로 다시 정렬해 적용한다(RemoveSubtree → RemoveChild → MoveChild → InsertChild → Subtree → 나머지).
   실패하면 full HTML로 복구한다.

이전 트리(`last_vdom`)와 HTML(`last_html`)은 뷰 인스턴스가 연결당 들고 있고, Redis 백엔드로 직렬화할 수 있다.

## 2. diff 알고리즘에서 배울 것

`reconcile_keyed`(`diff.rs:762`)는 교과서적 키 기반 재조정이다. `dj-key`/`data-key`로 항목을 매칭하고,
old-only는 삭제, new-only는 삽입, 매칭된 쌍은 재귀 diff, 그리고 **살아남은 항목들의 옛 위치 수열에서
LIS(최장 증가 부분수열)를 구해 LIS 밖의 항목만 `MoveChild`**한다(`lis.rs`, O(k log k) patience sorting).
중복 키는 "모호"로 강등해 위치 기반으로 다루고(DJE-051), 키 있는 항목과 없는 항목이 섞이면 경고하고 LIS를
끈다(DJE-050).

그 밖의 장치는 전부 **"HTML 트리를 diff하기 때문에" 필요해진 것들**이다.

- `<!--dj-if id-->…<!--/dj-if-->` 경계: `{% if %}` 블록이 열리고 닫히면 뒤 형제의 절대 인덱스가 밀려 엉뚱한
  노드를 patch하므로, 경계 id로 묶어 body만 재귀 비교한다. #1826은 그 판정을 절대 인덱스에서
  "(앞선 비경계 형제 수, 같은 레벨의 서수)"로 바꾼 수정이다. wireview의 `{% if %}`는 블록 단위 dynamic이라
  이 문제가 없다.
- `dj-update="ignore"`: 서버 baseline에 옛 자식을 splice해 두고 내부 재귀를 끊는다. #1252는 그때 옛 cached
  HTML까지 복사해 stale 문자열이 살아남던 회귀다.
- `[dj-virtual]`: 화면에 보이는 창만 DOM에 있는 목록에서는 인덱스 주소가 무의미하므로 키로 주소 지정하는
  별도 opcode 4종을 둔다(ADR-026).

VDOM 크레이트의 테스트 파일 중 15개가 이슈 번호를 달고 있다는 사실(크레이트 전체로는 46개)이 이 접근의 비용을 말해 준다 — 렌더된 HTML에서
구조를 **되찾는** 일은 구조를 **알고 있는** 일보다 어렵다.

## 3. 두 모델의 페이로드 — 직접 잰 것

wireview 쪽은 `bench/benchapp`의 `BenchList`(항목 50개, 항목당 dynamic 셋)로 `render_diff`의 JSON 바이트를
쟀다(부록 A). 서명된 `data-state`는 dynamic 파트라 상태가 바뀌면 함께 다시 나간다 — 이 템플릿에서는 항목
50개가 상태 안에 있어 토큰만 약 600바이트다. 그래서 아래 표의 바닥은 diff가 아니라 토큰이다.

| 변경 (50항목) | wireview 바이트 | 읽는 법 |
|---|---:|---|
| 항목 하나의 텍스트 | 669 | 거의 전부 토큰. diff 본문은 수십 바이트 |
| 항목 하나의 불리언(class 분기) | 661 | 위와 같다 |
| 끝에 하나 추가 | 673 | 새 항목의 dynamics만 |
| **앞에 하나 삽입** | **2,162** | 뒤 50개 항목의 dynamics가 한 칸씩 밀려 전부 다시 나간다 — GAP-030 |
| 첫 항목 삭제 | 2,084 | 같은 이유 |
| 마지막을 맨 앞으로 | 2,119 | 같은 이유 |
| 변경 없음 | 0 | |

키 기반이면 앞 삽입·삭제·이동이 "끝에 추가"와 같은 급(≈ 700)이 된다. 항목이 무거울수록 격차는 커진다.

Codex가 계산한 최소 단위 비교(diff 본문만, 봉투·토큰 제외)는 방향이 같다. `<p>{{x}}</p>`의 x 변경은
wireview 13바이트 대 djust 46바이트(`{"type":"SetText","path":[0],"text":"hello"}`), `class="{{x}}"`는
14 대 69, 두 항목 앞에 하나 삽입은 49 대 220(삽입되는 `VNode`의 메타데이터). **단일 dynamic 변경은
슬롯 번호와 값만 보내는 쪽이 늘 작다.** 반대로 한 항목 안에 dynamic이 많고 그중 하나만 바뀌면 항목
전체 dynamics를 보내는 wireview가 커질 수 있고, `<p>hello {{name}}</p>`처럼 텍스트 노드 하나에 static과
dynamic이 섞이면 djust는 텍스트 노드 전체를 보낸다. 서로 지는 경우가 있지만 **키 기반 comprehension 하나로
wireview의 큰 손해가 사라지고, 나머지는 wireview가 이긴다.**

## 4. 성능 주장을 읽는 법

- README의 **"7~11× faster"는 템플릿 엔진 렌더 속도**다. 같은 표에서 "Static markup … 1.0×"이고 본문이
  "static markup is not faster"라고 적는다. VDOM·wire·클라이언트 적용은 제외한다고 README가 명시한다.
- 구조도의 "diff <100µs"는 그에 해당하는 트리 규모·기계·결과 파일이 고정돼 있지 않다. `benches/diff.rs`는
  이미 만든 `VNode`를 넣고 diff만 재는 Criterion 정의이지 결과표가 아니다.
- `PERFORMANCE_BRAINSTORM.md` §7의 실측(50행 model-backed)에서 이벤트당 total 3~4ms 중 Rust render 0.4ms·
  parse 0.25ms·diff 0.2ms이고, **state sync(4.5ms)·SQL·Python 객체 crossing이 Rust 구간보다 크다.** djust
  자신의 병목도 diff가 아니다.
- **wireview에 대입하면**: 이벤트당 0.2~0.6ms의 60% 이상이 Django 렌더이므로, 나머지 전부를 0으로 만드는
  비현실적 상한도 1.67×다. diff가 그중 10%이고 10배 빨라지면 1.10×다(Amdahl, 설명용 가정).

## 5. 이 조사에서 드러난 wireview 쪽 사실

- **`onBeforeElUpdated`의 반환값은 버려진다.** `wireview-boost.js:morph`의 `beforeNodeMorphed`는 콜백을
  부른 뒤 무조건 `true`를 돌려준다. 공개 JSDoc이 반환형을 `void`로 선언하므로 결함이 아니라 **기능의
  범위**다 — 사용자는 속성을 옮겨 붙이는 보정은 할 수 있어도 "이 서브트리는 건드리지 마라"는 veto를 할 수
  없다. 그 veto는 `wire-stream` 컨테이너에만 하드코딩돼 있다(`isStreamContainer`). GAP-033의 어휘를 정할 때
  이 자리부터 정리한다(§7).
- `Comprehension.diff`(`core/rendered.py:154`)는 i번째와 이전 i번째를 비교하고 바뀐 항목의 dynamics
  **전체**를 `u[i]`로 보낸다. GAP-030의 정확한 현 위치다.

## 6. Rust 자체의 손익

산다: 큰 순수 연산의 CPU 효율, GIL 밖의 병렬 구간, 타입 있는 트리 처리.
낸다: maturin·PyO3 툴체인, Python ABI × free-threaded 조합, macOS·Linux·Windows(x64·ARM64) wheel 매트릭스,
소스 빌드 폴백, 네이티브 크래시 디버깅, 기여자의 Rust 진입 장벽. 이 저장소는 **Windows·SQLite·컴파일러
없는 설치**를 배포 전제로 두고 있다(daphne조차 ARM64 wheel 문제로 빼는 판단을 했다). Rust 확장은 그 전제에
릴리스 책임 한 겹을 얹는다.

그리고 wireview의 병목은 Rust diff가 만질 수 있는 곳에 있지 않다. 렌더가 병목이면 답은 (1) 렌더 횟수를
줄이는 것 — 저장 버스트 합치기(`rails-benchmark-2026-09-18.md` §5.3: 저장 20회에 렌더 21회), 의존성이
없는 fragment의 재렌더 회피(djust의 `changed_keys` 아이디어는 여기서 유효하다, 단 Django 태그·필터·
context 의존성을 정확히 알 때만) — 이고 (2) 그 다음이 순수 Python `Rendered` diff·마커 파싱의 실측이다.
그 실측에서 diff가 지배적이라는 증거가 나오면, **기존 프로토콜을 유지한 채 선택적 네이티브 가속기**를 Python
폴백·동일 결과 속성 테스트·Windows wheel과 함께 비교하는 것이 순서다. 지금은 그 증거가 없다.

## 7. GAP별 함의

- **GAP-030 (#69) 키 기반 comprehension.** Rendered 모델 안에서 푼다: 항목마다 key를 알고(템플릿 `{% for %}`의
  key 지정), 서버는 `key → item dynamics`와 순서를 따로 diff한다. 첫 판은 순서 배열 전체를 보내도 된다
  (여전히 O(n)이지만 항목 내용은 안 보낸다). 큰 목록에서 순서 페이로드가 문제로 잰 뒤에 LIS와 anchor(`before_key`)
  op를 넣는다. 클라이언트는 `rendered.mjs`가 키로 항목을 재배열해 HTML을 복원하면 되고 **idiomorph도 그대로다.**
  계약으로 정할 것: 중복 키, 키 없는 항목이 섞일 때, 비어 있다가 채워질 때, 템플릿이 바뀌었을 때의 폴백.
  `data-state` 토큰이 비교 바닥이라는 것도 같이 적는다 — 항목을 상태에 들고 있는 컴포넌트는 항목을 조회로
  옮기는 편이 더 큰 절감이다.
- **GAP-033 (#72) sticky.** djust는 세 ADR로 나눴다 — 같은 소켓 안에서 내비게이션을 건널 때 인스턴스와 DOM을
  떼어 붙이기(011), 이미 살아 있는 인스턴스를 태그가 자동 재부착(014), 소켓이 끊긴 뒤 stable id로 복원(018,
  부모·자식 모두 opt-in). wireview도 **"내비게이션 생존"과 "재연결 복원"을 한 기능으로 묶지 않는다.** 경계를
  넘는 이동은 전체 로드라는 0.3.0의 결정과 맞물려 범위는 "한 live_session 안"이다(`backlog-review-2026-09-10.md`).
  Turbo PR #1562의 교훈까지 합치면 어휘는 셋 — 내비게이션 생존, 서브트리 morph 제외, 속성 단위 제외.
- **GAP-035 (#74) 배치 업데이트.** djust의 VDOM에서는 답을 찾지 못했다. 같은 클래스 N개의 assigns를 모아
  한 번에 load하는 훅이 답이고, wire 프레임을 하나로 합치는 것과는 다른 문제다.
- **GAP-034 (#73) dead view**와는 무관하다.

## 8. 다음 단계

1. GAP-030 설계 메모: wire 형식 확장안(`{"k": [...order], "u": {key: dynamics}, "n": …}` 류), 폴백 계약, 측정
   시나리오(§3의 표 + 항목 500개). 착수 전에 `bench/`에 목록 편집 시나리오를 넣는다.
2. delta round-trip 속성 테스트: `Rendered` old/new → 실제 payload → `rendered.mjs` 적용 → HTML 동일성을 연속
   변이(삽입·삭제·재정렬·조건 토글·빈 목록)에서 검사. Python이 만든 픽스처를 node 테스트가 소비한다.
3. `onBeforeElUpdated`의 boolean 계약 또는 `wire-update="ignore"` 도입 여부를 GAP-033 어휘와 함께 결정.

## 9. 조사 방법과 한계

- Codex(gpt-6-astra medium)가 Herdr pane에서 11분간 소스를 읽고 원문을 썼다. 나는 그중 판정에 걸리는 것을
  다시 열어 확인했다: `websocket.py:633`, `Patch` enum(`lib.rs:629`), `reconcile_keyed`·`lis.rs`, `render_with_diff`와
  `try_text_region_fast_path`, README 성능 절, `Comprehension.diff`, `wireview-boost.js:morph`.
- 실행한 것은 wireview 쪽 페이로드 측정뿐이다. djust의 Rust·Python·JS 테스트와 벤치는 돌리지 않았고,
  연결당 메모리도 재지 않았다. §3의 djust 바이트는 wire 스키마에 맞춰 손으로 만든 예시다.
- djust의 SSE 폴백·HTTP-only 모드·PWA·LiveView Native ADR은 이 메모의 범위 밖이다
  (`rails-benchmark-2026-09-18.md` §4가 짚었다).

## 부록 A. 페이로드 측정 재현

```bash
# bench 설정으로 mount() → render_diff() 의 JSON 바이트를 잰다 (testproj 위, 채널 레이어 불필요)
DJANGO_ALLOW_ASYNC_UNSAFE=1 uv run pytest -c pyproject.toml --rootdir . --ds bench.settings -s -q <아래 파일>
```

```python
import json
import pytest
from bench.benchapp.live import BenchList
from wireview.testing import mount

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _items(n, start=0):
    return [{"name": f"item {k}", "qty": k, "done": k % 3 == 0} for k in range(start, start + n)]


async def _live(**state):
    view = await mount(BenchList, **state)
    view._repo.is_live = True
    await view.wire.render_diff(view.component, view._repo)  # first render sets the snapshot
    return view


async def _measure(view, mutate):
    mutate(view.component)
    diff = await view.wire.render_diff(view.component, view._repo)
    return len(json.dumps(diff)) if diff is not None else 0


async def test_list_edit_payloads():
    n = 50
    cases = {
        "text in one item": lambda c: c.items[10].__setitem__("name", "renamed"),
        "toggle done on one item": lambda c: c.items[10].__setitem__("done", True),
        "append one at end": lambda c: c.items.append(_items(1, 999)[0]),
        "insert one at front": lambda c: c.items.insert(0, _items(1, 999)[0]),
        "remove first": lambda c: c.items.pop(0),
        "move last to first": lambda c: c.items.insert(0, c.items.pop()),
        "no change": lambda c: None,
    }
    for name, mutate in cases.items():
        print(name, await _measure(await _live(items=_items(n)), mutate))
```
