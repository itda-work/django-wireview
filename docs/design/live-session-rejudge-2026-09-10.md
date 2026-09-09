# live_session 재판정 (Codex gpt-6-astra, 2026-09-10)

> 5라운드. 4라운드의 차단 둘을 고친 상태에 대한 재판정이고, 결론은 **다시 보류**였다 — 해시 제거가
> nonce 없는 세션에 **새 회귀**를 만들었고(로그아웃 뒤 지연된 첫 join이 통과), 무경계 토큰의 전환
> 계약이 "14일 기다리면 소진된다"는 틀린 전제 위에 있었다(그 토큰은 **스스로 갱신된다**).
>
> 둘 다 고쳤다. 재읽기가 이제 지문만이 아니라 **세션이 그 사용자를 여전히 인증하는지** 묻고, 전환
> 절차는 기존 연결 종료와 `SIGNING_KEY_FALLBACKS = []`를 명시한다. 그 아래 넷도 반영했다.
> 무엇을 어떻게 고쳤는지는 `CHANGELOG.md`에.
>
> 인용된 줄 번호는 리뷰 시점(`27969c2`)의 것이다.


검토일: 2026-09-10. 대상: `main`, `27969c287c236ab4c085c2e14ceb9409b15a206c`.
설계·3라운드·계약 테스트 리뷰·4라운드 원문과 현재 구현을 대조했다. 이전 리뷰의 판정이나 최신 커밋의 설명을 승인 근거로 삼지 않았다.

## F. 릴리스 판정

**릴리스 보류를 유지한다.** 비밀번호 변경 뒤 로그아웃이 기존 구독 토픽을 놓치는 결함은 수정됐고, 원래 재현기 다섯 개의 실패도 의도한 결함 단언에서 확인했다. 그러나 해시를 제거하면서 **nonce 없는 인증 세션을 삭제해도 지연된 첫 join이 허용되는 회귀**가 생겼다. 무경계 v2 토큰의 전환을 명시적 운영 계약으로 다루는 선택은 수용 가능하지만, 현재 설명은 **토큰 재발급으로 노출이 연장되는 점, 기존 소켓의 종료, Django fallback 상속**을 충분히 반영하지 않는다. 이 둘을 아래 최소 기준으로 닫기 전에는 승인하지 않는다. 하네스의 종료 timeout 잔여 문제와 문서 불일치는 별도로 보고하며, 확인하지 않은 브라우저·브로커 장애를 새 결함으로 세지 않는다.

### 차단 항목과 최소 수용 기준

| 차단 | 최소 수용 기준 |
|---|---|
| nonce 없는 세션 삭제 후 지연 join 허용 | 첫 경계 join에서 새로 읽은 인증 세션의 존재·사용자 귀속을 확인한다. nonce 없는 세션은 경계 진입을 거절하고 재로그인을 요구하는 방식도 가능하다. **정상 nonce 있음/없음 대조군, 삭제·만료 뒤 첫 join과 반복 시도 거절, HTML·이벤트 효과 없음**을 실행으로 고정한다. 토픽 안정성을 다시 깨는 auth hash 단순 복원만으로 끝내지 않는다. |
| 무경계 토큰 전환 계약의 불완전함 | 보호 클래스의 `_live_sessions` 선언, request processor 확인, **정책 전환 시 기존 무경계 연결과 옛 워커의 종료**, 필요한 경우 키 교체와 **명시적 `SIGNING_KEY_FALLBACKS=[]`**를 한 배포 절차로 명시한다. 14일 대기만으로 소진된다는 해석을 제거하고 재발급 반례와 완화책을 테스트한다. 선언 누락 전부를 W010이 잡는다는 문장도 수정한다. 이 운영 계약 대신 서버가 강제하는 정책 세대/폐기 기준을 구현해도 된다. |

## 실행 근거

- Python 3.12.14 / Django 6.0, memory 채널 레이어.
- 원본 `.wireview/test_final_probes.py`: **5 failed, 2 passed**. 결함의 존재를 단언하는 테스트이므로 아래 실패 지점 해석이 중요하다.
- 계약·동작·checks·하네스 네 파일: 원본 재현기와 합쳐 **5 failed, 465 passed, 11 skipped**. 재현기 두 통과를 제외하면 정규 네 파일은 **463 passed, 11 skipped**다.
- 새 변이 검증에는 `tests/test_signing.py`도 추가했다. 원복 뒤 다섯 파일: **473 passed, 11 skipped**, websockets 폐기 예정 API 경고 2개.
- 추가 재현·대조군 `.wireview/test_rejudge_probes.py`: **13 passed**. 일부는 결함 존재를 단언하므로 제품 승인 점수가 아니다.
- 새 변이 **4종**을 실제 파일에 적용했다. 각 파일의 원본 바이트를 보관하고 `finally`에서 직접 복구했다. 두 생존 중 하나는 추가 대조군으로 검출했다.
- 첫 제한 환경 실행은 하네스 두 재현이 `sock.bind()`의 `PermissionError`로 실패했다. 이것은 수정 증거로 버리고, loopback 바인딩 권한을 확보해 재실행했다. 아래 판정은 권한 확보 후 로그를 기준으로 한다.
- 키 교체 첫 보조 실험도 `override_settings(WIREVIEW=...)`만 사용해 실제 모듈 상수를 바꾸지 못했다. 이 결과는 버렸다. 최종 실험은 기존 signing 테스트처럼 `wireview.settings`의 실제 키·fallback 상수를 변경하고 대조했다.
- 전체 저장소 스위트, 브라우저 E2E, 실제 Redis/NATS, 다중 워커 롤링 배포, 지원 버전 매트릭스는 이번에 실행하지 않았다. consumer 재현은 실제 명령·저장소·서명 코드와 기록용 outbound를 사용하며 실 WebSocket 왕복은 아니다.

로그: `.wireview/rejudge-verified.log`, `rejudge-restored.log`, `rejudge-new-probes.log`, `rejudge-mut-*.log`.
실행기: `.wireview/rejudge_mutations.py`, `rejudge_survivor_check.py`.

## A. 원본 재현기 일곱 개의 판정

| 원본 테스트 | 최종 결과와 실패 지점 | 해석 |
|---|---|---|
| `test_password_update_then_logout_misses_original_socket` | 실패, 54행의 구독·발행 토픽 교집합이 비어 있다는 단언 | 실제 교집합이 있다. login→join→비밀번호 변경→`update_session_auth_hash`→logout이 끝난 뒤의 의도한 단언이다. 토픽 불일치 수정 확인. |
| `test_first_post_upgrade_relogin_never_retires_nonce_less_socket` | 실패, 67행의 무효화 메시지가 없다는 단언 | 이제 메시지가 발행된다. 이것만으로 올바른 토픽이라고 할 수는 없어 새 `test_nonceless_relogin_reaches_original_topic`에서 기존 consumer의 인증 토픽과 직접 대조했고 일치했다. |
| `test_inactive_user_keeps_fingerprint_but_authorize_still_decides` | 실패, 129행의 익명·기존 사용자 지문이 같다는 단언 | 실제 Channels `get_user`가 AnonymousUser를 반환하고 세션 키가 남아 있다는 앞선 조건은 성립한다. 이제 지문만 다르다. 추가 `test_inactive_token_is_actually_refused`로 authorize 없는 경계에서도 join이 reload로 거절됨을 확인했다. |
| `test_startup_timeout_returns_while_thread_is_alive` | 실패, 158행의 `is_alive()` | 시작 timeout 예외는 기대대로 발생했고 반환 시 스레드는 이미 종료됐다. 5초 뒤 스스로 끝나는 대역을 15초 join이 기다린 결과다. 고쳐진 경로의 증거지만 종료 timeout 전체의 증거는 아니다. |
| `test_successful_harness_leaves_loop_unclosed` | 실패, 182행의 `not loop.is_closed()` | 실제 서버가 시작·종료됐고 스레드는 종료, 루프는 닫혔다. 단순 환경 오류가 아니다. |
| `test_old_public_v2_state_bypasses_new_view_only_policy` | 통과 | 정상 무경계 v2 토큰으로 새 정책 호출 없이 join, 저장소 등록. 여전히 재현된다. |
| `test_missing_request_processor_issues_replayable_unbound_state` | 통과 | processor 없는 실제 Django 템플릿 렌더가 200과 무경계 토큰을 내고, 익명 consumer의 join·bump가 성공한다. 여전히 재현된다. |

따라서 “다섯 실패”는 최종 환경에서 모두 의도한 결함 단언의 실패다. 단, 그 다섯 개의 반례가 사라졌다는 것과 기능의 모든 수명 경로가 안전하다는 것은 별개의 주장이다.

## B·D. 인증 지문 변경

### [심각도: 높음 · 릴리스 차단] nonce 없는 세션은 로그아웃 뒤 지연된 첫 join을 통과한다

- **어디**: `wireview/core/live_session.py:154`, `wireview/consumer.py:345`, `wireview/consumer.py:355`.
- **무엇**: `_reload_session()`은 새 세션 데이터를 읽지만 사용자 객체는 `self.repo.user`를 그대로 사용한다. nonce 없는 로그인에서 지문은 `pk + 빈 nonce`다. 서버 세션이 삭제돼 새 데이터가 `{}`가 되어도 옛 사용자 pk가 남아 지문이 같다. 이후 `authorize=lambda ctx: ctx.user.is_authenticated`도 오래된 사용자 객체로 통과한다. 구독을 재읽기 전에 설치해도, logout이 첫 join **이전**에 끝났으면 받을 과거 메시지는 없다.
- **근거**: `test_delayed_join_after_logout[False]`에서 실제 cache SessionStore에 로그인하고 nonce만 제거해 기존 세션 형태를 만든다. consumer 스냅샷·정상 경계 토큰을 만든 뒤 실제 Django `logout()`으로 세션을 삭제한다. 새 SessionStore가 `{}`임을 확인하고 첫 `command_join`을 실행했다. 컴포넌트가 생겼고 빈 최신 세션을 보면서도 `command_user_event(..., 'bump', ...)`가 상태를 바꿨다. 같은 순서에서 nonce를 유지한 `[True]` 대조군은 reload와 저장소 미등록을 확인했다.
- **변경과의 관계**: 직전 구현은 새 `{}`에서 `_auth_user_hash`가 사라져 이 비교가 달라졌다. 지금은 그 입력을 제거해 pk 하나가 남는다. 명시적 AnonymousUser 수정으로 해결되지 않는다. 이 경로는 사용자 객체를 다시 읽지 않아 AnonymousUser가 되지 않기 때문이다. 지원하려고 명시한 업그레이드 전 세션의 실제 회귀다.
- **최소 수용 기준**: 위 차단 표의 첫 행. nonce 없는 세션을 계속 허용한다면 새 세션의 인증 키·귀속 검증을 지문 비교와 분리해야 한다. logout 전후 구독 순서 테스트만 추가해서는 이 반례를 잡지 못한다.

### 비밀번호 변경 자체에 대한 판정

토픽에서 `_auth_user_hash`를 빼는 선택은 타당하다. `update_session_auth_hash()`가 유지하려는 **현재 로그인**의 소켓과 후속 logout을 같은 nonce로 묶는다. 원래 토픽 불일치 결함은 닫혔다.

대가는 wireview 토큰이 더 이상 비밀번호 해시 변화 자체를 구별하지 않는다는 것이다. 현재 로그인을 유지한 비밀번호 변경 전 토큰도 같은 nonce 아래에서는 재사용할 수 있다. 이것은 유지된 로그인 계약으로 설명할 수 있다. 비밀번호 변경만으로 모든 토큰·소켓을 퇴역시키는 기능이라고 설명해서는 안 된다.

반면 정상 Channels 인증을 거친 **새 연결**의 비밀번호 검증까지 제거된 것은 아니다. 설치된 `channels/auth.py`는 현재 사용자 auth hash와 세션 해시를 비교하며 불일치 시 flush하고 익명으로 만든다. `test_password_change_new_handshake_rejects_old_cookie`에서 비밀번호만 변경한 뒤 이 인증 함수를 거치면 익명이 되고 기존 경계 토큰이 거절됐다. 이 실험은 cache 백엔드의 세션 키를 사용했다. 기존 연결의 사용자·권한을 이벤트마다 갱신하지 않는 한계는 원래 있었으며, 이번 수정이 모든 계정의 새 로그인 인증을 무력화했다고 확대하지 않는다.

토픽 식별과 세션 유효성은 다른 역할이다. nonce 기반 토픽은 유지하되 새 세션 유효성 판정을 보강하는 것이 적절하다.

### [심각도: 중간 · 전환 가용성] 새 브라우저의 첫 로그인도 다른 nonce 없는 로그인에 무효화를 발행한다

- **어디**: `wireview/core/live_session.py:434`, `wireview/core/live_session.py:459`.
- **무엇**: 같은 사용자의 nonce 없는 세션들은 pk 하나로 합쳐진다. 이제 이전 nonce 유무에 관계없이 발행하므로, 별도 브라우저의 첫 로그인도 그 사용자의 다른 nonce 없는 소켓 토픽으로 발행한다. “바로 이 세션의 재로그인만 퇴역”하는 동작이 아니다.
- **근거**: `test_new_login_retires_other_nonceless_login`에서 서로 다른 session_key의 두 실제 로그인을 만들었다. 첫 세션의 nonce를 제거하고 연결한 뒤 두 번째 로그인을 실행하자, 첫 consumer가 구독한 인증 토픽이 발행 목록에 있었다. 두 번째 로그인은 새 nonce로 다른 지문을 가졌다. 기록용 브로커 실험이므로 물리 소켓 종료를 관찰했다고 하지는 않는다.
- **판정·대안**: 인증 권한 획득이 아니라 기존 세션들의 연결 종료 범위가 넓어지는 비용이다. 첫 재로그인의 퇴역 누락을 닫기 위한 보수적 전환으로 수용할 수 있지만 사용자 문서에 밝혀야 한다. 새로운 로그인마다 브로커 발행 한 번도 추가된다. nonce가 이미 있는 다른 정상 로그인은 별도 토픽이므로 이 실험의 대상이 아니다. 다른 사용자로 login할 때 Django가 먼저 flush해 옛 nonce를 잃는 한계는 기존부터 있었고 이번 수정도 해결하지 않는다.

## C. 무경계 v2 토큰을 계약으로 처리할 수 있는가

**설계 선택 자체는 수용 가능하다.** 서버가 서명된 클래스 소속을 `_live_sessions`로 제한하고, 공개용 컴포넌트는 계속 공개로 쓰게 하는 모델에 정책 세대 프로토콜을 반드시 추가해야 하는 것은 아니다. 보호 컴포넌트에 소속 선언을 요구하는 것도 합리적이다. 원래 두 테스트의 통과를 숨기지 않고 processor 누락의 실제 우회까지 문서화한 것은 개선이다.

그러나 “서버가 발급 시점을 모른다”는 설명은 부정확하다. `TimestampSigner`에 서명된 시각이 있으며 만료 판정도 한다. 부족한 것은 **그 클래스·페이지가 언제부터 보호 대상인지에 대한 서버 기준과 연결 정보**다. 시각 자체가 없어서 구조적으로 해결 불가능한 문제가 아니다. 현재 `TestABoundaryAddedLaterDoesNotReachBackwards`는 무경계 허용과 선언된 클래스의 거절을 나란히 확인하지만, 같은 클래스의 실제 배포 전후 전환·토큰 갱신·기존 소켓 종료까지 검증하지 않는다.

### [심각도: 높음 · 전환 계약 차단] 무경계 토큰의 수명은 접근 가능 기간의 상한이 아니다

- **어디**: `wireview/core/state.py:187`, `wireview/core/state.py:207`, `wireview/consumer.py:272`, `docs/features/live-session.md:285`.
- **무엇**: 무경계 상태로 join한 컴포넌트는 렌더에서도 무경계 토큰을 다시 발급한다. 최초 토큰 하나는 `STATE_MAX_AGE` 뒤 만료되지만, 그 토큰으로 발급받은 다음 토큰은 별도 수명을 갖는다. 따라서 아무 조치 없이 14일 기다리는 것을 정책 우회의 종료 시점으로 삼을 수 없다. join된 컴포넌트의 이벤트 수명도 토큰 만료와 별개다.
- **근거**: `test_unbound_state_is_renewable_past_transition_deadline`은 수명을 100초로 두고 t=1000에 공개 토큰을 발급한다. 거절 정책을 등록한 뒤 t=1090에 기존 토큰으로 join하고 **실제 render 출력에 포함된 새 토큰**을 추출했다. t=1101에 원래 토큰은 `SignatureExpired`, 새 토큰은 무경계 상태로 다른 consumer의 join을 허용했다. 정책 호출은 0회다. 테스트에서 별도로 서명해 공급한 새 토큰이 아니다.
- **최소 수용 기준**: 문서에 개별 토큰의 만료와 권한 회수의 차이를 명시하고, 모든 보호 클래스의 소속 선언과 기존 무경계 연결 종료를 포함한 전환을 제시한다. TTL 대기만을 완화책으로 승인하지 않는다.

### 키 교체는 실제로 동작하는가

**옛 토큰의 새 join 검증을 거절하는 데는 동작한다.** 다음 네 조합을 실제 `command_join`으로 확인했다.

| 새 키에서 `SIGNING_KEY_FALLBACKS` | Django `SECRET_KEY_FALLBACKS` | 옛 무경계 토큰 |
|---|---|---|
| `[]` | 옛 키 포함 | 거절 |
| `[옛 키]` | 빈 목록 | 허용 |
| `None` | 옛 키 포함 | 허용 |
| `None` | 빈 목록 | 거절 |

`None`은 “fallback 없음”이 아니라 **Django에서 상속**이다(`wireview/core/signing.py:52`). 폐기 목적의 예시는 `SIGNING_KEY_FALLBACKS=[]`를 명시해야 한다. 현재 구현은 올바르지만 기존 signing 스위트는 `[]`와 `None`을 혼동하는 새 변이를 놓쳤다.

**키 교체 자체가 기존 소켓을 강제로 닫지는 않는다.** `test_rotated_key_does_not_retire_already_joined_public_socket`에서 기존 공개 consumer가 살아 있는 동안 사용 키를 바꾸고 이벤트를 보내자 이벤트가 실행됐고, 렌더가 새 키의 무경계 토큰을 발급했다. 그 토큰으로 다른 consumer도 join했다. 이는 키를 런타임 상수에서 바꾼 제어 실험이며 실제 롤링 배포 재현은 아니다. 그래도 키 검증을 하지 않는 기존 이벤트 경로에 별도 종료가 필요함은 확인한다. 실제 설정 변경은 프로세스 재시작으로 적용하는 경우가 많지만, **모든 기존 연결·옛 키 발급 워커가 종료됐다는 조건**을 전환 절차에 포함해야 한다. 키만 바꾸면 열린 페이지가 즉시 reload한다는 보장은 없다.

또 `wireview/settings.py`는 import 때 설정을 모듈 상수로 읽는다. `get_signer()`가 매 호출 이 상수를 읽는 것과 Django 설정 파일 변경이 실행 중 워커에 자동 반영되는 것은 다르다.

### [심각도: 낮음] 전환 안내와 지문 설명이 아직 구현과 어긋난다

- **어디**: `docs/features/live-session.md:193`, `docs/features/live-session.md:291`, `wireview/checks.py:311`, `wireview/consumer.py:251`.
- **무엇**: 사용자 문서에는 여전히 지문 입력에 `_auth_user_hash`가 포함된다고 적혀 있다. W010은 선언 누락 전체를 찾는 것이 아니라 `_on_mount`가 있고 `_live_sessions`가 없는 클래스 등을 경고한다. 페이지의 authorize에만 의존한 클래스는 이 검사로 찾지 못한다. consumer의 비밀번호 변경 전 상태는 더 이상 맞지 않는다는 주석도 유지된 현재 로그인에 대해서는 틀리다.
- **고치려면**: pk+nonce로 문서를 맞추고, W010이 모든 보호 대상을 찾아주는 것이 아님을 밝힌다. 전환 대상 클래스 목록은 배포자가 직접 확인해야 한다.

## D. 하네스 변경

### [심각도: 중간 · 비차단] 시작 timeout 뒤 종료 timeout까지 넘으면 살아 있는 스레드를 두고 반환한다

- **어디**: `tests/testproj/e2e_server.py:132`, `tests/testproj/e2e_server.py:142`, `tests/testproj/e2e_server.py:144`.
- **무엇**: 시작 대기까지 `finally`로 묶은 것은 올바른 개선이다. 하지만 `join(timeout=...)` 뒤 생존 판정은 여전히 컨텍스트 바깥에 있고, 시작 실패나 테스트 본문 예외가 전파되면 그 판정 자체를 건너뛴다. 종료가 끝나지 않은 상태에서 DEBUG가 복원되고 소켓도 닫힌다.
- **근거**: `test_startup_timeout_survives_join_timeout`은 시작·종료 timeout을 각각 0.01초로 줄이고 Event를 기다리는 스레드를 넣었다. 시작 timeout 예외를 받은 시점에 스레드는 살아 있고 DEBUG는 복원돼 있었다. 실험 뒤 Event를 풀고 join하여 모든 스레드를 정리했다. 원본 재현기의 5초 대역은 기본 15초 join 안에 끝나므로 이 조건을 보지 못한다.
- **판정**: 새로 시작 실패 cleanup을 망가뜨렸다는 지적은 아니다. 개선 뒤 남은 경로이며 기존 리뷰의 종료 timeout 우려가 완전히 닫히지 않았다. 제품 인증 차단과 분리한다.
- **고치려면**: 정상·시작 실패·본문 예외 모든 탈출에서 종료 판정을 cleanup 내부에서 수행하고, 종료 실패를 원래 예외와 함께 드러낸다. Python 스레드를 안전하게 강제 종료할 수 없으므로 유한 시간 내 격리까지 보장하려면 서버를 자식 프로세스로 실행하는 선택이 필요하다. 단순 assert 이동만으로 살아 있는 스레드가 정리된다고 주장하면 안 된다.

정상 종료의 loop close와 스레드 간 `call_soon_threadsafe` 예약은 개선이다. 이번 실행에서 새 루프 누수나 정상 하네스 실패는 확인하지 못했다. 남은 task·async generator까지 완전히 회수한다는 별도 검증은 하지 않았다.

## E. 새 변이의 실제 결과

브리프와 이전 리뷰에 기록된 변이는 반복하지 않았다. 각 변이마다 정규 다섯 파일 전체를 실행했다. 정상 기준은 **473 passed, 11 skipped**다.

| 새 변이 | 실제 변경 | 정규 스위트 | 판정 |
|---|---|---|---|
| `login_constant_nonce` | login 시 nonce를 고정 문자열로 발급 | 2 failed, 471 passed | 검출. 두 번째 login의 nonce 변화와 이전 세대 퇴역 계약이 잡는다. |
| `unknown_policy_accept` | 등록이 사라진 경계의 거절을 빈 성공 문자열로 변경 | 2 failed, 471 passed | 검출. 계약·동작 양쪽의 unknown boundary 테스트가 잡는다. |
| `explicit_empty_fallback_inherits` | `if fallbacks is None`을 `if not fallbacks`로 변경 | 473 passed | 생존. 명시적 빈 목록도 Django의 옛 키를 상속하게 되는 잘못된 구현을 놓친다. |
| `harness_drop_threadsafe_shutdown` | `call_soon_threadsafe(...shutdown...)` 호출만 생략, 종료 플래그는 유지 | 473 passed | 생존. 다만 정상 Uvicorn은 종료 플래그만으로도 끝날 수 있어, 이를 곧바로 비동등 결함이나 필수 테스트 누락으로 세지 않는다. |

fallback 생존 변이는 추가한 네 키 조합 대조군으로 다시 실행하여 **1 failed, 3 passed**를 확인했다. 실패는 `SIGNING_KEY_FALLBACKS=[]`인데 Django 옛 키를 상속해 토큰이 허용되는 정확한 사례다. 추가 테스트는 원본에서 통과한다. 하네스 변이가 생존했다는 이유만으로 존재하지 않는 제품 결함을 만들지 않았다.

변이는 모두 파일별 원본 바이트로 되돌렸다. 커밋·stash·`git checkout .`·외부 쓰기를 하지 않았다. 보고서와 실행 자료는 gitignore된 `.wireview/`에만 남겼다.

최종 `git status --short` 출력은 비어 있었다. 추적 파일 변경 없음.
