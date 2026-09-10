# live_session 2회차 재판정 (Codex gpt-6-astra, 2026-09-10)

> 6라운드. **릴리스 가능** 판정이다. 남은 것들 — signed-cookie의 옛 쿠키 재사용, 브로커 메시지 유실,
> 이미 열린 연결의 권한 변경 미반영 — 은 이 구현이 만든 결함이 아니라 세션 저장소·브로커·연결 수명에서
> 오는 일반적 한계이고, 문서에 그 범위가 적혀 있다.
>
> 이 판정 이후에도 하나가 더 나왔다. 세션의 pk 비교를 Django의 방식(`_meta.pk.to_python`)으로 옮기면서
> `type(user)`를 썼는데, 실제 연결의 user는 `SimpleLazyObject`라 `_meta`가 없다 — **E2E가 잡았고 단위
> 테스트는 못 잡았다.** `get_user_model()`로 고치고 lazy user 회귀 테스트를 붙였다.


## F. 판정

**릴리스할 수 있다.** 직전 재판정의 차단이었던 nonce 없는 세션의 지연 join은 실제로 거절되며, 무경계 토큰 전환 문서도 토큰 갱신·연결 종료·키 fallback의 상호작용까지 명시했다. 남은 signed-cookie의 옛 쿠키 재사용, 브로커 메시지 유실, 이미 열린 연결의 권한 변경 미반영은 이 기능 구현이 새로 만든 결함이 아니라 저장소·브로커·연결 수명에 따른 일반적 한계다. 문서와 운영 절차로 경계를 분명히 한 현재 상태에서 추가 릴리스 차단으로 만들 근거는 없었다.

## A. 재현기

`.wireview/test_rejudge_probes.py`를 현재 커밋에서 실행한 결과는 **1 failed, 12 passed**였다. 실패는 `test_delayed_join_after_logout[False]`의 기존 결함 단언이다. 이 테스트는 삭제된 nonce 없는 세션이어도 join되어야 한다고 기대하지만, 현재 consumer는 `live_session ... login ... has ended`로 reload를 보내고 저장소에 컴포넌트를 만들지 않는다. 따라서 실패는 결함이 사라졌기 때문에 발생했다.

나머지 12개는 다음을 확인한다.

- nonce가 있는 삭제 세션은 거절되고, 살아 있는 nonce 세션은 허용된다.
- 재로그인은 이전 nonce 없는 연결의 실제 토픽으로 발행된다. 같은 사용자의 다른 nonce 없는 로그인까지 닫히는 것은 전환 기간의 과잉 종료다.
- 비활성 사용자를 Channels가 `AnonymousUser`로 복원하면 경계 join은 거절된다.
- 명시적 `SIGNING_KEY_FALLBACKS=[]`는 옛 키를 받지 않고, `None`은 Django의 `SECRET_KEY_FALLBACKS`를 상속한다.
- 토큰 갱신으로 무경계 노출이 `STATE_MAX_AGE`를 넘어 지속될 수 있다는 전환 반례와, 키 교체만으로 이미 join한 공개 소켓을 닫지 못한다는 반례가 유지된다.

직전의 `.wireview/test_final_probes.py`도 다시 확인했다. 7개 중 5개는 수정된 결함 단언에서 실패했고, 2개는 무경계 v2 토큰 및 request processor 누락의 노출을 여전히 재현했다. 하네스의 loopback 바인딩 권한을 확보한 실행을 기준으로 해석했다.

## B. 인증 재검증 범위

`_session_still_names_this_user()`는 인증된 연결에 대해서만 새 세션의 Django `SESSION_KEY` 값이 연결 사용자의 pk와 같은지 확인한다. 삭제·flush된 서버 저장 세션은 빈 mapping이므로 거절된다. `_auth_user_id`가 다른 사용자를 가리키는 경우도 거절된다. 익명 연결은 사용자 귀속을 요구하지 않으며, 토큰의 auth fingerprint와 페이지 `authorize`가 별도로 결정한다. 세션 키가 없는 연결은 backend에 재조회할 키가 없으므로 기존 스냅샷을 사용한다. 정상 Django 로그인은 키를 만들기 때문에 이 경로는 저장되지 않은 테스트용 세션·명시적 호출 경로에 해당한다.

signed-cookie는 서버에 폐기 기록이 없으므로 옛 서명 쿠키를 다시 제출하면 세션이 여전히 사용자를 지명할 수 있다. 새 검사는 그 백엔드의 stateless 한계를 제거하지 않는다. 이 범위를 넘어서 익명·signed-cookie를 모두 차단하려면 서버 측 폐기 세대 저장소가 추가로 필요하다.

경계 밖에서는 `_reload_session()`과 사용자 귀속 검사를 호출하지 않는다. 무경계 페이지는 인증 세대에 묶이지 않고 공개 컴포넌트를 허용하는 기존 계약이므로 이 범위가 맞다. 보호 컴포넌트는 `_live_sessions` 선언으로 무경계 페이지에서도 거절되어야 한다.

## C. 무경계 토큰 전환

문서로 처리한 선택은 현재 충분하다. 토큰은 발급 시각을 갖지만, 페이지가 보호 대상으로 바뀐 시점은 봉투에 없으므로 서버가 과거의 공개 토큰에 새 `authorize`를 소급 적용할 수 없다. 보호할 클래스에 `_live_sessions`를 선언하고 request context processor를 보장하며, 기존 연결과 옛 워커를 종료하는 절차를 제시한 것은 최소한의 현실적 계약이다.

키 교체는 새 join의 옛 토큰을 거절하는 데 동작한다. `SIGNING_KEY_FALLBACKS=[]`를 명시해야 하며, `None`은 Django fallback 상속이므로 폐기용 교체에서 사용하면 안 된다. 키 교체만으로 이미 join한 소켓을 닫지는 않으므로 워커·연결 종료가 반드시 함께 있어야 한다. 이 조건이 문서에 반영되어 전환 절차가 실제 동작한다.

## D. 새 문제 여부

직전처럼 인증 지문 수정이 새 회귀를 만들었는지 확인했다. 새 검사는 nonce 없는 세션의 삭제 후 지연 join을 닫았고, 익명 사용자·다른 사용자 pk·signed-cookie·키 없는 세션의 의미도 계약 범위 안에 있다. 재로그인 시 nonce 없는 토픽을 넓게 무효화하는 동작은 문서에 적힌 전환 기간의 가용성 비용이지 인증 우회가 아니다.

하네스는 `_stop()`에서 종료 timeout을 원래 예외를 덮지 않고 보고하며, 정상 종료에서는 verdict를 검사한다. 스레드를 파이썬에서 강제 종료할 수 없다는 일반적 한계는 남지만, 테스트 하네스가 그것을 조용히 성공으로 처리하지 않는다.

## E. 새 변이

기존 27개는 반복하지 않고 아래 셋만 실제 적용했다. 각 변이 뒤 파일을 개별 패치로 원복했다.

| 변이 | 결과 | 판정 |
|---|---:|---|
| `reread_skips_user_check` — 새 세션의 사용자 귀속 검사 생략 | 1 failed, 418 passed, 11 skipped | `test_delayed_join_after_logout[False]`가 검출 |
| `explicit_empty_fallback_inherits` — `[]`를 `None`처럼 처리 | 1 failed, 10 passed | `test_an_explicit_empty_fallback_list_means_no_fallbacks`가 검출 |
| `harness_no_shutdown_verdict` — 종료 후 생존 검사 제거 | 1 failed, 10 passed, 2 warnings | `test_a_shutdown_that_does_not_finish_is_reported`가 검출 |

정상 기준선은 계약·동작·checks·하네스·서명 테스트 **473 passed, 11 skipped**였다. 추가 재현기는 현재 구현에서 **12 passed, 1 fixed-defect assertion failed**였다. 변이와 재현 테스트는 모두 `.wireview/`에만 기록했고, 변이 파일은 원본 바이트로 복구했다.

최종 `git status --short`는 비어 있다.
