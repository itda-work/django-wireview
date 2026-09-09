# 설계 메모

결정과 그 근거를 남기는 곳이다. 기능 사용법은 [`../features/`](../features/), 프로토콜 같은
구현 정본은 [`../implementation/`](../implementation/)에 있다.

| 문서 | 무엇 | 상태 |
|------|------|------|
| [transport-abstraction.md](./transport-abstraction.md) | 전송 추상화(`Outbound`/`Broker`), 채널 레이어 선택, Windows·NATS 실측, 연결당 메모리의 원인 | 결정됨 (GAP-026 완료). 5절 이후가 실측 기록이다 |
| [distributed-uploads.md](./distributed-uploads.md) | 청크 업로드를 어느 워커에서나 받는 방법: 무상태 HTTP + 계산된 경로 + 브로커로 오는 가변 상태. 버린 갈래 셋(스티키·브로커 RPC·공유 레지스트리)과 이유 | 결정됨, 구현 완료 ([#83](https://github.com/itda-work/django-wireview/issues/83)) |
| [session-extraction.md](./session-extraction.md) | 컨슈머에서 세션 로직을 떼어 내는 계획과 착수 기준 | 착수 대상 아님 ([#60](https://github.com/itda-work/django-wireview/issues/60)) |
| [live-session.md](./live-session.md) | 페이지 단위 인증·정책 경계 (Phoenix의 live_session) | 설계 초안, 구현 전 ([#58](https://github.com/itda-work/django-wireview/issues/58)) |
| [live-session-review-2026-09-09.md](./live-session-review-2026-09-09.md) | 위 두 문서 초안에 대한 적대적 리뷰 원문 (Codex gpt-6-astra) | 반영 완료 |
| [live-session-review-2026-09-09-round2.md](./live-session-review-2026-09-09-round2.md) | 1라운드 반영 결과를 다시 검증한 2라운드 | 반영 완료. 여기서 내 수정 자체의 오류 셋이 나왔다 |
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
