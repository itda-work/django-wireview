# Phoenix LiveView 1.2.12 대비 세부 기능 조사

조사일 2026-10-13, wireview 기준 HEAD `a4e957c4`, 프로토콜 11. 사용자 정정에 따라 **모든 영역을 1.2.12로 재검토**했다.
요청한 hexdocs.pm 주소는 가져오기 오류가 있어 같은 버전의 공식 phoenix-live-view.hexdocs.pm과 v1.2.12 태그 소스를 읽었다.
“있음”은 대응 기능 존재, “부분”은 옵션·실행 의미 차이, “없음”은 공개 계약 부재다. API나 wire 호환을 의미하지 않는다.
부재 판정은 검색뿐 아니라 JS 빌더/명령 switch, HookContext, serializer, Component/MountedComponent 표면을 읽어 확인했다.
경로는 저장소 루트 기준이며 테스트 파일은 읽은 근거다. 이번 실행 범위는 마지막의 문서 테스트뿐이다.

> **이후(2026-10-14):** 훅 영역 일부가 달라졌다. `pushEvent`는 콜백이 없으면 응답의 Promise를 돌려주고 응답이 오지 않으면
> 거절한다([#222](https://github.com/itda-work/django-wireview/issues/222)). `handleEvent`는 해제 함수를 돌려준다.
> 응답은 그 이벤트의 렌더가 반영된 뒤 온다([#223](https://github.com/itda-work/django-wireview/issues/223)). 아래 표의 해당 행은 조사 시점의 판정이다.

## 1.2 변경 기록 재검토

[공식 CHANGELOG](https://raw.githubusercontent.com/phoenixframework/phoenix_live_view/v1.2.12/CHANGELOG.md)를 RC부터 1.2.12까지 확인했다.

| 변경 | wireview 재판정 |
|---|---|
| ColocatedCSS, TagFormatter, 모듈 debug 주석 제외 | 없음, GAP-061 |
| JS JSON payload | to_json/서버 js 전송은 이미 있음. 자동 payload 인코딩은 부분, GAP-041 |
| test_warnings·복구 폼 ID 검사 | 없음, GAP-056 |
| no-unused-field·patch-focused | 서버 사용 여부 전송 / 포커스 패치 opt-in 없음, GAP-048/045 |
| 이동 취소·navigation_type | 취소는 있음, 서버 조회는 없음, GAP-062 |
| beforeUpdate(toEl)·동적 hook·disconnect 중복 억제 | 부분, GAP-046 |
| 외부 업로드 취소·자동제출·0ms transition·focus 스택 정리 | GAP-053/050/043/042에서 최신 계약 반영 |
| URL scheme·제어문자 수정 | 공통 URL 정책 검증 우선, GAP-066. Phoenix CVE를 wireview 취약점으로 단정하지 않음 |
| moveBefore·portal namespace/reset·async 제거·caret·stale diff | 포털/native moveBefore 없음. 나머지는 기존 경로 회귀 대상으로 분류, 같은 버그라고 단정하지 않음 |
| 타입 배포·IE11 문구·formatter 버그·내부 debug 도구 | 공개 기능 GAP로 일괄 환산하지 않음. wireview 타입 선언 있음 |

`max_entries_mode`, `validator`, portal도 1.2.12 공식 공개 표면에서 확인했다. 최초 도입이 변경 기록에 명시되지 않은 기능을
모두 “1.2 신규”라 부르지 않는다. 아래 표가 최종 비교이며 초기 1.1 판정은 대체됐다.

## 바인딩·이벤트

기준: [Bindings](https://phoenix-live-view.hexdocs.pm/1.2.12/bindings.html).

| Phoenix 기능 | wireview 현황과 근거 파일:줄 | 판정 | 메모 |
|---|---|---|---|
| click/focus/blur/keydown/keyup | on 이벤트 위임, `wireview/static/wireview/wireview.js:3222`, `wireview/event_transpiler.py:31` | 있음 | 문법은 다름 |
| click-away | 요소 밖 판정 없음, `wireview/static/wireview/wireview.js:3277` | 없음 | GAP-038 |
| window-keydown/keyup/focus/blur | 루트 DOM 이벤트 위임만, `wireview/static/wireview/wireview.js:3277` | 없음 | GAP-038; 임의 이벤트 이름이 전역 바인딩은 아님 |
| phx-key·조합 키 | key/ctrl/alt/shift/meta 필터, `wireview/static/wireview/events.mjs:117` | 있음 | 서버 key payload와 별개 |
| phx-value-*·key payload | 고정 on kwargs만, `wireview/event_transpiler.py:107`, `wireview/static/wireview/wireview.js:3370` | 부분 | DOM 값/키 정보 자동 수집 없음, GAP-040 |
| 숫자 debounce/throttle | 타이머 있음, `wireview/static/wireview/wireview.js:3350` | 있음 | 각 요소·바인딩 기준 |
| blur debounce·기본 지연·폼 타이머 초기화·다른 키 예외 | 숫자 필수, `wireview/event_transpiler.py:99`, `wireview/static/wireview/wireview.js:3350` | 없음 | GAP-040 |
| mounted/remove/connected/disconnected | 합성 수명주기 바인딩 없음, `wireview/static/wireview/wireview.js:3222` | 없음 | hook은 대체 수단, GAP-039 |
| viewport top/bottom·overrun | observer/handler 있음, `wireview/static/wireview/wireview.js:2062`, `wireview/static/wireview/wireview.js:2238` | 부분 | JS 체인·page_loading 옵션 없음, GAP-044 |
| ignore | 모든 속성과 자식 고정, `wireview/static/wireview/wireview-boost.js:165` | 부분 | Phoenix data-* 병합과 다름, GAP-045 |
| patch-focused | 기본 입력 보존만, `wireview/static/wireview/values.mjs:52` | 없음 | 명시적 서버 우선 패치 없음, GAP-045 |
| track-static | 템플릿의 번들 버전 URL만, `wireview/templatetags/wireview.py:55` | 없음 | 열린 페이지 자산 비교 없음, GAP-059 |
| .once/.capture/.passive/.self | 미지원 수정자 거부, `wireview/event_transpiler.py:31` | 설계상 제외 | Phoenix 공개 점 수정자도 아니므로 비교 오기. 별도 GAP 아님 |

## JS 명령·옵션

기준: [JS](https://phoenix-live-view.hexdocs.pm/1.2.12/Phoenix.LiveView.JS.html),
[실행 소스](https://raw.githubusercontent.com/phoenixframework/phoenix_live_view/v1.2.12/assets/js/phoenix_live_view/js.js).

| Phoenix 기능 | wireview 현황과 근거 파일:줄 | 판정 | 메모 |
|---|---|---|---|
| show/hide/toggle·클래스·속성 | 빌더와 실행 있음, `wireview/js.py:44`, `wireview/static/wireview/wireview.js:3665` | 있음 | 가시성/시간 의미는 아래 차이 |
| 다중 CSS 대상 | querySelectorAll, `wireview/static/wireview/wireview.js:3567` | 있음 | 누락으로 오판하지 않음 |
| toggle_attribute | set/remove만, `wireview/js.py:44` | 없음 | 유무/두 값 토글, GAP-041 |
| exec/concat | 빌더 메서드 없음, `wireview/js.py:44` | 없음 | 전역 wireview.exec는 다른 API, GAP-041 |
| inner/closest | 문자열 선택자만, `wireview/static/wireview/wireview.js:3567` | 없음 | GAP-041 |
| focus/focus_first | 단순 input/button/tabindex 탐색, `wireview/static/wireview/wireview.js:3763` | 부분 | href 링크·숨은 조상 필터 차이, GAP-042 |
| push_focus/pop_focus | 스택 없음, `wireview/static/wireview/wireview.js:3665` | 없음 | GAP-042 |
| ignore_attributes | 명령 없음, `wireview/static/wireview/wireview.js:3665` | 없음 | 속성별/와일드카드, GAP-045 |
| transition/start/end 세 묶음·blocking | 단일 클래스+시간, `wireview/js.py:32`, `wireview/static/wireview/wireview.js:3605` | 부분 | GAP-043 |
| 기본/0ms·다음 push 실행 시점 | await 체인, `wireview/static/wireview/wireview.js:3917` | 부분 | 시간 있는 transition 뒤 push 지연. 시간 없으면 클래스 잔류, GAP-043 |
| show/hide 이벤트·가시성 | hidden 기준, `wireview/static/wireview/wireview.js:3684` | 부분 | 시작/종료 이벤트 없음, GAP-043 |
| push value/target | 값과 첫 루트 대상, `wireview/static/wireview/wireview.js:3778` | 부분 | 자손 소유권·다중 대상 없음, GAP-044 |
| push loading/page_loading | 해당 옵션 없음, `wireview/js.py:400` | 없음 | GAP-044 |
| navigate/replace/patch | navigate 있음, `wireview/js.py:432` | 부분 | 명시적 patch 없음, GAP-062 |
| dispatch detail/bubbles | CustomEvent, `wireview/static/wireview/wireview.js:3815` | 있음 | click도 CustomEvent |
| dispatch blocking/done/dispatcher | 완료 계약 없음, `wireview/static/wireview/wireview.js:3815` | 없음 | GAP-044 |
| JSON 인코딩/서버 전송 | to_json·js 전송, `wireview/js.py:488`, `wireview/core/meta.py:708` | 부분 | 임의 payload 객체 자동 인코딩은 없음. GAP-041 |
| 패치 뒤 명령 효과 | sticky 재적용, `wireview/static/wireview/sticky.mjs:1`, `wireview/static/wireview/wireview.js:935` | 있음 | GAP-037 중복 이슈 불필요 |

## 훅·LiveSocket 옵션

기준: [ViewHook](https://raw.githubusercontent.com/phoenixframework/phoenix_live_view/v1.2.12/assets/js/phoenix_live_view/view_hook.ts),
[JS interop](https://raw.githubusercontent.com/phoenixframework/phoenix_live_view/v1.2.12/guides/client/js-interop.md),
[LiveSocket](https://raw.githubusercontent.com/phoenixframework/phoenix_live_view/v1.2.12/assets/js/phoenix_live_view/live_socket.ts).

| Phoenix 기능 | wireview 현황과 근거 파일:줄 | 판정 | 메모 |
|---|---|---|---|
| el·mounted/updated/destroyed/disconnected/reconnected | HookContext/Manager, `wireview/static/wireview/wireview.js:1642`, `wireview/static/wireview/wireview.js:1715` | 있음 | 세부 타이밍은 완전 일치 아님 |
| beforeUpdate(toEl) | 컴포넌트 훅 전체에 인자 없이 호출, `wireview/static/wireview/wireview.js:1368`, `wireview/static/wireview/wireview.js:1894` | 부분 | 요소별 변경 감지·다음 DOM 전달 없음, GAP-046 |
| 동적 hook 교체·class 정의 | hook id 있으면 skip, 함수만 복사, `wireview/static/wireview/wireview.js:1753`, `wireview/static/wireview/wireview.js:1796` | 없음 | GAP-046 |
| pushEvent | 콜백만, `wireview/static/wireview/wireview.js:1670`, `wireview/static/wireview/wireview.js:1983` | 부분 | Promise/ref/timeout rejection 없음, GAP-046 |
| pushEventTo | 컨텍스트에 없음, `wireview/static/wireview/wireview.js:1642` | 없음 | 다중 결과 집계 포함, GAP-046 |
| handleEvent/removeHandleEvent | 등록 배열만, `wireview/static/wireview/wireview.js:1679` | 부분 | ref·개별 해제 없음, destroy 전체 해제, GAP-046 |
| js/liveSocket/upload/uploadTo | 컨텍스트 없음, `wireview/static/wireview/wireview.js:1642` | 없음 | 전역 exec/addFiles 대안, GAP-046/047 |
| push_event window 방송·before 옵션 | 훅에만 전달, `wireview/core/component.py:860`, `wireview/static/wireview/wireview.js:743` | 부분 | GAP-044 |
| dom.onBeforeElUpdated | 공개 콜백 있음, `wireview/static/wireview/wireview.js:4251` | 있음 | 미구현으로 오판하지 않음 |
| params/metadata/bindingPrefix/uploaders | 고정 socket 초기화, `wireview/static/wireview/wireview.js:160` | 부분 | 재연결은 있음. 확장 옵션은 GAP-047/053 |
| 정적 페이지/루트 밖 hook | 컴포넌트 내부 scan, `wireview/static/wireview/wireview.js:1753` | 없음 | Phoenix도 정적 hook은 mounted만. GAP-061 |
| disconnect 중복 억제 | 훅별 상태 가드 없음, `wireview/static/wireview/wireview.js:1927` | 부분 | 실제 중복 발생 재현은 미실행, GAP-046 회귀 항목 |

## 폼·피드백

기준: [Form bindings](https://raw.githubusercontent.com/phoenixframework/phoenix_live_view/v1.2.12/guides/client/form-bindings.md).

| Phoenix 기능 | wireview 현황과 근거 파일:줄 | 판정 | 메모 |
|---|---|---|---|
| change/submit | 일반 DOM change/submit, `wireview/static/wireview/wireview.js:3222` | 부분 | 타이핑은 on input 사용. Phoenix change와 의미 다름, GAP-048 |
| 입력별 값·_target | 가까운 폼 전체 serialize, `wireview/static/wireview/wireview.js:1559` | 부분 | 단일 input 규약·_target 없음, GAP-048 |
| successful controls·submitter | name 요소 열거, `wireview/static/wireview/wireview.js:1580` | 부분 | disabled/비submit 버튼 제외 없음. boost FormData 경로는 별도, GAP-048 |
| 배열/중첩 이름 | 체크박스·select 지원, `wireview/static/wireview/wireview.js:1580`, `wireview/utils.py:190` | 부분 | 점·숫자 인덱스 규약, user[email]과 다름, GAP-048 |
| _unused_·used_input? | 클라이언트 touched 이름 Set, `wireview/static/wireview/wireview.js:2980` | 부분 | 서버 메타데이터·폼별 동명 격리 없음, GAP-048 |
| no-unused-field | 원래 unused 미전송, `wireview/static/wireview/wireview.js:1580` | 없음 | GAP-048이 used-input 도입할 때 함께 설계 |
| reset change·invalid number | 합성/validity 분기 없음, `wireview/static/wireview/wireview.js:3222`, `wireview/static/wireview/wireview.js:1580` | 없음 | GAP-048 |
| auto-recover·custom·ignore | wire-auto-recover opt-in, `wireview/static/wireview/wireview.js:1111` | 부분 | socket reconnect만, ignore 예약값 없음, GAP-049 |
| trigger-action | 서버→HTTP 제출 전환 없음, `wireview/static/wireview/wireview.js:3222` | 없음 | boost 폼과 다름, GAP-050 |
| disable-with·submit 잠금 | bound 요소/표시 버튼, `wireview/static/wireview/wireview.js:3170` | 부분 | 모든 입력 readonly/버튼 disabled 계약 없음, GAP-050 |
| 업로드 완료 뒤 submit | auto_upload 분기만, `wireview/static/wireview/wireview.js:2561` | 없음 | auto_upload=False 제출 연동 없음, GAP-050 |
| 입력/캐럿/IME 보존 | value guard/commit ack, `wireview/static/wireview/values.mjs:52` | 있음 | 개별 input type 완전 동등성은 미측정 |
| to_form/inputs_for/password | Django/Pydantic 경로, `wireview/utils.py:175` | 부분 | Ecto 복제 제외. 동적 폼 연결 사용성은 GAP-048 문서 범위 |

## 내비게이션·플래시

기준: [LiveView API](https://phoenix-live-view.hexdocs.pm/1.2.12/Phoenix.LiveView.html).

| Phoenix 기능 | wireview 현황과 근거 파일:줄 | 판정 | 메모 |
|---|---|---|---|
| push_patch/navigate/replace | 같은 path 여부로 선택, `wireview/core/meta.py:394`, `wireview/static/wireview/navigation.mjs:289` | 부분 | JS/link 명시적 patch와 navigation_type 없음, GAP-062 |
| live_session·sticky·history | 세션 경계·복원 구현, `wireview/core/live_session.py:440`, `wireview/static/wireview/navigation.mjs:1` | 있음 | Django 모델에 맞는 대응 |
| before-navigate 취소·완료 알림 | mayNavigate, `wireview/static/wireview/wireview-boost.js:320` | 있음 | redirect/일부 popstate 취소 제한 |
| flash 저장·clear·이동 전달 | 클라이언트 표시, `wireview/core/meta.py:421` | 부분 | 서버 kind map/redirect carry 없음, GAP-063 |
| live_title prefix/suffix/default | 전체 title만 전송, `wireview/core/meta.py:413` | 부분 | 편의 컴포넌트 GAP-065 |
| URL scheme·제어문자 거부 | resolve_url 후 전달, `wireview/core/meta.py:87`, `wireview/static/wireview/wireview-boost.js:553` | 부분 | 공통 검증 없음. 실제 공격 재현/영향 확인 우선 GAP-066 |

## 스트림

기준: [LiveView stream API](https://phoenix-live-view.hexdocs.pm/1.2.12/Phoenix.LiveView.html#stream/4).

| Phoenix 기능 | wireview 현황과 근거 파일:줄 | 판정 | 메모 |
|---|---|---|---|
| stream/insert/reset/at/update_only | API·계획 함수 있음, `wireview/core/component.py:1306`, `wireview/core/component.py:1419`, `wireview/static/wireview/streams.mjs:48` | 있음 | reset=True 기본은 다름; update_only 이미 지원 |
| signed limit | 음수 미trim·양수 at에 따라 방향, `wireview/static/wireview/streams.mjs:72` | 부분 | GAP-051 |
| stream_configure·객체 delete | 호출별 dom_id, delete 문자열/정수, `wireview/core/component.py:1473` | 부분 | 영구 config/객체 id 해석 없음, GAP-051 |
| non-stream child | reset innerHTML 제거, `wireview/static/wireview/wireview.js:901` | 없음 | GAP-051 |
| remove animation | 즉시 제거 경로, `wireview/static/wireview/wireview.js:901` | 없음 | GAP-039 |
| moveBefore 상태 보존 | sticky 이어받기는 있음, `wireview/static/wireview/wireview.js:935` | 부분 | native moveBefore 경로 없음, GAP-060 |
| stream_async | 수동 조합만, `wireview/core/component.py:1208`, `wireview/core/component.py:1306` | 없음 | GAP-054 |

## 업로드

기준: [UploadConfig 소스](https://raw.githubusercontent.com/phoenixframework/phoenix_live_view/v1.2.12/lib/phoenix_live_view/upload_config.ex),
[allow_upload](https://phoenix-live-view.hexdocs.pm/1.2.12/Phoenix.LiveView.html#allow_upload/3).

| Phoenix 기능 | wireview 현황과 근거 파일:줄 | 판정 | 메모 |
|---|---|---|---|
| 청크/진행률/preview/cancel | 서버 API·입력 태그, `wireview/core/component.py:1532`, `wireview/templatetags/wireview.py:1051` | 있음 | scoped token·다중 worker도 구현 |
| accept MIME/wildcard/any | 빈 값 또는 확장자 비교, `wireview/features/uploads.py:255` | 부분 | image/* 판정 없음, GAP-052 |
| max_entries_mode·validator | 활성 entry count만, `wireview/features/uploads.py:479` | 없음 | selected/total 선택·사용자 validator 없음, GAP-052 |
| progress callback·disallow | 완료 콜백만, `wireview/core/component.py:1662`, `wireview/core/component.py:1532` | 없음 | 내부 progress 표시와 구분, GAP-052 |
| 단일 consume·postpone | 전체 consume 후 finally 정리, `wireview/core/component.py:1696` | 부분 | GAP-052 |
| auto_upload·submit gating | 기본 True, `wireview/features/uploads.py:252`, `wireview/static/wireview/wireview.js:2561` | 부분 | Phoenix 기본 False; 기존 기본 반전 금지, GAP-050 |
| chunk_timeout·writer | 옵션 없음, `wireview/core/component.py:1532` | 없음 | token 만료와 청크 유휴 timeout은 다름, GAP-053 |
| external presign/XHR | url/method/headers, `wireview/static/wireview/wireview.js:2725` | 있음 | 단순 직접 업로드 |
| custom uploader·onCancel·이동 정리 | 고정 XHR만, `wireview/static/wireview/wireview.js:2725` | 부분 | registry·사용자 취소 알림 없음, GAP-053 |
| external progress/metadata error | 브라우저 progress·완료, `wireview/static/wireview/wireview.js:2742` | 부분 | 서버 callback/오류 구조 계약 추가 필요, GAP-052/053 |

## 비동기·서버 수명주기

기준: [AsyncResult](https://phoenix-live-view.hexdocs.pm/1.2.12/Phoenix.LiveView.AsyncResult.html),
[LiveView 소스](https://raw.githubusercontent.com/phoenixframework/phoenix_live_view/v1.2.12/lib/phoenix_live_view.ex).

| Phoenix 기능 | wireview 현황과 근거 파일:줄 | 판정 | 메모 |
|---|---|---|---|
| start_async/handle_async·제거 정리 | task·render gate, `wireview/core/component.py:1060` | 있음 | 동명 작업은 이전 task 실제 취소 |
| cancel reason·키/AsyncResult 대상 | 문자열 name/boolean, `wireview/core/component.py:1154` | 부분 | GAP-054 |
| assign_async 여러 키·reset·이전 성공 | 단일 coroutine/새 결과, `wireview/core/component.py:1208`, `wireview/async_result.py:41` | 부분 | 재로딩 중 ok 유지 없음, GAP-054 |
| async_result 슬롯 UI | 상태 자료구조만, `wireview/async_result.py:41` | 없음 | if로 대체, GAP-065 |
| on_mount·attach/detach | event/params/after_render, `wireview/core/component.py:602` | 부분 | info/async stage·reply halt 없음, GAP-058 |
| get_connect_info/params | user/session/request 일부 대응, `wireview/core/component.py:307` | 부분 | 공통 연결 metadata API 없음, GAP-047 |
| assign_new/changed?/render_with | 필드/render 확장, `wireview/core/component.py:307`, `wireview/template_engine.py:105` | 부분 | lazy 공유·dirty 조회·동적 renderer 공개 API 없음, GAP-067 |
| transport_pid·handle_call/cast·supervisor·hibernate_after | asyncio/Pydantic, `wireview/core/component.py:307` | 설계상 제외 | BEAM PID·프로세스·GC 모델 전제 |

## 컴포넌트·슬롯

기준: [Phoenix.Component](https://phoenix-live-view.hexdocs.pm/1.2.12/Phoenix.Component.html),
[LiveComponent](https://phoenix-live-view.hexdocs.pm/1.2.12/Phoenix.LiveComponent.html).

| Phoenix 기능 | wireview 현황과 근거 파일:줄 | 판정 | 메모 |
|---|---|---|---|
| 함수 required/default/type | 시그니처·기본 타입 coercion, `wireview/function_components.py:231` | 부분 | full Pydantic 검증 아님 |
| attr values/examples/doc/global/include/global_prefixes | **kwargs만 대안, `wireview/function_components.py:208` | 없음 | 선언·허용 목록·문서 생성 없음, GAP-055 |
| named/default/repeated slot·let·required | SlotContainer/태그, `wireview/slots.py:310`, `wireview/templatetags/wireview.py:651` | 있음 | 기본 합성 지원 |
| slot attrs·validation | fill은 let만, `wireview/templatetags/wireview.py:670` | 없음 | entry label 같은 속성 없음, GAP-055 |
| identity/update/update_many/myself | LiveComponent/라우팅, `wireview/live_component.py:214`, `wireview/templatetags/wireview.py:763` | 있음 | 부모 렌더별 일괄 갱신 |
| send_update/send_update_after | parent/child 즉시 갱신, `wireview/core/component.py:1737` | 부분 | 예약 API 없음, GAP-058 |
| portal | 템플릿 태그·patcher에 없음, `wireview/templatetags/wireview.py:1`, `wireview/static/wireview/wireview.js:1368` | 없음 | teleport·이벤트 소유권 보장 없음, GAP-064 |
| focus_wrap/dynamic_tag/live_title | 공개 태그 없음, `wireview/templatetags/wireview.py:1` | 없음 | 사용자가 구현 가능, GAP-042/065 |
| ColocatedHook/JS/CSS | 외부 훅 수집만, `wireview/features/hooks.py:42` | 없음 | GAP-061 |
| HEEx/Ecto/OTP 문법·형식 | Django 템플릿/Python, `wireview/template_engine.py:477` | 설계상 제외 | 유용한 의미는 이식하되 언어 문법 자체는 복제 안 함 |

## 테스트·개발 도구

기준: [LiveViewTest](https://phoenix-live-view.hexdocs.pm/1.2.12/Phoenix.LiveViewTest.html),
[ColocatedCSS](https://phoenix-live-view.hexdocs.pm/1.2.12/Phoenix.LiveView.ColocatedCSS.html),
[ColocatedJS](https://phoenix-live-view.hexdocs.pm/1.2.12/Phoenix.LiveView.ColocatedJS.html).

| Phoenix 기능 | wireview 현황과 근거 파일:줄 | 판정 | 메모 |
|---|---|---|---|
| mount/call/render·navigation·stream helpers | MountedComponent, `wireview/testing.py:382` | 있음 | 테스트 API 제공 |
| element/form·render click/change/submit/key/focus/blur | call은 핸들러 직접 호출, `wireview/testing.py:938` | 없음 | DOM 연결 검증 없음, GAP-056 |
| render_hook/child lookup/HTTP submit/title/reply/push 단언 | 공개 헬퍼 목록, `wireview/testing.py:382` | 없음 | raw 메시지 형식은 내부, GAP-056/057 |
| file_input/preflight/render_upload | 헬퍼 없음, `wireview/testing.py:382` | 없음 | 내부 통합 테스트와 구분, GAP-057 |
| render_async | task 완료 대기 API 없음, `wireview/testing.py:382` | 없음 | GAP-057 |
| test_warnings·missing_form_id 제외 | 설정 없음, `wireview/testing.py:1248` | 없음 | 검사별 raise/warn/ignore, GAP-056 |
| debug/latency/profiling/telemetry | 브라우저 도구·Django signal, `wireview/static/wireview/wireview.js:4023`, `wireview/telemetry.py:169` | 있음 | 이름/대시보드 구현이 다른 것은 누락 아님 |
| 편집기 metadata·템플릿 진단 | checks/수집, `wireview/checks.py:1`, `wireview/features/hooks.py:42` | 있음 | 고급 attr/slot 검사는 GAP-055 |
| TagFormatter·모듈 debug opt-out | 대응 API 없음, `wireview/template_engine.py:477` | 없음 | Django용 플러그인 설계, GAP-061 |
| 개발 reload·생산 asset 감지 | template reload, `wireview/core/template_reload.py:1` | 부분 | static_changed 없음, GAP-059 |

## CSP·보안·성능·제외

기준: [Security](https://phoenix-live-view.hexdocs.pm/1.2.12/security-model.html),
[assigns와 change tracking](https://phoenix-live-view.hexdocs.pm/1.2.12/assigns-eex.html).

| Phoenix 기능 | wireview 현황과 근거 파일:줄 | 판정 | 메모 |
|---|---|---|---|
| nonce·인라인 JS 없는 바인딩 | nonce/명령 JSON, `wireview/templatetags/wireview.py:55`, `wireview/event_transpiler.py:31` | 있음 | CSP 완화 필요 없음 |
| CSRF/origin/서명/session auth | 각 검증층, `wireview/core/csrf.py:1`, `wireview/core/origin.py:31` | 있음 | 애플리케이션 인가는 별도 |
| 서버 assigns 비공개 | 서명 상태 전송, `wireview/core/state.py:199` | 부분 | 암호화 아님. exclude_fields 규약을 적용하는 설계 차이 |
| static/dynamic diff·목록 증분 | rendered protocol, `wireview/core/rendered.py:67` | 있음 | 명시적 :key 대신 내용 matching은 의도된 대안 |
| 변경 없는 동적 표현식 평가 생략 | 일반 노드 resolve 실행, `wireview/template_engine.py:122` | 부분 | 렌더 후 diff와 다름. temporary 특수 추적은 있음, GAP-060 |
| temporary/skip/shared_render | opt-in 최적화, `wireview/core/component.py:981`, `wireview/core/shared_render.py:1` | 있음 | 일반 dependency tracking과 구분 |
| LongPolling | 기존 제외 결정, `docs/design/longpolling-fallback.md:1` | 설계상 제외 | WebSocket 필수 제품 정책. BEAM 때문에 불가능한 것은 아님 |
| nested LiveView PID/독립 프로세스 | 중첩 컴포넌트 모델, `wireview/live_component.py:1` | 설계상 제외 | UI/오류 복구와 BEAM 프로세스 격리는 다른 계약 |

## 추가 옵션 교차 확인

앞 표와 같은 1.2.12 공식 JS/LiveSocket/Component 소스를 기준으로 이름 단위 표에서 빠지기 쉬운 옵션을 따로 확인했다.

| Phoenix 기능 | wireview 현황과 근거 파일:줄 | 판정 | 메모 |
|---|---|---|---|
| show/toggle display·in/out | display와 show/hide transition 있음, `wireview/js.py:97`, `wireview/static/wireview/sticky.mjs:148` | 부분 | 기본 display는 빈 값. Phoenix block/table 기본과 다름, GAP-043 |
| LiveSocket connect/disconnect/getSocket/version | 공개 전역은 visit/send/exec 등, `wireview/static/wireview/wireview.js:3830` | 없음 | 자동 연결은 있음. 안정된 제어 핸들 API는 GAP-047 |
| replaceTransport | 고정 WebSocket, `wireview/static/wireview/wireview.js:160` | 설계상 제외 | 현재 transport 제품 정책. 수동 disconnect 필요성과 혼동하지 않음 |
| defaults·loader/disconnected timeout·reload jitter | Django RECONNECT 설정, `wireview/templatetags/wireview.py:80` | 부분 | 재시도 있음. Phoenix 생성자 옵션 계약은 아님, GAP-047 |
| storage/viewLogger·IME change 차단 옵션 | 내부 logger·값 보호·키 필터, `wireview/static/wireview/wireview.js:3442`, `wireview/static/wireview/events.mjs:117` | 부분 | 저장소/logger 주입·change 전송 옵션 없음, GAP-047/048 |
| dom onDocumentPatch/onPatchStart/End/onNodeAdded/jsQuerySelectorAll | 공개 dom은 beforeElUpdated만, `wireview/static/wireview/wireview.js:4251` | 없음 | patch/view-transition·selector 확장, GAP-047 |
| execJS·js() | 전역 exec 있음, `wireview/static/wireview/wireview.js:3917` | 부분 | encoding과 호출 객체 다름, GAP-041/046 |
| getFileURLForUpload·drop target·entry errors | getPreviewUrl/drop_zone/uploads 목록, `wireview/static/wireview/wireview.js:4011`, `wireview/templatetags/wireview.py:1015`, `wireview/core/component.py:1643` | 부분 | 미리보기·드롭·오류 상태 존재. Phoenix 타입/클래스 이름은 다름 |
| 지연 시뮬레이션·세션 debug 설정 | 송신 고정 지연/메모리 값, `wireview/static/wireview/wireview.js:1205`, `wireview/static/wireview/wireview.js:4054` | 부분 | 양방향·sessionStorage 의미는 없음, GAP-047 |
| embed_templates·assigns_to_attributes | 외부 template 경로·내부 attrs escape, `wireview/function_components.py:340`, `wireview/templatetags/wireview.py:956` | 부분 | wildcard 함수 생성·공개 attributes 필터 helper 없음. Python식 편의는 GAP-055/065 |
| assign/update·connected?·put_private | 필드·수명주기·PrivateAttr 모델, `wireview/core/component.py:307` | 부분 | 동등한 기본 작업 가능, 동일 socket API는 아님. lazy/dirty 추가는 GAP-067 |
| internal attachDebugBuffer | 공개 wireview 대응 없음, `wireview/static/wireview/wireview.js:4121` | 설계상 제외 | 공식 소스가 @internal 표시. public 목록에 섞여 있어도 이번 공개 parity에서는 제외 |

GAP-067은 이미 있는 `Meta.lazy_properties`를 새 기능으로 다시 만들지 않는다. 그 구현은
`wireview/core/lazy_context.py:1`의 렌더별 지연 property 계산이며, 공유 초기 assign/dirty 조회와 구별한다.

## 테스트 근거와 한계

| 영역 | 읽은 기존 테스트 | 확인 범위 |
|---|---|---|
| 바인딩/CSP | `tests/test_event_binding.py:123`, `tests/test_csp_e2e.py:1` | modifier 거부·strict CSP |
| JS | `tests/test_js.py:1`, `tests/test_js_commands_e2e.py:1`, `tests/test_sticky_e2e.py:1` | 현재 빌더·브라우저·sticky |
| 훅 | `tests/test_hooks_e2e.py:59`, `tests/test_hooks.py:1` | 소유권·이동·응답 |
| 폼 | `tests/test_forms_e2e.py:1`, `tests/test_input_values_e2e.py:1` | 폼·value guard |
| 내비게이션 | `tests/test_history_e2e.py:1`, `tests/test_navigation_helpers.py:1`, `tests/js/navigation.test.mjs:1` | path·취소·헬퍼 |
| 스트림 | `tests/test_streams.py:296`, `tests/js/streams.test.mjs:1`, `tests/test_streams_e2e.py:1` | update_only·순서·구버전 |
| 업로드 | `tests/test_uploads.py:1`, `tests/test_uploads_e2e.py:1`, `tests/test_multiworker_uploads.py:1` | token·entry·다중 worker |
| 비동기 | `tests/test_start_async.py:1`, `tests/test_async_result.py:1`, `tests/test_async_lifetime.py:1` | 완료·취소·제거 |
| 컴포넌트 | `tests/test_function_component.py:1`, `tests/test_slots.py:1`, `tests/test_update_many.py:1` | 인자·슬롯·일괄 갱신 |
| 보안 | `tests/test_security.py:1` | 기존 방어층, 새 URL 입력 행렬은 미실행 |

요청한 문서 테스트는 기본 uv 캐시 권한 오류 후 임시 캐시로 실행하여 **1396 passed**.
전체 기능 E2E·성능 벤치마크·새 취약점 재현을 실행했다고 주장하지 않는다. 초안은 `issue-drafts.md`에 연결한다.
이미 구현된 sticky JS·다중 CSS 대상·update_only·전역 DOM 콜백·이동 취소·JSON 직렬화를 새 누락으로 만들지 않았다.
