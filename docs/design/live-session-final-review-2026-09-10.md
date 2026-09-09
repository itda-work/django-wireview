# live_session 최종 적대적 리뷰 (Codex gpt-6-astra, 2026-09-10)

> 4라운드. 판정은 **릴리스 보류**였고, 차단 항목 둘과 그 아래 다섯을 지적했다. 전부 반영했다 —
> 무엇을 어떻게 고쳤는지는 `CHANGELOG.md`에 있다. 두 가지는 고치는 대신 **계약으로 명시**했다:
> 공개 시절에 발급된 토큰은 나중에 붙인 경계를 지나지 않는다는 것(서버가 토큰의 발급 시점을 모른다)과,
> request context processor 누락이 경고일 뿐 실행을 막지 않는다는 것. 둘 다 문서에 전환 절차와 함께
> 적었고 `TestABoundaryAddedLaterDoesNotReachBackwards`가 노출과 완화책을 나란히 고정한다.
>
> 인용된 줄 번호는 리뷰 시점(`6633e8c`)의 것이다.


검토 기준: `main`, `7ebdbe5..6633e8c`, 2026-09-10. 최초 브리프 이후 추가된 `87b45c3`, `6633e8c`를 포함했다. 재개 메모에 따라 질문 2~5를 중심으로 검증했다. **“이전 지적을 전부 고쳤다”는 주장은 승인하지 않는다.** 주요 마운트 경로의 수정은 확인했지만, 인증 세대 교체의 결정적인 누락과 경계 없는 토큰의 재사용 조건이 남았다.

## 실행 근거와 범위

- Python 3.12.14 / Django 6.0 / Channels 4.3.2.
- 지정 Python 스위트: **453 passed, 11 skipped**. 계약·동작·checks·하네스 네 파일을 함께 실행했다.
- 전체 비 E2E·비 slow: **1,280 passed, 11 skipped, 24 deselected**. 스레드 예외 경고를 오류로 올리는 설정을 유지했다.
- JS 경계 모듈: **11 passed**.
- JS를 빌드하고 collectstatic 후 실제 Chromium 경계 이동 E2E: **8 passed**, memory 채널 레이어.
- 원본 결함·한계 재현: `.wireview/test_final_probes.py`, **7 passed**. 여기서 통과는 결함 또는 한계가 존재한다는 단언의 성공이다.
- 실제 boost 소스를 DOM·fetch·RAF 대역에 연결한 `.wireview/final_boost_probe.mjs`: 경계 거절의 params 억제와 예약 취소는 확인했고, 느린 fetch 앞의 캐시 morph는 여전히 관찰했다.
- **새 변이 8개를 실제 소스에 적용하고 파일별 원본 바이트로 복구했다.** 기존 메모의 제외 목록은 반복하지 않았다. 결과와 실행기는 아래에 기록했다.

로그는 `.wireview/final-resume-baseline.log`, `final-full.log`, `final-e2e.log`, `final-js.log`, `final-probes.log`, `final-mut-*.log`에 있다. 실 Redis/NATS의 유실·멤버십 만료, 다중 워커 장애, 지원 버전 전체 매트릭스는 실행하지 않았다. GitHub 조회는 네트워크 제한으로 실패했다. 외부 이슈나 코멘트를 작성하지 않았다.

## 질문 2 — 수정 뒤 남거나 새로 드러난 문제

### [심각도: 높음] 경계 없이 발급된 v2 토큰은 나중에 붙인 페이지 정책도 우회한다

- **어디**: `wireview/consumer.py:272`, `wireview/core/state.py:237`, `wireview/core/live_session.py:160`, `docs/features/live-session.md:80`, `docs/features/live-session.md:265`.
- **무엇**: 소속 선언이 없는 클래스의 정상 v2 토큰에서 `s`가 비어 있으면, 현재 프로젝트가 페이지에 `authorize`를 붙였더라도 첫 join은 정책 없이 허용된다. v1은 전역 LEGACY 차단으로 막았지만, **v2 공개 페이지 → 같은 페이지에 경계 추가** 전이는 막지 않는다. 새로 경계를 도입하면서 과거 공개 토큰까지 폐기된다고 해석하면 안 된다. 모든 빈 경계 토큰을 거절하라는 뜻은 아니다. 현재도 의도적으로 공개된 컴포넌트와 과거 공개였던 컴포넌트를 서버가 구별할 정보가 없다는 문제다.
- **근거**: `test_old_public_v2_state_bypasses_new_view_only_policy`에서 경계 없는 v2 토큰을 먼저 발급하고, 모든 사용자를 거부하는 새 정책을 등록한 뒤 첫 join을 보냈다. `authorize` 호출은 0회, 컴포넌트는 저장소에 존재했다. 이는 정상 서명 재사용이며 서명 위조가 아니다.
- **무엇 — 새 경고의 실제 보안 영향**: request context processor가 없는 경우도 동일한 토큰을 만든다. 문서의 “뷰 데코레이터는 그대로 동작하므로 문이 열리는 것은 아니지만”은 HTTP 요청에 한정해서만 맞다. 허용된 사용자가 받은 경계 없는 토큰을 **익명 소켓에서 재사용하면** 페이지 정책·인증 지문·로그아웃 구독이 모두 빠질 수 있다. `_live_sessions`를 선언한 클래스는 거절되어 이 재현에 해당하지 않는다.
- **근거**: `test_missing_request_processor_issues_replayable_unbound_state`는 processor 없는 Django 엔진에서 인증된 request로 실제 `render(request, ...)`를 호출했다. 데코레이터는 통과했고 응답은 200, 나온 토큰의 경계는 빈 문자열이었다. 그 토큰으로 익명 consumer를 join하고 `bump`를 호출했더니 상태가 바뀌었다. 뷰 직접 호출과 consumer 대역 재현이며, 이 사례 자체를 브라우저 HTTP→WS 왕복으로 실행한 것은 아니다. 새 W010은 경고만 내고 이 실행을 막지 않는다.
- **고치려면**: 보호 클래스를 `_live_sessions`로 제한하는 것을 보안 전환 절차에 명시하고, 기존 무경계 상태를 폐기하는 배포 절차를 제공한다. 서명 키 변경을 이용한다면 이전 키 fallback이 그 토큰을 계속 받아 주지 않아야 한다. view-only 보호를 보장하려면 검증 가능한 발급/정책 세대 등 추가 서버 계약이 필요하다. processor 누락은 가능한 한 런타임에서도 실패로 처리하고, 최소한 문서의 “문이 열리지 않는다”는 문장은 삭제해야 한다. v2를 먼저 배포해 v1을 소진시키는 절차만으로 이 전이는 해결되지 않는다.

### [심각도: 높음] 비밀번호 변경 후 로그아웃은 기존 소켓이 듣지 않는 토픽으로 발행한다

- **어디**: `wireview/core/live_session.py:152`, `wireview/core/live_session.py:384`, `wireview/consumer.py:371`, `docs/features/live-session.md:200`, `docs/features/live-session.md:229`.
- **무엇**: 지문은 nonce 외에 `_auth_user_hash`도 포함한다. Django의 `update_session_auth_hash()`는 로그인 nonce를 유지하면서 해시를 바꾸지만 wireview 무효화 시그널을 내지 않는다. 기존 소켓은 이전 해시의 토픽에 남고, 이후 정상 `logout()`은 새 해시의 토픽에 발행한다. **정상 브로커에서도 결정적으로 빗나간다.** 세션 키를 지문에서 뺀 것으로 이 수명 문제 전체가 해결되지는 않았다.
- **근거**: `test_password_update_then_logout_misses_original_socket`에서 실제 Django `login()` → consumer join → `set_password()/save()` → `update_session_auth_hash()` → `logout()`을 실행했다. 기록된 발행 토픽 중 기존 consumer의 구독 토픽은 없었고, 기존 저장소의 이벤트는 계속 처리됐다. 메시지를 임의로 유실시킨 실험이 아니다. 브로커는 기록용 대역이며 실제 물리 소켓 종료 여부를 별도로 실험하지 않았다.
- **고치려면**: 상태 검증용 지문과 연결 폐기용 세대 식별자를 분리하거나, 해시 교체 전에 이전 지문을 무효화하는 공식 통합 경로를 둔다. “해시 교체 뒤 지금 세션으로 invalidate”는 이미 바뀐 토픽을 계산하므로 해결책이 아니다. 문서에 `update_session_auth_hash()`를 **사용한 경우에도** 옛 소켓과 후속 logout의 토픽이 갈라질 수 있음을 밝혀야 한다.

### [심각도: 중간] nonce 없는 기존 로그인은 첫 재로그인 뒤 폐기할 수 없는 소켓을 남긴다

- **어디**: `wireview/core/live_session.py:133`, `wireview/core/live_session.py:425`, `wireview/core/live_session.py:430`, `docs/features/live-session.md:204`.
- **무엇**: 업그레이드 전 로그인에는 nonce가 없다. 이 세션으로 열린 소켓은 pk+auth hash 토픽을 구독한다. 첫 재로그인이 새 nonce를 찍어도 `if previous:`가 거짓이라 옛 세대 발행이 없다. 이후 로그아웃도 새 nonce 토픽으로만 간다. “다음 login이 해결한다”는 설명은 새로 발급될 세대의 구분에는 맞지만 **이미 열린 소켓에는 틀리다**. nonce 없는 같은 사용자의 여러 로그인도 같은 지문으로 합쳐져 한 세션의 logout이 다른 세션 소켓까지 닫을 수 있다.
- **근거**: `test_first_post_upgrade_relogin_never_retires_nonce_less_socket`는 실제 로그인 세션에서 nonce만 제거해 업그레이드 전 형태를 만든 뒤 join했다. 재로그인 시 인증 무효화 메시지는 없었고, 이어진 logout 토픽도 기존 구독과 달랐다. 옛 컴포넌트의 `bump`는 실행됐다. 같은 사용자의 복수 로그인 토픽 충돌은 지문 입력의 정적 대조이며 별도 다중 소켓 실험은 하지 않았다.
- **고치려면**: nonce 없는 로그인도 명시적으로 퇴역시키는 전환 경로를 제공하거나, 경계 기능 도입 전에 기존 세션·열린 연결을 종료하고 재로그인을 요구하는 배포 절차를 제공한다. 단순히 새 nonce를 생성하는 것으로 끝내지 말고 옛 지문에 대한 폐기까지 검증해야 한다.

### [심각도: 중간] 익명 사용자에 남은 인증 키는 이전 인증 사용자의 지문과 같아진다

- **어디**: `wireview/core/live_session.py:144`, `wireview/consumer.py:296`; 설치된 `channels/auth.py:44`.
- **무엇**: `user is None`과 `AnonymousUser`를 함께 pk fallback 대상으로 취급한다. 인증 백엔드가 사용자를 반환하지 않았지만 세션 데이터가 남아 있으면, 익명 연결과 과거 인증 연결의 지문이 같아진다. 지문이 사용자 객체의 인증 여부까지 보증하지 않는다.
- **근거**: 설치된 Channels 4.3.2는 **해시 불일치 시 flush한다**. 재개 메모의 예시는 이 버전에서 무효다. 대신 실제 사용자 `is_active=False` 후 Channels `get_user()`를 실행했다. 반환은 AnonymousUser였지만 세션의 인증 키는 남았고 지문은 이전 사용자와 같았다. `authorize` 없는 경계는 이전 토큰으로 join을 허용했다. 같은 재현에서 `authorize=lambda ctx: ctx.user.is_authenticated`를 넣으면 정상적으로 거절했다(`test_inactive_user_keeps_fingerprint_but_authorize_still_decides`).
- **고치려면**: 로그아웃 발행에서 사용자 객체가 없는 경우의 복원과, 인증 문맥 비교에서 익명 사용자를 다루는 경우를 구별한다. 최소한 토큰 지문은 인증 여부 변경까지 구별하도록 한다. 이 결과를 **인증 술어 우회나 임의 세션 위조**로 확대 해석하지 않는다. 서버 저장형 세션 데이터나 signed-cookie 내용을 공격자가 서명 없이 마음대로 바꿀 수 있다는 증거는 없다.

### [심각도: 중간] E2E 서버 시작 실패는 스레드 종료를 기다리지 않고 설정을 복원한다

- **어디**: `tests/testproj/e2e_server.py:113`, `tests/testproj/e2e_server.py:123`, `tests/test_e2e_harness.py:70`.
- **무엇**: `try/finally`는 서버가 시작되어 `yield`에 도달한 뒤만 감싼다. 시작 타임아웃에서는 terminate만 하고 raise하므로 join 없이 DEBUG override와 소켓 컨텍스트를 벗어난다. 기존 문제의 정상 종료 경로는 닫았지만 실패 경로는 남았다. 정상 종료에서도 join timeout이 끝난 뒤 살아 있는 스레드를 확인하는 시점은 설정 컨텍스트를 벗어난 뒤다.
- **근거**: `test_startup_timeout_returns_while_thread_is_alive`에서 Event로 제어하는 서버 스레드와 짧은 시작 timeout을 사용했다. `serve()`가 AssertionError를 반환하고 DEBUG가 원래 값으로 복원된 시점에 스레드는 살아 있었다. 확률적 부하 실험이 아니라 시작이 끝나지 않는 조건을 결정론적으로 만든 것이다. 재현 종료 시 Event를 풀고 모든 스레드를 join해 정리했다.
- **고치려면**: thread.start 이후의 시작 대기까지 같은 cleanup 범위로 감싸고, 모든 탈출 경로에서 종료 요청과 join을 실행한다. 시작 실패·시작 timeout·실행 중 실패·종료 timeout·테스트 본문 예외를 각각 검증한다. 사전 bind 자체는 포트 선택 경합을 줄이는 타당한 변경이다.

### [심각도: 낮음] 하네스는 종료된 스레드의 이벤트 루프를 닫지 않는다

- **어디**: `tests/testproj/e2e_server.py:68`, `tests/testproj/e2e_server.py:75`, `tests/testproj/e2e_server.py:87`.
- **무엇**: `new_event_loop()`에 대응하는 `close()`가 없다. 스레드 join은 루프의 정리를 대신하지 않는다. `terminate()`의 다른 스레드에서 실행하는 `loop.create_task()`도 안전한 스레드 간 예약 API가 아니다.
- **근거**: 정상 `serve()` 종료 뒤 thread는 죽었지만 `loop.is_closed()`는 False였다(`test_successful_harness_leaves_loop_unclosed`). 재현에서는 루프를 직접 닫았다. 장시간 실행의 FD 증가나 debug 모드의 잘못된 스레드 예외는 따로 재현하지 않았으므로 관찰했다고 주장하지 않는다.
- **고치려면**: 서버 스레드에서 루프와 남은 작업을 정리하거나 `asyncio.Runner` 등 수명 관리 도구를 사용한다. 외부 스레드의 종료 예약은 `call_soon_threadsafe` 같은 적합한 경로로 전달한다.

## 질문 2의 나머지 후보 판정

| 후보 | 판정과 근거 |
|---|---|
| `_reload_session`의 snapshot 교체 | 정상 첫 경계 join에서는 컴포넌트 생성 전에 교체된다. 이후 생성되는 `self.session`은 새 SessionView를 받고, 인증 외 데이터만 바뀌면 지문은 같으므로 HTTP 토큰도 계속 유효하다. 인증 데이터가 다르면 생성 전에 거절한다. 실패 후 재시도에서 원래 session_key를 보존하는 것이 중요하며, 이 보존을 없앤 새 변이는 실제로 잡혔다. 기존 컴포넌트가 낡은 snapshot을 가진 채 새 snapshot과 섞이는 정상 경로는 찾지 못했다. |
| 새 `_mount_in_template` 조기 반환 | 새로 옮긴 `has_mounted or has_joined → not mount_halted` 자체는 `_mount`의 같은 조건과 같다. 다리 전에 declaration 검사를 하고, 경계·훅이 모두 없는 경우 생략하는 기존 분기까지 포함하면 두 함수 전체가 임의 상태 조합에서 동일한 것은 아니다. 정책을 인스턴스 생존 중 임의 변경하거나 거절된 객체를 수동 재등록하는 경우를 정상 경로의 취약점으로 세지 않았다. |
| 다리·트랜잭션 | 정상 live render의 sync/async 왕복은 기존 테스트를 통과했다. 첫 마운트의 교착을 재현하지 못했다. 이벤트 루프 위에서 직접 render하는 보조 스레드 경로는 별도 DB 연결/트랜잭션 가시성을 고려해야 한다. 원래 있던 분기이며 이번 조기 반환이 그 경로를 새로 만든 것은 아니다. DB 백엔드 전체에서 교착이 없다는 증명이나 부하 측정은 하지 않았다. |
| `testing.mount()` freeze | 거절 뒤 render가 빈 결과/redirect가 되는 의도적인 동작 변경이다. 이전의 보호 HTML 출력을 기대한 테스트는 달라질 수 있다. 전체 테스트에서 새 호환성 실패는 없었다. 단, `MountedComponent.call()`은 여전히 직접 핸들러를 호출하므로 서버의 “거절 id는 이벤트를 받지 않음”을 완전히 모델링하지 않는다. 문서상 직접 테스트 API인 점을 고려해 운영 서버의 인증 우회로 세지 않는다. |
| `STATE_ACCEPT_LEGACY` 전역 차단 | 경계가 있는 프로젝트의 v1을 허용할 근거가 없다는 판단은 타당하다. 공개 페이지까지 reload시키는 보수적 호환성 비용이며 문서·W010과 일치한다. 다만 위에서 재현한 **무경계 v2**의 정책 전환까지 해결한 것은 아니다. |
| 스레드 예외 경고의 오류 승격 | 전체 비 E2E 1,280개와 E2E 8개가 이 설정 아래 통과했다. 새 운영 결함을 찾지 못했다. 하네스가 BaseException을 잡아 `.error`에 저장한 뒤 전달을 빠뜨리는 경우는 unhandled-thread 경고가 아니므로 이 필터가 구제하지 못한다. |

## 질문 3 — 새 변이 8개의 실제 결과

실행기: `.wireview/final_mutations.py`. 각 변이마다 지정 네 Python 파일을 함께 실행했다. 정상 기준은 453 passed / 11 skipped다. 아래 “생존”은 **그 실행 범위에서만** 의미한다. Python 변이에 JS 모듈 테스트를 함께 돌렸다고 주장하지 않는다.

| 새 변이 | 지정 스위트 실패 | 추가 실행 | 판정 |
|---|---:|---|---|
| `subscribe_after_reread`: 구독을 백엔드 재읽기 뒤로 이동 | 0 | 없음 | 생존. 읽기와 구독 사이 logout 창을 다시 여는 순서 변경을 놓친다. |
| `params_omit_render`: 실제 params 명령의 send_render 삭제 | 0 | 전체 비 E2E **1 실패**; 경계 E2E 8 통과 | 지정 계약의 빈틈. **전체 스위트는 검출**한다. |
| `event_omit_render`: 실제 user event 명령의 send_render만 삭제 | 0 | 전체 비 E2E **12 실패**; 경계 E2E 8 통과 | 지정 계약의 빈틈. **전체 스위트는 검출**한다. handler 자체를 없앤 과거 변이와 다르다. |
| `harness_discard_runtime_error`: 시작 이후 `.error` 전달 삭제 | 0 | 없음 | 생존. 기존 실패 테스트는 시작 실패만 만든다. |
| `harness_omit_timeout_termination`: 시작 timeout의 terminate 삭제 | 0 | 없음 | 생존. 시작 timeout 경로를 검증하지 않는다. |
| `harness_omit_shutdown_verdict`: join 뒤 alive 검사 삭제 | 0 | 없음 | 생존. 스레드가 종료되지 않는 대조 입력이 없다. |
| `reread_forgets_session_key`: 새 SessionView에 session_key 미전달 | 1 | 없음 | `test_a_connection_refused_by_the_re_read_cannot_simply_ask_again`이 검출. |
| `ignore_slot_arguments`: `_render_with_slots` 대신 일반 render 호출 | 1 | 없음 | `TestAChildInsideASlot`의 허용 대조군이 검출. 거절만 검사했다면 지나갔을 변이다. |

넓은 범위 재검증 실행기는 `.wireview/final_mutations_broad.py`다. params 변이는 `tests/test_live_component_render.py::test_a_child_that_appears_through_params_changed_is_joined`, event 변이는 같은 파일과 `tests/test_live_component_slots.py`가 잡았다. 이 둘을 “어떤 테스트도 못 잡는다”고 보고하면 잘못된 결론이다.

### [심각도: 중간] 새 자식 테스트는 실제 명령의 렌더 연결을 대신 실행한다

- **어디**: `tests/test_live_session_contract.py:1404`, `tests/test_live_session_contract.py:1415`.
- **무엇**: event는 repo.dispatch_event를 직접, params는 parent.params_changed를 직접 부르고, 테스트가 send_render를 붙인다. 따라서 실제 consumer 명령에서 렌더 호출이 사라져도 이 테스트는 계속 자식을 나타나게 한다. 추가 영역 자체가 무의미한 것은 아니다. 나중에 등장한 일반 자식의 거절/허용은 검증하지만 명령부터 출력까지의 연결은 검증하지 않는다.
- **근거**: 위 두 렌더 생략 변이가 지정 스위트를 통과했다. 전체 스위트는 LiveComponent 경로의 다른 테스트로 검출했다.
- **고치려면**: 최소 대표 사례는 실제 `command_user_event`와 `command_params_changed`를 호출하고, 수신 프레임으로 새 자식의 존재/부재를 판단한다. 모든 거절 사유를 모든 트리거와 다시 곱할 필요는 없다.

### [심각도: 중간] 토픽 일치 테스트는 구독과 재검증의 순서를 보장하지 않는다

- **어디**: `tests/test_live_session_contract.py:1256`, `wireview/consumer.py:279`.
- **무엇**: 같은 토픽을 계산하는 것과 logout을 놓치지 않는 설치 순서는 다른 계약이다. 현재 원본의 subscribe → reload 순서는 맞지만 순서를 뒤집은 구현도 테스트를 통과한다. 토픽 대조군도 dict(session.items())를 전달해 persisted session의 재읽기를 이 사례에서 실행하지 않는다.
- **근거**: `subscribe_after_reread`가 453개 전부 통과했다. 하네스 실패 처리 세 변이의 생존도 함께 확인했다.
- **고치려면**: 기록용 subscribe와 실제 백엔드 read 사이에 제어 가능한 logout을 넣어 각각의 시점에서 거절/폐기를 관찰한다. 하네스는 정상 종료가 아닌 시작·실행·종료 실패 입력을 추가한다. 원본의 현재 순서가 틀렸다는 지적은 아니다.

추가로 남은 테스트 한계: `joined()`의 허용 단언은 “최대 1회”이고 호출됐다면 순서를 보지만 경로별 “반드시 1회”를 직접 요구하지 않는다. 업로드 대조군이 root의 일부 누락은 간접 검출한다. 예외 전달 단언은 `PATHS_THAT_PROPAGATE`만 검사하고 consumer가 흡수하는 자식 오류의 로그·거절 응답을 전부 관찰하지 않는다. 이 둘에는 이번에 별도 변이를 하지 않았으므로 생존 결과로 세지 않았다. 슬롯 테스트는 일반 Component의 기본 슬롯 대표 사례이지 모든 let/fill·LiveComponent 슬롯 조합의 증명은 아니다.

## 질문 4 — 문서와 보장의 경계

1. **백엔드 표의 핵심 구분은 맞다.** 올바르게 경계에 묶인 토큰과 정상 Django 인증 세션을 전제로, 서버 저장형 세션 삭제는 새 연결의 인증을 제거하고 signed-cookie는 보관된 쿠키 재사용을 서버에서 폐기하지 못한다. [Django의 cookie 세션 설명](https://docs.djangoproject.com/en/6.0/topics/http/sessions/#using-cookie-based-sessions)과 일치한다. 표를 “모든 토큰·모든 설정에서 새 연결은 막힌다”로 읽으면 위의 무경계 토큰 재현이 반례다. “signed_cookies는 막지 못한다”는 **옛 쿠키를 보관해 재제출하는 공격자**의 경우이며 정상 브라우저의 익명 쿠키는 별개다.
2. **최선 노력은 정확한 방향이지만 복구라는 표현은 제한해야 한다.** [Channels 규격](https://channels.readthedocs.io/en/stable/channel_layer_spec.html#capacity)은 그룹 용량 초과 폐기를, [멤버십 규정](https://channels.readthedocs.io/en/stable/channel_layer_spec.html#persistence)은 만료를 명시한다. 원본에는 이미 열린 연결을 주기적으로 재검증하는 루프가 없다. “다음 연결에서 회수”는 기존 악성 소켓이 언젠가 스스로 재연결한다는 보장이 아니다. 연결이 계속 살아 있으면 이벤트도 계속 가능하다. 잘못된 세대 토픽 계산은 이 전송 계층의 최선 노력과 별개의 결함이다. `docs/DEPLOYMENT.md`도 같은 범위로 읽어야 한다.
3. **request processor 경고는 필요하지만 안전성 설명은 과하다.** 새 W010은 정식 DjangoTemplates 설정의 누락을 보고하는 검사이며 자동 복구나 실행 차단이 아니다. `Context`에 request를 직접 넣는 렌더나 사용자 정의 backend 등 모든 전달 경로를 판정하지 않는다. processor를 설정해도 request를 엔진에 넘기지 않는 렌더까지 보장하지 않는다. 특히 “문이 열리는 것은 아니다”는 위 익명 토큰 재사용 재현과 맞지 않는다.
4. **성능 표는 `live-session.md`가 아니라 `lifecycle-hooks.md:92`에 있다.** 새 가드로 동일 인스턴스의 재렌더가 다리를 생략하는 구조와 횟수 테스트는 확인했다. 88/308/56µs 수치 자체는 이번에 독립 재측정하지 않았다. 실행기·샘플 수·훅 내용·sync/async 경로가 없는 표를 일반적인 비용 보장으로 승인하지 않는다. “그것이 비용 전부”도 사용자 훅이 DB/외부 작업을 하는 경우까지 포함하면 틀리다. “경계도 훅도 없는 경로는 추가 다리를 안 건넌다”가 정확한 범위다. 경계를 쓰지 않아도 `_on_mount`를 쓰는 프로젝트는 다리를 건넌다.
5. **popstate는 전체 설계 계약까지 닫히지 않았다.** 응답 거절 이후 아직 실행되지 않은 RAF 취소와, early popstate 거절 시 앞선 작업 취소는 확인했다. 하지만 느린 fetch가 답하기 전에 같은 이름의 캐시를 RAF로 실행하면 `morph → newContent(join 알림) → assign`이 된다. 실제 boost 소스 대역 실험으로 확인했다. 캐시된 HTML 자체의 회수나 서버 인가 우회라고 주장하지 않는다. 사용자 문서는 선행 캐시 복원을 설명하므로 그 설명은 맞지만, 설계 AC5의 “검증 전 DOM 반영 없음”과 boost 소스의 “통과 전 newContent 없음”은 더 강하다. 이를 보장하려면 경계 내부 캐시도 서버 응답 검증 뒤에 반영해야 한다.

## 이전 지적의 최종 상태

같은 변이를 반복하지 말라는 재개 지시를 따랐으므로, 아래의 “닫힘”은 현재 코드·실행된 테스트에서 **원래 지적한 실패 조건이 해결됐다는 범위**다. 모든 인접 조건을 증명한다는 뜻은 아니다. 원본 round3에는 헤더의 “8개”보다 많은 항목이 있으므로 본문 기준으로 정리했다.

| 구현 3라운드 / 재검증 지적 | 판정 | 현재 근거 |
|---|---|---|
| mount 예외 뒤 저장소/LiveComponent 렌더 생존 | 닫힘 | 예외를 halted로 처리하고 abandon, 계약 매트릭스 통과 |
| 중첩 일반 Component의 세션 훅 생략 | 닫힘 | 템플릿 mount gate와 허용/거절 매트릭스 통과 |
| 첫 join을 logout 뒤로 미뤄 발행 회피 | 닫힘, 서버 저장형 조건 | subscribe 후 재읽기, flush 뒤 첫/재시도 거절 테스트 통과 |
| signed-cookie 일반 저장이 지문을 변경 | 닫힘 | session_key 제외, 안정된 nonce 입력, 동작/세대 테스트 통과 |
| signed-cookie 보관 쿠키 재생 | 부분적 | 동작은 그대로, 백엔드 제한을 문서화함. 폐기 기록 없음 |
| pub/sub 유실·멤버십 만료 뒤 기존 소켓 지속 | 부분적 | 보장 문구를 축소했지만 이벤트 재검증 없음 |
| v1 첫 join의 view-only 정책 우회 | 닫힘, v1 범위 | 경계 등록 시 LEGACY 전역 차단. 무경계 v2 전이는 별도 잔존 |
| cross-boundary fetch 전 params 통지 | 닫힘 | 응답 검증 뒤 통지, 실제 boost 소스 대역으로 재확인 |
| popstate의 예약 취소 / early refusal 취소 | 부분적 | 거절 뒤 미실행 작업 취소는 닫힘. 거절 전 캐시 반영은 잔존 |
| async 함수 뷰 및 async CBV의 거절 경로 | 닫힘 | async wrapper와 CBV 판별, 허용·거절 테스트 통과 |
| 재로그인 시 이전 세대 발행 | 부분적 | nonce 있는 동일 사용자 재로그인은 토픽 일치. nonce 없는 전환과 해시 교체는 잔존 |
| 재읽기 결과로 세션 정책 데이터를 교체 | 닫힘 | fresh snapshot 교체 및 mfa 정책 테스트 통과 |
| authorize 횟수·snapshot 문서 | 부분적 | join마다 평가함을 수정. session은 첫 경계 join에서 갱신되므로 “connect 때 snapshot”만으로 표현하면 여전히 부정확 |

| 계약 테스트 리뷰의 11개 지적 | 판정 | 현재 근거 |
|---|---|---|
| 실제 백엔드 장애·실패 후 재시도 미검증 | 닫힘 | broken backend와 실제 cache flush/재시도 입력 |
| 재로그인 기대 토픽이 실제 구독과 다름 | 닫힘, 기존 nonce 조건 | 실제 Django login 세션으로 양쪽 토픽 대조. 순서·세대 전환 추가 축은 위에 별도 기록 |
| child_restore 입력 형태가 잘못됨 | 닫힘 | tuple 입력, restored-only 상태 보존·폐기 검사 |
| 세션 훅 halt/exception 사유 없음 | 닫힘 | 별도 거절 사유로 생성 경로 매트릭스에 포함 |
| HTTP LiveComponent 자식 경로 없음 | 닫힘 | dead_live_child 행 |
| testing_mount가 합성 payload만 검사 | 부분적 | 실제 render와 freeze는 수정. 직접 call은 서버 거절 동작과 다름 |
| joined 실행·순서를 관찰하지 않음 | 부분적 | 거절 0회·순서·중복 관찰 추가. 허용 경로의 필수 실행 단언은 일부 간접적 |
| 예외 전달을 관찰하지 않음 | 부분적 | 직접 전파 경로의 예외 타입 확인. 로그로 흡수하는 경로의 전달 관찰은 불완전 |
| 클라이언트 명령 허용 대조군이 호출하지 않음 | 닫힘, 명령 효과 범위 | 실제 명령/상태·업로드 효과 검사. 렌더 연결 삭제는 지정 스위트가 놓침 |
| 거절 rejoin 전에 허용 인스턴스 없음 | 부분적 | 허용→halt 전이 추가. raise·자식·작업·구독 정리 전체를 같은 전이에서 검증하지는 않음 |
| E2E 스레드 종료를 기다리지 않음 | 부분적 | 정상 종료 join 추가. 시작 timeout 실패 경로는 여전히 미정리 |

## 질문 5 — 릴리스 판단

**현 상태를 “로그아웃과 정책 전환을 보장하는 인증 경계”로 릴리스하는 것은 보류한다.** 우선 경계 없는 v2 토큰의 정책 전환·processor 누락 시 익명 재사용 조건과, 비밀번호 해시 변경 뒤 정상 logout이 옛 소켓을 놓치는 문제를 해결하거나 지원 계약·배포 절차를 명확히 제한해야 한다. 그다음 nonce 없는 기존 로그인 전환, 익명 지문의 인증 여부 구분, 하네스 실패 정리와 계약 테스트의 순서 검증을 보완해야 한다. signed-cookie 재생과 pub/sub 유실은 이미 문서화된 설계 한계이므로 “모든 기존 연결 폐기”라는 보장을 하지 않는 조건에서 별도로 수용할 수 있다. 정상 설정의 마운트·거절·경계 이동 경로는 폭넓은 테스트를 통과했다는 사실도 함께 평가해야 한다.

**릴리스 차단 항목**: 위의 **높음 2건**. 최소 수용 기준은 재현 조건을 막는 구현 또는 적용 가능한 공식 통합/전환 절차, 그 조건을 직접 실행하는 테스트, 실제 보장과 일치하는 문서다. “최선 노력” 문구만 추가하는 것은 잘못된 토픽이나 무경계 토큰 발급의 해결책이 아니다.

## 원복 확인

변이는 파일을 직접 원복했으며 checkout·stash를 사용하지 않았다. 재개 시점에는 `?? .agents/`, `?? .codex/`가 있었고 건드리지 않았다. 리뷰 도중 추가된 `704de51`은 이 디렉터리의 gitignore 변경이며 기능 검토 대상에는 변화가 없다. 최종 `git diff --exit-code`, `git diff --cached --exit-code`가 통과했고 **`git status --short`는 빈 출력**이었다. 리뷰 산출물과 재현기·로그는 `.wireview/`에만 남겼다.
