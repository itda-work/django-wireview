# 설계 메모

결정과 그 근거를 남기는 곳이다. 기능 사용법은 [`../features/`](../features/), 프로토콜 같은
구현 정본은 [`../implementation/`](../implementation/)에 있다.

| 문서 | 무엇 | 상태 |
|------|------|------|
| [transport-abstraction.md](./transport-abstraction.md) | 전송 추상화(`Outbound`/`Broker`), 채널 레이어 선택, Windows·NATS 실측, 연결당 메모리의 원인 | 결정됨 (GAP-026 완료). 5절 이후가 실측 기록이다 |
| [broadcast-fanout.md](./broadcast-fanout.md) | 브로드캐스트 하나가 연결 1,000개에 닿는 632 ms의 단계별 실측(연결당 약 680 µs를 한 코어에서 직렬로, 렌더 52%·마커 파싱 18%), 줄이는 선택지 A~E, Phoenix와의 비교 | **1단계(B) 구현** ([#176](https://github.com/itda-work/django-wireview/issues/176)). 연결당 723 → 460 µs, 팬아웃 722 → 445 ms(같은 날 비교, §6). 2단계(A)·3단계(D)는 남았다. 측정은 `bench/fanout_profile.py` |
| [distributed-uploads.md](./distributed-uploads.md) | 청크 업로드를 어느 워커에서나 받는 방법: 무상태 HTTP + 계산된 경로 + 브로커로 오는 가변 상태. 버린 갈래 셋(스티키·브로커 RPC·공유 레지스트리)과 이유 | 결정됨, 구현 완료 ([#83](https://github.com/itda-work/django-wireview/issues/83)) |
| [session-extraction.md](./session-extraction.md) | 컨슈머에서 세션 로직을 떼어 내는 계획과 착수 기준 (GAP-027) | 1단계 완료([#60](https://github.com/itda-work/django-wireview/issues/60), v1.0.0rc3), 3단계는 [#83](https://github.com/itda-work/django-wireview/issues/83)으로 완료. 2·4단계는 5절의 착수 기준을 만족할 때 연다 |
| [live-session.md](./live-session.md) | 페이지 단위 인증·정책 경계 (Phoenix의 live_session) | 구현 완료. 사용법은 [features/live-session.md](../features/live-session.md) |
| [live-session-review-2026-09-09.md](./live-session-review-2026-09-09.md) | 위 두 문서 초안에 대한 적대적 리뷰 원문 (Codex gpt-6-astra) | 반영 완료 |
| [live-session-review-2026-09-09-round2.md](./live-session-review-2026-09-09-round2.md) | 1라운드 반영 결과를 다시 검증한 2라운드 | 반영 완료. 여기서 내 수정 자체의 오류 셋이 나왔다 |
| [live-session-review-2026-09-09-round3.md](./live-session-review-2026-09-09-round3.md) | 구현 완료본에 대한 3라운드. 재현 테스트를 붙여 왔다 | 반영 완료. 봉투 결합이 아니라 그 옆(예외 경로·중첩 컴포넌트·인증 수명)에서 여덟 개가 나왔다 |
| [live-session-test-review-2026-09-09.md](./live-session-test-review-2026-09-09.md) | 계약 테스트 자체에 대한 리뷰. 구현 변이 25개로 "무엇이 통과하는가"를 쟀다 | 반영 완료. 테스트 결함 열하나와 구현 버그 넷이 나왔다 |
| [live-session-final-review-2026-09-10.md](./live-session-final-review-2026-09-10.md) | 4라운드. 판정은 릴리스 보류였다 | 반영 완료. 차단 둘(해시가 로그아웃 토픽을 어긋나게 함, 경계 없는 토큰의 정책 전환)과 그 아래 다섯 |
| [live-session-rejudge-2026-09-10.md](./live-session-rejudge-2026-09-10.md) | 5라운드 재판정. 다시 보류였다 | 반영 완료. 4라운드 수정이 만든 회귀 하나와, 전환 계약이 딛고 있던 틀린 전제(무경계 토큰은 스스로 갱신된다) |
| [live-session-rejudge2-2026-09-10.md](./live-session-rejudge2-2026-09-10.md) | 6라운드. **릴리스 가능** | 남은 것은 세션 저장소·브로커·연결 수명의 일반적 한계이고 문서에 범위가 적혀 있다 |
| [longpolling-fallback.md](./longpolling-fallback.md) | WebSocket 폴백 (GAP-012, [#59](https://github.com/itda-work/django-wireview/issues/59)) | **결정됨: 만들지 않는다.** WebSocket 이 필수 전제다. 버린 길 셋(스티키 라우팅·무상태 세션·SSE)과 그 이유가 여기 있다 |
| [colocated-hooks.md](./colocated-hooks.md) | 컴포넌트 옆의 JS 훅 (GAP-032, [#71](https://github.com/itda-work/django-wireview/issues/71)) | 결정됨, 구현 완료. §7이 먼저 하라고 한 예제·E2E가 훅의 첫 사용자다. §2-(가)의 순서 경합은 재현해서 고쳤다 |
| [vision.md](./vision.md) | 시작할 때의 프로젝트 비전과 계획 | **기록.** 보존만 한다. 지금의 정본은 [ROADMAP](../ROADMAP.md)·[FEATURE-GAP](../FEATURE-GAP.md) |
| [security-audit-plan.md](./security-audit-plan.md) | 2025년의 보안 감사 계획(공격 표면과 체크리스트) | **기록.** 보존만 한다. 수정은 #28, 지금의 경계는 [SECURITY.md](../../SECURITY.md) |
| [migration-pydantic.md](./migration-pydantic.md) | Pydantic v1 → v2 마이그레이션 가이드 | **끝난 작업의 기록.** 지금 구현은 `wireview/core/component.py` |
| [backlog-review-2026-09-10.md](./backlog-review-2026-09-10.md) | `v0.3.0` 이후 잔여 이슈에 무엇을 다시 물어야 하는지 | 이슈가 정본이고 이 문서는 빠져 있는 사실만 적는다 |
| [rails-benchmark-2026-09-18.md](./rails-benchmark-2026-09-18.md) | Rails 7.1~8.1이 기본 경로에서 걷어낸 것, 같은 축에서 본 Django 6.1, 그리고 wireview가 배울 것. Solid Cable식 DB 폴링 레이어 시제품의 실측(NATS와 대등)과 저장 버스트의 렌더 횟수 실측이 여기 있다 | **조사 메모 — 결정 아님.** 제안은 이슈가 되기 전까지 구속력이 없다. 조사 중에 나온 결함 둘은 고쳤다([#87](https://github.com/itda-work/django-wireview/issues/87), [#88](https://github.com/itda-work/django-wireview/issues/88)) |
| [djust-vdom-review-2026-09-18.md](./djust-vdom-review-2026-09-18.md) | djust의 Rust VDOM(html5ever 파싱 → 트리 diff → opcode)을 읽고 wireview에 차용할 것을 가렸다. 목록 편집 유형별 페이로드 실측이 여기 있다 | **조사 메모 — 결정 아님.** 판정은 "Rust·VDOM·opcode는 두고, 키 기반 comprehension·위젯 보존 계약·sticky 수명 분리·delta round-trip 테스트를 가져온다" |
| [djust-vdom-review-codex-2026-09-18.md](./djust-vdom-review-codex-2026-09-18.md) | 위 조사의 소스 정독 원문 (Herdr pane의 Codex gpt-6-astra) | 손대지 않은 근거 문서. 함수·구조체 이름 단위의 근거는 여기에 |
| [keyed-comprehension.md](./keyed-comprehension.md) | GAP-030([#69](https://github.com/itda-work/django-wireview/issues/69)): 목록 항목을 키가 아니라 **내용으로** 짝지어 앞 삽입·삭제·이동을 그 항목만의 페이로드로 만든다. wire 형태 `{"k": [...]}`, 옛 클라이언트를 위한 `?vsn=2`, Phoenix `mergeKeyed`와의 비교, 시제품 실측 | **결정됨, 구현 완료.** Codex 리뷰를 반영한 2판대로 구현했다. 실측은 [features/html-diff.md](../features/html-diff.md) |
| [keyed-comprehension-review-codex-2026-09-19.md](./keyed-comprehension-review-codex-2026-09-19.md) | 위 메모 첫 판과 round-trip 테스트에 대한 적대적 리뷰 원문 (Codex gpt-6-astra). 반례·재현 스크립트 포함 | 반영 완료. 정확성 반례는 없었고 선택 규칙·성능 근거·토큰 조언·검증 범위가 고쳐졌다 |
| [temporary-assigns-change-tracking.md](./temporary-assigns-change-tracking.md) | GAP-006([#111](https://github.com/itda-work/django-wireview/issues/111)): 초기화한 temporary assign을 변경으로 치지 않는다. 렌더 중 읽기 추적, 블록의 정적 분석, 버린 길 넷 | 결정됨, 구현 완료 |
| [csp-event-binding.md](./csp-event-binding.md) | [#90](https://github.com/itda-work/django-wireview/issues/90): `{% on %}`을 인라인 `on*` 속성에서 `wire-on-*` 데이터 속성과 `<html>`의 위임 리스너로. 수정자 의미 보존, 라이브가 아닐 때의 진행형 향상, 옮기며 드러난 결함 일곱(업로드 태그가 브라우저에서 한 번도 동작하지 않았던 것 포함) | 결정됨, 구현 완료. 사용법은 [features/csp.md](../features/csp.md) |
| [input-values.md](./input-values.md) | #91·#92: 사용자가 고친 입력값을 렌더가 언제 덮어도 되는가. 확정 액션과 보조 동작, `ref`로 응답 짝짓기(허락은 그 render의 morph에만), 보낸 뒤의 입력, IME, 옛 서버와의 버전 방향 | 결정됨, 구현 완료 (2판) |
| [input-values-review-codex-2026-09-19.md](./input-values-review-codex-2026-09-19.md) | #92 첫 구현에 대한 리뷰 원문 (Codex gpt-6-astra). 응답 허락과 morph 프레임의 경합, `myself` 범위, 보낸 뒤 입력, 답 없는 ref | 반영 완료 ([input-values.md](./input-values.md) 2판) |
| [stubs-input-values-review-codex-2026-09-19.md](./stubs-input-values-review-codex-2026-09-19.md) | #89 스텁 생성·#91 입력값 보존·classmethod descriptor 수정에 대한 구현 리뷰 원문 (Codex gpt-6-astra). 재현 스크립트 출력 포함 | #89 쪽(발견 1·2·7~10)은 반영 완료. #91 쪽(3~6)은 [#92](https://github.com/itda-work/django-wireview/issues/92) |
| [auto-broadcast-fields.md](./auto-broadcast-fields.md) | [#144](https://github.com/itda-work/django-wireview/issues/144): AUTO_BROADCAST가 senders 모델의 모든 필드를 보내는 문제. 수신자가 실제로 읽는 필드 전수, 선택지 다섯(별도 키·매핑 senders·전역 pk 모드·모델 선언·기본 뒤집기), 부분 페이로드의 빠진 필드를 기본값이 아니라 deferred로 복원해야 하는 이유(#153의 `decode`가 구현) | **구현됨(1.1): 안 A2(senders 매핑)와 W017.** 1.0은 바꾸지 않는다 |
| [broadcast-patch.md](./broadcast-patch.md) | [#178](https://github.com/itda-work/django-wireview/issues/178)(#176의 D): 항목을 한 번 렌더하고 한 번 직렬화한 스트림 삽입·삭제, `push_event`, `JS()`를 렌더 없이 구독 연결 모두에 보낸다. 연결마다 다른 것은 컴포넌트 id뿐이라 브라우저 프로토콜은 그대로다. Phoenix·Turbo·Livewire 조사, API 후보 셋(`Broadcast` 빌더 권장), 사람마다 다른 내용을 발행 때 막는 법, join 순서(잡기·놓기), 세션 경유(D1)와 프로세스 fastlane(D2)의 시제품 실측(연결 1,000개 29.6~33.9 ms, 18.7~25.8 ms) | **구현**(§11). 권장안 그대로, D1을 재고 D2(프로세스 fastlane)까지. 연결 1,000개 18.0~19.7 ms(InMemory·Redis·NATS), 알림 경로는 276.3~742.6 ms. 측정은 `bench/compare_fastapi/stream_fanout.py` |
| [render-part-queries.md](./render-part-queries.md) | [#182](https://github.com/itda-work/django-wireview/issues/182): 렌더 부분(템플릿 줄·property·핸들러·작업)별 SQL 귀속으로 N+1을 보여 주는 개발용 훅. 렌더 경계의 현황, 줄은 노드 자신의 `origin`으로(상속·`block.super`·`let:` 슬롯·덮어쓴 사용자 노드에서 단언), shared render 수신자의 검증 렌더·서명 SQL과 묶음 서명을 요청한 쪽으로 되돌리는 법, 래퍼를 `execute_wrappers` 맨 아래에 두는 이유, 비용(켬↔끔: 템플릿 SQL 하나에 약 7~22 µs, 쿼리 없는 렌더는 0~7 µs. 끔 대 기능 전은 측정하지 못했고 설정 조회 등의 비용이 남는다, §4-4), 표시(로그·`wireview.testing`·편집기) | **결정됨, 1·2단계 구현됨.** 내부 경계 + 스택 위치, `DEBUG_RENDER_QUERIES`(None = DEBUG), telemetry 제외. 단계 3은 #188 |
| [render-queries-editor.md](./render-queries-editor.md) | [#188](https://github.com/itda-work/django-wireview/issues/188)(#182의 단계 3): 렌더의 SQL 귀속을 개발 서버가 JSONL로 남기고 VS Code 확장이 템플릿 줄과 property의 `def` 줄에 inlay hint로 보인다. 프로세스별 세그먼트 파일(덧붙이기만, 끝까지 쓰고 실패하면 되돌림), 켜는 조건과 억제(실제 `DEBUG`, 환경 변수, `wireview.testing`), 컴포넌트 클래스별 렌더 스냅샷과 0건 기록으로 낡은 숫자 지우기(1.0은 렌더 단위만), 줄이 맞다는 근거(실행 중인 `Template.source`의 지문을 그 객체에 캐시, Origin이 컴파일마다 새로 생기는 로더에서만 — 캐시된 옛 컴파일본을 잡는다), Django 템플릿 캐시가 언제 비워지는지, 단일 writer 전제와 최선 노력 겹침 감지, 원격, 형식 버전(나노초는 문자열), 시제품 기록 비용(쿼리 7건 렌더에 +21~49 µs) | **결정됨(3판), 구현 전.** §9의 권고를 모두 채택했다. property 힌트를 포함하고, A(서버) → B(확장) 두 커밋, B 뒤 확장 0.2.0. 리뷰 반영은 §0. 형제 LiveComponent를 묶는 후속은 [#189](https://github.com/itda-work/django-wireview/issues/189) |
| [live-component-lifecycle.md](./live-component-lifecycle.md) | LiveComponent가 **지금 실제로** 어떻게 만들어지고 렌더되고 사라지는가, 그리고 그로부터 나오는 설계 질문 | 현황 조사. 답은 각 이슈에서 |
| [live-component-lifecycle-review.md](./live-component-lifecycle-review.md) | 위 조사의 검증 리뷰 원문 | 반영 완료. 초판의 결론 하나가 틀렸다 |
| [live-component-ownership.md](./live-component-ownership.md) | 위 조사에 대한 답: 자식은 부모 소유, 부모 템플릿에는 참조만, 자식 렌더는 비동기 단계에서 같은 메시지에 묶는다 | 결정됨, 구현 완료 ([#78](https://github.com/itda-work/django-wireview/issues/78) [#79](https://github.com/itda-work/django-wireview/issues/79) [#80](https://github.com/itda-work/django-wireview/issues/80)) |
| [live-component.md](./live-component.md) | LiveComponent 설계 | 구현됨 (GAP-005) |
| [live-component-issues.md](./live-component-issues.md) | LiveComponent 초기 구현의 문제와 개선 계획 (2025-12-09) | 과거 기록 |
| [phase3-plan.md](./phase3-plan.md) | Navigation·Form 단계 계획 (2025-12-09) | 과거 기록 |

## 여기에 무엇을 쓰나

- **결정과 그 이유.** 무엇을 골랐는지보다 **무엇을 버렸고 왜 버렸는지**가 더 오래 쓸모 있다.
- **실측.** 성능을 주장하는 문장에는 조건(기계·레이어·프로세스 수·연결 수)을 함께 적는다.
  외삽이면 외삽이라고 적는다.
- **리뷰 원문.** 설계가 크게 바뀌었으면 그 계기를 남긴다. 다음 사람이 "왜 이렇게 복잡한가"를
  물을 때 답이 되는 것은 결론이 아니라 기각된 대안이다.
