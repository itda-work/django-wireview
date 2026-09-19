# #89·#91 구현 리뷰 — Codex 원문

> Herdr pane에서 Codex(gpt-6-astra, medium)가 2026-09-19에 쓴 적대적 구현 리뷰 원문이다. 대상은 `a76836c`(컴포넌트
> classmethod·staticmethod descriptor), `24478a5`(#89 스텁 생성), `c5812c0`(#91 입력 중인 값 보존)이다. #89 후속은
> 이 리뷰의 1·2·7~10을, #91 후속 이슈는 3~6을 다룬다. 손대지 않은 근거 문서로 두고, 본문이 가리키는 scratch 파일 중
> 재현 스크립트의 출력은 부록에 옮겼다.

---

# #89·#91 적대적 구현 리뷰

## 커밋별 판정

| 커밋 | 판정 | 근거 |
|---|---|---|
| `a76836c` | **그대로** | descriptor 보존 수정은 타당하다. 상속·재검증·property·LiveComponent 확인에서 새 결함을 찾지 못했다. |
| `24478a5` | **수정 필요** | 원래 보고된 별칭·별표 문제는 해결됐으나 ParamSpec에서 생성 자체가 실패한다. typing 클래스 import 누락과 잘못된 타입·시그니처도 남는다. |
| `c5812c0` | **수정 필요** | 단순 입력 보존은 작동하지만 commit을 요청/응답과 연결하지 않아 다른 렌더가 입력을 지울 수 있다. 표시의 해제 누락과 호출 경로별 동작 차이도 있다. |

되돌려야 한다고 판단한 커밋이나 차단 등급 발견 사항은 없다. 수정의 기본 방향은 유지할 수 있다.

검토 기준은 현재 HEAD `c5812c0`이며 대상 세 커밋을 `git show`로 읽었다. 제공된 이슈 원문과 실제 구현·예제·테스트를 대조했다. 저장소 파일은 수정하지 않았다. 아래 경로는 저장소 기준이다.

## 발견 사항 — 중요

### 1. Callable의 ParamSpec은 생성기를 중단시키고, Concatenate는 잘못된 인자 개수로 바뀐다 — #89

**근거:** `wireview/management/commands/wireview_stubs.py:658–661`.

`params`가 Ellipsis가 아니면 항상 순회 가능한 인자 목록으로 취급한다.

**실행 재현:** `review2_stubs.py`에서 `P = typing.ParamSpec('P')`를 만들고 다음을 실행했다.

- `Callable[P, int]` → `TypeError: 'typing.ParamSpec' object is not iterable`.
- 이를 실제 컴포넌트의 메서드 파라미터에 넣고 `generate_stub_content()`를 호출해도 같은 예외가 발생한다.
- `Callable[Concatenate[str, P], int]` → `Callable[[Any], int]`.

두 번째는 느슨해진 타입만이 아니다. 임의의 후속 인자를 허용하는 callable이 정확히 한 인자를 받는 callable로 바뀐다. 해당 파일 및 명령의 후속 스텁 생성도 첫 번째 예외에 영향을 받는다.

**제안:** 구체적 list/tuple 인자 목록만 순회한다. ParamSpec·Concatenate를 보존할 수 없다면 `Callable[..., R]` 또는 전체 `Any`로 내려야 한다. 직접 렌더 테스트뿐 아니라 컴포넌트→파일 생성 경로에도 회귀 테스트가 필요하다.

### 2. typing.IO·BinaryIO·TextIO를 써 놓고 import하지 않는다 — #89

**근거:** 같은 파일 `665–691`. `_class_name()`은 typing의 실제 클래스도 `from_imports`에 기록하지만, `import_lines()`는 module이 `typing`이면 무조건 건너뛴다. 별도로 관리하는 `self.typing`에는 이 이름들이 들어가지 않는다.

**실행 재현:** `async def io(self, stream: typing.IO[str]) -> None`이 있는 컴포넌트의 출력은 `stream: IO[str]`인데 import는 `Any, ClassVar`뿐이다. `BinaryIO`, `TextIO`도 렌더러에서 같은 누락을 확인했다. 생성 파일 `review2_generated.pyi`에 `ruff check --no-cache --select F,E9`를 실행하면 **F821 IO**가 나온다.

**제안:** typing 클래스 import도 하나의 이름 관리 경로로 합친다. `typing` 모듈이라고 버리지 말고 수집한 이름을 실제 import에 병합한다.

### 3. commit 표시를 해당 액션의 응답보다 먼저 온 렌더가 소비한다 — #91

**근거:** `wireview/static/wireview/wireview-boost.js:38,75`, `wireview/static/wireview/wireview.js:2615`, `wireview/consumer.py:1045–1051`. 표시는 DOM 요소의 Set이고 요청 식별자가 없다. 모든 morph가 `delete(field)`로 표시를 소비한다. render payload도 이벤트 응답과 일반 렌더를 구분할 식별자를 제공하지 않는다.

**재현 순서:**

1. 입력의 debounced input 이벤트 A가 먼저 전송된다.
2. Enter 액션 B를 전송하여 입력에 commit 표시가 붙는다.
3. 아직 도착하지 않았던 A의 응답이 먼저 morph한다. B의 답이 아닌데도 입력을 비우고 표시를 지운다.
4. B의 실제 응답은 더 이상 commit으로 인식되지 않는다.

브로드캐스트뿐 아니라 같은 연결에서 먼저 보낸 이벤트의 응답으로도 가능한 순서다. B를 보낸 뒤 새로 입력한 글자가 A 응답에 지워지는 것이 직접적인 손실이다. 응답 전 입력을 잠그거나 전송 시점의 값/편집 세대를 보관하지 않으므로 B의 올바른 응답도 전송 이후의 새 입력을 지울 수 있다.

**실행 근거:** `review2_values.mjs`는 실제 `valueGuard` 코드를 추출해 실행한다. `commit → unrelated keep`은 false, 표시 소비 뒤 `actual reply keep`은 true임을 assertion으로 확인했다. 실제 WebSocket/브라우저 경합을 재현한 것은 아니다.

**제안:** 요청 식별자·대상 컴포넌트·전송 당시 값 또는 편집 세대를 기록하고 해당 응답에서만 정리한다. 응답이 없는 일반 렌더는 pending commit을 소비하면 안 된다. 전송 뒤 추가된 입력도 구별해야 한다.

### 4. 부모 diff 없이 자식만 바뀐 응답은 부모 입력의 commit 표시를 남긴다 — #91

**근거:** `wireview/static/wireview/wireview.js:223–239`. release는 `changedChildren.length === 0`일 때만 호출한다. 자식만 morph하면 부모 폼의 입력에는 `keep()`도 호출되지 않는다.

**재현:** 부모 폼 액션이 부모 HTML을 그대로 두고 LiveComponent의 props/출력을 바꾼다. 응답이 `{id: parent, diff: null, children: {child: childDiff}}`이면 자식만 morph되고 부모 필드의 표시가 남는다. 이후 그 필드에 계속 입력하고, 부모의 무관한 렌더가 도착하면 stale commit 때문에 값이 지워진다.

**실행 근거:** `review2_values.mjs`에서 실제 `_processMessage()`의 render 분기를 추출하고 DOM/스케줄 경계만 모형으로 대체했다. 위 payload 처리 후 Set에 부모 필드가 남고 다음 `keep()`이 false인 것을 확인했다.

같은 수명 문제는 stream container를 건너뛰는 morph(`wireview-boost.js:101`)에도 있다. stream 안의 필드를 commit한 뒤 컨테이너 밖만 바뀌면 그 필드를 방문하지 않는다. `myself`로 자식을 호출하면서 조상 폼 전체를 mark하는 경우에도 실제 morph/release 범위와 표시 범위가 달라질 수 있다. 이 두 변형은 소스상 추론이며 브라우저 재현은 하지 않았다.

**제안:** morph가 우연히 방문한 필드 집합에 의존하지 말고, 액션마다 기록한 필드를 그 액션 완료 시 정리한다. 자식-only·stream·myself 경로를 포함한다.

### 5. JS().push와 직접 send에서는 Enter/submit 후 비우기가 달라진다 — #91

**근거:** `wireview/static/wireview/wireview.js:2606–2616,2954–2971,3035–3078`. commit은 선언형 `value.h` 분기에만 있고 `value.js`, JS push, 공개 `send()`에는 없다.

**재현:** 값 속성 없는 input에서 Enter를 `JS().push('add')`로 연결한다. 서버가 목록을 갱신하고 같은 빈 input을 렌더해도, 입력은 edited·동일 server value·non-committing이므로 계속 남는다. 동일 핸들러를 일반 문자열 바인딩으로 호출하면 비워진다. `wireview.send(input, 'add', {}, 'keypress')`도 같은 차이가 있다.

**실행 근거:** 모형 재현에서 unmarked 입력의 `keep()`이 true임을 확인했고, 호출 경로에서 commit이 없음을 직접 읽었다. 실제 브라우저의 JS push E2E는 실행하지 않았다.

폼 밖의 독립 버튼도 단순히 같은 컴포넌트라는 이유로 다른 입력을 commit하지 않는다. 이는 현재 규칙 자체와는 일치하지만, 서버 HTML의 빈 값만으로 입력을 비우던 앱에는 변경이다. `button form="id"`도 `.closest('form')`만 사용하므로 연결된 폼을 인식하지 못한다. 후자는 전송 직렬화에도 기존 제약이 있어 이번 커밋만의 새 결함이라고 보지는 않는다.

**제안:** 전송 공통 경로에 입력 유지/확정 의도를 전달한다. JS push와 직접 호출에서도 확정 대상 또는 reset 정책을 지정할 수 있어야 한다. 전역적으로 모든 send를 commit 처리하면 input 이벤트 보호를 다시 깨뜨리므로 명시적 구분이 필요하다.

### 6. search의 방향키가 아직 전송되지 않은 검색어를 과거 값으로 되돌릴 수 있다 — #91

**근거:** `examples/search/templates/search/live_search.html:6–16`, `examples/search/live.py:63–85`, `wireview/static/wireview/wireview.js:2615`.

**재현:** 기존 query가 `python`이고 결과가 열린 상태에서 `python new`까지 친 뒤 300ms debounce가 끝나기 전에 ArrowDown을 누른다. `navigate(direction)`은 query를 갱신하지 않고 선택 인덱스만 바꾼다. 하지만 keydown을 commit으로 취급하므로 응답의 `value="python"`이 포커스된 입력을 덮는다. 이후 search debounce는 이미 짧아진 값을 읽을 수 있다.

**실행 근거:** production guard로 `value='python new', defaultValue='python', focused=true, committing=true` 및 다음 서버 값 `python`을 넣으면 보호하지 않는 것을 확인했다. 실제 DB/브라우저 예제는 실행하지 않았다.

이전 구현도 지울 수 있던 흐름이지만, #91의 새 규칙으로도 보존하지 못하는 실제 예제 반례다. `input` 이외의 이벤트를 모두 확정으로 해석하는 것이 원인이다.

**제안:** 방향키·Escape 같은 보조 액션과 입력 확정 액션을 구별한다. debounce 대기 중 방향키를 누르는 테스트를 추가한다.

## 발견 사항 — 경미

### 7. 이름 충돌 처리가 일부 경로에만 적용돼 다른 타입으로 바인딩될 수 있다 — #89

**근거:** `wireview/management/commands/wireview_stubs.py:626–629,665–680,700–706`.

**실행 재현:**

- 외부 클래스 이름이 `int`인 경우 외부 타입과 진짜 builtin int가 모두 `int`로 출력되고 `from review_external import int`가 추가된다. builtin annotation도 외부 클래스를 가리키게 된다.
- 같은 모듈의 `Outer.Widget`를 렌더하면서 최상위 컴포넌트 이름에 `Widget`이 있으면 `Widget`을 반환한다. 같은 모듈 분기가 중첩 클래스 검사보다 앞에 있어 서로 다른 타입을 혼동한다.
- `Any`라는 컴포넌트와 `typing.Any`도 공통 이름 예약 없이 충돌 가능하다. 렌더러에 local_names={'Any'}를 주어도 `from typing import Any`를 그대로 내는 것을 확인했다.

반대로 서로 다른 외부 모듈의 일반 동명 클래스 두 개는 두 번째가 Any로 내려가 정상적으로 충돌을 피한다.

**제안:** builtin·typing·베이스 클래스·컴포넌트·외부 import 이름을 한 곳에서 예약하고 alias를 사용한다. 같은 모듈 타입도 이름 문자열만 비교하지 말고 실제 선언 대상인지 확인한다.

### 8. 파라미터를 위치가 아닌 self/cls라는 이름으로 지워 잘못된 시그니처를 만든다 — #89

**근거:** `wireview/management/commands/wireview_stubs.py:798–800`, `wireview/management/commands/wireview_lsp.py:232–235`.

**실행 재현:** 실제 컴포넌트의 `async def accepts(self, cls: int)`가 `accepts(self)`로 생성된다. staticmethod의 `def utility(self: int)`도 `utility()`가 된다. 전자는 새 formatter의 cls 제거 때문이고, 후자는 기존 공용 extract_parameters의 self 제거를 새 staticmethod 지원이 그대로 사용해서다.

**제안:** raw descriptor에서 얻은 시그니처와 binding을 기준으로 실제 receiver 한 개만 제거한다. staticmethod에서는 이름에 관계없이 파라미터를 모두 남긴다. `def f(self, /, **kwargs)`처럼 receiver만 positional-only인 경우도 `/`가 사라지므로 함께 검사한다.

### 9. 기본값·동적 필드명의 기존 문법/미정의 이름 문제는 남아 있다 — #89의 미완료 범위

**근거:** `wireview/management/commands/wireview_stubs.py:745,843,857–866`.

**실행 재현:**

- `n: float = float('inf')`가 metadata에 `'default': inf`로 출력된다. 실제 ruff 결과는 **F821 inf**다. nan도 같은 repr 경로다.
- Pydantic `create_model(..., **{'class': (int, 1)})`는 생성 가능하지만 스텁은 `class: int`라서 `ast.parse()`가 SyntaxError를 낸다.

둘 다 대상 커밋 이전부터 있던 코드의 결함이다. 새 변경이 만들었다고 보지는 않지만, “모든 이름을 바인딩하는 유효한 스텁”이라는 완료 주장에는 예외다. Enum 기본값도 그 값의 repr을 그대로 출력하므로 일반 scalar 이외 값에 대한 안전성이 보장되지 않는다.

**제안:** metadata 기본값은 안전한 literal만 재귀 직렬화하고 나머지는 ellipsis로 내린다. 필드명은 identifier/keyword를 검사하여 직접 선언 불가능한 필드는 metadata로만 남기는 등의 정책을 정한다.

### 10. eval 폴백이 성공했던 애너테이션도 다시 실행한다 — #89

**근거:** `wireview/management/commands/wireview_stubs.py:579–595`.

**실행 재현:** 애너테이션을 `{'a': 'side_effect()', 'b': 'Unbound'}`로 둔 함수에 `_resolved_signature()`를 한 번 호출하면 side_effect 실행 횟수가 **2**다. 전체 eval이 중간 실패한 후 모든 애너테이션을 다시 평가하기 때문이다.

평가 대상은 이미 import한 애플리케이션 코드라서 별도의 외부 공격 입력 경계라고 주장하지는 않는다. 다만 스텁 생성은 애너테이션의 함수 호출을 실행할 수 있고 부작용을 중복시킨다. 동적 반환이라면 먼저 해석한 타입과 최종 타입도 달라질 수 있다.

**제안:** 처음부터 애너테이션별로 한 번만 해석하고, 실패한 것만 Any로 처리한다. 해석 범위와 실행 부작용을 문서화한다.

## 옳다고 확인한 것

- Python 테스트를 다음 조건으로 실행해 **44 passed**를 확인했다: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/test_component_descriptors.py tests/test_stubs_valid.py tests/test_security.py -p no:cacheprovider -o 'addopts=--nomigrations --strict-markers' -q`. 캐시 쓰기를 막기 위해 기본 addopts의 `--ff`를 제외했다. 최초 시도는 cacheprovider를 끈 상태의 `--ff` 때문에 사용법 오류가 났고, 위 명령으로 정상 실행했다.
- `node --test tests/js/values.test.mjs`: **5 passed**. 이 테스트는 값 보존 진리표와 입력 종류 분류를 증명한다. commit 생성·수명·DOM morph는 증명하지 않는다.
- descriptor 재현에서 상속한 classmethod는 Child에 바인딩되고, 이미 validate_call한 staticmethod는 문자열 `2`를 검증해 4를 반환했다. property는 property로 남았고 다중 상속 mixin classmethod와 LiveComponent classmethod도 정상 호출됐다.
- 사용자 async classmethod는 `_is_user_defined_method`에서 노출 대상으로 판정된다. `new`, LiveComponent의 `update`는 차단된다. 이는 “사용자가 정의한 공개 callable”이라는 현재 보안 규칙과 일치한다. classmethod라는 이유만으로 비공개라고 기대하면 안 되며, 내부 helper에는 `_`가 필요하다. 이 커밋이 새로 프레임워크 메서드를 노출했다는 근거는 없다.
- 원래 #89의 `t.Any`, `*args`, `**kwargs`, 일반적인 positional-only/keyword-only, descriptor 출력은 기존 테스트가 확인한다. Annotated[int, ...]는 int, Self와 enum Literal은 Any, 문자열 재귀 요소는 list[Any]로 완화되는 것을 실행 확인했다. 일반 collections.abc Callable/Awaitable와 외부 클래스 import도 테스트 범위에서 정상이다.
- todo 새 항목 입력은 Enter의 직접 handler 바인딩이므로 새 commit 경로를 탄다. chat은 Enter와 폼 밖 Send 버튼 모두 서버가 `push_js(JS().set_value(..., ''))`로 직접 비운다(`examples/chat/live.py:76–96`). 따라서 chat의 Send 버튼이 이번 변경만으로 비워지지 않는다고 보고해서는 안 된다.
- search의 Clear도 `set_value`와 focus를 명시한다. notifications는 입력 상태를 서버 필드에 저장하는 바인딩이고, quiz는 시작 후 입력 단계가 교체된다. 이 경로들에서 별도의 확정된 결함은 찾지 못했다.
- idiomorph `syncInputValue`는 input과 textarea 모두 value 변경 전에 `beforeAttributeUpdated('value', ...)`를 확인한다(`node_modules/idiomorph/dist/idiomorph.esm.js:725–778`). 따라서 textarea라서 callback이 전혀 통하지 않는다는 의심은 맞지 않는다. 다만 실제 DOM 부작용까지 실행 검증한 것은 아니다.
- hook `updated()`는 morph 뒤, FeedbackManager.updated()는 그 뒤 실행된다(`wireview.js:954–973`). 따라서 hook이 보는 값은 guard 적용 후의 DOM 값이다. FeedbackManager는 touched 상태와 오류 표시 클래스만 관리하며 값을 복원하거나 commit을 해제하지 않는다.

## 확인하지 못한 것과 검증의 한계

- 브라우저 E2E는 실행하지 않았다. 별도 브라우저 세션을 만들지 않고 요청 범위 내 Python·Node·소스 검토만 수행했다. `review2_values.mjs`는 production guard와 실제 render 분기를 추출하지만 DOM 요소·프레임 스케줄·컴포넌트 morph를 모형으로 대체한다. 실제 idiomorph 통합 테스트라고 해석하면 안 된다.
- 새 E2E 여섯 개는 소스로 검토했다. #91 완료 조건의 “두 debounced 입력을 서로 다른 시점에 타이핑”을 그대로 실행하는 테스트는 여기에 없다. 첫 테스트는 무관한 click, 두 번째는 한 입력의 debounce 응답이다. 앞선 응답과 뒤의 Enter가 겹치는 순서, child-only, JS push, textarea도 다루지 않는다.
- `defaultValue` 동기화에 따른 textarea 자식 텍스트 morph, 원래 없던 `value` 속성이 빈 속성으로 생기는 현상, number의 invalid/badInput, range/date 정규화, IME 조합·선택 영역은 실제 브라우저로 확인하지 않았다. 특히 `value !== defaultValue`는 편집 이력이 아니라 현재 두 문자열의 비교이고, `event.isComposing` 검사는 새 경로에 없다. 이를 곧바로 확정된 브라우저 버그로 판정하지 않았다.
- checkbox/radio/select는 새 guard 대상에서 제외되므로 보존 정책이 확장되지 않는다. text 입력 문제의 한정된 수정으로는 설명 가능하지만, 모든 폼 상태 보존을 보장하지 않는다.
- stream의 일반 부모 morph는 container를 건너뛰어 기존 행을 유지한다. 그러나 동일 id stream insert는 `replaceWith`, reset은 `innerHTML=''`를 사용한다(`wireview.js:490,514`). 이런 스트림 갱신은 guard를 우회하므로 입력값 보존 대상이 아니다. 이는 기존 스트림 동작이며 브라우저 재현은 하지 않았다.
- boost 내비게이션도 같은 `morph(document.body, newBody)`를 쓴다(`wireview-boost.js:218`). 같은 id/구조로 매칭되는 새 페이지 필드가 이전 draft를 이어받을 가능성이 있다. 페이지 간 상태 보존 정책 및 실제 DOM 매칭을 검증하지 않았으므로 추가 확인 대상으로 남긴다.
- 비라이브 상태에서 즉시 실행한 선언형 서버 바인딩은 `isLive` 검사에서 차단되므로 일반적인 경우에는 commit하지 않는다(`wireview.js:2585,2663`). 단, debounce 대기 뒤에는 live 여부를 다시 검사하지 않는다. 서버에 없는 handler 이름도 클라이언트에서 검증하지 않아 commit 후 서버 오류로 정상 완료되지 않을 수 있다. 연결 종료·에러 뒤 표시 수명은 별도 E2E로 검증하지 않았다.
- 모든 Python 버전·Pydantic 버전의 typing 동작, 전체 스텁에 대한 pyright/mypy 의미 검사는 실행하지 않았다. AST 이름 검사만으로는 이름 충돌, 잘못된 Callable arity, 삭제된 인자 같은 의미 오류를 잡을 수 없다.

## 재현 산출물

모든 파일은 이 리뷰와 같은 scratchpad 디렉터리에 있다.

- `review2_stubs.py`, `review2_stubs.log`: typing·실제 컴포넌트 생성·descriptor·충돌·eval 재현.
- `review2_generated.pyi`: IO 및 inf F821을 포함한 실제 생성물.
- `review2_values.mjs`, `review2_values.log`: production guard 및 render 분기 기반의 결정론적 모형 재현.

저장소 루트에서 Python 재현은 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python <scratchpad>/review2_stubs.py`, JS 재현은 `node <scratchpad>/review2_values.mjs`로 실행한다. 스크립트가 만드는 파일도 같은 scratchpad 안으로 제한했다.

---

## 부록 A. review2_stubs.log

```text
TYPE typing.Callable[~P, int] => TypeError 'typing.ParamSpec' object is not iterable
TYPE typing.Callable[typing.Concatenate[str, ~P], int] => Callable[[Any], int] ['from typing import Any', 'from collections.abc import Callable']
TYPE typing.IO[str] => IO[str] []
TYPE <class 'typing.BinaryIO'> => BinaryIO []
TYPE <class 'typing.TextIO'> => TextIO []
TYPE typing.Annotated[int, 'x'] => int []
TYPE typing.Self => Any ['from typing import Any']
"""Auto-generated type stubs for wireview components.

DO NOT EDIT - regenerate with: python manage.py wireview_stubs
"""

# Generated: 2026-09-19T11:08:55.819763+00:00

from typing import Any, ClassVar
from wireview.component import Component

class Probe(Component):
    """
    Base class for wireview components.
    
    Components are Pydantic models that can be rendered to HTML and
    updated in real-time via WebSocket.
    """

    n: float

    async def accepts(self) -> None: ...
    async def io(self, stream: IO[str]) -> None: ...
    @staticmethod
    def utility() -> int: ...

    # Wireview metadata for IDE support
    __wireview_attrs__: ClassVar[dict[str, dict[str, Any]]] = {'n': {'type': 'float', 'required': False, 'default': inf}}
    __wireview_handlers__: ClassVar[list[str]] = ['accepts', 'io']

FULL GENERATION: TypeError 'typing.ParamSpec' object is not iterable
COLLISION: int int ['from review_external import int']
LOCAL ANY: Any ['from typing import Any']
EVAL: (a: int, b: 'Unbound') evaluation count: 2
KEYWORD FIELD: invalid syntax (<unknown>, line 19)
DESCRIPTORS: Child 4 Child True Live
EXPOSURE: True False False
TWO MODULES: Same Any ['from typing import Any', 'from mod_a import Same']
NESTED SAME MODULE: Widget
ENUM LITERAL: Any
RECURSIVE: list[Any]
```

## 부록 B. review2_values.log

```text
RACE: unrelated render takes draft; actual reply keeps next draft
CHILD-ONLY: completed parent action leaves mark; next unrelated parent morph takes draft
SEARCH: ArrowDown during 300ms debounce authorizes reverting python new to python
UNMARKED: JS.push / direct send cannot clear a same-server-value input through morph
FORM-ATTRIBUTE: external button does not mark its associated form
```
