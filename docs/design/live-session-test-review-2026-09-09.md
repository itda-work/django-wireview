# live_session 계약 테스트 리뷰 (Codex gpt-6-astra, 2026-09-09)

> 구현이 아니라 **테스트**에 대한 리뷰다. 구현 변이 25개를 실제로 적용해, 어떤 잘못된 구현이 스위트를
> 통과하는지 확인했다. 지적을 모두 반영했고, 그 과정에서 구현 버그 넷이 더 나왔다 —
> 재읽기 실패 후 재시도 허용, 재로그인 무효화의 토픽 오류, `testing.mount()`가 거절된 컴포넌트를
> 렌더한 것, 재읽기가 세션 키를 지워 다음 검사를 무의미하게 만든 것.
>
> 인용된 줄 번호는 리뷰 시점의 것이고 수정 이후와는 어긋난다. 무엇을 어떻게 고쳤는지는
> `CHANGELOG.md`에 있다.


검토 기준: `501e7b1` + 요청 시점의 미커밋 변경. 구현 수정은 남기지 않았다.

**깨진 구현이 통과한다.** 특히 자식 복원 어댑터, 세션 장애 모킹, 재로그인 토픽의 기대값이 실제 계약을 검증하지 않는다. 추가 재현에서는 변이하지 않은 구현에서도 재읽기 거절 후 재시도 통과, 재로그인 시 잘못된 토픽 발행, `testing.mount()` 거절 후 HTML 렌더를 확인했다.

## 실행 범위와 해석

- Python: `tests/test_live_session_contract.py`, `tests/test_live_session.py`, `tests/test_checks.py` 전체. 원본 **259 passed, 7 skipped**. 계약 파일만 수집하면 **177개**이며 170개 통과, 7개 skip이다. 모듈 순서를 뒤집어도 259 passed, 7 skipped였다.
- JS: `node --test tests/js/live-session.test.mjs` — 원본 **11 passed**.
- E2E: `tests/test_live_session_e2e.py`, Chromium, memory 채널 레이어, 임시 SQLite DB — 원본 **7 passed**. 샌드박스의 Chromium 실행 차단은 실행 권한 확장으로 해소했다. 아래 변이 결과의 E2E는 JS를 빌드하고 collectstatic한 뒤 실행했다.
- 서로 독립적인 구현 변이 **25개**를 실제 적용했다. 각 변이마다 Python 3개 파일 전체 또는 해당 JS 파일을 실행했다. Python에서 살아남은 10개는 E2E도 실행했다. 저장소 전체 테스트에 대한 변이 점수는 아니다.
- 추가 재현 테스트 5개는 원본에서 모두 실패했다. 의도한 계약 단언의 실패이며 기존 테스트의 실패 수에 섞지 않았다.
- 실험 스크립트·변이별 전체 로그·원문 백업: `/tmp/wireview-test-review/`. `mutate.py`, `more.py`, `e2e_mutants.py`, `test_probes.py`로 재현할 수 있다. 직접 바이트 원복했으며 `git checkout`·stash를 사용하지 않았다.

## 지적

### [심각도: 높음] 세션 재읽기 실패를 모킹해 버리고, 실패 후 재시도를 검증하지 않는다

- **어디**: `tests/test_live_session_contract.py:867`, `tests/test_live_session_contract.py:754`; 관련 구현 `wireview/consumer.py:274`, `wireview/consumer.py:336`.
- **무엇**: 장애 테스트는 실제 백엔드 대신 `_reload_session` 전체를 항상 `False`인 함수로 바꾼다. 바로 위 `broken()`은 한 번도 호출하지 않는다. 따라서 구현이 백엔드 예외를 잡아 `True`를 반환해도 통과한다. 또한 거절 지속성은 같은 컴포넌트의 `_mount()`만 검사한다. 연결의 첫 재읽기가 실패한 뒤 같은 유효 토큰으로 다시 join하는 경우는 빠졌다.
- **근거**: `_reload_session`의 예외 처리 `return False`를 `return True`로 바꾼 `reload_fail_open`이 Python 전체를 통과했다. 별도 원본 재현에서는 `_reload_session`이 계속 `False`를 반환하도록 하고 같은 연결에 같은 토큰을 두 번 보냈다. 첫 응답은 `reload`, 두 번째는 `render`였고 저장소에 `target`이 생겼다. 첫 시도에서 `_auth_topic`을 설정한 뒤 실패하므로 두 번째는 재읽기를 건너뛴다. 재시도 문제는 구현 변이 없이 재현했다.
- **어떻게 고치나**: 세션 키가 있는 실제 Store/SessionView를 만들고 **백엔드의 load 지점**에서 예외를 발생시켜 `_reload_session` 본문을 실행한다. `connection_id` 등 실제 연결 속성도 채운다. 같은 연결에서 첫·둘째 join 모두 마크업/상태/저장소 등록/이벤트 처리가 없는지 단언한다. 백엔드 예외와 로그인 세대 불일치 두 실패를 나누고, 성공한 연결의 두 join은 재읽기 한 번으로 허용되는 대조군을 유지한다.

### [심각도: 높음] 재로그인 무효화 테스트의 기대 토픽이 실제 연결의 토픽과 다르다

- **어디**: `tests/test_live_session.py:887`, 특히 `:899`; 관련 구현 `wireview/core/live_session.py:431`.
- **무엇**: 기대값이 이전 세션 전체가 아닌 `{AUTH_GENERATION_KEY: first}`로 계산된다. 실제 로그인 세션에는 `_auth_user_hash`도 있고 이 값도 지문에 포함된다. 테스트가 구현의 누락을 기대값에서도 그대로 반복하므로, 아무 연결도 듣지 않는 토픽에 발행해도 통과한다.
- **근거**: 발행 자체를 제거한 `login_no_invalidation`은 기존 테스트가 잡았다. 그러나 원본 구현으로 실제 `login()` 직후 `auth_topic(auth_fingerprint(user, request.session))`을 보관하고 재로그인 발행 토픽과 비교한 재현은 실패했다. nonce는 바뀌고 메시지도 발행되지만 목적지가 달랐다.
- **어떻게 고치나**: 첫 로그인 후 실제 consumer가 구독한 토픽을 보관하고 두 번째 로그인에서 그 토픽으로 `session_invalidated`가 발행되는지 검사한다. 최소한 첫 세션 전체를 스냅샷으로 저장해 기대값을 구해야 한다. 다른 토픽을 듣는 연결은 유지되고 이전 세대 연결은 닫히는 대조군까지 추가한다. nonce 변경·발행 호출·연결 종료를 서로 단절된 테스트로만 확인하지 않는다.

### [심각도: 높음] child_restore는 정상 자식 토큰을 한 번도 복원하지 않는다

- **어디**: `tests/test_live_session_contract.py:425`; 관련 구현 `wireview/consumer.py:162`.
- **무엇**: `command_join`은 children 값의 `(child_name, child_state)` 쌍을 기대하지만 어댑터는 `{"name": ..., "state": ...}`를 준다. dict 순회가 두 키를 풀어 **클래스명 `"name"`, 토큰 `"state"`**를 전달한다. 서명 오류로 항상 버린 뒤 부모 템플릿에서 새 자식을 만드는 경로가 실행된다. 허용 대조군도 기본값 `note="ok"`만 보므로 복원과 신규 생성을 구별하지 못한다.
- **근거**: 원본에서 `unsign_envelope` 호출을 기록해 `('state', 'name')`을 확인했고, `No ":" found in value` 경고도 나왔다. `child_drop_all_restores`(복원 루프를 비움), `child_skip_validation_call`(자식 경계 검사 호출 생략), `child_ignore_auth`(자식 인증 비교만 제거)가 모두 Python 전체를 통과했다. 경계 비교만 제거하는 변이는 기존 `test_a_restored_child_state_is_refused_from_another_boundary`가 잡았지만, 이 테스트는 헬퍼 직접 호출이라 **호출 지점 삭제**는 잡지 못했다.
- **어떻게 고치나**: `children={"target": (cls.__name__, child_token)}`로 전달한다. 템플릿 props에는 없는 `note="restored-only"`를 토큰에 넣고 허용 시 실제 자식의 필드와 렌더 결과에 보존되는지 단언한다. 같은 클래스·같은 경계·다른 인증, 같은 클래스·같은 인증·다른 경계, 클래스만 다름을 별도 입력으로 준다. 거절된 **복원 상태**는 버리고 현재 페이지 정책 아래 기본 상태 자식을 만드는 동작도 확인해야 한다. 복원 거절과 컴포넌트 자체 거절은 다른 계약이다.

### [심각도: 높음] 세 가지 거절 사유에 세션 훅의 거절이 없다

- **어디**: `tests/test_live_session_contract.py:153`, `tests/test_live_session_contract.py:225`.
- **무엇**: halt/exception 클래스는 모두 **컴포넌트 `_on_mount`**에만 실패 훅을 둔다. `boundary`의 세션 훅은 항상 cont다. 세션 훅이 실행됐다는 순서는 검증하지만, 그 훅의 거절을 따르는지는 검증하지 않는다.
- **근거**: `LiveSession.run_on_mount()`가 halt를 받았을 때 `{"halt": True, ...}` 대신 `{"cont": True}`를 반환하는 `session_hook_ignore_halt`가 Python 전체를 통과했다. 세션 훅의 예외를 삼키는 별도 변이는 실행하지 않았다.
- **어떻게 고치나**: `session_halt`, `session_exception`을 추가한다. 자식 경로에서는 부모까지 거절해 버리지 않도록 `component.id == "target"`일 때만 실패하는 정책 훅을 쓴다. 뒤따르는 컴포넌트 훅·`joined()`·렌더의 부작용이 전혀 없는지도 기록한다. 경계의 `authorize=False/raise`는 컴포넌트 생성 매트릭스와 별도로 HTTP/첫 join 입구에서 검증한다.

### [심각도: 높음] HTTP에서 렌더되는 LiveComponent 자식 경로가 빠졌다

- **어디**: `tests/test_live_session_contract.py:434`; 관련 구현 `wireview/templatetags/wireview.py:884`.
- **무엇**: `dead_render`는 일반 Component 하나, `live_child`와 `child_restore`는 모두 `is_live=True`다. HTTP 부모 템플릿의 `{% live_component %}`가 `is_live=False` 분기에서 직접 마운트·렌더되는 경로는 별도로 존재한다. 여기서 검사를 생략하면 첫 HTTP 응답에 보호된 자식이 실린다.
- **근거**: HTTP 자식 분기의 `if not _mount_in_template(live_comp, repo):`를 `if False:`로 바꾼 `dead_live_child_skip_mount`가 Python 전체를 통과했다. 다른 템플릿 마운트 경로는 유지한 국소 변이다.
- **어떻게 고치나**: `dead_live_child` 행을 추가한다. `is_live=False` 저장소로 허용 부모를 HTTP 렌더하고 그 내부의 보호된 LiveComponent에 기존 거절/허용 단언을 적용한다. 블록/슬롯 버전에도 같은 경계·저장소가 전달되는 대표 사례를 추가한다.

### [심각도: 중간] testing_mount는 실제 결과 대신 기대 결과를 직접 만들어 낸다

- **어디**: `tests/test_live_session_contract.py:310`, `:479`, `:493`, `:525`.
- **무엇**: 어댑터는 `mounted.render()`를 호출하지 않고 `mount_halted`이면 빈 문자열, 아니면 `SECRET`와 클래스 마커를 합성한다. 따라서 이름이 렌더·서명 상태 검사여도 실제 HTML/서명 발급은 전혀 검증하지 않는다. 실제 테스트 API의 핸들러 호출도 저장소가 없다는 이유로 검사 대상에서 빠진다.
- **근거**: `MountedComponent.render()`를 항상 빈 문자열로 만드는 `testing_render_noop`이 Python 전체를 통과했다. 더 직접적으로, 원본에서 `await mount(CxHalts, ...)`와 `await mount(CxElsewhere, ...)`는 `mount_halted=True`였지만 **`mounted.render()`에 `CX-SECRET`이 포함됐다**. 기존 합성 payload가 이 차이를 숨겼다.
- **어떻게 고치나**: 실제 `mounted.render()`를 payload로 사용하고 허용/거절 결과를 비교한다. 허용 HTML의 해당 컴포넌트 `data-state`를 추출해 `unsign_envelope`까지 검사한다. 테스트 API에도 거절 후 `call()` 차단을 계약으로 요구한다면 실제 `mounted.call("bump")`와 필드 변화로 검사한다. 서버 저장소 접근이 불가능한 skip 자체는 정당하지만, 대신 검사할 수 있는 렌더·호출까지 합성하거나 생략하면 안 된다.

### [심각도: 높음] 허용·거절 모두 joined()의 실행 여부와 순서를 관찰하지 않는다

- **어디**: `tests/test_live_session_contract.py:130`, `tests/test_live_session_contract.py:531`; 관련 구현 `wireview/repository.py:375`.
- **무엇**: CALLS에는 두 종류의 mount 훅만 기록된다. `joined()`가 인가 전 실행되거나 두 번 실행되어 DB 쓰기·외부 작업을 시작해도, 최종 마크업/저장소만 정리되면 현재 불변식을 만족한다. 거절의 총체성에 인가 이전 라이프사이클 부작용이 없다.
- **근거**: `repo.join()`에서 `component._mount()` 직전에 `await component.joined()`를 추가한 `joined_before_mount`가 Python 전체를 통과했다. 이 변이는 거절된 컴포넌트의 joined를 한 번, 허용된 컴포넌트의 joined를 두 번 실행한다.
- **어떻게 고치나**: probe 컴포넌트의 `joined()`에 외부 리스트 기록 또는 명시적인 카운터를 둔다. 라이브 허용은 `session → component → joined`가 한 번, 거절은 joined 0회여야 한다. HTTP는 joined를 호출하지 않는 경로 계약에 맞춰 별도로 단언한다.

### [심각도: 중간] “실패가 드러난다”는 단언이 예외·로그·거절 응답을 보지 않는다

- **어디**: `tests/test_live_session_contract.py:496`.
- **무엇**: `Outcome.raised`를 수집해 놓고 거절 검사에서는 사용하지 않는다. 예외 사유도 `outcome.calls`가 비어 있지 않은지만 확인하므로 세션 훅 하나가 돌았다는 것만으로 충분하다. 나머지 단언은 기존 “마크업 없음”과 같다. 예외를 삼키고 빈 결과로 조용히 성공해도 통과한다.
- **근거**: `_mount_in_template()`의 예외 처리에서 `repo.abandon(component); raise`를 `repo.abandon(component); return False`로 바꾼 `template_swallow_exception`이 Python 전체를 통과했다.
- **어떻게 고치나**: 경로마다 기대되는 실패 전달 방법을 Outcome에 명시한다. 직접 HTTP/테스트 mount는 실제 RuntimeError의 타입·메시지, consumer가 흡수하는 경로는 remove/reload 명령 또는 해당 컴포넌트의 오류 로그를 검사한다. 자식 오류처럼 원래 로그만 남기는 경로에 무조건 `pytest.raises`를 요구하지 않는다. 예외 훅 자체가 호출됐는지도 구별되는 라벨로 확인한다.

### [심각도: 중간] 클라이언트 명령 대조군은 그 명령을 호출하지 않는다

- **어디**: `tests/test_live_session_contract.py:742`.
- **무엇**: `command`로 여섯 번 매개변수화하지만 본문은 동일한 join과 저장소 존재 검사뿐이다. 따라서 “정상 대상 명령도 모두 무시한다”는 구현에 대한 대조군이 아니다. 거절 쪽의 `outbound.commands == []`도 명령별 서버 부작용을 직접 관찰하지 않는다.
- **근거**: `command_user_event()` 첫 줄에 `return`을 넣는 `user_event_noop`이 Python 전체를 통과했다. 브라우저 E2E의 `test_a_server_push_out_of_the_boundary_reloads`에서는 실제로 실패했다(나머지 6개 통과). 따라서 전체 스위트의 미검출 사례가 아니라 Python 대조군 자체의 결함이다.
- **어떻게 고치나**: 허용 쪽도 `CLIENT_COMMANDS[command](consumer, "target")`를 실제 호출한다. user_event는 note 변화, hook_event는 구현한 hook의 기록, 업로드는 유효한 등록/완료 항목의 변화, leave는 제거·leaving 기록을 단언한다. 빈 업로드 목록과 없는 ref만으로 허용 대조군을 구성하지 않는다.

### [심각도: 중간] 거절 rejoin 행에는 기존에 허용된 인스턴스가 없다

- **어디**: `tests/test_live_session_contract.py:326`; 보완되지 않는 기존 사례 `tests/test_live_session.py:499`.
- **무엇**: 거절 클래스의 첫 join부터 실패하므로 두 번째 join 직전 저장소가 비어 있다. 기존 인스턴스를 퇴역시킨 뒤 새 마운트가 거절되는 rejoin 실패 분기를 검증하지 않는다. 허용 rejoin 대조군은 있지만, “이전에 허용 → 새 정책/상태에서 거절” 전이는 없다.
- **근거**: 어댑터와 consumer의 기존 인스턴스 분기를 정적으로 대조했다. 이 전이에 특화한 구현 변이는 실행하지 않았다. 기존 동작 테스트도 `repo.remove()`를 직접 호출한 뒤 다시 join하므로 consumer의 실패 전이 검사를 대신하지 못한다.
- **어떻게 고치나**: 첫 호출에는 cont, 두 번째에는 halt/raise인 훅으로 같은 클래스·id를 join한다. 첫 인스턴스와 자식·이벤트 핸들러가 존재함을 먼저 확인하고 재join 후 모두 제거됐는지, leaving/작업 정리와 새 마크업 억제가 지켜지는지 검사한다. 두 번 다 허용되는 대조군도 유지한다.

### [심각도: 낮음] E2E 서버 스레드가 테스트 종료 뒤에도 살아 전역 설정 복원을 경합한다

- **어디**: `tests/test_live_session_e2e.py:42`, `tests/test_live_session_e2e.py:72`.
- **무엇**: `run()`의 `@override_settings(DEBUG=True)`와 서버 스레드 수명 종료가 맞물린다. fixture teardown은 `terminate()`만 호출하고 thread가 끝날 때까지 join하지 않아 다음 테스트 또는 pytest 종료와 겹칠 수 있다.
- **근거**: 원본 E2E는 7 passed/종료 코드 0이었지만 이후 `Exception in thread Thread-7`과 `AttributeError: 'override_settings' object has no attribute 'wrapped'`가 출력됐다. 구현 변이는 하지 않았다. 서버 스레드의 예외가 성공 결과에 반영되지 않는 실제 실행 증거다.
- **어떻게 고치나**: 각 서버 실행에 독립적인 설정 컨텍스트를 사용하고 teardown에서 종료 요청 후 제한 시간 내 thread.join을 수행한다. 스레드의 시작/실행 오류를 fixture로 전달하며, 시작 대기에도 timeout을 둔다.

## 25개 변이 결과

숫자는 해당 Python 파일에서 실패한 테스트 수다. `0 / 0`은 **Python 259개 전부 통과**를 뜻한다. `test_checks.py`는 모든 변이에서 통과했다. E2E를 실행하지 않은 변이는 `—`로 표시한다.

| 실제 변이 | 계약 실패 | 기존 동작 실패 | E2E 실패 | 판정 |
|---|---:|---:|---:|---|
| 선언된 클래스 항상 거절 (`declaration_always_deny`) | 7 | 3 | — | 검출 |
| 선언된 클래스 항상 허용 (`declaration_always_allow`) | 40 | 6 | — | 검출 |
| abandon에서 freeze 제거 (`abandon_without_freeze`) | 14 | 2 | — | 검출 |
| abandon에서 remove 제거 (`abandon_without_remove`) | 36 | 5 | — | 검출 |
| 컴포넌트 훅을 세션 훅보다 먼저 실행 (`hook_order`) | 8 | 2 | — | 검출 |
| root 봉투 a 비교 제거 (`root_ignore_auth`) | 1 | 1 | — | 검출 |
| root 봉투 s의 연결 내 혼합 비교 제거 (`root_ignore_boundary_mix`) | 2 | 1 | — | 검출 |
| 재읽기 후 스냅샷 교체 제거 (`reload_no_snapshot`) | 0 | 1 | — | 검출 |
| 백엔드 예외에서 True 반환 (`reload_fail_open`) | 0 | 0 | 0 | **생존** |
| 인증 지문에 pk만 사용 (`fingerprint_pk_only`) | 4 | 2 | — | 검출 |
| 인증 지문에 auth hash만 사용 (`fingerprint_hash_only`) | 5 | 2 | — | 검출 |
| 재로그인 이전 세대 무효화 발행 제거 (`login_no_invalidation`) | 0 | 1 | — | 검출 |
| 자식 봉투 a 비교 제거 (`child_ignore_auth`) | 0 | 0 | 0 | **생존** |
| 자식 경계 검사 호출 생략 (`child_skip_validation_call`) | 0 | 0 | 0 | **생존** |
| 자식 복원 루프 비움 (`child_drop_all_restores`) | 0 | 0 | 0 | **생존** |
| 세션 훅 halt를 cont로 처리 (`session_hook_ignore_halt`) | 0 | 0 | 0 | **생존** |
| HTTP LiveComponent 마운트 검사 생략 (`dead_live_child_skip_mount`) | 0 | 0 | 0 | **생존** |
| 템플릿 마운트 예외를 False로 삼킴 (`template_swallow_exception`) | 0 | 0 | 0 | **생존** |
| MountedComponent.render가 항상 빈 문자열 (`testing_render_noop`) | 0 | 0 | 0 | **생존** |
| 반복 _mount가 무조건 True (`mount_sticky_wrong`) | 2 | 0 | — | 검출 |
| command_user_event가 즉시 return (`user_event_noop`) | 0 | 0 | 1 | E2E에서 검출 |
| NavigationGate.abandon no-op (`navigation_abandon_noop`) | — | — | — | JS 1개 실패로 검출 |
| mount 전에 joined 추가 실행 (`joined_before_mount`) | 0 | 0 | 0 | **생존** |
| 자식 봉투 s 비교 제거 (`child_ignore_boundary`) | 0 | 1 | — | 검출 |
| v2 봉투 클래스 비교 제거 (`class_skip_check`) | 1 | 0 | — | 검출 |

스냅샷 교체 누락은 기존 `test_a_policy_reading_session_data_sees_the_fresh_session`, 재로그인 발행 삭제는 기존 `test_logging_in_again_retires_the_generation_it_replaces`가 잡았다. 이 둘은 **계약 파일 단독으로는 놓치지만 전체 요청 스위트에서는 이미 검증되는 항목**이므로 별도 결함으로 세지 않았다. JS `NavigationGate.abandon()` no-op도 `abandoning ends the navigation in flight without starting another`가 잡았다.

## 빠진 축과 매트릭스 판정

- **수집 자체는 정상**: 거절 3 × 경로 7 × 단언 5 = 105, 허용 7 × 3 = 21, 경계 대조 7 × 2 = 14가 수집된다. 다만 7개 경로라는 이름과 달리 자식 복원 행은 실제 복원 경로가 아니며, 거절 rejoin도 기존 객체가 있는 전이가 아니다.
- **7 skip의 산식은 정상**: testing_mount의 저장소/이벤트 검사 3 × 2 = 6, 허용 이벤트 검사 1. 실제 서버 저장소 누락을 skip이 숨기지는 않는다. 문제는 위의 합성 payload와 테스트 API 직접 호출 미검증이다.
- **경로 목록은 완전하지 않다**: HTTP LiveComponent 분기는 확정 누락이다. `component_block`은 `_build_and_render_component`, `live_component_block`은 `_render_live_component`를 공유하므로 무조건 독립 행으로 곱할 필요는 없다. 다만 슬롯/함수 컴포넌트 안의 중첩 태그가 같은 저장소·경계를 유지하는지 대표 통합 사례가 유용하다. Function Component 자체는 상태/서명/join이 없는 별도 종류이므로 5개 단언을 그대로 적용하면 안 된다.
- **재렌더 트리거는 새 생성 경로와 구별해야 한다**: `params_changed`, `component_dispatch_event`, 서버 브로드캐스트가 허용 부모를 바꿔 보호 자식을 처음 나타나게 할 수 있다. 이들은 `send_render`/트리 처리와 템플릿 생성 경로로 합류한다. 트리거별로 모든 매트릭스를 복제하기보다 “처음에는 자식 없음 → 이벤트/params/broadcast 후 거절 자식 등장”의 대표 사례와 재사용/update 사례를 추가한다. 이 트리거들에 대한 별도 변이는 하지 않았다.
- **거절 사유 3개는 전부가 아니다**: 세션 훅 halt/raise, HTTP/소켓 authorize 거절·예외, 봉투의 자식별 경계/인증 불일치, 세션 백엔드 실패·세대 종료가 서로 다른 분기다. 전부 7개 생성 경로와 곱하는 대신 해당 입구의 매트릭스로 나누는 편이 정확하다.
- **10개 불변식에 추가할 것**: 인가 전에 joined/다른 사용자 훅이 부작용을 내지 않음; 정상 자식 복원 상태는 보존되고 거절된 복원 상태만 폐기됨; 거절 연결의 재시도도 fail-closed; 실제 발행 토픽과 실제 구독 토픽의 일치; HTTP 문서의 request 경계·메타·여러 컴포넌트 봉투가 같은 경계를 가짐. 현재 “페이지 하나”에 비해 “연결 하나” 검증이 강하다.
- **브라우저 검증의 범위**: 최종 window probe 소실은 전체 로드를 잘 구별한다. 다만 로드 전 잠깐 발생한 morph/join까지 없었는지, `wireview-boost.js`가 gate의 abandon을 실제 호출하는지는 별도 관찰이 필요하다. 현재 같은 경계 대조군은 빈 이름 두 페이지뿐이다. 이름 있는 같은 경계의 두 페이지 이동, 지연 fetch/RAF와 교차 이동의 경합을 추가할 가치가 있다. 이 JS 통합부 삭제 변이는 실행하지 않았으므로 살아남았다고 단정하지 않는다.
- **전역 오염**: live_session 레지스트리는 계약 fixture가 저장→비움→복원한다. CALLS도 매 테스트 전에 비운다. 컴포넌트 이름은 `cxprobe.live` 및 Cx 접두사, 부모 클래스 캐시는 재등록을 줄인다. 컴포넌트 클래스 등록 자체는 복원되지 않지만 이번 정순/역순 실행에서 실패나 충돌은 관찰하지 않았다. 전역 등록 목록을 순회하는 미래 테스트에는 영향을 줄 수 있으므로 “오염 없음”을 전역적으로 보장한 것은 아니다.

## 이 스위트로 잡히지 않는 잘못된 구현

우선순위순이다. 아래의 “원본 재현”은 가상 변이보다 강한 증거로, 현재 구현이 이미 통과하고 있는 경우다.

1. 세션 재읽기 실패 후 동일 연결의 두 번째 join을 허용한다 — **원본 재현**. 백엔드 예외 시 `True`를 반환하는 변이도 생존.
2. 재로그인 무효화를 이전 연결이 구독하지 않는 토픽으로 발행한다 — **원본 재현**. 발행 자체를 삭제하는 변이는 기존 테스트가 잡으므로 구별해야 한다.
3. 자식 복원 봉투의 인증 검사를 빼거나, 경계 검사 호출 자체를 생략한다 — **실제 변이 생존**.
4. 세션 on_mount의 halt를 무시한다 — **실제 변이 생존**.
5. HTTP LiveComponent 자식 마운트 검사를 생략한다 — **실제 변이 생존**.
6. mount 인가 전에 joined를 실행하고 허용 시 다시 실행한다 — **실제 변이 생존**.
7. 거절된 `testing.mount()` 결과가 실제 render에서는 비밀 HTML을 내보낸다 — **원본 재현**. 반대로 테스트 도우미 render가 항상 빈 문자열이어도 생존.
8. 모든 자식 복원 상태를 버리고 매번 기본 상태로 생성한다 — **실제 변이 생존**.
9. 템플릿 마운트 예외를 외부에 드러내지 않고 빈 결과로 바꾼다 — **실제 변이 생존**.

`command_user_event()` no-op은 Python 대조군의 결함을 드러내는 변이다. E2E에서 잡히므로 위의 전체 스위트 생존 목록에는 넣지 않았다. 실제 실행하지 않은 rejoin 실패 전이와 JS 경합 변이도 이 목록에서 제외했다.
