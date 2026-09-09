# live_session 적대적 리뷰 3라운드 (Codex gpt-6-astra, 2026-09-09)

> 구현(`2f06127`)에 대한 리뷰 원문이다. 지적을 재현 테스트로 확인하고 전부 고쳤다 — 무엇을 어떻게
> 고쳤는지는 `CHANGELOG.md`와 [live-session.md](./live-session.md) 헤더에 있다. 이 문서는 판정 자체가
> 아니라 **왜 그것이 구멍인지**를 남기려고 보존한다. 인용된 줄 번호는 리뷰 시점(`2f06127`)의 것이고
> 수정 이후 코드와는 어긋난다.
>
> 재검증 라운드에서 넷이 더 나왔다: async **클래스 기반** 뷰의 거절 경로(허용 경로가 가리고 있었다),
> 로그아웃 없는 재로그인이 옛 세대를 남기는 것, 세션 재확인이 지문만 대조해 세션 데이터를 읽는 정책을
> 놓치는 것, popstate 거절이 이미 예약된 작업을 취소하지 않는 것. 넷 다 고쳤다.
>
> "질문 1의 세 한계"에 대한 평결(불가피 / 완화 가능 / 닫을 수 있음)이 문서 끝에 있다.


검토 대상: `main`의 `2f06127`, 2026-09-09. 지정된 핵심 코드·설계·사용자 문서·테스트를 읽었다. 라이브러리 소스는 변경하지 않았다. 결론은 **현재 구현을 로그아웃까지 보장하는 인증 경계로 승인하기 어렵다**. 서명 봉투의 결합 자체보다, 마운트 실패 처리와 인증 수명 관리에 실제 구멍이 있다.

검증 환경은 Python 3.12.14 / Django 6.0이다. 기존 `tests/test_live_session.py`와 `tests/test_checks.py`는 **71개 통과**, `tests/js/live-session.test.mjs`는 **7개 통과**했다. 별도 `.wireview/test_review_probes.py`의 **8개 재현 테스트도 통과**했다. 여기서 통과는 아래 결함이 존재한다는 단언이 성공했다는 뜻이다. `.wireview/review_boost_probe.mjs`는 실제 boost 소스를 읽고 DOM·fetch·morph를 대역으로 바꿔 호출 순서를 검증한다. 브라우저 E2E와 실제 다중 워커 장애 실험은 실행하지 않았다. GitHub 이슈 조회는 네트워크 제한으로 실패했다.

재현 명령:

```sh
DJANGO_ALLOW_ASYNC_UNSAFE=1 .venv/bin/python -m pytest .wireview/test_review_probes.py -q
node .wireview/review_boost_probe.mjs
```

## 확인된 지적

### [심각도: 높음] 마운트 훅이 예외를 던지면 거절 대상이 살아남고 LiveComponent는 렌더까지 된다
- **어디**: `wireview/repository.py:369`, `wireview/consumer.py:203`, `wireview/consumer.py:950`, `wireview/core/component.py:585`
- **무엇**: `{"halt": True}`만 안전하게 처리한다. 세션 훅 또는 컴포넌트 훅이 `PermissionDenied`나 DB 오류를 던지면 root는 클라이언트에 remove만 보내고 저장소에 남는다. LiveComponent는 예외를 로그로 삼킨 뒤 `has_joined=True`가 되고, `halted`에 없어서 정상 렌더 대상이 된다. `has_mounted`도 훅 실행 전에 설정하므로 실패한 인스턴스의 후속 `_mount`가 검사를 재시도하지 않는다.
- **재현/논거**: `test_root_hook_exception_leaves_event_target`에서 세션 훅이 `PermissionDenied`를 던진 뒤 `dispatch_event('root', 'bump', ...)`가 실제로 상태를 바꿨다. `test_hook_exception_leaves_live_child_rendered`에서는 같은 예외를 던진 `kid`가 저장소와 render 메시지의 `children`에 모두 남았다. 프로토콜상 권장 거절 방식이 halt라는 사실은 예상치 못한 인가 조회 실패 때 허용해도 된다는 근거가 아니다.
- **고칠 수 있나**: 닫을 수 있다. 마운트가 정상 완료되기 전까지 이벤트·렌더 대상 등록을 유예하거나, 예외 경로에서도 halt와 동일하게 freeze·제거·자식 및 자원 정리를 수행한다. `has_mounted`/`has_joined`는 실패를 허용 완료로 해석하지 않도록 별도 실패 상태를 둔다. 대가는 초기화 실패 시 해당 컴포넌트가 사라지거나 오류 응답을 내는 것이다. 허용 전 훅이 시작한 작업·구독도 정리해야 한다.

### [심각도: 높음] 중첩 일반 Component는 세션 훅을 거치지 않고 HTML과 이벤트 대상을 만든다
- **어디**: `wireview/templatetags/wireview.py:112`, `wireview/templatetags/wireview.py:153`, `wireview/repository.py:171`, `wireview/repository.py:405`; `docs/features/live-session.md:74`, `docs/features/lifecycle-hooks.md:83`
- **무엇**: 라이브 부모의 `{% component %}`는 `_live_sessions` 이름만 맞으면 `_mount` 없이 등록·렌더된다. 페이지의 `on_mount`가 특정 자식에 대해 halt하려고 해도 실행되지 않는다. 이는 이미 문서화한 HTML 누출에 더해 **인가 훅을 거치지 않은 객체가 이벤트를 받는 문제**다.
- **재현/논거**: `test_nested_component_session_hook_does_not_run`은 `admin` 안의 부모가 `_live_sessions={"lsx-admin"}`인 일반 자식을 렌더하게 하고, 세션 훅은 그 자식 id에만 halt하도록 했다. 자식 훅 호출은 없었지만 `secret` HTML과 `guarded` 동적 값이 render 프레임에 포함되었고, 직접 보낸 `bump` 이벤트도 실행되었다. `tag_header`는 이 인스턴스에 `data-is-live="true"`까지 붙이므로 문서의 “자기 join은 그 뒤에야 훅을 돌린다”도 항상 성립하지 않는다. 페이지 `authorize` 자체를 우회한 것은 아니지만, 페이지에 속한 모든 컴포넌트에 세션 훅이 적용된다는 계약은 깨진다.
- **고칠 수 있나**: 닫을 수 있다. 일반 중첩 컴포넌트도 렌더 전 비동기 마운트 단계로 수집·정착시키거나 LiveComponent처럼 참조를 먼저 만들고 허용 후 출력한다. 단기적으로 세션 훅이 있는 페이지의 중첩 일반 Component를 거절하거나 LiveComponent 사용을 강제할 수 있다. 대가는 렌더 구조 변경 또는 호환성 제한이다. `declaration_allows`만으로는 같은 세션 내부의 자식별 훅 정책을 대체할 수 없다. 문서에서 컴포넌트 훅의 한계를 설명한 것만으로 세션 훅의 보편 적용 주장을 정당화할 수 없다.

### [심각도: 높음] 첫 join을 로그아웃 뒤로 미루면 정상 브로커에서도 무효화를 놓친다
- **어디**: `wireview/consumer.py:95`, `wireview/consumer.py:109`, `wireview/consumer.py:274`, `wireview/consumer.py:280`, `wireview/consumer.py:304`
- **무엇**: connect에서 사용자·세션·지문을 확보하지만 인증 토픽 구독은 첫 허용 join 뒤에 한다. 그 사이의 로그아웃 발행은 해당 소켓에 도달하지 않는다. 이후 join은 connect 때의 사용자와 세션만 검사하므로 폐기된 로그인으로 컴포넌트를 만들 수 있다. 짧은 우연한 경쟁만이 아니라, 클라이언트가 첫 join 전송을 의도적으로 늦출 수 있는 창이다.
- **재현/논거**: 순서는 로그인 상태로 connect → join 보류 → 다른 요청에서 logout → 기존 정상 토큰으로 첫 join이다. `test_logout_before_first_join_is_missed`에서 consumer 스냅샷과 기록용 브로커로 이 순서를 재현했고, 무효화 발행 때 구독이 없으며 그 뒤 join·`bump`가 성공함을 확인했다. 실제 DB/cache 세션을 삭제하더라도 이 코드 경로에는 백엔드를 재조회하는 단계가 없다는 것이 코드 근거다. 이 재현 자체는 실소켓 E2E가 아니다.
- **고칠 수 있나**: 닫을 수 있다. 인증 세대의 폐기 기록을 공유 저장소에 먼저 남기고, 구독을 설치한 다음 저장소에서 현재 유효성을 다시 검사해야 한다. 서버 저장형 세션은 새 SessionStore로 존재·만료·사용자를 다시 확인할 수 있다. 구독을 connect로 당기는 것만으로는 인증 스냅샷 획득과 구독 사이의 경쟁이 남는다. 대가는 join 시 공유 저장소 조회와 실패 시 거절하는 정책이다.

### [심각도: 높음] signed-cookie의 session_key를 로그인 세대로 쓰면 일반 저장만으로 토픽이 바뀐다
- **어디**: `wireview/core/live_session.py:111`, `wireview/core/live_session.py:129`, `wireview/core/session.py:80`, `wireview/consumer.py:306`; `docs/features/live-session.md:160`
- **무엇**: “signed-cookie의 session_key는 항상 None”이라는 전제가 틀렸다. 설치된 Django의 해당 SessionStore는 **서명된 쿠키 문자열 전체**를 session_key로 쓴다. 장바구니 등 인증과 무관한 세션 변경에도 문자열과 지문이 바뀐다. 기존 소켓은 옛 토픽을 듣는데, 나중의 로그아웃은 새 쿠키의 토픽으로 발행하므로 소켓이 닫히지 않는다. HTTP 렌더 후 SessionMiddleware가 쿠키를 저장하면 HTML의 지문과 곧이어 연결할 소켓의 지문도 어긋난다.
- **재현/논거**: `test_cookie_changes_topic_and_old_cookie_replays`에서 실제 signed-cookie SessionStore에 login·save 후 `cart`만 바꾸어 저장했는데 지문이 바뀌었다. `test_cookie_response_save_changes_http_fingerprint`는 렌더 시점 지문을 구한 뒤 실제 SessionMiddleware.process_response가 발급한 쿠키로 지문을 다시 구해 불일치를 확인한다. 매 응답 세션을 갱신하는 앱이면 반복 reload로 이어질 수 있다. DB/cache는 일반 저장으로 키가 바뀌지는 않지만, 명시적인 `cycle_key()`나 `update_session_auth_hash()` 뒤의 logout이 옛 소켓 토픽을 찾지 못하는 같은 종류의 수명 관리 문제는 남는다.
- **고칠 수 있나**: 닫을 수 있다. 쿠키 표현과 독립된 무작위 로그인 세대 nonce를 로그인 때 만들어 저장하고, 일반 세션 저장에는 유지한다. 로그인·세대 교체 시 옛 세대를 명시적으로 폐기하며 새 nonce는 HTTP 상태 발급 전에 확정한다. 세션 키를 지문에서 빼고 사용자 pk·auth hash만 남기는 수정은 같은 사용자의 여러 로그인 세대를 구분하지 못하므로 충분하지 않다. 대가는 로그인/세대 교체 통합과 마이그레이션이다.

### [심각도: 높음] signed-cookie의 옛 쿠키와 옛 상태를 함께 재전송하면 로그아웃 후 새 연결도 허용된다
- **어디**: `wireview/core/live_session.py:320`, `wireview/consumer.py:274`; `docs/features/live-session.md:160`, `docs/design/live-session.md`의 §6-1 AC6
- **무엇**: 현재 무효화는 일회성 발행뿐이며 폐기 기록이 없다. 로그인 당시 signed-cookie와 정상 상태 토큰을 보관하면 logout 뒤 새 소켓을 같은 쿠키로 열 수 있다. Django가 같은 사용자로 인증하고 지문도 같으므로 `authorize=lambda ctx: ctx.user.is_staff` 같은 정책은 통과한다. 정상 브라우저가 새 쿠키를 쓰는 경우와 보관된 쿠키 재사용은 다른 위협이다.
- **재현/논거**: 실제 Django login/save → 옛 쿠키 보관 → logout → 옛 쿠키를 넣은 새 Request에서 `get_user` 순서를 실행했다. 사용자가 그대로 인증되고 지문도 옛 값과 같았다(`test_cookie_changes_topic_and_old_cookie_replays`). 이것을 join이 거절할 추가 조건은 없다. Django도 signed-cookie 세션은 로그아웃으로 서버 측 폐기되지 않는다고 명시한다. [Django 세션 문서](https://docs.djangoproject.com/en/6.0/topics/http/sessions/#using-cookie-based-sessions). 일반 DB/cache 백엔드는 삭제된 세션을 새 연결에서 다시 읽으므로 이 특정 재생과는 다르다.
- **고칠 수 있나**: 닫을 수 있다. nonce별 폐기 목록 또는 서버 측 유효 세대 레코드를 보관하고 새 연결·재검증 때 조회한다. 보관 기간은 옛 쿠키와 상태가 유효할 수 있는 기간을 덮어야 한다. 완전한 무상태 signed-cookie만 유지한다면 명시적 로그아웃의 사후 폐기를 보장할 수 없으며 만료 시간으로만 노출을 줄일 수 있다. 이 경우 백엔드 제한을 문서와 system check에 밝혀야 한다. 앞 항목의 안정된 nonce만 도입하고 폐기 조회를 생략해도 이 문제는 남는다.

### [심각도: 높음] 브로커를 사용해도 일회성 그룹 발행만으로 모든 기존 소켓의 폐기를 보장할 수 없다
- **어디**: `wireview/consumer.py:304`, `wireview/core/live_session.py:344`, `wireview/core/transport.py:80`; `docs/features/live-session.md:164`
- **무엇**: 메시지 유실이나 그룹 멤버십 만료 후 로그아웃은 재시도·재검증으로 보완되지 않는다. 인증 토픽은 한 번만 구독하고 갱신하지 않는다. 문서가 다중 워커 InMemory 문제만 말하면 공유 브로커에서는 로그아웃 폐기가 보장되는 것으로 읽힌다.
- **재현/논거**: Channels 규격은 그룹 발행의 용량 초과 시 조용한 메시지 폐기와, 마지막 group_add 이후 `group_expiry` 경과 시 멤버십 만료를 허용/요구한다. `_auth_topic`이 있으면 재구독하지 않으므로 오래 열린 소켓에 영향을 줄 수 있다. [Channels 채널 레이어 규격](https://channels.readthedocs.io/en/stable/channel_layer_spec.html). 이 항목은 코드와 규격으로 확인했으며 Redis/NATS 실부하 재현은 하지 않았다. 구체 백엔드별 만료 시간·유실 조건은 별도 확인이 필요하지만, 추상 Broker 계약 자체에 영속적인 폐기 보장이 없다는 결론은 확실하다.
- **고칠 수 있나**: 인증 지속 문제는 닫을 수 있다. 공유 폐기 상태를 정본으로 두고 pub/sub를 빠른 종료 알림으로 쓰며, 주기적 또는 이벤트 직전 검증으로 유실을 회수한다. 멤버십 갱신만 추가하면 만료는 줄어들지만 유실은 남는다. 대가는 조회/갱신 트래픽과 저장소 장애 시 허용하지 않는 운영 정책이다. 네트워크 단절 중 모든 물리 소켓을 즉시 닫는 보장과, 폐기된 사용자의 다음 민감한 작업을 차단하는 보장은 구분해야 한다.

### [심각도: 높음] LEGACY 허용 시 뷰에만 추가한 페이지 정책은 v1 첫 join에 적용되지 않는다
- **어디**: `wireview/core/state.py:266`, `wireview/consumer.py:262`, `wireview/consumer.py:267`, `wireview/core/live_session.py:146`; `docs/features/live-session.md:204`
- **무엇**: “정책이 걸린 페이지는 v1도 거절한다”는 주장은 `_live_sessions`를 선언했거나 연결에 이미 경계가 확정된 경우에만 맞는다. 서버는 첫 join에 대해 실제 페이지 URL의 정책을 확인하지 않는다. 컴포넌트의 `_live_sessions`가 기본값인 상태에서 뷰에만 `@admin.view`를 붙이고 LEGACY를 허용하면, 이전 정상 v1 토큰이 첫 join에서 빈 정책을 정한다. 페이지의 authorize와 세션 훅이 모두 빠진다.
- **재현/논거**: `test_v1_first_join_bypasses_page_only_policy`는 모든 접근을 거부하는 정책이 등록된 상황에서 소속 선언 없는 `LsxFree`의 정상 v1 토큰을 첫 join으로 보냈다. 저장소에 컴포넌트가 생성되고 정책은 None이었다. 실제 적용 시나리오는 구버전 페이지가 발급한 토큰을 갖고 있는 사용자가 v2 배포에서 새로 뷰에 붙은 경계를 우회하는 것이다. 기존 테스트는 `_live_sessions`를 선언한 `LsxGuarded`만 검사하므로 이 조건을 놓친다. 기본값인 LEGACY 비허용에서는 발생하지 않는다.
- **고칠 수 있나**: 닫을 수 있다. 경계를 도입하는 배포에서 LEGACY를 계속 거절하거나, v1 허용 대상을 명시적으로 공개 클래스에 한정한다. 다른 방식은 서버가 검증하는 페이지 bootstrap 티켓으로 연결의 경계를 먼저 확정하는 것이다. 클라이언트가 보내는 서명 없는 경계 이름이나 URL만 신뢰해서는 안 된다. 단기 대가는 구버전 탭의 재초기화이며, 문서는 페이지 데코레이터만으로 LEGACY가 안전해지지 않는다고 수정해야 한다.

### [심각도: 중간] boost는 목적지 경계 검증 전에 기존 컴포넌트에 params_changed를 보낸다
- **어디**: `wireview/static/wireview/wireview-boost.js:234`, `wireview/static/wireview/wireview-boost.js:264`, `wireview/static/wireview/wireview.js:139`, `wireview/static/wireview/wireview.js:289`
- **무엇**: `replaceContentFromUrl`은 fetch보다 먼저 `sendNewLocation()`을 호출한다. wireview.js의 listener는 즉시 `sendQueryString()` → `params_changed`를 보낸다. push의 `.then(sameSession)` 검사는 이 선행 전송을 막지 못한다. 경계 밖으로 이동할 때 옛 인증 문맥의 컴포넌트가 목적지 쿼리를 처리하고 렌더할 수 있다.
- **재현/논거**: JS 재현에서 다른 경계 응답의 호출 순서는 `params_changed → fetch → assign`이었다. popstate에서는 같은 알림이 두 번 나갔다. 이는 설계 §3-3의 “통과 전 params_changed 전파를 막는다”와 반대다. 이 사실만으로 서버 인증 우회라고 주장하지는 않는다. 악성 클라이언트는 원래 임의 params를 보낼 수 있으므로 객체별 인가는 별도다. 정상 클라이언트에서도 잘못된 순서로 훅의 조회·부작용이 발생한다는 결함이다.
- **고칠 수 있나**: 닫을 수 있다. 네비게이션 시작 알림과 서버에 보내는 params 알림을 분리하고, 최종 응답 검증 및 실제 DOM 반영 뒤에 단 한 번 전송한다. 현재 `replaceBodyContent`는 requestAnimationFrame을 예약하고 바로 반환하므로 `await HistoryCache.push()` 자체도 DOM 반영 완료를 보장하지 않는다. 렌더 완료 promise와 최신 네비게이션 식별자를 도입하는 대가가 있다.

### [심각도: 중간] popstate가 예약한 캐시 morph는 뒤늦은 경계 거절로 취소되지 않는다
- **어디**: `wireview/static/wireview/wireview-boost.js:140`, `wireview/static/wireview/wireview-boost.js:238`, `wireview/static/wireview/wireview-boost.js:260`
- **무엇**: history의 이름이 현재 이름과 같으면 캐시 body를 먼저 예약한다. 그 URL이 현재 서버에서는 다른 경계로 변경되었거나 로그인 페이지로 리다이렉트되더라도, fetch가 `location.assign()`을 결정한 뒤 기존 예약은 살아 있다. 같은 이름이지만 인증 세대가 바뀐 캐시도 이름 비교만으로 통과한다.
- **재현/논거**: JS 대역 재현에서 admin으로 기록된 history 캐시와 public 최종 응답을 넣었다. `assign` 뒤에도 예약된 콜백이 남아 실행 시 `morph`를 수행했다. 실제 브라우저에서 새 문서가 커밋되기 전 이 콜백이 실행되는 구체 타이밍은 **확신 없음**이며 E2E로 확인하지 않았다. 다만 취소나 세대 재검사가 없고, fetch가 느리면 검증 전에 캐시가 보인다는 코드 사실은 확실하다. 이전에 브라우저로 전달된 HTML을 서버가 회수할 수 있다는 주장은 아니다.
- **고칠 수 있나**: 닫을 수 있다. 인증 경계 안의 캐시 복원은 서버 확인 뒤 수행하거나, 적어도 네비게이션 세대와 취소 토큰을 두어 떠나는 문서의 예약 작업을 폐기한다. 경계 이름뿐 아니라 인증 세대의 유효성도 서버에서 확인해야 한다. 대가는 즉시 뒤로가기 표시를 일부 포기하는 것이다.

### [심각도: 중간] @session.view가 async 함수 뷰를 동기 함수로 바꾼다
- **어디**: `wireview/core/live_session.py:223`; `docs/features/live-session.md:96`, `docs/features/lifecycle-hooks.md:74`
- **무엇**: 데코레이터는 항상 `def wrapper`를 만든다. async 대상에서 coroutine을 반환할 뿐 await하지 않고, wrapper 자체도 coroutine 함수로 표시되지 않는다. Django의 함수 뷰 처리 경로에서는 HttpResponse 대신 미실행 coroutine을 받아 오류가 된다. 보조 스레드로 HTTP 훅을 실행하는 처리가 있어도 뷰 데코레이터 단계의 이 문제를 해결하지 못한다.
- **재현/논거**: `test_async_view_wrapper_is_sync`에서 장식된 async 함수에 대해 coroutine 함수 판정은 False이고 반환값은 coroutine임을 확인했다. Django 설치 소스의 뷰 실행/응답 검사와 일치한다. 전체 Django Client 요청 재현은 수행하지 않았으므로 테스트가 직접 확인한 범위는 wrapper의 형태와 반환값이다. 단순 인증 우회가 아니라 async 함수 뷰 사용 시 장애다.
- **고칠 수 있나**: 닫을 수 있다. 대상의 coroutine 여부에 따라 async wrapper를 제공하고, 동기 authorize는 적절한 sync/async 브리지에서 실행한 뒤 대상을 await한다. CBV도 async dispatch/handler와 조합해 Django 요청 경로 테스트를 해야 한다. 대가는 두 wrapper와 async 호환성 테스트다. 지원하지 않을 계획이면 문서에서 동기 뷰로 제한하고 등록 단계에서 명확히 거절해야 한다.

### [심각도: 낮음] authorize의 실제 호출 횟수와 재검증 데이터에 대한 문장이 부정확하다
- **어디**: `wireview/consumer.py:276`, `wireview/core/component.py:614`; `docs/features/live-session.md:172`
- **무엇**: 구현은 연결당 한 번이 아니라 **매 root join마다** authorize를 실행한다. 같은 연결의 추가 컴포넌트 join이나 재join에서도 술어가 돈다. 그러나 사용자 객체와 세션 스냅샷은 그대로이므로 `ctx.user.is_staff` 같은 속성은 최신화되지 않는다. 반대로 술어가 직접 DB를 조회한다면 같은 연결에서도 이후 join에서 권한 변경을 볼 수 있다.
- **재현/논거**: `_enter_live_session`에는 authorize 완료를 캐시하는 조건이 없고, `name/auth` 검증 뒤 매번 `policy.allows`를 호출한다. 즉 “열려 있는 동안 다시 묻지 않는다”와 “다음 연결에서야 반영된다”는 일반화가 모두 정확하지 않다. 이미 마운트한 객체의 이벤트를 재인가하지 않는다는 핵심 한계는 그대로다.
- **고칠 수 있나**: 문서를 “각 join에서 connect 시점 문맥으로 검사하며, 일반 이벤트마다 재검증하지 않는다”로 고친다. TTL 재검증 구현 시에는 술어 반복뿐 아니라 사용자·권한 캐시·세션 로딩까지 새로 해야 한다. 기능을 연결당 한 번으로 바꾸는 것은 별도의 비용/보장 선택이다.

## 의심했으나 독립적인 취약점으로 확정하지 않은 항목

### [심각도: 무효] HMAC 32자리 절단 자체가 인증 경계를 깨지는 않는다
- **어디**: `wireview/core/live_session.py:132`
- **무엇**: SHA-256 HMAC의 32 hex 문자는 128비트다. 여기서 현실적인 위조·표적 충돌 취약점은 발견하지 못했다. 동일한 익명 입력이 동일 지문을 갖는 것은 절단 충돌이 아니라 입력이 같은 결과다.
- **재현/논거**: 로그인 사용자 pk, 세션 키, auth hash를 결합한다. 표준 DB/cache 로그인에서 새 세션 키를 받으면 지문이 달라진다. 실제 문제는 위의 쿠키 표현 변화·폐기 조회 부재이지 digest 길이가 아니다. 다만 pk가 없고 세션도 없는 익명 경계 소켓들은 같은 토픽을 공유하므로, 세션 없는 익명 logout 발행이 그 소켓들을 함께 끊을 수 있다. 이는 익명별 격리/가용성 문제이며 다른 사용자의 인증 권한 획득으로 확인되지는 않았다.
- **고칠 수 있나**: 절단은 유지할 수 있다. 익명별 연결 수명 격리가 필요하면 서버가 발급한 익명 nonce를 사용하거나 인증 상태 없는 logout의 불필요한 발행을 생략한다. nonce는 세션·쿠키 발급 및 캐시 효율에 비용을 준다.

### [심각도: 무효] v2에서 첫 join 순서만 바꾸어 보호 상태의 s/a를 벗길 수는 없다
- **어디**: `wireview/consumer.py:261`, `wireview/core/state.py:333`
- **무엇**: 공개 v2 토큰을 먼저 보내면 연결이 공개로 고정되고 뒤의 admin 토큰은 이름 불일치로 거절된다. admin 토큰을 먼저 보내면 auth와 authorize를 검사한다. 두 정상 토큰의 조합만으로 이를 통과하는 반례는 찾지 못했다.
- **재현/논거**: 클래스·정책·인증 값이 같은 서명 봉투에 들어 있고 이후 경계를 혼합하지 않는다. 검증 실패 전 이름을 먼저 고정하는 탓에 잘못된 첫 join이 그 연결의 재시도를 막을 수 있지만, 클라이언트 자신이 제어하는 연결의 가용성 문제다. 위 LEGACY 허용과 로그아웃 전 connect 문제는 별개로 유효하다.
- **고칠 수 있나**: 이름 확정은 모든 검증이 성공한 뒤로 옮기는 편이 낫지만 그 자체를 높은 심각도의 수정으로 보지는 않는다. 실제 라우트 소속까지 보장하려면 별도의 서버 검증 페이지 티켓이 필요하다.

### [심각도: 무효] 경계가 다른 자식 상태를 버리고 부모 props로 다시 만드는 선택 자체는 합리적이다
- **어디**: `wireview/consumer.py:169`, `wireview/repository.py:233`, `wireview/consumer.py:956`
- **무엇**: 잘못된 경계의 상태를 받았다는 이유만으로 서버 템플릿이 지정한 자식 자체까지 금지할 필요는 없다. 외부 상태를 폐기하고 부모의 정상 렌더 props로 만드는 것은 합리적이다.
- **재현/논거**: `_child_boundary_refusal`이 거절한 payload는 이번 restore map에 들어가지 않고, LiveComponent는 부모의 정책으로 `_mount`를 거친다. 다만 이것의 안전성은 위에서 발견한 예외 처리와 일반 중첩 컴포넌트 경로를 고친다는 조건이 필요하다. “자식 재생성이 문제”와 “재생성 뒤 인가가 누락된다”를 혼동하면 안 된다.
- **고칠 수 있나**: 상태 폐기 방식은 유지하고 모든 생성 경로의 허용 전 렌더·이벤트 등록 금지를 공통 불변조건으로 만든다. 클래스·id·부모 귀속에 대한 더 강한 restore 검증은 별도 보강 대상이며, 이번 검토에서는 그 자체의 객체 권한 침해를 확정하지 않았다.

### [심각도: 무효] HTTP 보조 스레드 분기 자체에서 경계 판정을 건너뛰는 코드는 발견하지 못했다
- **어디**: `wireview/templatetags/wireview.py:105`, `wireview/templatetags/wireview.py:117`
- **무엇**: 두 분기 모두 동일 객체의 `_mount`를 호출하고 결과를 기다린 뒤 halt면 제거한다. 이 분기만으로 인증이 우회된다는 근거는 없다.
- **재현/논거**: 다만 보조 스레드는 요청 스레드의 DB 트랜잭션과 동일하지 않고, raw ThreadPoolExecutor는 호출 문맥의 ContextVar 전파를 자동 보장하지 않는다. 요청 중 미커밋 데이터를 읽거나 ContextVar 기반 테넌트를 쓰는 앱에서 판정 차이가 날 가능성은 있지만 **확신 없음**이며 이번에 그런 앱의 실제 반례를 만들지는 않았다. 문서의 “어느 쪽이든 페이지는 그려진다”는 모든 훅·트랜잭션 조합의 보장으로 확대하면 안 된다.
- **고칠 수 있나**: 권장 경로를 템플릿 렌더 전체의 적절한 sync/async 브리지로 정하고, 별도 스레드 경로의 문맥·트랜잭션 계약을 명시한다. 단순히 새 스레드에 루프를 만들었다고 async 루프에서 동기 ORM을 직접 써도 되는 것은 아니다.

### [심각도: 무효] body에 있는 단일 경계 meta나 빈 response.url만으로 경계 우회는 확인되지 않았다
- **어디**: `wireview/static/wireview/live-session.mjs:35`, `wireview/static/wireview/wireview-boost.js:238`
- **무엇**: `querySelector`는 head와 body 모두에서 meta를 찾는다. 단일 meta가 body 안에 있어도 같은 이름의 응답만 morph하므로 위치만으로 경계가 바뀌지는 않는다. `response.url`이 비면 원래 요청 URL을 사용하는 fallback도 이미 있다.
- **재현/논거**: 정상적인 단일 meta에 대한 코드 판정이다. head/body 중복 meta 또는 이동마다 meta 위치가 달라져 사라지는 경우에는 불필요한 전체 로드가 생길 수 있으나 이번에 인증 우회는 확인하지 않았다. fetch는 이 코드에서 manual redirect나 no-cors를 요청하지 않는다. opaque 응답을 별도 환경에서 주입하는 경우의 최종 URL 추론까지 보장된다는 뜻은 아니다. 빈 응답·실패 상태를 HTML로 파싱하는 일반적인 복구 문제와 경계 우회를 구분해야 한다.
- **고칠 수 있나**: meta를 head에 한 번만 두는 계약을 문서화하고, 실패·불투명 응답은 원래 URL로 전체 이동하도록 명시적으로 처리하면 견고해진다. 정상 응답의 최종 URL fallback을 없앨 이유는 없다.

## 질문 1의 세 한계 각각에 대한 평결

### 1. 열린 연결의 권한 변경 미반영 — **완화 가능**

이벤트당 ORM 왕복을 피하려는 **설계 선택**이지 구조적 불가능이 아니다. 또한 현재 코드는 정확히는 매 join에서 술어를 다시 실행하며, 매 이벤트에서 사용자와 세션을 갱신하지 않는다.

실용적인 선택은 연결당 재검증 lease/TTL이다. 만료 후 첫 이벤트는 새 SessionStore로 세션 존재·만료를 읽고, 현재 사용자와 auth hash를 확인하고, 권한 캐시를 새로 만든 뒤 authorize가 통과해야 실행한다. 유휴 연결도 빨리 닫으려면 서버 타이머를 추가한다. 일반 이벤트 수가 아니라 활성 연결 수/TTL에 비례하는 조회 비용으로 노출 시간을 제한할 수 있다. 공유 로그인 세대·사용자 권한 버전과 pub/sub를 결합하면 보통은 즉시 반영하고 누락은 TTL로 회수할 수 있다. Channels도 장기 consumer에서 주기적 인증 확인을 안내한다. 다만 설치 코드에서 `get_user(scope)`는 기존 SessionStore를 사용하므로, 캐시된 session을 그대로 넘기는 것만으로 세션 삭제를 새로 읽는다고 가정하면 안 된다. [Channels 인증 문서](https://channels.readthedocs.io/en/stable/topics/authentication.html).

`params_changed`에서만 검사하면 네비게이션하지 않는 소켓은 계속 살아 있으므로 보편적인 해결이 아니다. 민감한 이벤트만 직전 검사하는 방식은 실용적이지만 모든 관련 실행 경로를 포함해야 한다. 클라이언트가 직접 부를 수 있는 이벤트뿐 아니라 서버 메일·업로드·비동기 작업의 효과도 계약에 맞게 다룬다. 엄격히 “권한 회수 커밋 뒤 어떤 변경도 허용하지 않음”이 필요하면 민감한 작업과 버전 검증을 같은 트랜잭션/동기화 경계에 두어 검사-사용 사이의 경쟁까지 닫아야 한다. 비용 없이 즉시 반영하는 선택지는 없지만 이벤트마다 일반 authorize 전체를 실행하는 것만이 유일한 선택지도 아니다.

### 2. InMemory 다중 워커의 로그아웃 알림 누락 — **완화 가능**

서로 격리된 프로세스의 메모리만으로 다른 프로세스의 로그아웃을 알 수 없다는 점은 불가피하다. 하지만 **반드시 메시지 브로커여야 하는 것은 아니다**. 공유 DB의 폐기 세대 조회·폴링, 공유 캐시와 만료 lease 등으로도 전달할 수 있다. 브로커를 쓰면 신속해지지만 위에서 확인했듯 그것만으로 사후 폐기가 완성되지는 않는다.

현재 `W006`은 `check --deploy`에서만 나오는 일반 브로드캐스트 경고다(`wireview/checks.py:185`, `wireview/checks.py:428`). live_session이 선언된 경우 일반 `check`에도 “다른 워커의 로그아웃이 기존 권한을 종료하지 못한다”는 보안 의미를 명시할 수 있다. 운영의 다중 워커 여부는 설정만으로 확정하기 어려우므로 명시적인 배포 모드/엄격 검사 옵션을 두고, 그 모드에서 InMemory를 오류로 올리는 것이 낫다. `check --deploy --fail-level WARNING`을 배포 파이프라인에 넣는 즉시 가능한 대안도 있다. 외부 프로세스가 몇 개 떠 있는지 무조건 추정하여 개발 단일 프로세스까지 실패시키는 것은 피한다.

공유 폐기 상태와 이벤트 직전 검증으로 “폐기 후 새 민감 작업 거절”은 닫을 수 있다. 네트워크 단절 때 모든 소켓의 물리적 즉시 종료까지 보장하려면 fail-closed lease와 장애 대응 비용을 받아들여야 한다. 따라서 주어진 낮은 비용·알림 중심 구조의 한계는 완화 가능으로 판정한다.

### 3. v1 → v2 전환 시 한 번 reload — **닫을 수 있음**

전체 문서 reload는 보안상 유일한 복구 방식이 아니다. 현재 쿠키로 인증하는 HTTP bootstrap에서 **현재 페이지를 다시 렌더해 새 경계와 v2 토큰을 받고**, 기존 소켓을 종료·재연결한 후 새 상태로 초기화하면 전체 문서 로드 없이도 같은 보안 재설정을 할 수 있다. 대가는 bootstrap 프로토콜, 상태/DOM 교체의 원자성, 리다이렉트·실패 처리와 임시 UI 상태 손실이다. 두 단계 배포로 먼저 v2 티켓을 발급하고 오래된 토큰의 최대 수명만큼 기다리는 운영 대안도 있다.

다만 v1에는 원래 발급한 인증 세대와 페이지 경계가 없으므로 **모든 기존 v1 상태를 손실 없이 새 경계로 승격하는 것은 그 토큰만으로 안전하게 할 수 없다**. 부족한 근거는 현재 사용자로 다시 인가·렌더하여 채우거나 미리 발급한 신뢰 가능한 메타데이터로 보완해야 한다. 서명 없는 클라이언트 경계 이름으로 보충해서는 안 된다.

따라서 기본값의 reload는 단순하고 타당한 절충이다. `STATE_ACCEPT_LEGACY`로 v1을 경계 없음으로 읽는 디코딩도 사실에 충실하다. 그러나 그 설정을 새 페이지 경계와 함께 안전한 범용 롤아웃 창으로 설명하는 것은 옳지 않다. 위의 v1 첫 join 반례를 닫거나 공개 클래스에 한정해야 한다. “reload 자체는 없앨 수 있음”과 “출처가 없는 옛 상태를 그대로 신뢰할 수 있음”은 별개다.
