# 설계 리뷰 원문 (Codex gpt-6-astra, 2026-09-09)

> `docs/design/live-session.md`와 `docs/design/session-extraction.md`의 초안에 대한 적대적 리뷰다.
> 두 설계 문서는 이 리뷰를 반영해 고쳤고, 여기서 나온 현재 코드의 결함은
> [#76](https://github.com/itda-work/django-wireview/issues/76)(서명이 클래스에 묶이지 않음),
> [#77](https://github.com/itda-work/django-wireview/issues/77)(업로드 레지스트리 소유권)로 떼어 냈다.
> 반영 내역을 추적할 수 있도록 원문을 그대로 둔다.

---

검토 기준: 저장소 HEAD `91ad207`, 2026-09-09. 대상 문서와 관련 구현을 읽고, 저장소 파일을 변경하지 않는 임시 Python 실행으로 두 가지를 재현했다. 아래에서 **현재 코드의 확인된 동작**과 **제안 설계에 남은 조건부 취약점**을 구분한다. 미구현 기능의 공격 성공을 실측했다고 주장하지 않는다.

## 1. [높음] 정상 서명끼리 조합하면 페이지 정책을 우회할 수 있다

**대상:** `docs/design/live-session.md` §3-1, §3-2, §5-2, §6 AC1·AC3.

**문제:** 세션 이름의 서명과 `data-state` 서명이 독립적이고, 컴포넌트의 `_live_sessions`는 생략하면 전부 허용한다. 따라서 페이지에 정책을 한 번 선언해 모든 컴포넌트를 보호한다는 AC1이 성립하지 않는다. “클라이언트가 admin을 자칭”하는 것보다 **유효한 public 정책으로 보호 대상을 실행**하는 공격이 문제다. admin 정책 이름을 임의로 보내더라도 서버가 그 정책의 RequireStaff를 반드시 실행한다면, 이름 선택 자체가 곧 권한 획득은 아니다.

**근거·재현 조건:**

- 현재 `consumer.py:75-91`은 `name`, `state`, `children`만 받아 복원한다. 페이지와 연결을 묶는 서버 상태가 없다.
- 제안 그대로라면, 공격자는 public 페이지의 정상 정책 서명과 이전에 확보한 관리자 컴포넌트 상태를 직접 WebSocket join에 함께 보낼 수 있다. 해당 컴포넌트에 `_live_sessions`와 별도 인증 훅이 없으면 public의 빈 훅 목록이 적용된다. 위조가 없으므로 AC3 테스트는 이를 잡지 못한다.
- 하나의 연결에 public과 admin 서명을 차례로 보내는 경우도 금지 규약이 없다. “페이지 하나에 세션 하나”는 서버가 페이지를 식별·기억하지 않으면 강제할 수 없다.
- §5-2의 경고 제안은 그 자체로 이 결합을 복구하지 않는다. 빠진 컴포넌트 선언을 다시 요구하는 것은 §1의 반복 선언 문제를 그대로 남긴다.

**제안:** HTTP 렌더 시 정해진 정책을 각 컴포넌트의 서명된 발급 정보에 넣고, join에서 클래스·상태·정책·인증 문맥의 일치를 검증하도록 계약을 정해야 한다. 연결에도 인증 문맥/정책 식별자를 보관하고 변경을 서버에서 거절하거나 재인증해야 한다. 익명 public 서명, 정책 누락, 유효한 두 서명의 교차 조합, 혼합 join을 인수 테스트에 포함해야 한다.

## 2. [높음] 현재 data-state는 페이지·사용자뿐 아니라 컴포넌트 클래스에도 묶이지 않는다

**대상:** `docs/design/live-session.md` §3-2 및 §6.

**문제:** 현재 위험 설명은 “A 페이지의 상태를 B 연결에서 사용”하는 수준에 머문다. 실제로는 공격자가 **별도로 전송하는 클래스 이름을 바꾸는 것**도 가능하다. 보호 컴포넌트의 상태를 훔치지 않고 공개 컴포넌트의 서명 상태를 재사용할 수 있는 조건을 빠뜨렸다.

**근거:**

- `core/state.py:40-54`: 서명 대상은 `model_dump_json(exclude=...)`; 기본 제외 필드는 `{"user", "wire"}`(`core/component.py:138`). 클래스 이름과 사용자 결합, 만료 검사가 없다.
- `consumer.py:81-90`: 상태만 unsign하고 별도 `name`을 저장소에 전달한다.
- `core/component.py:329-339`: 그 이름으로 클래스를 resolve한 뒤 상태를 넣는다. `repository.py:82-90`의 기존 id 경로는 요청 클래스와 기존 클래스 일치도 검사하지 않는다.
- 일회성 Python 실행에서 필드가 호환되는 `ReviewPublic(Component)`, `ReviewProtected(Component)`를 정의하고, 전자의 `sign_state()` 결과를 후자의 `repo.build()`에 넣었다. 출력은 `Public signed state builds: ReviewProtected user authenticated: False`였다. 이는 클래스 치환 확인이며, 실제 앱 데이터 탈취를 실행한 것은 아니다.

**위험의 정확한 범위:** 서명은 임의 필드 변조를 막는다. 타인의 상태가 저절로 생기거나 피해자의 `user`로 바뀌지는 않는다. 현재 사용자는 연결에서 주입된다. 그러나 호환되는 상태로 다른 클래스의 `joined()`, 렌더, 노출 이벤트에 도달할 수 있고, 과거에 발급된 객체 id·상태를 재사용할 수 있다. 객체 소유권과 현재 권한을 서버에서 확인하지 않는 앱에서는 데이터 노출·변경으로 이어질 수 있다. 반대로 모든 접근에서 현재 사용자와 객체 권한을 검사한다면 페이지 간 재사용만으로 권한 상승이라고 단정할 수 없다.

**제안:** 서명 봉투에 정규화된 클래스 식별자와 정책/인증 문맥을 결합하고, 재join의 id·클래스 일치를 검증해야 한다. 현재 상태 형식을 계속 무조건 허용하면 강화된 경로를 우회할 수 있으므로 구형 서명의 전환 규칙도 필요하다. 객체 단위 권한 검사는 별도로 유지해야 한다.

## 3. [높음] “세션 이름 + 사용자 pk면 로그아웃 후 재사용이 막힌다”는 거짓이다

**대상:** `docs/design/live-session.md` §3-3, §4, §5-1.

**문제:** 사용자 pk는 로그인 세션의 유효성이나 폐기 여부를 나타내지 않는다. 클라이언트의 전체 로드는 로그아웃 후 열린 다른 탭이나 직접 연결한 클라이언트를 폐기하지 못한다.

**근거·재현 방법:**

- `consumer.py:32-45`는 연결 scope의 user를 저장소에 전달한다. `command_user_event`와 `command_hook_event`(`154-195`)에는 인증 세션을 다시 확인하는 공통 경로가 없다.
- 탭 A에서 인증된 연결을 열고 탭 B에서 HTTP 로그아웃한 다음, A에서 이동 없이 이벤트를 보내면 join 훅이나 boost 경계 비교를 거치지 않는다. scope의 사용자 pk와 이전 서명의 pk를 비교해도 일치한다.
- 같은 사용자로 재로그인하면 pk가 같아 이전 서명과 새 로그인도 구별되지 않는다. 익명 사용자의 pk는 개인별 식별자가 아니다.
- 현재 `core/state.py`는 `Signer`이며 검증에 `max_age`가 없다. 사용자 pk를 추가하는 것만으로 만료도 생기지 않는다.
- Channels 공식 문서 역시 장기 연결 도중 다른 곳에서 로그아웃될 수 있으므로 `get_user(scope)` 등으로 상태를 확인해야 한다고 설명한다. [Channels Authentication](https://channels.readthedocs.io/en/stable/topics/authentication.html)
- Phoenix도 live_session과 별도로 로그아웃 시 소켓 식별자에 disconnect를 브로드캐스트하는 방법을 문서화한다. [Phoenix Security considerations](https://phoenix-live-view.hexdocs.pm/security-model.html#disconnecting-all-instances-of-a-live-user)

**제안:** 로그인 세션/인증 세대와 토큰을 결합하고 서버에서 그 유효성을 확인하는 규칙, 로그아웃·권한 회수 시 기존 연결과 구독을 폐기하는 규칙, 재연결 시 재검증을 별도로 명시해야 한다. 중요 이벤트의 현재 권한 검증도 필요하다. 전체 로드는 정상 클라이언트의 인증 문맥 갱신 수단으로 한정해 설명해야 한다.

## 4. [높음] join 훅만으로 “이 페이지에 들어올 수 있는가”를 보장하지 못한다

**대상:** `docs/design/live-session.md` §3-1 예제, §3-2, §4.

**문제:** 예제의 `@admin.view`는 페이지 소속 선언만 설명하고, RequireStaff가 최초 HTTP 렌더 전에 실행되는지 정하지 않는다. join에서 거절해도 이미 HTTP로 보낸 HTML과 data-state는 회수할 수 없다.

**근거·재현 방법:** `templatetags/wireview.py:60-81`은 HTTP 경로에서 `is_live=False` 저장소를 만들고 `repo.build()` 후 즉시 `_render()`한다. 보호용 Django 데코레이터 없이 문서 예제처럼 구성하고 JavaScript를 끈 채 GET하면, 현재 방식에서는 WS join 여부와 무관하게 초기 렌더가 응답에 포함된다. 제안의 설명만으로는 이 경로에 인증 검사가 추가되는지 알 수 없다. boost도 `wireview-boost.js:217-221`에서 HTTP fetch를 하므로 같은 문제를 갖는다.

**제안:** 데코레이터가 HTTP 접근을 실제로 보호하는지, 아니면 별도 Django 인증/인가가 필수인지 명확히 해야 한다. HTTP 인증 실패 시 민감한 초기 HTML·상태가 아예 생성되지 않는 테스트가 필요하다. Phoenix의 mount 인증은 초기 HTTP와 연결 시점 양쪽을 다룬다. [Phoenix Mounting considerations](https://phoenix-live-view.hexdocs.pm/security-model.html#mounting-considerations)

## 5. [높음] “모든 컴포넌트 join”과 halt의 서버 측 효과가 정의되지 않았다

**대상:** `docs/design/live-session.md` §3-2, §5-3, §6 AC1.

**문제:** `command_join`에 훅을 붙이는 것만으로 렌더 중 만들어지는 LiveComponent까지 보호되지 않는다. 또한 halt를 클라이언트 remove/redirect로 구현하면 서버 저장소의 컴포넌트가 남아 이벤트를 받을 수 있다.

**근거:**

- 일반 join은 `repository.py:221-229`에서 build/등록 후 joined를 호출한다.
- LiveComponent는 `repository.py:107-168`의 별도 build 경로에서 등록되고, `181-209`에서 joined/update가 실행된다. `consumer.py:99-104`는 부모 렌더를 전송한 **뒤** 이 큐를 처리한다.
- `consumer.py:92-95`의 join 예외 경로는 `component_remove()`를 부르지만, 그 메서드(`358-360`)는 브라우저 remove만 전송한다. 저장소 삭제는 하지 않는다. `repository.py:245-270`의 이벤트 디스패치는 저장소에 남은 객체를 사용한다.
- 따라서 기존 오류 경로를 halt 처리로 재사용하는 구현은 안전하지 않다. 부모 훅의 통과만으로 자식의 `_live_sessions` 제한까지 검사된다고 볼 수도 없다.

**제안:** 일반 컴포넌트·children 복원·LiveComponent 생성·재join 모두에 정책 상속/검증 지점을 정의해야 한다. 거절 객체는 이벤트 대상으로 등록되지 않아야 하며, 이미 등록했다면 자식·작업·구독까지 롤백해야 한다. halt 이후 직접 이벤트를 보내는 테스트와 자식 초기 HTML 전송 전 차단 테스트를 추가해야 한다.

## 6. [높음] 다중 프로세스 업로드 실패는 이미 확인 가능하며, “세션 상태로 이동”만으로 해결되지 않는다

**대상:** `docs/design/session-extraction.md` §2, §4 단계 0·3, §5.

**문제:** “다른 프로세스로 가면 지금도 실패한다”는 진단은 맞다. 그러나 이를 E2E 실측 전에는 버그인지 모르는 것처럼 취급하고, 3단계 해결책에 단순한 세션 상태 이동을 동등하게 놓은 것은 잘못이다. 레지스트리는 이미 컴포넌트의 `_upload_registry`로 존재하며 전역 dict는 HTTP가 그 객체를 찾는 인덱스다.

**근거와 실행 결과:**

- `views.py:32,44,71`: 프로세스 로컬 dict에 저장하고 조회한다.
- `consumer.py:519-528`: WS가 소유한 레지스트리 객체를 그 프로세스에 등록한다.
- `views.py:114-117`: 레지스트리가 없으면 토큰 검증보다 먼저 404를 반환한다. Broker 사용은 파일 처리 뒤 진행 통지(`156-157,195-212`)에만 있다.
- 독립 인터프리터 A에서 `register_upload_registry("review-component", UploadRegistry(...))`를 호출하고, 새 인터프리터 B에서 동일 id로 `UploadView.post()`를 직접 구동했다. 실제 출력:

```text
Process A registry exists: True
Process B registry exists: False
Process B HTTP: 404 {"error": "Component not found"}
```

이는 두 프로세스의 조회/HTTP 실패를 재현한 것이다. 로드밸런서·실제 WS·전체 업로드 E2E를 실행한 것은 아니다. 그러나 토큰을 보기 전에 실패하므로 유효한 업로드 토큰도 이 분기를 해결하지 못한다. 외부 presigned 업로드는 별도 경로이므로 이 결론을 모든 업로드 방식에 확대해서는 안 된다.

**제안:** 버그 수정을 세션 export/import나 프런트 교체와 독립적으로 진행할 수 있게 순서를 바꿔야 한다. 필요한 것은 소유 프로세스로의 명시적 라우팅/RPC 또는 공유 업로드 상태와 파일 저장소다. `views.py:143-175,189-192`가 로컬 경로와 mutable entry를 갱신하므로, dict를 외부화하는 것만이 아니라 파일 접근·원자적 진행 상태·취소/완료 동기화까지 검증해야 한다. E2E는 버그 존재 판정의 선행조건보다 수정 검증 수단으로 적절하다.

## 7. [높음] 업로드 레지스트리는 단일 프로세스에서도 연결 간 충돌·수명 누수가 있다

**대상:** `docs/design/session-extraction.md` §2, §4 단계 3.

**문제:** 문서는 프로세스 분산만 난제로 잡지만, 현재 키는 연결 id가 없는 component id다. 페이지 안에서만 고유한 id를 프로세스 전체 키로 쓰므로 두 탭/연결의 등록과 삭제가 간섭한다. 이를 그대로 외부 저장소로 옮기면 충돌 범위만 넓어진다.

**근거·재현 방법:**

- `views.py:44`의 `_upload_registries[component_id] = registry`는 같은 id의 이전 등록을 덮어쓴다.
- A와 B가 같은 고정 id로 업로드 컴포넌트를 join하면 B의 레지스트리가 남는다. A에서 leave하면 `consumer.py:118-122` → `views.py:56-58`이 B의 레지스트리를 pop하고 cleanup한다. 서명 상태 재사용이나 탭 복제로도 같은 id가 생길 수 있다.
- 진행 통지 그룹도 `wireview_upload_<component_id>`(`consumer.py:527`, `views.py:200`)이어서 같은 id의 연결이 통지 채널을 공유한다.
- `consumer.py:47-68`의 disconnect는 leaving 호출과 일반 구독 정리만 하고 업로드 레지스트리를 해제하지 않는다. 기본 `Component.leaving()`(`core/component.py:368-389`)도 정리하지 않는다. 업로드 그룹은 `self.subscriptions`에 추가되지 않으므로 그 루프만으로 명시적 해제되지 않는다.

**제안:** 업로드 소유권 키와 토큰·HTTP 주소·통지 대상을 연결/세션에 묶어야 한다. 삭제는 소유권을 확인하고, leave·disconnect·재연결 교체·프로세스 사망에 대한 정리 규칙을 정의해야 한다. 단일 프로세스 두 연결 테스트도 0/3단계에 포함해야 한다. 이 근거만으로 타인의 파일 내용을 읽을 수 있다고까지 단정하지는 않는다.

## 8. [중간] 클릭 인터셉터에만 경계를 넣으면 정상 클라이언트도 우회한다

**대상:** `docs/design/live-session.md` §3-3, §6 AC2.

**문제:** “클릭 인터셉터, isStreamContainer 옆”이라는 구현 위치는 실제 코드와 맞지 않는다. history 복원과 서버 명령이 클릭을 거치지 않는다.

**근거:**

- `isStreamContainer`는 클릭 처리부가 아닌 범용 morph의 노드 콜백(`wireview-boost.js:31-49`)에 있다.
- 서버 `redirect`/`push`는 `wireview.js:238-259`에서 HistoryCache를 직접 호출한다.
- `popstate`(`wireview-boost.js:233-238`)는 캐시된 body를 먼저 morph한 뒤 fetch한다. 캐시에는 body·scroll만 있고 head의 세션 메타가 없다(`199-203`).
- fetch 경로(`215-221`)도 title과 body만 반영한다. head에 둔 정책 메타를 별도로 갱신하지 않으면 같은 정책 내 이동 후 새 서명 관리가 불명확하다.
- `push()`는 문서 검증 전 history를 변경하고, `replaceContentFromUrl()`는 응답을 받기 전 newLocation을 발행한다. `wireview.js:136-138`는 이를 받아 기존 컴포넌트에 쿼리를 보낸다.

**제안:** fetch 결과·history 캐시·서버 내비게이션이 합류하는 공통 단계에서 정책/인증 문맥을 검증하고, 통과 전 DOM 반영과 기존 컴포넌트의 params 변경을 막아야 한다. 메타 없음, 최종 redirect 응답, 뒤로/앞으로, 서버 push/redirect, 비동기 응답 순서도 다뤄야 한다. 클릭 링크 E2E 하나로 AC2를 증명할 수 없다. 그리고 이것을 보강해도 악성 직접 WS 클라이언트에 대한 서버 검증은 별개다.

## 9. [중간] 추출만으로 mailbox 왕복은 사라지지 않으며, 직접 호출은 실행 의미를 바꾼다

**대상:** `docs/design/session-extraction.md` §1, §3, §4 단계 1.

**문제:** “Outbound와 저장소만 있으면 성립”, “분리하면 같은 프로세스에서는 왕복이 사라진다”는 현재 의존성과 맞지 않는다. 세션 추출과 mailbox 교체는 다른 변경이다. 또한 “부수적인 성능 이득”이라고 쓴 직후 “실측 전에는 주장하지 않는다”고 해 스스로 모순된다.

**근거:**

- `core/meta.py:457-470`는 자기 세션에도 Broker.send_to_session으로 보낸다. 메서드 소유 클래스를 바꿔도 이 호출은 유지된다.
- `core/meta.py:76-88`의 기본 Broker 선택, `repository.py:40-45,100-103`의 channel 의존성, `consumer.py:524`의 업로드 등록 조건도 어댑터 계약에서 해결해야 한다.
- `deffer()`(`core/meta.py:442-444`)는 후속 이벤트를 메일로 보낸다. 이를 즉시 await로 바꾸면 현재 이벤트가 끝나기 전에 다음 핸들러가 재진입할 수 있다.
- `flush_pending()`(`132-145`)과 `consumer.py:99-116`은 초기 렌더와 후속 명령 순서를 의도적으로 관리한다. async task 완료(`core/component.py:822-841`)도 같은 객체에 접근한다.

**제안:** 세션의 인증 문맥·주소·Broker·메일 수신·직렬 실행 규약을 명시하고, 추출 단계는 기존 순서 의미를 보존해야 한다. 로컬 mailbox 최적화는 별도 변경과 전후 측정으로 다뤄야 한다. “성능 이득”은 측정 전에는 가설로 낮춰야 한다.

## 10. [중간] export/import 목록으로는 동작하는 세션을 복원할 수 없다

**대상:** `docs/design/session-extraction.md` §2, §4 단계 2, §6; `live-session.md` §3·§5와의 연계.

**문제:** 컴포넌트 상태·Rendered·구독·쿼리스트링은 충분한 체크포인트가 아니다. 두 문서를 함께 구현하면 새 인증/정책 문맥조차 이 목록에 없다.

**근거:**

- `core/component.py:138`은 user·wire를 상태에서 제외한다.
- `repository.py:48-53`의 children과 대기 중 joined/update 큐, `live_component.py:103`의 private parent id가 있다.
- `core/component.py:188,775,840-841`에는 동적 훅과 실행 중 asyncio task가 있다. task는 일반 JSON 상태로 옮길 수 없다.
- `core/meta.py:132-145`의 대기 명령/브로드캐스트와 `310-330`의 구형 토큰 diff 스냅샷도 Rendered 하나로 대체되지 않는다.
- upload를 뒤 단계로 미뤄도 실행 중 작업과 권한 문맥의 복원 문제는 남는다.

**제안:** 이동 가능한 안전 시점, 취소/재실행할 작업, 재구축할 구독·주소, 신뢰 가능한 인증 정보의 재획득, 미지원 상태를 정해야 한다. import로 새 로그인 검증이나 정책 제한이 사라지지 않아야 한다. “data-state로 이미 복제됨”을 완전한 세션 직렬화의 근거로 사용하면 안 된다.

## 11. [중간] 테스트 공백을 “지금은 테스트할 방법이 없다”로 잘못 설명한다

**대상:** `docs/design/session-extraction.md` §4 마지막 문단, §5 테스트 가능성.

**문제:** 세션 추출의 테스트 편의성에는 동의하지만, 추출만이 컨슈머 hop을 테스트할 방법이라는 주장은 틀리다. 추출 후에도 mailbox를 건너뛰는 하네스만 쓰면 같은 종류의 누락이 재발한다.

**근거:**

- `tests/test_streams.py:133-172`는 이미 consumer를 직접 생성하고 `component_stream_op`에 실제 payload를 넘겨 limit 보존을 검증한다. 소켓이 필요 없다.
- `tests/test_nats_layer.py:90-101`는 WebsocketCommunicator로 ASGI connect→join→render를 돌린다. 실제 TCP 소켓 없이 컨슈머 경로를 구동하는 기존 패턴이다.
- `consumer.py:372-377`에는 limit 처리도 이미 있다. #65의 과거 발견은 당시 테스트의 범위 공백을 입증하지만 현재 구조에서 검증이 불가능함을 입증하지 않는다.

**제안:** 직접 consumer+가짜 Outbound 또는 ASGI communicator로 join→이벤트→render와 session mail을 검증하는 단기 대안을 제시해야 한다. 추출의 이점은 의존성과 테스트 비용 감소로 표현하고, 테스트 추가를 리팩터링 완료에 종속시키지 않아야 한다.

## 12. [중간] CPU 착수 기준과 메모리 결론의 적용 범위가 근거와 어긋난다

**대상:** `docs/design/session-extraction.md` §5.

**문제:** “CPU 처리량이 프로세스 확장으로 안 되는 지점”을 프런트 교체의 기준으로 삼았지만, 참조 문서는 그 교체가 Python 렌더 비용을 해결하지 못한다고 설명한다. 또한 한 워크로드의 deflate 해제 결과에서 “메모리는 당분간 병목이 아니다”라는 일반 결론은 나오지 않는다.

**근거:**

- `transport-abstraction.md` §2는 연결 계층 교체가 이벤트 처리량의 문제가 아니며 연결별 Django 재렌더를 가져갈 수 없다고 명시한다. Python 렌더·DB·경합이 원인이면 Go/Elixir 프런트로 병목이 제거되지 않는다.
- §5-2-1의 50.6 KB는 macOS, InMemory, 단일 프로세스, 항목 5개, 연결 2,000개의 측정이다. 10,000개가 0.5 GB라는 수치는 이 기울기의 외삽이며 프로세스 기본 RSS·큰 컴포넌트·운영 레이어·버퍼 증가까지 측정한 총량이 아니다.
- 211.5→50.6 KB는 조건이 같은 측정에서 약 4배 개선의 근거가 된다. 그러나 159 KB는 zlib 쌍의 별도 실험값이고 실제 RSS 차이는 160.9 KB다. 정밀도가 다른 수치를 같은 값처럼 합치지 않는 편이 정확하다.

**제안:** CPU 기준은 원인 프로파일링으로 연결 I/O 비용이 지배적임을 확인한 경우에 한정해야 한다. 메모리는 실제 배포 구성과 목표 연결 수에서의 예산·한계로 판단하고, 10,000 연결 수치를 추정치로 표시해야 한다. 테스트 개선·업로드 버그 수정은 이 성능 기준과 분리하는 것이 맞다.

## 짧게 동의하는 부분

- `_run_on_mount_hooks()`가 정의만 있고 호출되지 않는다는 §0의 지적은 현재 코드와 일치한다. #75를 보안 선행 문제로 잡은 것은 타당하다.
- 페이지에 공통 정책을 선언하는 UX는 여러 컴포넌트를 쓰는 wireview에 적합하다. 다만 서버의 실질 경계는 **검증된 발급 문맥과 인증 수명**, 개별 동작의 권한이어야 한다. 페이지는 선언 위치이고 boost는 그 경계를 따르는 클라이언트 동작이다.
- deflate 비용을 측정해 대규모 프런트 도입을 서두르지 않은 판단과, 업로드 정합성을 성능과 구분한 방향에는 동의한다.

## 검증 범위

코드·문서는 수정하지 않았다. 임시 Python 클래스와 레지스트리는 인터프리터 메모리에만 만들었고, 바이트코드 기록을 끈 상태에서 실행했다. 실행 확인은 두 프로세스의 업로드 404와 클래스 간 서명 상태 재사용이다. 로그아웃·boost·미구현 정책 우회는 코드 추적 및 재현 시나리오이며 실제 브라우저 E2E 결과가 아니다. 전체 테스트와 벤치마크는 실행하지 않았다. GitHub wip 조회는 네트워크 제한으로 실패했으며, 이슈 대화 내용에 관한 추정은 근거로 쓰지 않았다.
