# 구현 이슈 초안과 릴리스 로드맵

기준: Phoenix LiveView **1.2.12**, wireview `a4e957c4`. 근거 표와 공식 링크는 [phoenix-1.2-gap-codex-2026-10-13.md](./phoenix-1.2-gap-codex-2026-10-13.md).
아래는 게시하지 않은 초안이다. GAP-038~067은 **예약 전 제안 번호**이며 실제 등록 전에 충돌을 다시 확인한다.
`gh label list --limit 100`으로 실제 라벨을 확인했다. 열린 이슈 조회는 비어 있었고, 기존 GAP-037까지는 비교 문서를 기준으로 삼았다.
완료/과거 이슈 전체를 중복 조사했다고 주장하지 않는다. 착수 전 해당 closed 이슈와도 대조한다.


> **이후(2026-10-14):** GAP-046(훅 API)의 Promise 응답·거절과 핸들러 해제는 [#222](https://github.com/itda-work/django-wireview/issues/222)가
> 구현했다. `pushEventTo`·`js()`·upload 편의·class 등록·`beforeUpdate(toEl)`는 남아 있다.

## 우선순위와 의존 관계

| 순서 | 제안 릴리스 | 작업 묶음 |
|---|---|---|
| 0 | 1.7 선행, 결함이면 패치 | GAP-066 URL 정책 감사·회귀 방어 |
| 1 | 1.7 | GAP-038/040/041/042/043/045, GAP-048/049, GAP-051/052, GAP-056; GAP-046 기본 API |
| 2 | 1.8 | GAP-039/044/047 및 GAP-046 확장, GAP-050/053, GAP-054/055/058/062/063, GAP-057/059 |
| 3 | 1.9 | GAP-060/061/064/065/067; 설계·성능 검증 후 쪼개서 구현 |
| 4 | 2.0 검토 | transition 순서·stream limit·복구 기본값을 바꿀 경우에만 deprecation 후 전환. 자동 전환을 약속하지 않음 |

핵심 경로는 048→049/052→050, 051→054→057/058, 066→062→063,
043→039와 039/045/046→064다. 040/047은 schema를 먼저 공동 결정하여 구현 간 순환 의존을 없앤다.
각 릴리스는 용량 추정 전의 추천 묶음이며 출시 약속이 아니다.

## Epic 초안

| ID·제목 | 실제 라벨 | 관찰·범위·완료 기준·검증·호환/프로토콜 |
|---|---|---|
| E1: 이벤트·JS·훅의 브라우저 계약 확장 | epic, enhancement, type: feature-gap, area: js-interop, P1-core | 기본 기능명은 있어도 옵션·순서·소유권 계약이 빠졌다. GAP-038~047을 자식으로 둔다. 공통 selector/ref/수명주기 설계를 먼저 합의하고 자식 E2E·strict CSP·mixed-version 통과 시 완료. 기존 DSL/콜백 유지, 신규 opcode는 capability gate. 1.7→1.8 |
| E2: 폼 복구와 업로드 제출 수명주기 | epic, enhancement, type: feature-gap, area: forms, area: uploads, P1-core | serializer·복구·업로드 submit이 서로 분리되어 있다. GAP-048~050/052~053을 묶는다. 실제 입력→검증→업로드→submit→이동까지 실패/취소 E2E로 완료. 기존 opt-in/default 유지, payload·상태 협상. 1.7→1.8 |
| E3: 서버 데이터와 컴포넌트 선언 확장 | epic, enhancement, type: feature-gap, area: components, P2-advanced | 스트림·async·슬롯 일부 계약만 있다. GAP-051/054/055/058/062/063/065. schema·수명주기·이동 통합 회귀 통과 시 완료. 기존 Python API 유지, stream/상태/이동 frame gate. 1.7→1.9 |
| E4: 사용자 테스트·배포·자산 도구 | epic, enhancement, testing, area: devtools, P2-advanced | 내부 테스트는 있으나 사용자 헬퍼와 자산 도구가 부족하다. GAP-056/057/059/061. 공개 예제·negative 테스트·패키지/CSP 검사 통과 시 완료. helper는 기존 API 추가, join 자산 정보만 협상. 1.7→1.9 |
| E5: 렌더 평가·포털·상태 API 설계 | epic, enhancement, type: feature-gap, area: performance, P2-advanced | 전송 diff와 계산 생략/DOM 상태 보존을 구분해야 한다. GAP-060/064/067. 설계안을 분할하고 정확성·벤치 근거를 갖출 때 완료. opt-in 우선; encoding 변경만 wire version 증가. 1.9, E1/E3 이후 |

Epic은 추적 단위라 GAP 번호를 따로 소비하지 않는다. 큰 자식(046/060/061)은 설계 후 하위 구현 이슈로 나누되
같은 갭 번호를 중복 생성하지 않는다. 제안 번호의 등록 순서는 의존 순서와 달라도 된다.

## 공통 완료 조건과 프로토콜 정책

현재 프로토콜은 Python `wireview/core/rendered.py:67`과 JS `rendered.mjs:37`의 **11**이다.
새 서버→클라이언트 명령을 모르는 구 번들이 조용히 무시하는 것은 호환성으로 인정하지 않는다.
기존 협상에 capability/최소 버전 gate를 붙이고, 가능한 대체 동작·명시적 실패·페이지 갱신 중 계약을 선택한다.
새 encoding이 필수라면 여러 작업을 한 릴리스의 다음 버전으로 묶는다. 모든 항목이 각자 무조건 버전을 올릴 필요는 없다.
새 client/구 server, 구 client/새 server, rolling reconnect를 테스트한다. 아래 각 초안의 영향은 이 정책을 구체화한 것이다.

모든 구현 이슈는 사용자 문서·타입/편집기 metadata·필요한 회귀 테스트를 함께 완료한다.
일반 옵션 추가는 해당 unit/E2E, protocol 변경은 버전 조합, 성능 주장은 원본 벤치 결과를 요구한다.
이 문서 작업에서는 라이브러리 구현이나 이슈 등록을 하지 않았다.

## GAP-038: 창 단위 이벤트와 click-away 바인딩

라벨: `enhancement`, `type: feature-gap`, `area: js-interop`, `P1-core`

Epic: E1. 추천 릴리스: 1.7. 의존 관계: 없음.

**관찰·중요성** — 이벤트 위임은 DOM 루트에 한정되며 요소 밖 클릭·window key/focus를 해석하지 않는다(`wireview/static/wireview/wireview.js:3222`). 모달 닫기·전역 단축키마다 수동 훅이 필요하다.

**범위** — on DSL에 명시적 전역/away 모드를 추가하고 component 소유권·중첩 경계·제거 시 해제를 정의한다.

**완료 기준** — 내부 클릭은 away를 부르지 않고 외부 클릭은 한 번만 부른다. window 이벤트가 포커스 위치와 무관하게 올바른 component로 가며 제거/재연결 후 중복되지 않는다.

**테스트 방법** — parser 단위 + 브라우저에서 중첩 컴포넌트·동적 삽입·제거·재연결·IME/조합 키를 확인한다. 잘못된 modifier는 계속 거부한다.

**호환 영향** — 기존 on 문법 유지. .once 같은 Phoenix에도 없는 표기는 이 범위에 넣지 않는다.

**프로토콜 버전 영향** — 새 템플릿 바인딩 형식을 구 클라이언트가 이해하지 못한다. 지원 버전에서만 출력하거나 최소 번들 버전을 명시하고 불일치 시 갱신을 요구한다.


## GAP-039: 선언적 mount·remove·연결 수명주기 JS

라벨: `enhancement`, `type: feature-gap`, `area: js-interop`, `P2-advanced`

Epic: E1. 추천 릴리스: 1.8. 의존 관계: GAP-043.

**관찰·중요성** — 훅 콜백은 있지만 mounted/remove/connected/disconnected 명령 바인딩은 없다(`wireview/static/wireview/wireview.js:3222`). 제거 애니메이션이 끝나기 전에 DOM이 사라진다.

**범위** — 요소 수명주기 명령과 제거 대기, 이동 시 remove cascade 정책을 추가한다. Phoenix 1.2의 cascade opt-out도 검토한다.

**완료 기준** — 최초 mount·새 삽입은 각각 한 번 실행되고 이동/제거는 대기 후 정리한다. 제거된 요소의 이벤트와 타이머가 남지 않는다.

**테스트 방법** — E2E로 초기 join/stream delete/reset/boost 이동/중첩 제거/transition 0ms·오류를 검사한다.

**호환 영향** — 기존 훅 호출 순서 유지. 새 바인딩에만 적용하고 cascade 기본을 문서화한다.

**프로토콜 버전 영향** — 새 DOM 바인딩과 클라이언트 수명주기 기능. 지원 번들 gating 필요; server frame 추가 시 프로토콜 capability 필요.


## GAP-040: 이벤트 시간 제어와 동적 payload 규약

라벨: `enhancement`, `type: feature-gap`, `area: js-interop`, `P1-core`

Epic: E1. 추천 릴리스: 1.7. 의존 관계: GAP-047의 metadata 설계와 조율.

**관찰·중요성** — 숫자 debounce/throttle만 있고 blur/default/form reset/key 예외와 phx-value/key payload가 없다(`wireview/event_transpiler.py:31`, `wireview/static/wireview/wireview.js:3350`). 같은 UI가 Phoenix 예제와 다르게 요청한다.

**범위** — blur 모드·기본 지연·form별 타이머 취소·키별 throttle와 값/metadata 우선순위를 정의한다.

**완료 기준** — 빠른 다른 키와 같은 키 반복을 구별하고 submit이 오래된 change를 남기지 않는다. 고정 kwargs·DOM 값·metadata 충돌 우선순위가 문서와 일치한다.

**테스트 방법** — 가상 clock 단위 + 실제 blur/submit/IME E2E. 제거·재연결 시 timer leak 및 늦은 이전 값 전송을 검사한다.

**호환 영향** — 기존 숫자 의미는 유지. 새 payload를 기존 핸들러 kwargs에 무조건 섞지 않고 opt-in 메타데이터 공간을 쓴다.

**프로토콜 버전 영향** — 바인딩 확장 capability가 필요. metadata 필드는 선택적으로 협상하고 구 서버에는 전송하지 않는다.


## GAP-041: JS 속성 토글·체인 재사용·상대 선택자

라벨: `enhancement`, `type: feature-gap`, `area: js-interop`, `P1-core`

Epic: E1. 추천 릴리스: 1.7. 의존 관계: 없음.

**관찰·중요성** — JS 빌더에 toggle_attribute/exec/concat와 inner/closest가 없다(`wireview/js.py:44`). 재사용 UI와 접근성 속성 토글에 수동 JS가 필요하다.

**범위** — 속성 유무/두 값 토글, 안전한 attr 명령 실행, 체인 복사/결합, 상대 대상 선택자를 추가한다. 기존 to_json을 재사용해 이벤트 payload 인코딩 편의 API도 설계한다.

**완료 기준** — 결합이 원본 체인을 변형하지 않는다. 순환 exec는 제한하며 모든 속성 변경은 sticky를 유지한다. 기존 JSON 인코딩을 미구현으로 취급하지 않는다.

**테스트 방법** — 빌더·직렬화·멀티 target·중첩 closest·없는 attr·악성 JSON·재렌더 E2E.

**호환 영향** — 기존 CSS 문자열·JSON 객체 배열 형식 유지; 새로운 자동 객체 인코딩은 명시적 경로로 추가.

**프로토콜 버전 영향** — 새 JS 명령/selector 구조는 구 bundle에 보내지 않는다. 서버 emitted 명령에 버전 gate와 대체 동작을 둔다.


## GAP-042: 포커스 스택과 접근성 포커스 범위

라벨: `enhancement`, `type: feature-gap`, `area: js-interop`, `P1-core`

Epic: E1. 추천 릴리스: 1.7. 의존 관계: GAP-041과 공통 selector 공유.

**관찰·중요성** — focus_first는 제한된 selector이며 push/pop focus·focus_wrap가 없다(`wireview/static/wireview/wireview.js:3763`). 모달 복귀 포커스와 키보드 순환을 일관되게 만들기 어렵다.

**범위** — focus stack·정리·유효 focusable 탐색과 선택적 focus-wrap UI를 제공한다.

**완료 기준** — 중첩 모달을 닫으면 유효한 이전 요소로 돌아간다. 숨은 조상/disabled/inert는 건너뛰고 href 링크를 포함한다. view teardown은 stack을 비운다.

**테스트 방법** — 실제 브라우저 Tab/Shift-Tab·요소 제거·이동·sticky·중첩 모달 E2E; 접근성 트리와 activeElement 검사.

**호환 영향** — 기존 focus_first 개선으로 대상이 바뀔 수 있음을 릴리스 노트에 명시. 기존 함수를 갑자기 trap으로 바꾸지 않는다.

**프로토콜 버전 영향** — push/pop 신규 명령은 버전 gate. focus 탐색만 바뀌면 frame 변경 없음.


## GAP-043: 트랜지션 단계·시간·패치 차단 계약

라벨: `enhancement`, `type: feature-gap`, `area: js-interop`, `P1-core`

Epic: E1. 추천 릴리스: 1.7 opt-in; 기본 전환 2.0 검토. 의존 관계: 없음.

**관찰·중요성** — 단일 클래스/시간만 받고 await 체인이 다음 push를 지연한다(`wireview/js.py:32`, `wireview/static/wireview/wireview.js:3917`). 시간 생략/0은 클래스가 남는다.

**범위** — transition/start/end 클래스·blocking·시작/종료 이벤트·computed visibility와 display 기본 정책을 추가하고 명령 시작과 transition 완료를 분리한다.

**완료 기준** — 새 모드에서는 transition 다음 push가 즉시 시작된다. patch blocking 여부가 독립적이며 0ms·취소·요소 제거에도 클래스/잠금이 남지 않는다.

**테스트 방법** — 가상 clock 순서 테스트 + 실제 CSS E2E. 이벤트 순서·서버 ack·재렌더·hidden/CSS visibility를 검사한다.

**호환 영향** — 현재 지연에 의존하는 앱을 위해 기존 의미 유지 옵션과 이행 문서를 둔다. 기본값 무단 변경 금지.

**프로토콜 버전 영향** — 새 transition 구조와 blocking 의미는 capability로 협상. 구 client용 명령으로 의미를 보장 못 하면 명시적으로 거부한다.


## GAP-044: JS push·dispatch와 서버 이벤트 확장

라벨: `enhancement`, `type: feature-gap`, `area: js-interop`, `P2-advanced`

Epic: E1. 추천 릴리스: 1.8. 의존 관계: GAP-038, GAP-041, GAP-043.

**관찰·중요성** — push는 첫 컴포넌트 루트 target만, dispatch는 CustomEvent만이며 loading/page_loading/done/window 서버 이벤트가 없다(`wireview/static/wireview/wireview.js:3778`, `:3815`, `:743`).

**범위** — 소유권 기반 다중 target, loading selector/ref, 페이지 로딩 신호, dispatch 완료 계약, window 서버 이벤트와 패치 전 옵션을 추가한다. viewport JS 체인도 연결한다.

**완료 기준** — 실패/timeout도 잠금을 풀며 복수 대상 결과가 섞이지 않는다. before 이벤트는 patch 전에, 기본 이벤트는 뒤에 전달된다. click의 native 의미를 명확히 한다.

**테스트 방법** — 다중 component/실패/재연결/이동 중 push·blocking dispatch 완료 누락·viewport 중복 E2E.

**호환 영향** — 기존 component 한정 push_event와 CustomEvent 경로 유지. window 방송은 민감 payload의 기존 범위를 넓히므로 opt-in.

**프로토콜 버전 영향** — 새 frame 옵션은 구 클라이언트가 무시하지 않도록 gate. loading/target/dispatch 신규 명령 capability 필요.


## GAP-045: 선택적 DOM 속성 보존과 포커스 패치

라벨: `enhancement`, `type: feature-gap`, `area: js-interop`, `P1-core`

Epic: E1. 추천 릴리스: 1.7. 의존 관계: GAP-041.

**관찰·중요성** — wire-update=ignore는 data-*까지 무시하고 ignore_attributes/patch-focused가 없다(`wireview/static/wireview/wireview-boost.js:165`, `wireview/static/wireview/values.mjs:52`). 위젯 데이터 전달과 custom control 패치가 제한된다.

**범위** — 속성별·wildcard 보존, data 병합 opt-in, focused control 서버 우선 패치 속성을 설계한다.

**완료 기준** — 보존할 속성과 서버 data가 충돌 없이 갱신된다. focused opt-in만 서버 DOM이 우선하고 IME 기본 보호는 유지된다.

**테스트 방법** — 위젯 ignore·data 삭제·wildcard·dialog open·form-associated custom element·IME E2E.

**호환 영향** — 현재 ignore 전체 보존은 기본으로 유지한다. 기존 사용자의 dataset을 자동으로 덮지 않는다.

**프로토콜 버전 영향** — 새 JS 명령/DOM 옵션은 bundle gate 필요. 기존 morph만 유지할 때 frame 변경 없음.


## GAP-046: 훅 컨텍스트와 요소별 갱신 수명주기

라벨: `enhancement`, `type: feature-gap`, `area: js-interop`, `P1-core`

Epic: E1. 추천 릴리스: 1.7/1.8 단계 배포. 의존 관계: GAP-041, GAP-047; upload 편의는 GAP-053.

**관찰·중요성** — pushEvent는 콜백뿐이고 unsubscribe/targeting이 없으며 beforeUpdate는 인자 없이 컴포넌트 전체에 호출된다(`wireview/static/wireview/wireview.js:1642`, `:1368`). 동적 hook 교체와 class 정의도 없다.

**범위** — Promise 오류/응답 ref·pushEventTo·removeHandleEvent·js()/upload 편의·class 등록·동적 이름 교체·beforeUpdate(toEl)를 단계별로 추가한다.

**완료 기준** — 응답은 올바른 호출로 가며 timeout/disconnect는 reject한다. 바뀐 요소만 toEl을 받고 교체 훅은 한 번 destroy/mount된다. disconnected 중복 콜백을 억제한다.

**테스트 방법** — 콜백 예외 보존·늦은 응답·복수 target allSettled·unsubscribe·중첩/이동/ignore data·runtime hook E2E.

**호환 영향** — 콜백 API 유지. 기존 beforeUpdate 빈도에 의존하는 앱은 새 요소별 모드로 이행 안내. 전역 DOM 콜백은 이미 지원함.

**프로토콜 버전 영향** — 기존 hook ref를 우선 재사용. 실패 frame 추가가 필요하면 capability gate와 구 서버 timeout 동작을 명시한다.


## GAP-047: 연결 metadata와 클라이언트 확장 설정

라벨: `enhancement`, `type: feature-gap`, `area: transport`, `P2-advanced`

Epic: E1. 추천 릴리스: 1.8. 의존 관계: 없음; GAP-040/046과 공동 설계.

**관찰·중요성** — LiveSocket params/metadata/bindingPrefix에 대응하는 공통 설정이 없다(`wireview/static/wireview/wireview.js:160`). 요청 정보는 일부 있으나 연결 시 사용자 metadata를 읽기 어렵다.

**범위** — 타입이 있는 초기화 옵션·재연결 params 공급·이벤트 metadata·서버 connect info를 제공한다. prefix 변경은 기존 CSP DSL과 함께 검토한다. 수동 연결 제어·storage/logger 주입·로딩/IME 옵션·DOM patch/selector 콜백·양방향 지연 시뮬레이션도 하위 작업으로 나눈다. transport 교체는 기존 WebSocket 정책상 제외한다.

**완료 기준** — 재연결마다 params가 갱신되고 신뢰할 수 없는 값으로 검증된다. user/session·CSRF/origin 경계와 충돌하지 않는다.

**테스트 방법** — 설정 검증·민감 필드 로그 제외·변조 payload·재연결·서버/클라이언트 버전 조합 테스트.

**호환 영향** — 기존 자동 초기화와 기본 prefix 유지. 임의 metadata가 인증 정보를 덮지 못하게 한다.

**프로토콜 버전 영향** — join envelope 확장은 capability 협상 필요. 구 서버는 새 필드 없이 접속하고 필수 기능은 명확히 거부한다.


## GAP-048: 폼 직렬화·used-input·reset 의미 보완

라벨: `enhancement`, `type: feature-gap`, `area: forms`, `P1-core`

Epic: E2. 추천 릴리스: 1.7. 의존 관계: GAP-040과 타이머 조율.

**관찰·중요성** — name 전체 열거 serializer가 disabled/submitter를 구별하지 않고 _target/_unused_도 없다(`wireview/static/wireview/wireview.js:1580`). touched는 페이지 전역 이름 Set다.

**범위** — successful controls·submitter·폼/입력 scope·used-input·no-unused·reset/invalid number 처리와 Django 이름 규약을 통합한다.

**완료 기준** — 서로 다른 폼의 같은 이름이 피드백을 공유하지 않는다. disabled/누르지 않은 버튼은 빠지고 올바른 submitter만 포함된다. _target과 opt-out이 명세와 일치한다.

**테스트 방법** — 실제 FormData 대비 표 기반 테스트·multiple select/checkbox/disabled fieldset/form 연결 속성·nested names·reset·number·IME E2E.

**호환 영향** — 기존 dot/index 이름을 유지하고 Phoenix bracket 문법은 별도 지원 모드로 설계. strict handler에 새 kwargs를 무조건 주입하지 않는다.

**프로토콜 버전 영향** — 이벤트 메타데이터/serializer 모드를 협상해야 한다. 구 서버에 기존 payload를 보내는 경로를 남긴다.


## GAP-049: 재연결과 컴포넌트 재시작의 폼 복구

라벨: `enhancement`, `type: feature-gap`, `area: forms`, `P1-core`

Epic: E2. 추천 릴리스: 1.7. 의존 관계: GAP-048.

**관찰·중요성** — recoverForms는 socket reconnect의 wire-auto-recover opt-in만 처리한다(`wireview/static/wireview/wireview.js:1111`, `:199`). ignore도 비활성 예약값이 아니다.

**범위** — 컴포넌트 crash 재join·폼 ID 추적·custom recover·명시적 비활성·복구 완료 순서를 추가한다.

**완료 기준** — 같은 폼 입력이 새 인스턴스에 한 번 복구되고 다른 사용자의 폼으로 전달되지 않는다. 빈 복구 markup도 join을 끝낸다.

**테스트 방법** — 네트워크 끊김과 component crash를 별도로 재현. 폼 제거/ID 변경/비활성/중복 ID/업로드 필드 E2E.

**호환 영향** — 기존 opt-in 기본 유지. 현재 ignore라는 핸들러를 쓰는 앱을 위해 새 명시적 비활성 문법 또는 deprecation 제공.

**프로토콜 버전 영향** — 복구 완료 handshake 추가 시 capability 필요. 기존 on reconnect 경로는 구 서버와 유지한다.


## GAP-050: 서버 HTTP 폼 전환과 제출·업로드 잠금

라벨: `enhancement`, `type: feature-gap`, `area: forms`, `P1-core`

Epic: E2. 추천 릴리스: 1.8. 의존 관계: GAP-048, GAP-049, GAP-052.

**관찰·중요성** — 서버 trigger-action이 없고 일부 버튼만 잠근다. auto_upload=False를 submit과 연결하지 않는다(`wireview/static/wireview/wireview.js:3170`, `:2561`). 이중 제출·너무 이른 제출을 앱이 직접 막아야 한다.

**범위** — 명시적 서버→HTTP 제출, submitter 유지, 입력/버튼 잠금·복원, 업로드 완료/오류와 예약 submit 연동을 구현한다.

**완료 기준** — 유효 업로드 완료 전 핸들러는 실행되지 않고 실패/취소 시 예약 submit이 취소된다. HTTP 전환은 한 번만 발생하고 CSRF/목적지 정책을 지킨다.

**테스트 방법** — 느린 업로드·invalid entry·cancel·server error·disconnect·여러 submitter·원래 disabled/readonly 상태 E2E.

**호환 영향** — 기존 wire-boost 제출과 구분하고 새 opt-in 경로로 제공. auto_upload 기본 True 유지.

**프로토콜 버전 영향** — 서버 지시/업로드 상태와 client gate가 함께 필요. 구 번들에는 서버 submit 명령을 보내지 않는다.


## GAP-051: 스트림 signed limit·구성·정적 자식 보존

라벨: `enhancement`, `type: feature-gap`, `area: components`, `P1-core`

Epic: E3. 추천 릴리스: 1.7 opt-in. 의존 관계: 없음.

**관찰·중요성** — limit<=0은 trim하지 않고 reset은 모든 자식을 지운다. dom_id는 호출마다 전달한다(`wireview/static/wireview/streams.mjs:72`, `wireview/static/wireview/wireview.js:901`, `wireview/core/component.py:1473`).

**범위** — signed limit 모드, stream_configure, 객체→dom id delete, non-stream child 보존 규약을 추가한다.

**완료 기준** — 양/음 limit·at 조합이 명세대로 남기며 non-stream placeholder는 reset 뒤에도 유지된다. update_only와 기존 batch 순서는 계속 맞는다.

**테스트 방법** — 순수 계획 함수의 경계값·중복 id·동적 config 거부·reset/placeholder·구버전 stream E2E.

**호환 영향** — 현재 양수 limit/기본 reset=True는 보존. Phoenix 모드는 opt-in 후 별도 기본 전환 검토.

**프로토콜 버전 영향** — 기존 limit에 음수만 보내면 구 client가 무제한으로 해석한다. 새 모드 capability/프로토콜 gate 필수.


## GAP-052: 업로드 검증·진행률·소비 제어

라벨: `enhancement`, `type: feature-gap`, `area: uploads`, `P1-core`

Epic: E2. 추천 릴리스: 1.7/1.8. 의존 관계: 없음.

**관찰·중요성** — accept는 확장자만 검사하고 progress/validator/max_entries_mode/disallow/단일 postpone consume가 없다(`wireview/features/uploads.py:255`, `:479`, `wireview/core/component.py:1696`).

**범위** — MIME/wildcard 판정, 사용자 validator, selected/total 개수 정책, progress·disallow·단일 소비/유예 API를 추가한다.

**완료 기준** — 실패 entry와 소비 entry가 선택 모드별로 정확히 계산된다. 유예 파일은 남고 최종 소비/취소는 정리된다. 서버 callback은 entry 소유권을 검증한다.

**테스트 방법** — 위조 client MIME·확장자·재등록·다중 entry·progress 순서·callback 실패·다중 worker 파일 정리 테스트.

**호환 영향** — 빈 accept와 확장자 동작 유지. 타입 검증을 콘텐츠 안전 보장으로 과장하지 않는다. 기존 consume 자동 정리 유지.

**프로토콜 버전 영향** — 서버 전용 API는 버전 변경 불필요. client config/새 상태·유예 지시를 보낼 경우 capability gate 필요.


## GAP-053: 외부 uploader·writer·청크 timeout 확장

라벨: `enhancement`, `type: feature-gap`, `area: uploads`, `P2-advanced`

Epic: E2. 추천 릴리스: 1.8. 의존 관계: GAP-052, GAP-047.

**관찰·중요성** — 외부 업로드는 고정 XHR url/method/headers이며 사용자 registry/onCancel/writer/청크 timeout이 없다(`wireview/static/wireview/wireview.js:2725`, `wireview/core/component.py:1532`).

**범위** — custom uploaders와 progress/error/cancel 계약, 이동 시 정리, 서버 writer 및 유휴 timeout을 제공한다. multipart 자체는 플러그인 예제로 한정한다.

**완료 기준** — 취소·이동·timeout마다 writer/uploader가 한 번 정리되고 실패가 entry 오류로 전달된다. metadata 실패도 유효 entry 진행을 막지 않는다.

**테스트 방법** — 가짜 uploader/writer 실패 주입·네트워크 지연·취소·다중 worker·token 만료와 청크 timeout 분리·외부 progress E2E.

**호환 영향** — 기존 XHR을 기본 uploader로 유지. 임의 JS 코드를 metadata에서 실행하지 않고 등록 이름만 허용한다.

**프로토콜 버전 영향** — uploader 선택/취소 신호·writer 오류 schema 협상 필요. 구 bundle은 미지원 uploader를 시작하지 않는다.


## GAP-054: 비동기 assign 재로딩·취소와 stream_async

라벨: `enhancement`, `type: feature-gap`, `area: components`, `P2-advanced`

Epic: E3. 추천 릴리스: 1.8. 의존 관계: GAP-051.

**관찰·중요성** — assign_async는 단일 coroutine/새 AsyncResult이고 cancel은 이름만 받는다. stream_async가 없다(`wireview/core/component.py:1208`, `:1154`, `wireview/async_result.py:41`).

**범위** — 키 또는 키 묶음 관리, 이전 성공 유지/reset, assign 취소·이유, stream_async를 구현한다.

**완료 기준** — 늦은 이전 task가 새 결과를 덮지 않고 삭제된 컴포넌트는 렌더하지 않는다. 재로딩 중 성공 값 유지와 reset이 구별된다.

**테스트 방법** — 이벤트 장벽으로 race를 결정론적으로 재현. falsy key·다중 키 실패·취소·stream reset/limit·제거 중 handle_async 테스트.

**호환 영향** — 현재 coroutine API 유지. AsyncResult 상태 확장은 기존 loading/ok/failed 사용과 직렬화를 고려해 새 모드로 제공.

**프로토콜 버전 영향** — 서버 orchestration만은 frame 변경 없음. AsyncResult state schema와 stream 옵션 확장은 old state/client 조합을 검사하고 필요 시 gate.


## GAP-055: 함수 attr·global과 슬롯 entry 선언

라벨: `enhancement`, `type: feature-gap`, `area: components`, `P2-advanced`

Epic: E3. 추천 릴리스: 1.8. 의존 관계: 없음.

**관찰·중요성** — 함수 signature는 기본 coercion만 하며 global/values 선언과 slot attrs가 없다(`wireview/function_components.py:231`, `wireview/templatetags/wireview.py:670`). 선언형 재사용 컴포넌트의 오타를 잡기 어렵다.

**범위** — Python식 attr/slot metadata, values/examples/doc/global include/prefix, entry 속성·검증과 편집기 export를 구현한다. 외부 template 일괄 등록 편의도 선언 metadata를 재사용하는 opt-in으로 검토한다.

**완료 기준** — required/default/unknown/global/slot attrs의 오류 위치가 정확하고 문서·진단·실행 검증이 같은 metadata를 사용한다. **kwargs의 기존 허용도 유지한다.

**테스트 방법** — parser/coercion/정적·동적 값 검증·slot let 범위·반복 entry·진단 metadata snapshot. global 목록은 1.2 기준을 명시.

**호환 영향** — 기존 함수 decorator/signature와 슬롯 문법을 유지. 기본 coercion을 조용히 strict 검증으로 바꾸지 않는다.

**프로토콜 버전 영향** — 서버 템플릿·편집기 metadata 확장이며 기존 rendered frame이면 프로토콜 증가 불필요.


## GAP-056: DOM 기반 컴포넌트 테스트와 경고 설정

라벨: `enhancement`, `type: feature-gap`, `area: devtools`, `P1-core`, `testing`

Epic: E4. 추천 릴리스: 1.7. 의존 관계: GAP-048/049의 계약과 조율.

**관찰·중요성** — MountedComponent.call은 핸들러만 직접 호출하며 DOM binding을 검사하지 않는다(`wireview/testing.py:938`). 중복 ID/복구 폼 ID 경고 정책도 없다.

**범위** — element/form, 바인딩 기반 click/change/submit/key/focus/blur/hook, child lookup, HTTP 제출·submitter와 test_warnings를 제공한다.

**완료 기준** — 존재하지 않거나 잘못 연결된 selector/이벤트는 실패하고 올바른 nested owner로 전달한다. 검사별 raise/warn/ignore와 폼별 제외가 동작한다.

**테스트 방법** — 의도적으로 깨진 template로 실패 확인; 동일 시나리오를 브라우저와 헬퍼에서 비교한다. native JS 실행을 흉내 낸다고 주장하지 않는다.

**호환 영향** — 기존 mount/call은 유지. 새 기본 경고는 우선 opt-in 또는 명확한 릴리스 안내로 도입한다.

**프로토콜 버전 영향** — 사용자 테스트 API만 변경, wire 버전 변경 없음. 공통 serializer를 재사용해 client/server 괴리를 줄인다.


## GAP-057: 비동기·업로드 테스트와 메시지 단언

라벨: `enhancement`, `type: feature-gap`, `area: devtools`, `P2-advanced`, `testing`

Epic: E4. 추천 릴리스: 1.8. 의존 관계: GAP-052, GAP-054, GAP-056.

**관찰·중요성** — render_async/file_input/preflight/render_upload와 reply/push/title 전용 단언이 없다(`wireview/testing.py:382`). 테스트가 내부 메시지 형식에 결합한다.

**범위** — 관리 task 완료 대기, upload protocol 구동, 의미 기반 reply/event/title 단언을 공개한다.

**완료 기준** — 불필요한 sleep 없이 async 결과를 기다리고 timeout 원인이 보인다. 복수 파일/취소/거절 업로드를 실제 서버 경로로 검증한다.

**테스트 방법** — 프로토콜 메시지 key를 바꾸어도 공개 단언 테스트 유지; timeouts·누수·복수 entry·false 성공 검사.

**호환 영향** — sent_messages 목록은 유지하되 메시지 구조는 공개 계약으로 승격하지 않는다.

**프로토콜 버전 영향** — 헬퍼가 기존 서버 경로를 호출하므로 wire 버전 증가 없음; 지원 서버 capability에 따른 테스트 오류는 명확히 한다.


## GAP-058: 서버 lifecycle 확장과 예약 child update

라벨: `enhancement`, `type: feature-gap`, `area: components`, `P2-advanced`

Epic: E3. 추천 릴리스: 1.8. 의존 관계: GAP-054.

**관찰·중요성** — attach_hook은 세 단계만, send_update는 즉시 갱신만 제공한다(`wireview/core/component.py:602`, `:1737`). 공통 async 관찰·예약 UI 갱신을 개별 task로 처리해야 한다.

**범위** — Python 이벤트 모델에 맞는 async/info hook 및 reply halt, 취소 가능한 send_update_after를 추가한다.

**완료 기준** — halt/reply가 중복 render나 이벤트를 만들지 않고 예약 작업은 부모·자식 제거 시 취소된다. ID 재사용으로 새 자식에 낡은 update를 보내지 않는다.

**테스트 방법** — lifecycle 순서·세대별 예약 update·제거/재join·hook 실패를 장벽/가상 clock으로 검사.

**호환 영향** — 기존 hook 순서 유지. BEAM PID/GenServer API는 도입하지 않는다.

**프로토콜 버전 영향** — 서버 orchestration만이면 프로토콜 변경 없음. reply 새 형태가 필요하면 기존 hook 응답 schema 재사용 우선.


## GAP-059: 배포 정적 자산 변경 감지

라벨: `enhancement`, `type: feature-gap`, `area: devtools`, `P2-advanced`

Epic: E4. 추천 릴리스: 1.8. 의존 관계: GAP-047.

**관찰·중요성** — 번들 버전 URL과 개발 template reload는 있으나 열린 탭의 static_changed 대응이 없다(`wireview/templatetags/wireview.py:55`, `wireview/core/template_reload.py:1`). 장시간 열린 탭에서 구 asset 여부를 판단하기 어렵다.

**범위** — 추적 자산 manifest digest와 join 비교, 서버 조회/클라이언트 갱신 안내를 제공한다.

**완료 기준** — 동일 manifest는 조용하고 새 배포는 감지한다. 개발 reload와 production 신호가 분리되며 자동 무한 reload를 만들지 않는다.

**테스트 방법** — 두 manifest 배포·rolling server·캐시된 HTML·offline/reconnect·구 client/새 server 매트릭스.

**호환 영향** — 기본 자동 강제 reload 대신 앱이 선택하는 신호 제공. 기존 버전 쿼리 유지.

**프로토콜 버전 영향** — join metadata/응답 capability 필요. 구 client는 기존 방식으로 계속 연결한다.


## GAP-060: 렌더 의존성 추적과 DOM 이동 보존 검증

라벨: `enhancement`, `type: feature-gap`, `area: performance`, `P2-advanced`

Epic: E5. 추천 릴리스: 1.9 연구 후 분할. 의존 관계: GAP-055; stream 부분 GAP-051.

**관찰·중요성** — 일반 dynamic 표현식은 매 render 평가 후 diff한다(`wireview/template_engine.py:122`). stream은 native moveBefore를 쓰지 않는다. 출력 크기 최적화와 계산 생략/DOM 상태 보존은 다르다.

**범위** — 일반 assign dependency 추적의 안전한 opt-in 설계와 별도 native moveBefore 적용 실험을 한다. 큰 구현이면 두 하위 이슈로 분할한 뒤 착수한다.

**완료 기준** — 미변경 dependency는 평가하지 않고 nested mutation/조건부/슬롯에서 stale UI를 만들지 않는다. 이동 시 focus/iframe/animation 상태를 보존하며 fallback이 있다.

**테스트 방법** — 계산 호출 횟수 회귀·differential 렌더·실제 DOM 이동 E2E. 성능 주장은 make bench-compare와 원본 결과 기반 차트로 검증.

**호환 영향** — 내용 기반 목록 matching·temporary/shared_render를 유지. 자동 전체 적용 전에 부작용 template의 fallback을 정한다.

**프로토콜 버전 영향** — 평가 최적화/DOM 이동만이면 protocol 11 유지 가능. diff encoding을 바꾸면 버전 증가 및 mixed-version 검증 필수.


## GAP-061: 컴포넌트 자산 colocate와 정적 hook 도구

라벨: `enhancement`, `type: feature-gap`, `area: devtools`, `P3-polish`

Epic: E4. 추천 릴리스: 1.9. 의존 관계: GAP-046, GAP-055.

**관찰·중요성** — 외부 static 훅 수집만 있고 ColocatedHook/JS/CSS와 TagFormatter·module debug 제외가 없다(`wireview/features/hooks.py:42`). 정적 루트 밖 hook도 없다.

**범위** — Django 템플릿의 JS/CSS 추출 manifest, formatter 확장, 컴포넌트별 debug annotation 정책, 정적 mounted hook를 단계별 하위 작업으로 만든다.

**완료 기준** — 같은 자산이 중복되지 않고 이름 충돌/재빌드가 결정적이다. CSP nonce·외부 파일 정책을 지키며 정적 hook는 서버 없는 환경에서 명시적으로 제한된다.

**테스트 방법** — 수집/build/cache invalidation·JS와 CSS 동시 사용·strict CSP·SSR/static page·formatter 실패 테스트.

**호환 영향** — 기존 static/<app>/hooks 경로 유지. inline 실행을 새 기본으로 만들지 않는다. Python/HEEx 문법 복제는 제외.

**프로토콜 버전 영향** — 빌드 산출물·정적 hook client 기능의 최소 bundle 버전 필요. rendered frame 자체는 유지 가능.


## GAP-062: 명시적 patch 내비게이션과 목적지 분류

라벨: `enhancement`, `type: feature-gap`, `area: navigation`, `P2-advanced`

Epic: E3. 추천 릴리스: 1.8. 의존 관계: GAP-066.

**관찰·중요성** — 같은 path 규칙으로 patch를 고르며 JS/link patch와 navigation_type API가 없다(`wireview/core/meta.py:394`, `wireview/js.py:432`). 같은 뷰의 다른 경로로 params만 바꾸려는 의도가 표현되지 않는다.

**범위** — 명시적 patch/navigate 선택·JS/link API·서버 목적지 분류를 Django resolver/live_session 경계와 연결한다.

**완료 기준** — 동일 뷰 다른 경로는 명시적 patch가 가능하고 다른 인증 경계는 거부/전체 이동한다. history·replace·scroll·취소가 기존 계약과 맞는다.

**테스트 방법** — 같은 path/다른 view·다른 path/같은 view·경계 이동·popstate·URL 정책 E2E와 follow_push 헬퍼 비교.

**호환 영향** — 기존 push_to/replace_to 자동 규칙 유지. 이동 취소는 이미 있어 중복 구현하지 않는다.

**프로토콜 버전 영향** — url_change mode와 새 JS patch command는 capability gate. 구 client에서 full navigation 대체가 허용되는지 앱이 선택한다.


## GAP-063: 플래시의 서버 상태와 이동 전달

라벨: `enhancement`, `type: feature-gap`, `area: navigation`, `P2-advanced`

Epic: E3. 추천 릴리스: 1.8. 의존 관계: GAP-062.

**관찰·중요성** — put_flash는 client 표시 메시지이며 서버 kind map/redirect carry가 없다(`wireview/core/meta.py:421`). 저장 후 다른 페이지로 옮기는 메시지를 신뢰하기 어렵다.

**범위** — 선택적 서버 flash 상태·kind별 clear·단회 이동 전달과 session 경계 정책을 추가한다.

**완료 기준** — 한 번 이동한 메시지가 새 페이지에 한 번 나타나며 뒤로/재연결에서 중복되지 않는다. 다른 세션/탭으로 새지 않는다.

**테스트 방법** — live/HTTP 이동·새 탭·실패 이동·clear·중복 응답·session 경계 테스트.

**호환 영향** — 기존 client flash/toast API 유지; 서버 보관은 opt-in. 메시지 수명과 다중 탭 정책 문서화.

**프로토콜 버전 영향** — 이동 payload/cookie 등 전달 채널의 schema 필요. 구 client는 기존 flash 표시를 유지하고 새 carry를 무시하지 않게 gate.


## GAP-064: DOM 포털과 컴포넌트 소유권

라벨: `enhancement`, `type: feature-gap`, `area: components`, `P2-advanced`

Epic: E5. 추천 릴리스: 1.9. 의존 관계: GAP-039, GAP-045, GAP-046.

**관찰·중요성** — portal 태그/teleport patcher가 없다(`wireview/templatetags/wireview.py:1`, `wireview/static/wireview/wireview.js:1368`). overflow 밖 모달을 옮기면 기존 DOM 소유권 규칙이 깨질 수 있다.

**범위** — 명시적 source/unique target 포털·patch·hook/event ownership·제거/stream reset·namespace 보존을 구현한다.

**완료 기준** — 옮긴 노드는 원 component로 이벤트를 보내고 target 변경·삭제·재join 뒤 중복되지 않는다. SVG 등 namespace를 유지한다.

**테스트 방법** — portal nested hook/form/stream reset·boost 이동·target 없음/중복·SVG E2E 및 SSR/test helper 동작 문서화.

**호환 영향** — 기존 수동 DOM 이동은 자동 포털로 취급하지 않는다. 전용 새 API에만 적용.

**프로토콜 버전 영향** — 새 template markers/client ownership 경로를 구 bundle이 모르므로 지원 버전에서만 렌더; 필요하면 protocol bump.


## GAP-065: 비동기·title·동적 태그 UI 편의 컴포넌트

라벨: `enhancement`, `type: feature-gap`, `area: components`, `P3-polish`

Epic: E3. 추천 릴리스: 1.9. 의존 관계: GAP-054, GAP-055.

**관찰·중요성** — AsyncResult와 push_title은 있으나 async_result 슬롯 UI·live_title prefix/suffix·dynamic_tag 내장 API는 없다(`wireview/async_result.py:41`, `wireview/core/meta.py:413`).

**범위** — 작은 표준 컴포넌트 세 개를 공통 attr/slot 선언 위에 제공한다. focus_wrap은 GAP-042 소유로 분리한다. dynamic attributes 필터·escape helper는 dynamic_tag와 함께 제공한다.

**완료 기준** — loading/error/success 슬롯이 정확하고 title default/prefix/suffix 조합이 일관된다. dynamic tag는 이름 검증·escape를 지킨다.

**테스트 방법** — 컴포넌트 렌더·XSS/잘못된 tag·빈 title·async 재로딩 테스트와 사용 문서.

**호환 영향** — 사용자 컴포넌트 이름 충돌을 피하는 namespace. 기존 수동 템플릿 동작 유지.

**프로토콜 버전 영향** — 기존 HTML/title 명령 사용 시 wire 변경 없음.


## GAP-066: 내비게이션 URL 검증 정책 감사와 회귀 방어

라벨: `audit`, `security`, `area: navigation`, `P0-foundation`, `type: feature-gap`

Epic: 독립. 추천 릴리스: 1.7 선행; 결함 확인 시 패치 우선. 의존 관계: 없음.

**관찰·중요성** — 목적지 resolve_url과 client location.assign 사이에 공통 scheme/control-character 검증층이 보이지 않는다(`wireview/core/meta.py:87`, `wireview/static/wireview/wireview-boost.js:553`). Phoenix 1.2의 보안 수정을 계기로 입력 경계를 확인해야 한다. 현재 악용 재현은 하지 않았다.

**범위** — 서버 redirect/push/replace·JS navigate·visit·HTTP redirect 경로를 감사하고 허용 scheme/동일 origin/외부 목적지 정책을 문서화·통일한다.

**완료 기준** — 위험 scheme·ASCII tab/LF/CR·scheme-relative/backslash 입력이 각 공개 API에서 일관되게 거부되거나 명시적 외부 이동 정책을 따른다. 안전 URL·view reverse·query/fragment는 유지된다.

**테스트 방법** — 브라우저 URL parser와 Django resolver differential 표·악성 입력 회귀·CSP 유무·인코딩 경계 테스트. 재현 없이는 취약점 확정/심각도 공표하지 않는다.

**호환 영향** — 기존 외부 이동 기능을 무조건 막지 말고 허용 API를 분리한다. 거부되는 기존 입력은 보안 변경으로 명시한다.

**프로토콜 버전 영향** — 검증만 추가하면 wire 형식 변경 없음. 구 client에도 server 검사 적용하되 client-only navigate는 번들 갱신 필요.


## GAP-067: lazy assign·변경 조회·렌더러 선택 API 설계

라벨: `enhancement`, `type: feature-gap`, `area: components`, `P3-polish`

Epic: E5. 추천 릴리스: 1.9 설계 후 채택. 의존 관계: GAP-060.

**관찰·중요성** — 필드/default/render override는 있지만 assign_new/changed?/render_with의 명시적 대응이 없다(`wireview/core/component.py:307`, `wireview/template_engine.py:105`). 매번 계산과 공유 초기값의 경계를 앱이 결정한다.

**범위** — 기존 Meta.lazy_properties(렌더별 property 계산)를 재구현하지 않고 Python field 모델에 맞는 lazy default/shared initial value·dirty 조회·runtime renderer 선택의 필요성을 예제로 검증하고 최소 API를 구현한다.

**완료 기준** — 비싼 기본값을 필요할 때 한 번 계산하고 요청/사용자 간 공유가 새지 않는다. dirty 조회가 실제 렌더 dependency와 일치하며 renderer 교체가 fingerprint를 갱신한다.

**테스트 방법** — 요청 격리·nested mutable field·SSR→join·renderer 교체·사용되지 않는 기본값 미평가 테스트.

**호환 영향** — BaseModel 기본값/Pydantic validation과 충돌하면 독립 opt-in helper로 제공. 이름만 복제하는 API는 채택하지 않는다.

**프로토콜 버전 영향** — 서버 API만이면 기존 frame 사용. renderer static fingerprint 형식을 바꾸면 protocol gate 필요.

## 구현 후보에서 제외하거나 기존 기능으로 남기는 항목

| 항목 | 처리 이유 |
|---|---|
| BEAM transport_pid·GenServer call/cast·PID send_update·Task supervisor·hibernate_after | Python/asyncio/Django에 같은 프로세스 의미가 없어 설계상 제외. 유용한 예약 update/async 취소는 058/054로 별도 이식 |
| HEEx 구문·Ecto changeset·Phoenix Router 자체 | 언어/프레임워크 복제가 목표 아님. attr/slot/form/navigation 의미는 해당 이슈에서 구현 |
| LongPolling | 기존 GAP-012 제품 결정(WebSocket 필수)을 유지. 기술적으로 불가능해서 제외한 것은 아님 |
| .once/.capture/.passive/.self | Phoenix 공개 점 수정자가 아니다. 현 문서 오기는 수정했으며 GAP 번호를 소비하지 않음 |
| 서명 상태와 서버 assigns 차이 | 기존 wireview 모델. 비밀값 제외 규칙을 적용하며 암호화/전면 서버 상태 전환을 이번 parity 작업에 섞지 않음 |
| 명시적 :key와 내용 기반 목록 matching | 이미 다른 최적화가 구현돼 있다. 같은 문법 부재만으로 신규 GAP 생성하지 않음 |
| sticky JS·다중 CSS 대상·update_only·dom.onBeforeElUpdated·이동 취소·to_json | 이미 구현. 하위 옵션 갭만 별도 분류 |
| Phoenix 1.2 패치의 개별 버그·내부 debug tooling | wireview 재현 없이 동일 결함으로 등록하지 않음. 관련 이슈의 회귀 시나리오로만 반영 |

착수 전에 이 초안의 proposed API 이름은 설계 리뷰로 확정한다. 1.2.12와의 의미 차이를 숨기지 않되,
기존 사용자의 계약을 무조건 Phoenix 방식으로 바꾸는 것이 이 로드맵의 목표는 아니다.
