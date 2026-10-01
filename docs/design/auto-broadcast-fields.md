# AUTO_BROADCAST가 채널 레이어로 보내는 필드 줄이기 ([#144](https://github.com/itda-work/django-wireview/issues/144))

> **상태: 결정됨 — 안 A2를 1.1에서 구현한다. 1.0에서는 바꾸지 않는다.** (2026-10-01)

기준 커밋 `bf2faea`(1.0.0rc3). 이 문서를 쓰며 코드는 바꾸지 않았다. 실험은 저장소 사본에서 돌렸다.

## 요약

- **권고: 안 A2.** `senders`가 지금의 집합과 함께 **매핑도 받게** 한다. 모델마다 `"__all__"`, 필드 이름 튜플, `()`(pk만) 가운데 하나를 적는다. 집합으로 적으면 지금처럼 모든 필드를 보낸다. 기본 동작이 그대로이므로 폐기 경고는 필요 없다.
- **함께 고칠 것.** 필드 일부만 담긴 페이로드를 지금의 `serializer.decode`로 풀면 **빠진 필드가 기본값으로 조용히 채워진다.** 그 인스턴스를 저장하면 DB의 값을 덮어쓴다(실험 2). 그래서 필드를 줄이는 안은 모두 `Model.from_db`로 복원해서 보내지 않은 필드를 deferred로 남겨야 한다.
- **시점: 1.x(1.1)로 미룬다.** 추가만 하고 기본 동작은 바꾸지 않는다. 그래서 `COMPATIBILITY.md` 기준으로 마이너 릴리스에 넣을 수 있다. 1.0 전에 정해야 하는 것은 "기본값을 pk 전용으로 뒤집을지"(안 D) 하나뿐이다. 이것은 **하지 않기를 권한다**(§3).

---

## 1. 현재

### 1-1. 무엇이 어디로 가는가

```
post_save / pre_delete / m2m_changed  (senders의 모델만 연결된다. m2m은 through 모델)
  → broadcast_post_save · broadcast_pre_delete · broadcast_m2m_changed   (wireview/auto_broadcast.py)
      encoded = serializer.encode(instance)     ← 시그널이 온 시점, 트랜잭션 안에서
  → notify_mutation(names, action, encoded)     채널 이름의 "_"를 "-"로 바꾼다
  → utils.send_to(channel, "model_mutation", action=..., instance=encoded)   @on_commit: 커밋 뒤에 publish
  → get_broker().publish(channel, message)      채널 레이어(InMemory / Redis / NATS)
  → WireviewSession.model_mutation(data)        (wireview/session.py:1276)
      serializer.decode(data["instance"])
  → 구독한 컴포넌트마다 component.mutation(channel, action=..., instance=<Model>) → send_render
```

**채널 레이어에 실리는 메시지 한 건**

```python
{
    "type": "model_mutation",
    "channel": "bookmarks.bookmark.1",
    "action": "CREATED",            # ModelAction: CREATED/UPDATED/DELETED/ADDED/REMOVED/CLEARED
    "instance": '[{"model": "bookmarks.bookmark", "pk": 1, "fields": {"title": "…", "url": "…", "is_read": true, "created_at": "…"}}]',
}
```

`instance`는 Django JSON 직렬화기(`serialize("json", [instance])`)의 출력이다. **concrete 필드 전부**가 실리고, FK는 id로, **m2m 필드는 pk 목록으로** 실린다.

**같은 페이로드가 가는 채널.** 한 번의 저장이 여러 채널로 복제된다.

| 플래그 | 채널 | 보내는 곳 |
|---|---|---|
| `model` | `<app>.<model>` | post_save·pre_delete |
| `model_pk` | `<app>.<model>.<pk>` | post_save·pre_delete |
| `related` | 가리키는 행마다 `<app>.<model>.<fk_id>.<related_name>` (m2m 플래그가 켜져 있으면 m2m 쪽 행도) | post_save·pre_delete |
| `m2m` | 상대 행마다 `<app>.<model>.<pk>.<field>`, 그리고 바꾼 쪽 행의 `<app>.<model>.<pk>.<field>` | m2m_changed(post_*) |

**실험으로 확인한 사실** (Django 6.0, pydantic 2.13.5)

| # | 확인한 것 | 결과 |
|---|---|---|
| 1 | `senders={("auth","User")}`일 때 `encode(user)` | 페이로드 383바이트에 `password` 해시, `email`, `is_superuser`, `groups`·`user_permissions`의 pk 목록이 실린다. encode 한 번에 쿼리 2개(m2m 필드마다 1개)가 나간다 |
| 2 | 페이로드에서 `fields`를 `{"title"}`로 줄인 뒤 `serializer.decode` | `is_read=False`, `url=''`가 나온다. 기본값이다. `get_deferred_fields()`는 빈 집합이다. **오류 없이 틀린 값이 나온다** |
| 3 | 2의 인스턴스를 `.save()` | `decode`가 `save`를 `DeserializedObject.save`(= `save_base(raw=True)`)로 바꿔 두었다. 그래서 모든 컬럼을 쓴다. `created_at`이 없으면 `IntegrityError`가 나고, 있으면 DB의 `url`·`is_read`를 기본값으로 **덮어쓴다**. (#153 뒤로는 모델의 `save()`를 거치지만, 모든 컬럼을 쓰는 것은 같다) |
| 4 | `Bookmark.from_db("default", ["id","title"], …)` | 나머지 필드가 deferred로 남는다. async 문맥에서 읽으면 `SynchronousOnlyOperation`이 **크게** 난다. `.save()`는 `UPDATE … SET title` 하나만 쓴다 |
| 5 | deferred 필드가 있는 인스턴스(`only()` 등)를 저장 | post_save 수신자의 `encode`가 deferred 필드마다 `SELECT`를 한 번씩 부른다(4에서 필드 3개에 쿼리 3개). 지금도 있는 비용이다 |
| 6 | pydantic 2.13 `set[tuple[str,str]] \| dict[tuple[str,str], Literal["__all__"] \| tuple[str, ...]]` | 집합과 매핑을 둘 다 받는다. 값에 맨 문자열 `"username"`을 적으면 거절된다. 흔한 `("username")` 실수가 여기서 잡힌다. **하한 pydantic 2.7에서는 확인하지 못했다**(`make test-lowest` 몫) |

**페이로드가 닿는 곳.** 보안 판단의 근거다.

- 브로커. Redis는 `MONITOR`와 슬로우로그, 같은 NATS 계정의 와일드카드 구독자(`>`), 브로커 모니터링과 백업에 닿는다.
- 채널을 구독한 모든 컴포넌트의 `mutation()` 인자. 개발자가 렌더하거나 로그에 찍거나 상태에 담으면 그 필드는 다음 단계로 나간다. 상태에 담긴 모델은 pk로 서명되므로 `data-state`로 새지는 않는다(`core/model_state.py`).
- 브라우저에는 **직접 가지 않는다.** 렌더된 것만 간다.
- telemetry `broadcast_published`는 크기만 잰다(`payload_size`). 내용은 싣지 않는다. `notify_mutation`의 debug 로그도 action과 채널 이름만 남긴다.

### 1-2. 수신자가 실제로 읽는 것 (전수)

`serializer.decode`의 결과는 **DB에서 다시 읽은 것이 아니라 페이로드에서 복원한 것**이다. 관계 접근(`instance.room`)은 동기 쿼리라 async `mutation()`에서 실패한다. 그래서 수신자가 쓸 수 있는 것은 페이로드의 필드와 `<fk>_id`뿐이다.

| 위치 | 읽는 것 | 분류 |
|---|---|---|
| `examples/todo/live.py:58` XTodoList | `action`만 | 가 |
| `examples/todo/live.py:156` XTodoItem | `action`. `self.item = instance` 뒤 템플릿이 `id`·`completed`·`text`를 읽는다 | 라 |
| `examples/chat/live.py:126` | `action`, `room_id`, `pk`. `stream_insert`로 템플릿이 `username`·`content`·`created_at`을 읽는다 | 나+라 |
| `examples/dashboard/live.py:105` XStatCard | `name`으로 거른다. `AsyncResult.success(instance)`로 템플릿이 `label`·`value`·`change_percent`·`name`을 읽는다 | 다+라 |
| `examples/dashboard/live.py:150` ActivityFeed | `action`. `stream_insert`로 `pk`·`type`·`user_name`·`description`·`created_at` | 라 |
| `examples/notifications/live.py:71` Bell | 없음(`force_render`) | 가 |
| `examples/notifications/live.py:116` List | CREATED는 `stream_insert`로 `id`·`is_read`·`type`·`title`·`message`·`created_at`. DELETED는 `id` | 라 / 가 |
| `examples/poll/live.py:47` | `poll_id` | 나 |
| `examples/quiz/live.py:103, 228` | `quiz_id` | 나 |
| `examples/rating/live.py:65, 144` | `product_id` | 나 |
| `tests/testproj/bookmarks/live.py:48` (스킬 기준선) | `pk`, `is_read`(`_visible`). `stream_insert`로 `url`·`title`·`is_read` | 다+라 |
| README 353행 | 없음(다시 불러온다) | 가 |
| README 471행 | `id`. `self.items.append(instance)` | 가+라 |
| 튜토리얼 03 | `id`, `_should_show(instance)`(`completed`), `text`, `completed` | 다+라 |
| 튜토리얼 04 | `sender`, `pk`, `stream_insert` | 다+라 |
| 튜토리얼 05 | `name`, `AsyncResult.success(instance)`, `stream_insert` | 다+라 |
| 튜토리얼 06·14 | `pk`·`id`, `stream_insert` | 라 |
| 튜토리얼 10·11·13 | `poll_id`·`product_id`·`quiz_id` | 나 |
| 튜토리얼 09 | 테스트에서 `mutation()`을 직접 부른다(진짜 인스턴스). 페이로드 경로가 아니다 | — |
| `tests/test_auto_broadcast.py` | 채널과 action만 모은다(`instance`는 버린다) | — |

분류: **가** action·pk만 / **나** FK id로 거른다 / **다** 키가 아닌 필드로 거른다 / **라** 인스턴스를 그대로 렌더하거나 상태에 넣는다.

**정리하면**

- 민감한 필드(비밀번호, 토큰 따위)를 읽는 수신자는 **하나도 없다.**
- DELETED에서 읽는 것은 모두 `pk`/`id`다. 단, poll·quiz·rating은 action을 가리지 않고 FK로 거르므로 DELETED에도 `*_id`가 필요하다.
- 예제 수신자 13개 가운데 6개(todo 항목, chat, dashboard 둘, notifications 목록, bookmarks — 다·라)가 pk와 FK 말고 다른 필드를 쓴다. 튜토리얼은 03·04·05·06·14가 그렇다. **pk 전용을 기본으로 하면 이들 모두가 다시 조회하도록 바뀌어야 한다.** 나머지 7개(가·나)는 pk와 FK id만으로 충분하다.
- 페이로드 경로를 끝까지 지나는 테스트는 E2E(예제)뿐이다. 단위 테스트에는 `model_mutation` → `decode` → `mutation()`을 잇는 테스트가 없다.

---

## 2. 선택지

공통 전제: 필드를 줄이는 안(A·B·C·D)은 모두 수신 쪽 복원을 `Model.from_db(db, 보낸_attname들, 값들)`로 바꿔야 한다(실험 2·3·4). 그러면 보내지 않은 필드는 deferred가 된다. async에서 읽으면 `SynchronousOnlyOperation`이 나고, 필요하면 `await instance.arefresh_from_db()`(Django 5.0+, 하한 5.2에 있다)로 채운다. 저장은 불러온 필드만 쓴다. 이 복원 경로 없이 필드만 줄이면 **보안을 얻는 대신 조용한 데이터 오염을 들인다.**

### A. 설정의 모델별 필드 목록

**A1. 별도 키**

```python
"AUTO_BROADCAST": AutoBroadcast(
    model=True, model_pk=True,
    senders={("todo", "Item"), ("accounts", "User")},
    fields={("accounts", "User"): ("username", "is_active")},   # 적지 않은 모델은 모든 필드
)
```

**A2. `senders`가 매핑도 받는다** (권고)

```python
"AUTO_BROADCAST": AutoBroadcast(
    model=True, model_pk=True,
    senders={
        ("todo", "Item"): "__all__",                  # 지금과 같다
        ("accounts", "User"): ("username", "is_active"),
        ("rating", "Rating"): ("product",),           # FK는 이름으로 적는다 → product의 id 값이 간다
        ("audit", "Event"): (),                       # pk만. 받는 쪽이 필요하면 다시 읽는다
    },
)
# 집합으로 적으면 지금처럼 모두 "__all__"
senders={("todo", "Item")}
```

### B. pk(와 label)만 보내는 전역 모드

```python
"AUTO_BROADCAST": AutoBroadcast(model=True, senders={("todo", "Item")}, payload="keys")  # "full"이 기본

async def mutation(self, channel, action, instance):
    if action != ModelAction.DELETED:
        await instance.arefresh_from_db()        # 받는 쪽이 다시 읽는다
```

### C. 모델에 선언

```python
class ApiClient(models.Model):
    name = models.CharField(...)
    token = models.CharField(...)

    wireview_broadcast_fields = ("name",)        # 또는 class WireviewBroadcast: fields = (...)
```

### D. 기본을 pk 전용으로 바꾸고 전체 필드는 옵트인

```python
senders={("todo", "Item")}                        # 이제 pk만 간다
senders={("todo", "Item"): "__all__"}             # 지금 동작을 원하면 명시
```

### 비교표

| | A1 별도 키 | **A2 매핑 senders** | B 전역 pk 모드 | C 모델 선언 | D 기본 뒤집기 |
|---|---|---|---|---|---|
| 기본 동작 변화 | 없음 | 없음 | 없음 | 없음 | **있음**(모든 기존 수신자) |
| 공개 API 변화 | 설정 키 하나 추가 | `senders` 타입 확장(집합 ∪ 매핑) | 설정 키 하나 추가 | 모델 속성 이름이 새 통합 지점이 된다 | `senders` 집합의 **뜻**이 바뀐다 |
| 1.0 약속에 주는 부담 | 키 하나를 영구 유지. `senders`와 어긋나는 상태가 가능하다(검증 필요) | 합집합 타입을 영구 유지. 알릴 모델과 필드가 한 곳에 있어 어긋날 수 없다 | 전역 스위치라 "모델마다 다르게"를 나중에 또 더해야 한다 | 모델 계층에 wireview 이름이 박힌다. 서드파티 모델(`auth.User`)에는 쓸 수 없다 | 1.0 뒤라면 2.0에서만 가능하다 |
| 기존 사용자 영향·이행 | 없음. 폐기 경고 불필요 | 없음. 폐기 경고 불필요 | 없음. 폐기 경고 불필요 | 없음. 폐기 경고 불필요 | 예제 수신자 6/13과 튜토리얼 5편이 다시 읽도록 고쳐야 한다. 1.x에서 하려면 `WireviewDeprecationWarning` 한 주기(집합 형태에 경고) 뒤 2.0 |
| 보안 효과 | 적은 모델만 보호(옵트인) | 같다. `()`로 B의 효과를 모델 단위로 얻는다 | 켜면 전부 보호. 켜지 않으면 없음 | 자기 모델만. 가장 걱정되는 `User`류는 커스텀 유저 모델일 때만 | 기본 안전. 명시한 모델만 전체 필드 |
| 성능 | 페이로드가 줄고, 빠진 m2m 필드의 encode 쿼리도 사라진다. 수신 쪽 변화 없음 | 같다. `()`를 고른 모델만 수신 쪽 재조회 | 수신 쪽 재조회: 알림 1건 × 구독 컴포넌트 수(연결 × 컴포넌트)만큼 쿼리. 지금은 0 | A와 같다 | B와 같은 재조회가 기본값이 된다 |
| 구현 크기 | 코드 ~80줄, 테스트 ~150줄, 문서 6~8곳 | A1과 비슷(검증이 더 단순) | 코드 ~60줄, 문서에 재조회 패턴 | 코드 ~50줄. 문서가 "설정이냐 모델이냐" 두 길을 설명해야 한다 | A2 + 예제 6곳·튜토리얼 5편·README·스킬 개정, E2E 재검증 |
| 테스트 전략 | §4-3 공통 + 키 검증(senders에 없는 모델) | §4-3 | §4-3 + 재조회 수 측정 | §4-3 + 속성 탐색 | §4-3 + 모든 예제 E2E + 폐기 경고 |

**배포 주의 (A·B·C·D 공통).** 새 버전 프로세스가 줄인 페이로드를 보내는 동안 옛 버전 프로세스가 그것을 받으면 옛 `decode`가 빠진 필드를 기본값으로 채운다(실험 2). 롤링 배포라면 **새 버전을 모두 올린 뒤에** 필드를 줄이는 설정을 켠다. `channels-nats` 0.3 절처럼 COMPATIBILITY·DEPLOYMENT에 한 줄 적는다. 메시지 `type`을 바꾸는 방법은 쓰지 않는다. 옛 프로세스에는 그 핸들러가 없어서 더 크게 깨진다.

---

## 3. 권고: A2, 1.1에서

### 왜 A2인가

1. **기본이 그대로라 1.x 마이너에 들어간다.** 집합 형태는 지금 문서가 말하는 "적은 모델은 모든 필드가 간다"를 그대로 지킨다. 새 뜻은 새 형태(매핑)에만 붙는다. 폐기 절차가 필요 없다.
2. **B를 모델 단위로 포함한다.** 값 `()`가 pk 전용 모드다. 전역 스위치(B)는 "User만 줄이고 싶다"를 못 한다. 수신자 전수(§1-2)를 보면 모델마다 필요한 필드가 다르다.
3. **알릴 모델과 알릴 필드가 한 곳에 있다.** A1은 `fields`에만 있고 `senders`에 없는 모델을 검증해야 하고, 두 키가 어긋날 수 있다. A2는 그런 상태를 만들 수 없다. `resolve_senders`는 `sorted(config.senders)`로 도는데, 매핑이어도 키로 돈다. W015의 `not config.senders`도 그대로 맞다. 손댈 곳이 적다.
4. **C를 택하지 않는 이유.** 가장 흔한 위험 모델인 `auth.User`와 서드파티 모델에 쓸 수 없다. 모델 계층이 wireview를 알게 된다.
5. **D를 택하지 않는 이유.** 예제 수신자 13개 가운데 6개와 튜토리얼 5편이 키가 아닌 필드를 쓴다. 기본을 뒤집으면 모든 사용자가 재조회 fan-out(알림 1건에 구독 컴포넌트 수만큼 쿼리)을 떠안는다. 반면 막으려는 위험은 "개발자가 민감한 모델을 명시적으로 적은 경우"다. 이것은 이미 문서 5곳이 막고 있고 아래 W017이 기계적으로 짚는다. 이 위험은 브로커 안쪽(보통 같은 VPC)에 머문다.

### 1.0 전인가, 1.x인가 → 1.x (1.1)

- 막으려던 핵심(빈 `senders` = 전 모델 방송)은 rc에서 이미 막혔다. 남은 위험은 명시적 행동이 있어야 생기고, 문서화되어 있다(rc 감수의 판단과 같다).
- 새 복원 경로(`from_db`)는 모든 예제가 기대는 `mutation()`의 `instance`를 만든다. rc4 직전에 넣으면 E2E로 충분히 불려 볼 시간이 없다. 지금 rc4에는 M-1 같은 필수 항목이 남아 있다.
- **시점이 걸린 결정은 D 하나뿐이다.** "기본 안전"을 원한다면 1.0 전에 뒤집거나 2.0까지 기다려야 한다. D를 택하지 않으면 1.0에서 할 일은 없다.
- 1.0 전에 **해도 좋은 것**(선택): `docs/features/component-api.md`의 `mutation` 행에 "`instance`는 알림 페이로드에서 복원한 것이다. DB에서 다시 읽지 않고, 관계는 id만 있다"는 한 문장을 넣는다. 1.1에서 deferred 필드가 생겨도 이 문장이 거짓이 되지 않는다.
- 2.0 후보(지금 정하지 않음): 집합 형태를 폐기하고 모델마다 정책을 명시하게 한다. W017의 오탐 기록을 보고 판단한다.

### A2의 세부 규칙

| 항목 | 규칙 |
|---|---|
| 값 | `"__all__"` \| `tuple[str, ...]`. 맨 문자열은 거절한다(실험 6). 집합 형태는 모두 `"__all__"` |
| pk | 항상 실린다(Django 직렬화기의 `"pk"`). 목록에 적지 않는다 |
| FK | 필드 이름(`"product"`)으로 적고 `product_id`가 간다. attname(`"product_id"`)도 받는다 |
| m2m | 목록에 적으면 pk 목록이 간다(적을 때만 encode 쿼리). 복원 인스턴스에는 지금처럼 싣지 않는다 |
| 검증 | `connect()`가 `resolve_senders`와 함께 확인한다. 없는 필드나 역관계 이름은 기동 때 `ImproperlyConfigured`다. `senders`의 설치되지 않은 모델과 같은 처리다 |
| 인코딩 | `serialize("json", [instance], fields=<목록>)`. Django가 `fields=`를 그대로 지원한다(실험 확인). 세 발신 지점(post_save·pre_delete·m2m)이 한 헬퍼를 쓴다 |
| 복원 | 페이로드에 온 필드만으로 `from_db`. 나머지는 deferred다. 전체 필드 페이로드는 지금 방식 그대로 복원한다. **`save` 교체는 1.0 전에 없앴다(#153)**. 페이로드에 없는 필드를 deferred로 두는 처리는 #153이 `wireview/serializer.py`의 `_restore(instance, sent)`로 이미 넣었다 — 다중 테이블 상속 자식의 전체 페이로드가 사실상 부분 페이로드이기 때문이다. 부분 페이로드는 그 헬퍼에 보낸 필드 이름만 넘기면 된다 — 전체·부분 모두 모델의 `save`를 쓴다. 부분 페이로드는 그래서 불러온 필드만 쓴다(실험 4) |
| DELETED | 페이로드가 pre_delete 때 만들어지므로 적은 필드가 그대로 온다. `()`면 pk만 온다. 행이 이미 없으니 `arefresh_from_db`는 `DoesNotExist`다. 문서에 "DELETED에서 걸러야 하는 필드(FK 등)는 목록에 적는다"고 쓴다 |

### 새 체크 W017 (권고: 추가)

`checks.md`의 원칙("오탐 하나면 팀 전체가 검사를 무시한다")을 따라 **이름 휴리스틱(`token`, `secret`)은 쓰지 않는다.** 확실한 경우만 짚는다.

- 조건: `senders`의 모델이 `"__all__"`(집합 형태 포함)로 알려지는데, 그 모델이 `AbstractBaseUser`의 하위 클래스이거나(`password` 해시가 실린다) `AbstractBaseSession`의 하위 클래스다.
- 메시지 예: `WIREVIEW['AUTO_BROADCAST'] broadcasts every field of accounts.user, including its password hash.`
- hint: senders를 매핑으로 바꿔 보낼 필드를 적으라고 안내한다. 예: `senders={('accounts', 'User'): ('username',)}`.
- 1.1 전에는 줄이는 수단이 없어 hint가 "빼라"뿐이다. 그래서 W017도 A2와 함께 1.1에 넣는다.

---

## 4. A2를 고르면 할 일

### 4-1. 코드

| 파일 | 할 일 |
|---|---|
| `wireview/schemas.py` | `senders: set[tuple[str,str]] \| dict[tuple[str,str], Literal["__all__"] \| tuple[str, ...]]`. 정책을 꺼내는 메서드 하나(`fields_for(label)` 정도, 내부) |
| `wireview/auto_broadcast.py` | `connect()`에서 모델마다 정책을 풀고 필드 이름을 검증해 `_fields: dict[type[Model], tuple[str,...] \| None]`에 둔다. `serializer.encode(instance)` 세 곳을 `_encode(sender, instance)`로 바꾼다. m2m 수신자는 `type(instance)`의 정책을 쓴다. 모듈 docstring 갱신 |
| `wireview/serializer.py` | `encode(instance, fields=None)`. `decode`는 페이로드의 `fields` 키를 보고 전체면 지금 경로, 부분이면 `from_db` 경로. 모두 내부 모듈이다(`__all__` 공개 아님) |
| `wireview/checks.py` | `check_auto_broadcast_credentials` (W017) 등록. W015 hint 문구를 "leave out … or list the fields"로 바꾼다 |
| `wireview/session.py` | 바꿀 것 없음(`model_mutation`은 `decode`만 부른다). 주석의 결합 지점은 그대로다 |

### 4-2. 문서

| 파일 | 할 일 |
|---|---|
| `docs/features/settings.md` §모델 알림 | 매핑 형태, 값 세 종류, FK 표기, DELETED 주의, `arefresh_from_db` 패턴, 롤링 배포 주의. "민감한 모델은 넣지 않는다"를 "넣으려면 필드를 적는다"로 |
| `docs/features/component-api.md` | `mutation` 행: `instance`는 페이로드 복원본이고, 적지 않은 필드는 deferred다(async에서 읽으면 `SynchronousOnlyOperation` → `await instance.arefresh_from_db()`) |
| `docs/features/checks.md` | W017 행 추가, W015 설명 갱신 |
| `docs/COMPATIBILITY.md` | "모델 채널 이름" 행에 "`senders`는 집합 또는 모델→필드 매핑. 집합은 모든 필드". 채널 레이어 절에 "필드를 줄이는 설정은 모든 프로세스를 올린 뒤에 켠다" |
| `docs/DEPLOYMENT.md` | 롤링 배포 절에 위 순서 한 줄 |
| `docs/implementation/wire-protocol.md:82` | `model_mutation`의 `instance`가 필드 일부일 수 있고, 받는 쪽은 온 필드만 불러온다 |
| `README.md` 466행 부근 | 경고 문단 옆에 매핑 예 하나 |
| `docs/UPGRADING.md` | 1.1 절(새로): 필요 없음. 추가 기능이고 기본이 같다. 롤링 배포 주의만 DEPLOYMENT에 둔다. **UPGRADING §9의 문장은 1.0 기준이라 두고**, settings 링크만 유효한지 확인한다 |
| `skills/wireview/` (SKILL.md, references/component.md) | 설정 예와 "민감한 모델은 필드를 적는다" 한 줄. `wireview_agent_setup`로 배포되므로 휠 내용이 바뀐다 |
| `docs/FEATURE-GAP.md` | 해당 GAP이 없으면 두지 않는다(#144로 추적) |
| `CHANGELOG.md` `[Unreleased]` | `### Added`: senders 매핑과 필드 목록, W017. `### Changed`: 없음. 부분 페이로드의 deferred 복원은 새 형태에만 적용된다고 적는다. |

### 4-3. 테스트 (`tests/test_auto_broadcast.py`에 더한다. 새 파일이 필요하면 `test_auto_broadcast_fields.py`)

1. **발신 페이로드**: 매핑 `{("auth","User"): ("username",)}`로 `create_user` → publish된 메시지 문자열에 `password`·`email`이 없다. 세 경로(post_save, pre_delete, m2m, related 채널 포함)를 모두 본다. 지금의 `send_to` 패치 방식을 쓴다.
2. **집합 형태 불변**: 집합이면 페이로드가 지금과 바이트 단위로 같다. 회귀를 막는 기준이다.
3. **`()`**: 페이로드 `fields`가 비고 pk만 있다. DELETED도 pk가 온다.
4. **FK 표기**: `("product",)`와 `("product_id",)` 모두 `product_id`를 싣는다.
5. **검증**: 없는 필드, 역관계 이름, 맨 문자열 값 → 기동(`connect`) 때 `ImproperlyConfigured` 또는 `ValidationError`.
6. **복원(실험 2·3·4의 회귀)**: 부분 페이로드를 `decode` → 적지 않은 필드는 `get_deferred_fields()`에 있다. async에서 읽으면 `SynchronousOnlyOperation`이다. `await arefresh_from_db()` 뒤에는 DB 값이다. **`save()`가 적지 않은 컬럼을 덮어쓰지 않는다.**
7. **끝까지 잇는 단위 테스트(지금 없음)**: `WireviewSession.model_mutation`에 실제 페이로드를 넣고 `mount()`한 컴포넌트의 `mutation()`이 받은 인스턴스를 확인한다.
8. **encode 쿼리 수**: m2m 필드를 목록에서 빼면 encode가 m2m 쿼리를 내지 않는다(`capture_queries`).
9. **W017**: `auth.User`를 집합으로 → 경고, 매핑으로 필드를 적음 → 없음, `AbstractBaseUser` 하위 커스텀 모델 → 경고, 일반 모델 → 없음.
10. **E2E**: 스킬 기준선인 `tests/testproj/bookmarks`를 매핑 형태(`("title","url","is_read","created_at")`)로 바꾸고 기존 E2E가 그대로 통과하는지 본다. 예제 하나(rating이나 poll, `("product",)` + 필요 시 재조회)를 `()`/부분 목록으로 바꿔 부분 경로가 브라우저까지 가는지 본다. 나머지 예제는 집합으로 둔다. 두 형태가 함께 돈다.
11. `make test-lowest`에서 pydantic 2.7이 합집합 타입을 같게 푸는지 확인한다(실험 6은 2.13에서만 했다).

### 4-4. 하지 않는 것

- 메시지 `type`·채널 이름·`mutation()` 시그니처는 바꾸지 않는다.
- 전체 필드 페이로드의 `save` 교체(`save_base(raw=True)`)는 이 설계를 쓴 뒤 1.0 전에 없앴다(#153). 복원 인스턴스는 전체·부분 모두 모델의 `save()`를 쓰고 이미 있는 행(`_state.adding = False`)이다. 1.1에서 비대칭은 없다.
- 실험 5(deferred 인스턴스를 encode하면 필드마다 SELECT)는 이 이슈의 범위 밖이다. 목록에 적은 필드만 읽게 되면 부분적으로 줄어든다.
