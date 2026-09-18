# djust Rust VDOM 검토 — Codex 원문

> Herdr pane에서 Codex(gpt-6-astra, medium)가 2026-09-18에 쓴 조사 원문이다. 과제는 `docs/design/djust-vdom-review-2026-09-18.md`가
> 요약·판정하고, 이 파일은 손대지 않은 근거 문서로 둔다. 경로의 `D/`는 djust 클론(커밋 ed7e92a), `W/`는 이 저장소다.


검토 기준: 2026-09-18 로컬 소스. djust `ed7e92a`, django-wireview `a87f435`. 이하 `D/`는 `./djust/`, `W/`는 `/Users/allieus/Apps/itda-work/django-wireview/`를 뜻한다. 경로 뒤 함수·구조체 이름이 근거 위치다. **[확인]**은 소스·테스트·저장소 문서에서 확인한 사실, **[판단]**은 그에 근거한 설계 제안 또는 복잡도 추론이다. 문서의 과거 측정값을 이번 조사에서 재측정한 값으로 취급하지 않는다.

두 저장소의 코드는 수정하지 않았다. Rust/Python/브라우저 전체 테스트와 성능 벤치는 실행하지 않았다. §9의 작은 JSON 예시만 wireview의 실제 `Rendered`/`Comprehension` 클래스로 계산하고 UTF-8 바이트 수를 측정했다. djust 예시는 확인한 wire 구조를 수작업 구성한 것으로 Rust 실행 결과와 구별한다.

핵심 판정은 **wireview의 Rendered 모델을 유지하면서 키 기반 comprehension, 상태 수명 설계, 독립적인 클라이언트 검증을 차용**하는 것이다. Rust VDOM과 opcode 프로토콜 전체를 옮길 근거는 부족하다. 특히 “djust는 매 이벤트마다 HTML을 전부 파싱하고 msgpack 패치를 보낸다”는 설명은 현재 소스와 다르다. 기본 이벤트 전송은 JSON이며, 템플릿 의존성·fragment·text fast path·loop cache가 파싱과 diff를 우회하기도 한다.

## 1. 파이프라인

### 실제 이벤트 경로

[확인] `D/python/djust/websocket.py:LiveViewConsumer.handle_event`는 과거의 거대한 처리 본체가 아니라 `ViewRuntime`으로 넘기는 shim이다. 실제 `event` 수신은 `receive` → `_dispatch_runtime_owned` → `D/python/djust/runtime.py:ViewRuntime.dispatch_message` → `dispatch_event` → `_dispatch_event_inner` / `_dispatch_event_render` → `_render_and_send`로 이어진다. 인증·이벤트 허용 검사, render lock, 이벤트 핸들러 호출, changed-key 계산 및 skip-render 판단을 거친다. 모든 이벤트가 반드시 렌더하는 것은 아니다. 다음 표는 기본 HTML renderer에서 렌더가 필요한 경우다. actor/native renderer 등 선택 경로를 기본 경로와 혼합하지 않았다.

| 단계 | 함수·위치 | 입력 → 출력 |
|---|---|---|
| Python context 동기화 | `D/python/djust/mixins/template.py:TemplateMixin.render_with_diff`, `mixins/rust_bridge.py:RustBridgeMixin._initialize_rust_view`, `_sync_state_to_rust` | Django 템플릿 소스·디렉터리, context/state → `RustLiveView`의 state, changed keys, safe keys, raw Python sidecar |
| renderer 진입 | `D/python/djust/renderers/html.py:HtmlRenderer.render_with_diff` | 준비된 `view._rust_view` → `_rust_view.render_with_diff()` 호출 |
| 템플릿 컴파일/렌더 | `D/crates/djust_live/src/lib.rs:RustLiveViewBackend::render_with_diff`, `cached_template`; `djust_templates/src/lib.rs:Template::new`, `render_with_loader_collecting`, `render_with_loader_partial` | `template_source: String`, `Context`, loader → HTML `String`, fragment `Vec<String>`, 변경 fragment 인덱스 `Vec<usize>` |
| HTML → VDOM | `D/crates/djust_vdom/src/parser.rs:parse_html`, `parse_html_continue`, `handle_to_vnode` | `&str` → `Result<VNode>`; html5ever `parse_document` → `RcDom` → 루트 선택·정규화된 `VNode` |
| ignore 보존 | `D/crates/djust_vdom/src/lib.rs:splice_ignore_subtrees` | `&VNode`, `&mut VNode` → 이전 ignore 자식을 새 트리에 복사 |
| diff | `D/crates/djust_vdom/src/lib.rs:diff` → `diff.rs:diff_nodes` | `&VNode`, `&VNode` → `Vec<Patch>` |
| 다음 렌더의 ID 정합성 | `D/crates/djust_vdom/src/diff.rs:sync_ids` | old + mutable new → 생존 노드의 old `djust_id`를 new에 계승; **diff 후** 수행 |
| 패치 직렬화·baseline 저장 | `RustLiveViewBackend::render_with_diff` | `Vec<Patch>` → `serde_json::to_string`; Python 반환형 `(String, Option<String>, u64)` = hydrated HTML, patches JSON, Rust version. `last_vdom`, `last_html`, caches 갱신 |
| WS 메시지 | `D/python/djust/runtime.py:ViewRuntime._render_and_send`, `WSConsumerTransport.send` | patches JSON을 list로 읽음 → `{type:"patch", patches:[...], version:..., event_name:..., source:"event"}` → consumer `send_json` |
| 브라우저 | `D/python/djust/static/djust/src/03-websocket.js`, `12-vdom-patch.js:applyPatches`, `_applyPatchesInnerRaw`, `applySinglePatch` | JSON 객체/패치 배열 → 실제 DOM 수정, 성공 여부 반환; 실패 시 full HTML 복구 경로 |

질문에서 가리킨 `js/`에는 현재 markdown-editor가 있고, VDOM 클라이언트의 정본은 **`python/djust/static/djust/src/12-vdom-patch.js`**다. `patch.rs`도 패치 enum 선언 파일이 아니라 Rust 테스트용 적용기다. enum은 `djust_vdom/src/lib.rs:Patch`에 있다.

### 템플릿 엔진의 정체

[확인] `crates/djust_templates`는 Django 템플릿 문법을 Rust lexer/parser/renderer/filter로 구현한 **별도 엔진**이다. Django가 렌더한 HTML만 받아 diff하는 라이브러리라는 설명은 불완전하다. `Template::new`가 Rust AST를 만들고 loader가 include/extends를 처리한다. Django context 준비, 사용자 정의 tag/filter, Python 객체 속성 접근 등을 위한 Python bridge도 존재한다. `HtmlRenderer`의 “Django template + Rust VDOM”이라는 모듈 설명만 보고 Django의 Python renderer가 기본 실행기라고 해석하면 틀린다. 기본 호출 본체는 `RustLiveView.render_with_diff`다.

README의 Django 자체 template suite 98.57% 수치도 완전 동등성을 뜻하지 않는다. Rust 구현은 Django 호환성을 지속 관리하는 독립 표면이다. wireview는 `W/wireview/template_engine.py:MarkedVariableNode`, `ComprehensionNode`, `ConditionalNode`가 기존 Django Node를 감싸 원래 `render`를 호출한다. 또한 wireview는 컴파일된 Node에 marker를 넣은 뒤 **렌더된 marked HTML을 `Rendered.from_marked_html`로 분해**한다. 처음부터 DOM 트리를 컴파일하는 구조는 아니다.

### 항상 전체 parse/diff가 실행되는가

[확인] 아니다. `RustLiveViewBackend::render_with_diff`에는 다음 우회가 있다.

- `changed_keys`와 `node_html_cache`를 사용한 `render_with_loader_partial`: 의존성이 바뀌지 않은 AST fragment의 HTML 재사용.
- `fragment_text_map`/`text_node_index` 기반 text patch 생성: 안전한 텍스트 변경이면 html5ever와 일반 트리 diff를 건너뜀.
- `try_text_only_vdom_update_inplace` 등 HTML text-region 경로: 구조 변경이 없음을 확인할 수 있는 경우 기존 VDOM 갱신.
- loop render/parse cache: §5의 조건을 만족하면 반복 항목 재렌더·재파싱을 줄임.

따라서 “Rust가 빠르게 전체 트리를 diff한다”뿐 아니라 **이미 알고 있는 템플릿 정보를 써서 그 일을 안 하는 것**이 djust 성능 설계의 일부다.

## 2. diff 알고리즘

### 노드와 자식 매칭

[확인] `D/crates/djust_vdom/src/diff.rs`의 규칙은 다음과 같다.

1. `diff_node_into`: old/new `tag`가 다르면 `Replace` 하나로 새 subtree를 보낸다. 같은 tag의 `#text` 또는 `#comment`는 내용이 다를 때 `SetText`다. 같은 element tag는 속성을 먼저 diff하고 자식으로 내려간다. `id` HTML 속성은 그 자체로 리스트 key가 아니다.
2. parser는 비어 있지 않은 `dj-key` 또는 `data-key`를 `VNode.key`로 잡는다. `djust_id`와 key는 별개다. key는 앱의 항목 정체성, `djust_id`는 서버가 발급한 DOM 주소다.
3. `find_top_level_boundaries`: `<!--dj-if id="X"-->...<!--/dj-if-->` 구간을 깊이 계산으로 찾는다. 같은 boundary ID끼리 body를 재귀 비교하고 old-only는 `RemoveSubtree`, new-only는 `InsertSubtree`다. 경계 밖 자식은 marker span을 제외한 **상대 순서**로 비교한다.
4. `reconcile_siblings`: new 자식 중 하나라도 key가 있으면 `reconcile_keyed`, 전혀 없으면 `reconcile_indexed`. virtual parent는 별도 gate를 먼저 탄다.
5. `reconcile_indexed`: i번째 old/new를 짝짓는다. comment 대 non-comment는 remove+insert; 그 외는 재귀 비교하므로 tag 차이는 Replace다. 남는 old는 뒤에서부터 remove, 남는 new는 앞에서부터 insert. 키 없는 앞쪽 삽입을 항목 이동으로 알아내지 못한다.
6. `reconcile_keyed`: 양쪽 어느 쪽이든 중복된 key를 `ambiguous_keys`로 찾고 그 key들은 effectively-unkeyed 위치 그룹으로 강등한다(DJE-051). raw keyed/unkeyed 혼합도 경고한다(DJE-050). 유효한 key는 map으로 매칭하고, 비유효/무키 자식은 별도 그룹에서 상대 위치로 매칭한다.

### LIS 사용

[확인] 완전히 keyed인 자식 목록에서는 **new 순서로 생존 항목을 나열하고 그 old 위치의 수열**을 만든다. `lis.rs:longest_increasing_subsequence`는 patience sorting 방식으로 `tails`, 원래 인덱스, predecessor를 유지하고 이진 탐색(`partition_point`)으로 엄격 증가 LIS를 구한다. 반환값은 값 자체가 아니라 입력 수열 내 인덱스다.

예: old `[a,b,c,d]`, new `[c,a,b,d]` → old 위치 `[2,0,1,3]` → `[0,1,3]`에 해당하는 a,b,d는 상대 순서를 유지한다. c만 `MoveChild`가 필요하다. 생존 항목 m개에서 최소 이동 수는 m−LIS 길이이며, 이것은 순수 keyed 재정렬의 이동 개수에 대한 성질이지 전체 DOM 편집거리의 전역 최적성 보장은 아니다.

혼합 목록에서는 LIS 생략 최적화를 **끄고**, 절대 위치가 변한 keyed 항목을 이동시킨다. effectively-unkeyed 생존 노드도 `djust_id`가 있고 위치가 바뀌면 이동시킨다. 그렇지 않으면 key 없는 형제가 keyed 이동 사이에 잘못 남을 수 있다.

### 속성과 텍스트

[확인] `diff_attrs`는 key를 정렬해 결정적인 출력 순서를 만든다. 추가/변경은 `SetAttr`, 제거는 `RemoveAttr`. `dj-id`는 일반 속성 diff에서 제외하고, old의 `dj-*` 속성은 제거 패치를 내지 않는다. 후자는 event binding 보존 의도이지만 실제 조건은 `key.starts_with("dj-")` 전체다. 일반적인 “모든 새 속성 집합에 정확히 수렴하는 diff”와는 다르다.

텍스트는 문자열 편집 delta가 아니라 새 text node 값 전체를 `SetText.text`로 보낸다. element의 `dj-update="ignore"`는 wrapper 속성 비교 후 내부 재귀를 중단한다. `data-djust-replace`가 양쪽에 있으면 모든 자식을 remove+insert한다.

### virtual keyed ops

[확인] `tests/virtual_keyed_ops_2017.rs`와 `diff.rs:reconcile_virtual_keyed`의 virtual은 “가상 DOM 일반”이 아니라 **`[dj-virtual]` windowed list**를 뜻한다. 서버의 100번째 항목과 DOM의 100번째 자식은 같지 않다. 클라이언트는 보이지 않는 항목도 detached item pool에 보관하므로 `key`/`before_key`로 pool을 수정한다.

- 삭제는 `VirtualRemove`, 생존 항목 내용 변경은 row-root 상대 patch 배열을 가진 `VirtualUpdate`다. 화면 밖 row에도 변경이 적용된다.
- 구조 이동/삽입은 new 순서의 **역순**으로 방출한다. 다음 항목을 `before_key`로 삼으므로 그 anchor가 먼저 존재해야 한다.
- LIS를 여기에도 써서 append 한 번에 기존 10,000항목의 무의미한 move 10,000개를 보내지 않는다.
- 빈 new 목록도 virtual 경로여야 전체 pool 삭제가 된다. unkeyed/중복 key는 경고(DJE-052) 후 일반 index 경로로 fallback하며, 소스 자체가 windowed DOM에서 부정확할 수 있음을 명시한다.
- Python 설정 기본값은 ON이고 startup에서 Rust setter로 전달한다. Rust의 process-global `AtomicBool` 자체는 false다. 문서의 1.1.0/1.1.1 도입 버전 주석은 서로 다르므로 현재 gate를 근거로 판단했다.

### 복잡도

[판단: 소스 루프와 자료구조 분석] LIS만 보면 시간 O(k log k), 추가 공간 O(k). 경계 없는 보통 트리에서 hash lookup을 평균 O(1)로 두면 노드 순회 O(N), keyed 형제별 O(k log k), 속성 정렬 합계 O(Σ a log a), 문자열 비교/복사·삽입 subtree 직렬화 비용이 추가된다. patch path 복사도 깊이에 비례한다.

**전체 diff의 최악을 O(N log N)이라고 단정할 수 없다.** `non_boundary_count_before`가 각 boundary마다 prefix를 다시 세므로 같은 부모에 boundary가 O(N)개면 O(N²)가 된다. 중첩 marker body의 반복 스캔, 깊은 트리에서 path 누적 복사도 제곱 비용을 만들 수 있다. 문자 총량과 속성 수를 별도로 셀 필요가 있고, hash map에는 평균 시간 가정이 있다. `patch.rs`/브라우저 적용의 반복 ID 탐색·배열 이동 비용 역시 LIS 복잡도 밖이다. 따라서 bounded-depth·작은 attrs·정상 keyed list에서의 비용과 임의 입력의 최악을 구분해야 한다.

## 3. 패치 opcode와 주소·순서

[확인] 정본은 `D/crates/djust_vdom/src/lib.rs:Patch`의 `#[serde(tag = "type")]` enum이다. 14종이다. 아래 `path`는 `Vec<usize>`, `d?` 등은 `Option<String>`이며 None이면 JSON 필드가 생략된다. 인덱스는 `usize`, 문자열은 `String`, node는 `VNode`다.

| type | 전체 payload 필드 (`type` 제외) |
|---|---|
| `Replace` | `path, d?, node` |
| `SetText` | `path, d?, text` |
| `SetAttr` | `path, d?, key, value` |
| `RemoveAttr` | `path, d?, key` |
| `InsertChild` | `path, d?, index, node, ref_d?` |
| `RemoveChild` | `path, d?, index, child_d?` |
| `MoveChild` | `path, d?, from, to, child_d?` |
| `VirtualUpdate` | `path, d?, key, patches: Vec<Patch>` |
| `VirtualInsert` | `path, d?, key, node, before_key?` |
| `VirtualMove` | `path, d?, key, before_key?` |
| `VirtualRemove` | `path, d?, key` |
| `RemoveSubtree` | `id` |
| `InsertSubtree` | `id, path, d?, index, html` |
| `MoveSubtree` | `id, path, d?, index` |

`VNode`의 wire 필드는 `tag`, `attrs`, `children`, `text`, `key`, 선택적 `djust_id`다. `text`/`key`의 None은 JSON null로 남는다. `cached_html`은 `#[serde(skip)]`로 wire에서 제외된다. `attrs`는 정렬 직렬화한다.

### 주소 지정

[확인] 기본은 루트에서 significant children을 따라가는 `path`; 가능한 element에는 `d`로 `dj-id`를 직접 찾는다. child 연산의 `d`는 **부모**이고, `child_d`는 삭제/이동할 old 자식, `ref_d`는 삽입 anchor다. 모든 optional이 항상 생성되는 것은 아니며 `push_insert_child`의 일반 삽입은 `ref_d: None`이다. boundary `id`는 HTML `id`나 `dj-id`와 별도 namespace다. virtual `key`는 `VNode.key`다.

`parser.rs:handle_to_vnode`는 element에 base62 `dj-id`를 발급한다. text/comment에는 ID가 없어 path와 significant-child 정의가 특히 중요하다. whitespace-only text는 보통 제외하되 pre/code/textarea/script/style을 보존하고, 일반 주석은 제외하되 dj-if 주석은 남긴다. 클라이언트 `12-vdom-patch.js:getSignificantChildren`과 일치해야 한다.

`parse_html`는 thread-local counter를 reset하고 `parse_html_continue`는 이어 쓴다. 후속 render에서 old tree의 최대 ID보다 counter가 크도록 보정해 thread 이동/deserialize 이후에도 새 노드 ID가 충돌하지 않게 한다(`max_djust_id_in`, `ensure_id_counter_at_least`). 사용자가 넣은 `dj-id`는 유효한 base62여도 서버 발급값을 덮어쓰지 못한다. malformed 값은 trace를 남긴다(`tests/test_dj_id_validation_1253.rs`).

`sync_ids(old,new)`는 같은 tag의 생존 노드 ID를 계승하며 dj-if body/key/비경계 상대 위치를 고려한다. 이 처리가 없으면 1회 패치가 old ID로 성공하더라도 서버가 저장한 new에는 다른 ID가 남아서 **2회차부터 클라이언트에 없는 ID**를 참조한다. `test_sync_ids_dj_if_1408.rs`, `test_dj_update_ignore_dj_if_sync_ids_1417.rs`가 이 계열의 회귀를 다룬다. 생성 patch의 `d`/`child_d`/`ref_d`는 원칙적으로 old tree에 존재해야 한다. 삽입하는 새 node/HTML 안의 ID는 예외다.

[주의: 확인한 구현 한계] `sync_children`의 keyed map은 duplicate key를 마지막 값으로 덮어쓰며, `reconcile_keyed`의 ambiguous-key 강등과 동일한 로직을 공유하지 않는다. 또한 `splice_ignore_subtrees`는 일반 children을 **위치로** 걷는다. 이 둘을 “모든 중복키·재정렬·ignore 조합에 안전한 공통 matcher”라고 가져오면 안 된다. 이번 조사에서 해당 조합의 실패를 실행 재현한 것은 아니다.

### 순서는 단순 FIFO가 아니다

[확인] `torture_patch_batch_ordering_1420.rs`는 targeting handle 정합성과 survivor 변경/삭제 순서 사례를 검증한다. 그러나 주석의 “client applies in order”만 읽고 방출 순서를 그대로 실행한다고 설명하면 현재 JS와 어긋난다.

`12-vdom-patch.js:_sortPatches`는 RemoveSubtree(-2) → RemoveChild(0, 같은 부모에서는 descending index) → MoveChild(1) → InsertChild(2) → MoveSubtree/InsertSubtree(3, target index 오름차순) → 나머지(4)로 정렬한다. virtual 연산은 모두 같은 phase에서 **안정 정렬로 방출 순서를 유지**한다. `applyPatches`는 10개 이하 직접 적용과 큰 batch의 부모별 grouping/경계 span 지연 적용을 나눈다. `VirtualUpdate`도 phase 4라 생성 순서와 recursive row 적용을 함께 고려해야 한다.

`D/crates/djust_vdom/src/patch.rs:apply_patches`는 테스트용이며 remove → insert → move, 이후 속성/text를 적용하는 별도 모형이다. `RemoveSubtree`/`InsertSubtree`를 자체 처리하지도 않는다. Rust 적용기가 성공했다는 이유만으로 브라우저의 실제 순서·주소 해석이 동일하다고 할 수 없다.

## 4. wire 형식과 압축

### 기본 전송은 JSON

[확인] 현재 이벤트 경로의 `WSConsumerTransport.send`는 consumer `send_json`을 직접 호출한다. `RustLiveViewBackend::render_with_diff`도 JSON 문자열을 반환한다. 예를 들어 이벤트 응답의 대표 형태는 다음과 같다(부가 필드는 상황별 선택).

```json
{"type":"patch","patches":[{"type":"SetText","path":[0],"text":"hello"}],"version":2,"event_name":"increment","source":"event"}
```

`LiveViewConsumer._send_update`에는 `use_binary`이면 Python `msgpack.packb(patches)`를 보내는 분기가 있지만 `connect`에서 `self.use_binary = False`이며 기본 이벤트 runtime 경로와도 별개다. 이 분기는 envelope 전체가 아니라 patch list만 보내므로 “JSON과 동등한 완성된 binary transport”로 간주할 수 없다.

### MessagePack은 세 가지를 구별해야 한다

1. **선택적 binary diff API:** `RustLiveViewBackend::render_binary_diff` → `(HTML String, Option<PyBytes>, version)`; 현재는 `rmp_serde::to_vec_named(&patches)`를 사용해 named map으로 쓴다. 이 API가 기본 WS 경로에서 호출되는 것은 아니다.
2. **상태 저장:** `serialize_msgpack`/`deserialize_msgpack`는 `SerializableViewState`를 positional `rmp_serde::to_vec`로 저장한다. 여기에 state와 last VDOM이 들어간다.
3. **Python `_send_update` binary 분기:** 위의 `msgpack.packb` 경로다.

`Patch` enum 주석에 “binary API가 positional이라 깨진다”는 옛 설명이 남아 있지만 실제 producer는 #2130에서 `to_vec_named`로 고쳐졌다. `tests/wire_protocol_snapshot.rs:msgpack_named_round_trips_every_patch_shape`와 `python/djust/tests/test_binary_diff_msgpack_2130.py`가 그 수정의 근거다. 현재 producer를 여전히 깨진 것으로 보고하면 안 된다.

### snapshot이 고정하는 계약

[확인] `tests/wire_protocol_snapshot.rs`는 type 이름, field 이름·순서, optional 생략, nested VNode, cached_html 제외, virtual recursive payload를 고정한다. 예:

```json
{"type":"SetText","path":[0,1],"d":"3z","text":"hello"}
{"tag":"div","attrs":{},"children":[],"text":null,"key":null}
```

MessagePack 테스트는 모든 Patch shape의 named-map round trip과 positional 형식의 실패 witness를 함께 둔다. 내부 optional `d`를 positional array에서 생략하면 뒤 필드가 당겨지므로 `#[serde(default)]`만으로 해결되지 않는다. VNode의 `djust_id`는 마지막 serialized field라 default+생략이 가능하며 **배열 너비 6, 마지막 필드 djust_id**도 고정한다. 이는 wire 변경 검증에서 JSON만 검사하면 놓치는 실제 문제다.

### 압축

[확인] 패치 생성기에 gzip/zstd 압축이 붙는 것은 아니다. `config.py:websocket_compression`은 permessage-deflate를 원하는 **advisory flag**이고 실제 협상은 ASGI 서버가 맡는다. 이번 조사에서는 서버를 띄워 협상 여부나 압축률을 측정하지 않았다. 저장소 주석의 서버별 기본값·연결당 메모리 수치를 검증된 보편 사실로 전재하지 않는다.

`runtime.py:ViewRuntime._render_and_send`의 “patch compression”은 별개의 heuristic이다. patch가 **100개 초과**이고 full HTML 크기가 patch JSON의 **70% 미만**이면 Rust baseline을 reset하고 `html_update`로 바꾼다. 이것은 압축 codec이 아니라 표현 방식 선택이다.

`state_backends/redis.py:RedisStateBackend._compress`는 저장 상태가 threshold를 넘고 실제 절약될 때 zstd와 marker byte를 사용한다. **Redis state 압축과 브라우저 wire 압축은 별개**다.

## 5. 템플릿 인식 최적화

| 기능/회귀 | 해결하는 문제와 실제 메커니즘 |
|---|---|
| `dj-if` boundary | 조건 분기로 자식 수가 바뀔 때 뒤 형제의 절대 위치가 변해 엉뚱한 노드를 patch하는 문제. Rust template renderer가 식별 가능한 comment pair를 내고 parser가 보존하며 `diff_children`가 boundary ID로 묶는다. text-only 조건식과 element-bearing boundary를 동일하게 취급한다고 가정하면 안 된다. |
| `dj-update="ignore"` | 에디터·차트 등 client-owned 내부에 서버 patch가 들어가는 것을 막는다. `splice_ignore_subtrees`가 old 자식을 server baseline에도 유지하고 `diff_node_into`가 내부 재귀를 중단한다. wrapper 속성은 여전히 diff될 수 있다. 클라이언트 `applyDjUpdateElements`는 full HTML 갱신 때도 보존을 돕는다. 브라우저가 바꾼 subtree를 서버로 역동기화하는 기능은 아니다. |
| #1826 `dj_if loop spurious move` | 앞 boundary가 비어 있다가 채워지면 뒤 boundary의 절대 child index도 바뀐다. 이를 실제 이동으로 오인해 marker span move와 close-marker 해석이 실패했다. 이동 **판정**을 `(앞선 비경계 형제 수, 같은 레벨 boundary ordinal)`로 바꾸고 이동 **목표**는 여전히 new absolute index로 둔다. `test_dj_if_loop_spurious_move_1826.rs`, Python `test_diff_html_if_marker_rows_1826.py`, JS `dj_if_movesubtree_client_apply_1826.test.js`가 계층별 사례다. |
| #1252 stale ignore cache | old ignored node의 cached HTML이 old children과도 어긋난 상태에서 cache까지 복사하면 stale DOM 문자열이 살아남는다. `splice_ignore_subtrees`는 children/ID를 복사하되 `cached_html=None`으로 무효화하고 `cache_ignore_subtree_html`이 다시 계산한다. `test_ignore_subtree_invalidation_1252.rs`는 의도적으로 stale cache를 주입한다. |
| #1970 loop parse cache | render된 문자열만 재사용해도 html5ever가 그대로 모든 반복 항목을 재파싱하던 비용을 줄인다. `LoopRenderCache.parsed: HashMap<u64, Vec<VNode>>`가 **항목의 파싱된 subtree roots**를 보관한다. AST cache나 diff 결과 cache가 아니다. |

### loop render cache와 parse cache의 차이

[확인] `D/crates/djust_templates/src/loop_cache.rs:LoopRenderCache`에는 HTML fragment map과 parsed subtree map이 나란히 있다. hash는 `content_hash`에서 For body identity와 loop-variable bindings를 포함해 만든다. 단순한 item ID 캐시가 아니므로 내용이 바뀌면 miss이며, 서로 다른 For body의 같은 값도 같은 항목으로 합치지 않는다. `prune`이 현 render에서 본 항목만 남긴다. Python `config.py`의 `loop_render_cache_enabled` 기본값은 True이며 Rust cache 자체의 default-off와 구별한다.

parse HIT면 renderer가 원래 item HTML 대신 `<dj-pc-<nonce> h="...">...</dj-pc-<nonce>>` sentinel을 reduced HTML에 넣는다. `RustLiveViewBackend::try_parse_cache_splice`가 reduced HTML을 파싱하고 cached roots를 clone/splice한다. `djust_vdom::splice_loop_placeholders`가 조립된 **전체 트리를 preorder로 다시 걸어 ID를 부여**해 full parse와 같은 ID 배치를 만든다. 이후 일반 diff와 sync를 수행한다. cache MISS item은 `populate_parse_cache_from_manifest`가 fragment parse로 채우며 임시 parse가 전역 진행 counter를 오염시키지 않게 복원한다.

`tests/test_loop_parse_cache_1970.rs`의 핵심은 cache ON/OFF 결과의 HTML·ID byte identity, re-walk 없는 naive reuse의 충돌, 누락 subtree 오류, attrs 정렬, nonce 없는 사용자 `<dj-pc>`를 잘못 splice하지 않는 성질이다. 사용자 입력이 internal placeholder로 오인되지 않도록 render별 nonce를 쓴다.

캐시 대상은 보수적으로 제한한다. `body_is_cacheable`/`body_is_position_dependent`는 외부 context, forloop 참조, if marker, cycle/resetcycle, 중첩 for 등 출력이 항목 내용만으로 결정되지 않는 경우를 제외한다. `item_html_is_foster_safe`는 table/select 관련 HTML5 insertion-mode 위험을 거른다. missing cache, placeholder 개수 불일치, residual sentinel이면 full parse로 돌아간다. **파싱 재사용이 곧 전체 O(changed)라는 뜻은 아니다.** hash 계산, clone, full-tree ID 재부여, diff, HTML serialization 비용은 남는다.

## 6. 정확성 보장과 테스트의 한계

여기서 round trip에는 세 뜻이 있다. (1) `apply(old, diff(old,new)) ≈ new`, (2) `parse(to_html(vdom)) ≈ vdom`, (3) `deserialize(serialize(value)) = value`. `≈`는 test가 무시하는 ID/정규화 차이를 뺀 구조 동등성이다. 네트워크 지연의 “왕복시간”과 다른 말이다.

| 테스트 | 실제 불변식·검증 대상 |
|---|---|
| `proptest_round_trip_with_sync.rs:proptest_random_toggles_handles_always_resolve` | 2~5 boundary, body 1~4개, 5~20 step의 임의 toggle/내부 변경을 64 case 생성해 매 render의 targeting handle이 client tracker에 존재함을 검사; diff→apply→sync→다음 baseline 연쇄를 시험한다. |
| `fuzz_test.rs:identity_diff_produces_no_patches` | `diff(A,A)`는 빈 배열. |
| `fuzz_test.rs:round_trip_correctness` | 생성한 keyed tree 쌍의 diff를 Rust helper로 적용하면 new와 구조적으로 같다. |
| `fuzz_test.rs:no_panics_on_arbitrary_trees` | 임의 트리 쌍에서 diff가 panic하지 않는다. |
| `fuzz_test.rs:patch_count_bounded` | patch 수가 old+new 노드 수와 속성 수의 합 이하. |
| `fuzz_test.rs:round_trip_keyed_mutation` | A를 mutation해 B를 만들어 key overlap을 보장하고 move/reorder 경로의 apply→new 일치를 시험; 독립 생성에서 reorder가 거의 안 나오는 허점을 줄인다. 이 파일은 proptest 1,000 cases 설정이며 libFuzzer 실행 로그가 아니다. |
| `torture_test.rs` | 깊이 30/50, 넓은 형제 100/500, 중복/혼합 key, reverse/shuffle, attrs/text/entity/Unicode, 반복 counter·증가 목록 등에서 시나리오별 patch/구조/assertion을 검사; 모든 case가 동일한 round-trip helper를 쓰는 것은 아니다. |
| `torture_round_trip_with_sync.rs` | 탭 분기·독립 boundary toggle·긴 반복 뒤에도 서버가 다음 render에 참조하는 ID가 client tracker에 남아 있다. |
| `torture_deep_cascade_dj_if_1418.rs` | 깊이 10/12/15의 conditional cascade를 앞/중간/뒤에 놓고 연쇄 toggle해도 targeting handle drift가 없다. |
| `torture_html_round_trip.rs:assert_round_trip` | `VNode.to_html`을 다시 parse한 구조가 ID 차이를 제거하면 원래와 같다; boundary, ignore, entity, attrs도 포함. |
| `torture_patch_batch_ordering_1420.rs` | 생존/삭제/삽입이 섞인 batch에서 old client tracker의 targeting handle 해석과 특정 방출 순서 사례를 고정. |
| `virtual_keyed_ops_2017.rs:ops_are_only_correct_in_emitted_order` | virtual pool 적용은 방출 순서에서 맞으며 종류별 재정렬하면 anchor 의존성이 깨진다는 반례를 유지. |
| `wire_protocol_snapshot.rs` | 논리적인 diff 정답과 별개로 serialization 계약이 유지된다. |
| `free_threaded_safety.rs` | 12 OS thread×200회 parse/diff의 결정성·panic 부재, thread별 ID counter 독립성을 검사. |

**보장 범위의 중요한 제한 [확인]:** `tests/common/mod.rs:assert_handles_resolve`는 batch 이전 tracker에 ID가 있는지 확인한다. 이것만으로 “모든 선행 patch를 실제 적용한 후에도 ID가 존재한다”가 논리적으로 따라오지는 않는다. #1420 주석이 두 조건을 동일시하는 부분은 과장이다. 또한 `common::apply_all`은 `InsertSubtree.html`을 브라우저처럼 parse하지 않고 **정답 new_vdom에서 subtree를 직접 가져온다**. `patch.rs`의 phase도 JS와 다르다. 따라서 이 계열은 ID 지속성과 Rust 모델의 성질을 검증하는 훌륭한 도구지만 독립적인 browser oracle은 아니다.

차용할 때는 Rust/Python 생성 → 실제 wire bytes → 실제 JS `applyPartial`/morph까지 연결하는 테스트를 더 중요하게 봐야 한다. djust의 `tests/js/vdom_client_faithful_diff.test.js`, `dj_if_movesubtree_client_apply_1826.test.js`, `virtual-keyed-ops-2017.test.js` 같은 client-side 사례가 별도로 필요한 이유다. 이번 조사에서 이 테스트들을 실행해 통과 여부를 확인한 것은 아니다.

## 7. 성능 주장과 근거

### diff 벤치가 실제로 재는 것

[확인] `D/crates/djust_vdom/benches/diff.rs`는 Criterion으로 이미 만든 VNode를 `black_box`에 넣고 `diff`만 반복한다. parse, template render, Python bridge, JSON serialization, WS, 브라우저 apply는 빠진다.

| bench 함수 | 조건 |
|---|---|
| `bench_diff_no_changes` | branch factor 3, depth 1/2/3/4의 동일 트리 |
| `bench_diff_attr_changes` | 단일 class, 여러 attrs, attr add/remove |
| `bench_diff_text_changes` | 짧은 문자열, 1,000자 문자열 교체 |
| `bench_diff_children` | 1→2 append와 역 remove, 20개 unchanged |
| `bench_diff_keyed_children` | 3개 재정렬, 50개 완전 reverse |
| `bench_diff_tag_replace` | div→span |
| `bench_diff_real_world` | form validation show/hide, todo 10개 toggle |
| `bench_diff_deep_trees` | (depth,width)=(3,2),(4,2),(3,3),(5,2) |

이 파일은 **벤치 정의이지 측정 결과표가 아니다**. 여기서 보편적인 `<100µs`를 증명할 수 없다. README 구조도에는 template `<1ms`, diff `<100μs`라는 숫자가 있으나 그 주장에 해당하는 tree 규모·hardware·분포·결과 파일이 같이 고정돼 있지 않으므로 일반 보장 근거가 부족하다.

### README full-render 표

[문서 인용, 이번 재측정 아님] `D/README.md:Performance`: Apple silicon laptop, Django 5.2.16/Python 3.12, DEBUG=False, release build, 양쪽 template을 한 번 parse한 뒤 full render. `D/benchmarks/benchmark.py`는 7 timed batch의 median을 쓴다.

| shape | 행 수 | Django | djust | 문서 speedup |
|---|---:|---:|---:|---:|
| Static markup | 100 | 0.03 ms | 0.03 ms | 1.0× |
| Static markup | 10,000 | 2.85 ms | 2.81 ms | 1.0× |
| Simple list, 2 vars/row | 100 | 0.62 ms | 0.09 ms | 6.8× |
| Simple list, 2 vars/row | 10,000 | 63.7 ms | 8.96 ms | 7.1× |
| Filtered list | 100 | 2.32 ms | 0.21 ms | 10.8× |
| Filtered list | 10,000 | 241 ms | 21.5 ms | 11.2× |

반올림된 시간으로 나눈 값과 speedup 열이 완전히 일치하지 않을 수 있다. 이 표는 **VDOM과 wire size, WS 왕복, client apply를 제외**한다고 README가 명시한다. 이를 “Rust diff가 wireview보다 7~11배 빠르다”로 바꾸면 비교 대상과 측정 구간을 모두 바꿔버린다.

`benchmark.py`는 과거 16.7×/37.5× 광고 수치가 이 harness에서 재현되지 않아 수정했다고 밝힌다. debug extension이 release보다 약 7.6배 느려 결과를 역전시켰다는 주장이 있고 debug build를 거르는 guard가 있다. 이것도 이 환경의 과거 관측이며 일반 상수가 아니다.

`benchmarks/stress_templates.py`는 14종 template shape, 두 크기, 별도 compile 측정, unused-state 등 추가 실험을 제공한다. **출력 byte equality를 먼저 검사하고 다른 출력은 timing에서 제외**, median/min 및 release guard를 사용한다. 조사한 script 자체에는 모든 shape의 확정 결과표가 없다. README의 navigation 110~250ms 대 ~10ms, 10~20× 주장도 네트워크 가정/설명용 숫자이며 diff benchmark 결과가 아니다.

### PERFORMANCE_BRAINSTORM의 현재 내용

[문서 인용] 이 문서는 2026-08-23 brainstorm으로 시작하고 끝에 “성능은 미측정”이라는 오래된 문구가 남아 있지만, 현재 파일 §7에는 **이후 #2532 model-backed 측정표가 추가**돼 있다. 문서 전체를 전부 실측 또는 전부 추측으로 묶으면 안 된다.

§7 조건: release `maturin develop --release`, SQLite `:memory:`, warm-up 후 3 rounds median, 12 cores/load≈6의 조용한 머신, model-backed 50행×6열 계열. list_control mount는 process 첫 실행의 cold-start를 포함한다. 대표 수치를 그대로 옮기면:

| variant / phase | total ms | Rust render ms | parse ms | diff ms | HTML serialize ms | 비고 |
|---|---:|---:|---:|---:|---:|---|
| list_control mount | 8.43 | 0.39 | 0.23 | 0.00 | 0.13 | state sync 4.54, JIT/context 0.67 |
| list_control text_change | 3.08 | 0.00 | 0.00 | 0.00 | 0.13 | fragment fast path |
| list_control attr_change | 3.77 | 0.39 | 0.25 | 0.19 | 0.12 | full path |
| list_control row_text_change | 4.11 | 0.38 | 0.06 | 0.00 | 0.12 | region fast path |
| presenter_reverse attr_change | 14.20 | 6.13 | 0.24 | 0.18 | 0.12 | SQL 50 queries, 302 direct crossings + 950 proxy rewraps |
| snapshot text_change | 5.61 | 0.00 | 0.00 | 0.00 | 0.12 | persistence 2.40ms |

Python crossing 계측 wrapper의 중첩 시간 이중 합산 때문에 presenter `xing_ms≈5.1`은 높게 기록됐으며 수정 해석은 약 3.9ms라고 본문이 정정한다. phase 값은 계측/포함 관계가 있어 단순 합산하지 않는다. `render_with_diff`의 `diff_ms`는 diff뿐 아니라 sync/patch JSON 생성 구간도 포함하므로 Criterion 순수 diff와 동일하지 않다.

문서의 mount 11.89ms는 fresh communicator 측정이라 cache-hit msgpack clone 비용의 증거가 아니라는 자체 반증도 있다. `sync_to_async≈0%`는 별도 script의 과거 측정 주장이지 모든 앱에 적용할 값이 아니다. loop cache 설명의 ~8µs/item·50-item reorder cycle의 ~84%가 render라는 숫자도 해당 과거 workload 주장이다. 시점이 오래된 제안·병목 분석은 현 소스의 COW state 등 이미 바뀐 구현과 구별해야 한다.

[판단] 여기서 얻을 실용적인 근거는 “Rust diff가 더 빨라질 여지”보다 **state sync·ORM·Python object crossing이 Rust render/diff보다 클 수 있고, text update는 이미 diff를 하지 않을 수 있다**는 점이다. djust와 wireview의 workload가 다르므로 표의 absolute latency로 우열을 매기지 않는다.

## 8. Python 경계, GIL, baseline과 재연결

### PyO3로 넘기는 것

[확인] 기본 경로에서 Python은 `RustLiveView`에 template source/dirs와 context state를 넘긴다. `update_state`는 Python 객체를 `Value`/state로 변환하고, model/queryset 등은 `_sync_state_to_rust`의 normalization/JIT 단계를 거친다. raw Python 객체는 `set_raw_py_values: HashMap<String, Py<PyAny>>` sidecar로 붙을 수 있다. Python에서 VNode 트리를 만들어 매 이벤트 왕복시키는 구조가 아니다.

Rust 안에서 HTML을 생성하고 VDOM을 유지하며 Python으로 `(hydrated HTML str, patches JSON str 또는 None, version int)`를 돌려준다. 별도 utility `djust_live/src/lib.rs:diff_html(old_html:String,new_html:String)`는 **HTML 두 문자열**을 받아 parse/diff해 JSON을 반환한다. binary API는 bytes를 반환하고, state serialization도 bytes다. 이 API들의 경계를 혼동하면 안 된다.

### GIL

[확인] module은 `#[pymodule(gil_used = false)]`를 선언한다. free-threaded Python에서 import 때문에 자동으로 GIL을 켜지 않겠다는 선언이지 모든 메서드가 전통적인 CPython GIL을 자동 해제한다는 뜻은 아니다. `render_with_diff` 본체를 전체적으로 감싼 `py.detach`는 없고, Python sidecar 접근은 `Python::attach` 등을 사용한다. `fast_json_dumps`, markdown 같은 별도 pure-Rust 경로의 `py.detach`와 구별해야 한다.

`djust_vdom/tests/free_threaded_safety.rs`는 12개 OS thread에서 독립 문서를 parse/diff하고 thread-local `ID_COUNTER` 독립성을 검사한다. `djust_templates/tests/free_threaded_safety.rs`도 shared template/registry 등 병렬 안전성 범주를 다룬다. 이것이 동일한 mutable `RustLiveView`를 여러 이벤트가 동시에 바꿔도 된다는 보장은 아니다. Python runtime의 render lock과 caller별 backend instance 격리가 별도로 중요하다. `rust_bridge.py`는 InMemory backend hit도 msgpack clone을 반환해 같은 mutable Rust 인스턴스 공유를 피한다고 설명한다.

### 이전 VDOM의 저장

[확인] `RustLiveViewBackend.last_vdom: Option<VNode>`가 baseline이며, Python view의 `_rust_view`가 소유한다. `last_html`, fragment HTML, text map/index, loop render/parse cache도 있다. active view별 tree/state/cache 비용이 있으며 sticky child 등 별도 view도 자기 비용을 가진다. `SerializableViewState`는 template source, state, last_vdom, version, timestamp를 msgpack으로 저장한다. `last_html`, loop cache, raw handles 등은 transient로 복원 후 재구축한다.

`state_backends/memory.py:InMemoryStateBackend` 및 `state_backends/redis.py:RedisStateBackend`가 보관·복원 경로를 제공한다. 따라서 “항상 연결 하나에 트리 딱 하나”도 “클라이언트가 모든 VDOM 상태를 들고 서버는 stateless”도 아니다. 연결/view 수와 HTML 크기에 비례하는 메모리를 예상할 수 있지만, 이번 조사에는 **djust 연결당 RSS 실측이 없다**. wireview의 연결 메모리 수치를 djust 수치처럼 사용할 수 없다.

### 재연결과 full HTML 복구

[확인] 세션 backend의 Rust baseline deserialize와 사용자 상태 reconnect 복원은 별개다. `runtime.py:ViewRuntime.dispatch_mount`는 새 view를 준비하고 인증 후 opt-in session/signed snapshot을 적용하거나 `mount`를 실행한다. `enable_state_snapshot`은 기본 비활성이다. 상태가 복원되어도 브라우저 DOM과 임의의 오래된 diff를 바로 이어 붙이는 계약은 아니다. mount는 렌더 결과 HTML로 DOM baseline을 맞춘다.

`websocket.py:handle_request_html`, `_next_version_armed`와 runtime transport의 `next_client_version`은 patch 실패/버전 어긋남의 full HTML 복구를 다룬다. WS wire version은 consumer 소유로, reset될 수 있는 Rust render version과 구별해 단조성을 유지한다. `RustLiveViewBackend::reset`은 VDOM/cache baseline을 비운다.

sticky child reconnect는 ADR-018과 `mixins/sticky.py:restore_sticky_child_state`, `save_sticky_child_state`, `sticky_child_should_persist`의 별도 계약이다. **parent와 child 모두 snapshot opt-in**이어야 하고 stable sticky_id로 저장하며, tag가 child를 만드는 시점에 복원한다. 자동 `child_N` 같은 일시적인 ID를 영속 키로 쓰지 않는다. 동일 소켓 내 navigation에서 기존 DOM/인스턴스를 보존하는 ADR-011과 소켓이 끊긴 뒤 상태를 복원하는 ADR-018을 같은 기능으로 보면 안 된다.

## 9. wireview와의 대응

### a. 리스트 앞쪽 삽입/재정렬

| djust | wireview 현재 |
|---|---|
| `reconcile_keyed`는 stable key로 생존 항목을 찾고 LIS로 이동 수를 줄인다. 앞쪽 신규 항목만 InsertChild, 순수 reorder는 MoveChild 중심. 키가 없으면 위치 비교 한계가 있다. | `W/wireview/core/rendered.py:Comprehension.diff`는 i번째 dynamics list와 이전 i번째를 비교한다. 바뀐 item은 **item dynamics 전체**를 `u[index]`로 보내고 `n`으로 길이를 맞춘다. 앞쪽 삽입은 뒤 항목 대량 전송. GAP-030이다. |

[판단] 먼저 wireview `Comprehension`에 stable key→item state와 order를 도입한다. 서버가 key를 알아야 하며 HTML `id`만 추가해서는 전송량 문제가 해결되지 않는다. 처음에는 order key 배열 전체를 전송하는 단순 방식도 가능하지만 이는 여전히 O(n) wire다. 큰 목록에서 compact insert/remove/move가 필요할 때 LIS/anchor ops를 추가한다. idiomorph는 유지할 수 있으므로 서버 VDOM이 필요하지 않다.

### b. 서드파티 위젯 subtree 보존

| djust | wireview 현재 |
|---|---|
| `dj-update="ignore"`: 내부 patch 억제, old baseline 자식 splice, full HTML 적용에서도 보존. cache 무효화와 ID 생명주기까지 설계 대상이다. | `wireview/static/wireview/wireview-boost.js:morph`가 `isStreamContainer(fromEl)`이면 `beforeNodeMorphed`에서 false를 반환해 `wire-stream` subtree를 보존한다. 임의 위젯용 callback은 `wireview.dom.onBeforeElUpdated`가 설정한다. |

[중요한 확인] 현재 wrapper는 `callback(fromEl,toEl)`의 **반환값을 쓰지 않고 항상 true를 반환**한다. 공개 JSDoc도 반환형을 void로 선언하므로 기존 계약의 버그라고 단정하기보다 기능 범위의 차이로 봐야 한다. 사용자가 callback에서 false만 돌려주는 방식으로 morph를 veto할 수 없다. callback이 toEl에 class/attrs를 복사하는 등의 보정은 가능하다. `wire-stream` 보호도 일반 widget lifecycle 전체와 같지는 않다.

[판단] 일반 ignore 속성 또는 명시적 boolean callback 계약을 도입할 가치는 있다. attributes까지 무시할지 children만 무시할지, full navigation에도 유지할지, removed/reinserted wrapper의 lifecycle은 무엇인지 먼저 정해야 한다. djust의 subtree cache를 복제할 필요는 없다.

### c. 내비게이션을 건너 살아남는 컴포넌트

| djust | wireview 현재 |
|---|---|
| ADR-011: sticky child의 Python instance와 DOM을 stash해 목적지 slot에 다시 붙임. ADR-014: `{% live_render sticky=True %}`가 이미 살아 있는 instance를 자동 감지·reattach해 중복 mount를 방지. ADR-018: stable sticky_id의 session state를 reconnect 때 복원. | `docs/FEATURE-GAP.md`의 GAP-033은 미구현이다. `ComponentRef`와 `render.children`는 부모 재렌더 중 child HTML/업데이트를 분리하지만 boost navigation을 넘는 server instance·DOM 지속성 계약은 아니다. |

근거 구현은 `D/python/djust/mixins/sticky.py:StickyChildRegistry._preserve_sticky_children`, `restore_sticky_child_state`, `templatetags/live_tags.py`, `static/djust/src/45-child-view.js`다. [판단] stable ID, parent 소유권 이전, mount/leave 취급, DOM detach/attach, task/subscription 정리, 인증/live_session 경계를 함께 차용해야 한다. 서버 instance만 남기면 audio/focus가 깨지고 DOM만 남기면 이벤트 대상이 사라진다.

### d. 같은 컴포넌트 N개의 갱신

| djust | wireview 현재 |
|---|---|
| child/component 개별 이벤트·props 갱신과 한 patch batch의 적용 최적화가 있다. 조사한 `python/djust`/`crates`의 `.py`/`.rs`에서 `update_many`/`batch_update`에 해당하는 일반 컴포넌트 batch hook은 발견하지 못했다. `mixins/components.py`, `runtime.py:_dispatch_component_event`, Rust actor의 `update_component_props`는 개별 처리다. | GAP-035가 남아 있다. `render.children`으로 여러 child diff를 한 메시지에 담는 것과 N개의 ORM 조회를 한 번으로 줄이는 `update_many`는 다른 문제다. |

[판단] djust의 LIS/VDOM batch는 데이터 로딩 N+1의 답이 아니다. wireview에서는 같은 component class의 assigns를 모아 batch load한 뒤 instance별 update/render로 분배하는 hook이 직접적인 해결책이다. 쿼리 수와 결과 동일성을 검증해야 한다. 단순히 wire frame을 하나로 합치는 구현으로 GAP-035를 완료 처리하지 않는다.

### e. 같은 변경의 payload 크기

[실행한 작은 계산] 아래는 UTF-8 compact JSON의 **diff 본문만**이다. outer envelope, component ID, version, signed state 변경, 압축, 네트워크 frame overhead는 제외했다. wireview는 실제 로컬 `Rendered.get_diff`/`Comprehension.diff`를 실행했다. djust는 enum/schema에 맞춘 예시를 Python dict로 구성해 길이를 계산했으며 Rust producer를 실행하지 않았다. key/ID 길이와 DOM depth에 따라 달라진다.

| 변경 | wireview 예시 | djust 예시 | 바이트 |
|---|---|---|---|
| `<p>{{x}}</p>`의 x→hello | `{"0":"hello"}` | `[{"type":"SetText","path":[0],"text":"hello"}]` | 13 vs 46 |
| `class="{{x}}"`의 x→active | `{"0":"active"}` | `[{"type":"SetAttr","path":[],"d":"0","key":"class","value":"active"}]` | 14 vs 69 |
| `[A,B]` 앞에 X 삽입 | `{"0":{"u":{"0":["X"],"1":["A"],"2":["B"]},"n":3}}` | keyed `<li dj-key="x">X</li>`의 InsertChild+VNode 예시 | 49 vs 220 |
| `[A,B]` 뒤에 X 추가 | `{"0":{"u":{"2":["X"]},"n":3}}` | 같은 신규 node를 담는 InsertChild 계열 | wireview 29; djust는 예시와 비슷한 node 크기 |

220B 예시의 node는 아래 형태이며 ref_d는 없다.

```json
[{"type":"InsertChild","path":[],"d":"0","index":0,"node":{"tag":"li","attrs":{"dj-id":"3","dj-key":"x"},"children":[{"tag":"#text","attrs":{},"children":[],"text":"X","key":null}],"text":null,"key":"x","djust_id":"3"}}]
```

[판단] 작은 단일 dynamic 변경은 슬롯 번호와 값만 보내는 wireview가 작기 쉽다. djust는 type/path/key/ID overhead가 있지만, 많은 unchanged dynamic 값을 가진 item 내부에서 **한 attr만** 바뀌면 wireview의 현재 item 전체 dynamics 재전송보다 작아질 수도 있다. 반대로 `<p>hello {{name}}</p>`에서 name만 바뀌면 djust text node는 `hello ...` 전체를 보내고 wireview는 name 슬롯만 보낸다. attribute도 `class="fixed {{state}}"`라면 djust는 결합된 속성 전체, wireview는 state 값만 보낼 수 있다.

앞쪽 삽입은 작은 2행 예시에서는 VNode metadata가 더 비싸지만, wireview 위치 기반 전송은 뒤쪽 행 수에 비례하고 djust keyed 삽입은 새 subtree 크기 중심이라 충분히 큰 목록에서 역전된다. wireview keyed comprehension을 만들면 statics 공유와 기존 항목 재사용을 함께 얻을 수 있어 **VDOM으로 바꾸지 않고도 핵심 손해를 없앨 수 있다**. 초회 static 전송·full fallback·DOM morph CPU까지 포함한 실제 세션 측정으로 최종 판단해야 한다.

## 10. 차용 후보의 판정

아래 비용은 이번 조사에서의 상대 추정이다. “작음”은 제한된 코드·테스트 범위, “중간”은 Python/JS/protocol 동시 변경, “큼”은 lifecycle 또는 엔진 교체다. 실제 일정 추정은 아니다. 파일은 모두 wireview 쪽 변경 후보이며 이번에는 변경하지 않았다.

### (가) 알고리즘/설계 아이디어

| 후보·판정 | 이유·예상 비용 | 변경 후보 |
|---|---|---|
| **우선: stable-key comprehension** | GAP-030의 실제 wire 낭비를 없앤다. key→item dynamics와 order 분리부터 시작; 중간~큼. key 중복/누락, empty→nonempty, template 변경 fallback을 계약화해야 한다. | `wireview/template_engine.py`, `wireview/core/rendered.py`, `wireview/static/wireview/rendered.mjs`, `docs/implementation/wire-protocol.md` |
| **조건부: LIS와 key anchor ops** | 순수 재정렬의 move 수와 큰 order payload를 줄인다. 먼저 단순 keyed 모델을 확보한 뒤 큰 목록 측정으로 결정. 중간. DOM 직접 opcode보다 Rendered collection에 적용하는 것이 자연스럽다. | 위 Rendered Python/JS, 필요 시 `wireview/static/wireview/wireview.js` |
| **우선: 일반 widget 보존 계약 정리** | 현재 stream 보호는 있지만 callback return 무시로 veto를 구현할 수 없다. children-only ignore인지 전체 element skip인지 명확히 하고 테스트. 작음~중간. | `wireview/static/wireview/wireview-boost.js`, `wireview.js`, 관련 DOM/hook 문서 |
| **채택: sticky stable identity와 수명 분리** | navigation 생존과 reconnect snapshot을 별도 기능으로 설계. 인증 경계가 바뀌면 보존 허용 여부도 결정. 큼. | `wireview/live_component.py`, `repository.py`, `consumer.py`, `templatetags/wireview.py`, `static/wireview/wireview-boost.js`, `wireview.js`, 프로토콜 문서 |
| **조건부: dependency/항목 render cache** | render가 병목일 때 diff보다 직접적인 후보. Django tag/filter/외부 context/forloop/locale/권한 의존성을 정확히 알지 못하면 stale render 위험. 중간~큼; opt-in 순수 항목부터 측정. | `wireview/template_engine.py`, `wireview/core/rendered.py`, `wireview/core/component.py` |
| **별도 구현: component batch load/update** | GAP-035는 djust VDOM에서 답을 얻지 못했다. class별 load hook으로 ORM N+1 제거. 중간. | `wireview/live_component.py`, `repository.py`, `consumer.py`, `wireview/core/component.py` |

### (나) 테스트 전략

| 차용·우선도 | 이유·예상 비용 | 변경 후보 |
|---|---|---|
| **최우선: delta round trip** | Python `Rendered` old/new로 만든 payload를 JS `applyPartial`에 적용한 뒤 `buildHtml`이 새 render와 같아야 한다. 서버 구현을 복제한 test helper만 쓰지 않는다. 중간. | `tests/`의 Python/JS 공통 fixture, `wireview/core/rendered.py`, `static/wireview/rendered.mjs` 계약 테스트 |
| **최우선: 연속 mutation property** | 한 번의 성공이 아니라 insert/remove/reorder/조건 분기/empty/update를 반복해 client cache가 drift하지 않는지 검사. key overlap을 보장하는 mutation generator와 shrink 사용. 중간. | `tests/`에 property tests 및 JS fixture 소비 테스트 |
| **높음: 실제 browser identity 검증** | 결과 HTML뿐 아니라 input value/selection, focus, widget instance, sticky audio/DOM identity, stream row 유지 확인. 중간. | 브라우저 E2E tests, `wireview.js`, `wireview-boost.js` 관련 시나리오 |
| **높음: wire literal snapshot** | optional/null/empty/full/partial/children/ref 등의 계약을 고정. JSON만 쓸 때도 field 충돌·shape drift 방지. 작음. | protocol tests, `docs/implementation/wire-protocol.md` |
| **cache 도입 시 필수: ON/OFF equality + negative control** | fast path가 실제 실행됐는지 counter로 확인하고, 끄면 성능 특성 또는 특정 회귀가 드러나는지 검사. 출력이 다른 빠른 엔진은 비교하지 않는다. 중간. | cache tests 및 benchmark fixtures |

Rust property suite를 그대로 번역할 필요는 없다. wireview의 핵심 불변식은 “dynamic cache + partial payload가 full Rendered와 같고, 그 HTML morph가 보존 계약을 지킨다”다. djust의 `new_vdom`을 oracle에 직접 넣는 방식과 pre-batch ID 존재만으로 intra-batch correctness를 주장하는 방식은 차용하지 않는다.

### (다) 프로토콜

| 판정 | 이유·예상 비용 | 변경 후보 |
|---|---|---|
| **기존 JSON/Rendered 유지, keyed extension만 설계** | 현재 statics 재사용 장점을 보존하면서 GAP-030 해결. 새 shape를 기존 comprehension/block/ref와 구별하고 fallback·구버전 처리 명시. 중간. | `core/rendered.py`, `static/wireview/rendered.mjs`, `wireview.js`, `docs/implementation/wire-protocol.md` |
| **검토: full/partial 크기 선택** | djust의 “큰 patch보다 HTML이 작으면 fallback” 아이디어는 유용. wireview에서는 full Rendered와 partial 크기·client 비용을 비교해야 한다. 100/0.7 상수 복제는 근거 없음. 작음~중간, 먼저 측정. | `wireview/core/rendered.py`, `consumer.py`, render emission tests |
| **검토: generation/version + resync** | session cache/재연결/차후 여러 transport에서 stale baseline 검출을 명시화. Rust version/WS version 혼동 사례를 참고. 중간. 현재 요구에 맞는 범위만 도입. | `consumer.py`, `wireview.js`, 프로토콜 문서 |
| **보류: MessagePack 전환** | 작은 슬롯 JSON에는 이득이 제한적일 수 있고 named-map decoder·관측성·호환성 비용 발생. djust에서도 기본 event wire는 JSON. 실측 전 도입하지 않는다. 중간. | 도입한다면 transport/consumer, `wireview.js`, 양쪽 serialization snapshot |

### (라) 가져오면 안 되는 것

- **HTML→VDOM→opcode 전체 교체:** wireview가 이미 가진 템플릿 구조를 버리고 HTML5 parser, ID allocator/sync, opcode client, marker normalization을 새로 관리하게 된다. 비용 매우 큼. `template_engine.py`, `core/rendered.py`, `rendered.mjs`, `wireview.js`, 배포 구성을 광범위하게 바꾸지만 현재 병목 개선 근거가 없다.
- **loop parse cache 그대로:** wireview는 html5ever로 DOM을 만들지 않는다. 없는 병목을 만들고 캐시로 줄일 이유가 없다. 가져올 것은 dependency/invalidation 조건과 검증 방식이다. 현 파일 변경 불필요.
- **dj-id 전체 발급과 sync_ids 그대로:** Rendered의 슬롯/키 주소와 idiomorph에 맞지 않는 추가 정체성 계층이다. duplicate-key matcher 차이까지 포함해 유지 부담이 커진다. Python/JS 전체 변경 비용 큼.
- **dj-* 제거 금지 정책:** framework 고유 event binding 가정을 wireview attrs 일반 규칙으로 번역하면 stale attributes를 남길 수 있다. wireview의 lifecycle·event delegation 규칙으로 따로 판단한다.
- **Rust template engine 동시 도입:** diff 최적화와 다른 사업이다. Django tag/filter, ORM value, callable, localization, custom extension compatibility를 장기적으로 관리해야 한다. `template_engine.py`와 Django integration 전체에 영향, 비용 매우 큼.
- **성능 headline과 상수 복제:** `<100µs`, 7~11×, patch threshold 100 같은 것은 다른 workload/구간의 수치다. benchmark와 프로파일부터 마련한다.

### Rust 자체의 손익

[확인한 wireview 실측] `W/docs/design/transport-abstraction.md` §1은 macOS/Python 3.12/Channels 4.3/InMemory layer에서 실제 WS testproj를 측정해 **이벤트 CPU 0.2~0.6ms, 그중 60% 이상 Django template render**라고 기록한다. 해당 문서는 Windows·SQLite·WSL2/Docker 없는 배포를 중요한 전제로 삼고 Windows ARM64/x64 별도 측정도 둔다. 이 수치는 그 workload의 과거 실측이며 모든 앱의 보편값은 아니다.

[판단] 나머지 40% 전부를 비용 0으로 만드는 비현실적 상한조차 전체 speedup은 최대 약 **1/0.6 = 1.67×**다. 그 40%에는 diff 외 비용이 있으므로 Rust diff만의 실제 상한은 더 낮다. 예를 들어 diff가 총 10%이고 이를 10배 빠르게 해도 `1/(0.9+0.1/10)≈1.10×`다. 이는 Amdahl 설명용 가정이며 wireview diff 점유율을 10%로 측정했다는 뜻이 아니다.

Rust가 살 수 있는 것은 큰 순수 연산의 CPU 효율, 명시적으로 Python을 떠난 구간의 병렬 실행, typed tree 처리다. 하지만 **djust VDOM으로 교체**하면 wireview에 없던 전체 HTML parse·tree allocation·ID sync·larger snapshot·새 client protocol 비용도 산다. 단순히 현재 `Rendered`의 작은 diff를 Rust로 번역하는 것과 완전히 다르다. Python↔Rust 변환 비용도 수익에서 빼야 한다.

배포 비용은 Rust toolchain/maturin·PyO3, Python ABI 및 free-threaded 조합, macOS/Linux/Windows x64·ARM64 wheels의 build/test/release matrix, source build fallback, native crash·FFI 디버깅, 기여자의 Rust 학습 부담이다. Windows에서 Rust가 불가능하다는 뜻은 아니며, 이 조사에서 djust의 모든 Windows wheel 제공 여부를 검증하지는 않았다. wireview의 쉬운 Windows 배포 전제에 **검증할 release 책임이 추가**된다는 뜻이다.

**현재 판정: Rust diff 도입 보류.** 먼저 keyed comprehension으로 payload 문제를 해결하고, render·marker parsing·diff·serialization·morph를 분리 계측한다. Django render, N+1, 불필요한 재렌더가 큰 경우 그 원인을 해결한다. 이후 대형 실제 workload에서 pure-Python `Rendered` diff/marker parse가 지배적이라는 증거가 생기면 기존 프로토콜을 유지한 optional native accelerator를 제한적으로 비교할 수 있다. 그 경우에도 Windows wheels, Python fallback, 동일 결과 property tests, end-to-end 개선과 메모리 회귀까지 통과해야 채택 근거가 된다.
