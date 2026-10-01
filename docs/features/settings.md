# 설정

`settings.WIREVIEW`의 키 전부다. 여기 적힌 키가 공개이고([호환성 정책](../COMPATIBILITY.md)), 기본값의 정본은
`wireview/settings.py`의 `DEFAULT`다. `tests/test_settings_reference.py`가 이 표와 `DEFAULT`를 대조한다.

설정은 쓰는 시점에 읽는다. 테스트에서는 `override_settings(WIREVIEW={...})`로 바꾼다. 모르는 키나 없어진 키가
있으면 `manage.py check`가 `wireview.W014`로 알린다. 기동할 때만 의미가 있는 키는 **기동 시**로 표시했다 —
실행 중에 바꿔도 아무 일도 일어나지 않는다.

```python
from wireview import AutoBroadcast

WIREVIEW = {
    "BOOST_PAGES": True,
    "AUTO_BROADCAST": AutoBroadcast(model=True, model_pk=True, senders={("todo", "Item")}),
}
```

## 페이지와 연결

| 키 | 기본값 | 뜻 |
|----|--------|----|
| `BOOST_PAGES` | `False` | 링크와 `wire-boost` 폼을 전체 로드 없이 이동한다 ([boost](./boost.md)) |
| `CHECK_ORIGIN` | `True` | `Origin`이 `ALLOWED_HOSTS`에 없는 소켓을 거절한다 ([배포](../DEPLOYMENT.md#websocket의-origin)) |
| `COLLECT_HOOKS` | `True` | 각 앱의 `static/<app_label>/hooks/*.js`를 `{% wireview_header %}`가 싣는다 ([hooks](./hooks.md)). **기동 시** |
| `RECONNECT_MIN_DELAY_MS` | `1000` | 연결이 끊긴 뒤 첫 재시도까지의 최소 대기(밀리초) ([배포](../DEPLOYMENT.md#롤링-배포와-재연결)) |
| `RECONNECT_JITTER_MS` | `4000` | 첫 대기에 더하는 무작위 폭(밀리초). 페이지마다 한 번 뽑아 같은 순간 끊긴 페이지들을 흩는다 |
| `RECONNECT_MAX_DELAY_MS` | `10000` | 재시도 대기의 상한(밀리초) |
| `RECONNECT_GROW_FACTOR` | `1.3` | 재시도마다 대기에 곱하는 수. 1보다 작으면 클라이언트가 기본값을 쓴다. 쓸 수 없는 값과, 대기가 0이거나 첫 대기(`MIN_DELAY` + `JITTER`)가 `MAX_DELAY`를 넘는 조합은 `wireview.W016`이 알린다 |

## 서명

| 키 | 기본값 | 뜻 |
|----|--------|----|
| `SIGNING_KEY` | `None` | 서명 키. `None`이면 Django의 `SECRET_KEY`. 업로드 토큰과 `data-state`가 쓴다 |
| `SIGNING_KEY_FALLBACKS` | `None` | 키 로테이션용 옛 키 목록. `None`이면 `SECRET_KEY_FALLBACKS`. 자체 키를 두면 이것도 둔다 |
| `STATE_MAX_AGE` | `1209600` (14일) | 서명 상태(`data-state`)의 유효 기간(초) ([html-diff](./html-diff.md)) |
| `STATE_REFRESH_AFTER` | `None` | 상태가 같아도 이 시간(초)이 지나면 다시 서명한다. `None`이면 `STATE_MAX_AGE // 2`. `STATE_MAX_AGE`보다 작아야 한다 |

## 업로드

[chunked-uploads](./chunked-uploads.md)

| 키 | 기본값 | 뜻 |
|----|--------|----|
| `UPLOAD_TEMP_DIR` | `None` | 청크 저장소. `None`(또는 `""`)이면 시스템 temp. 워커들이 공유해야 한다. 첫 청크가 올 때 읽힌다(`wireview.W008`) |
| `UPLOAD_MAX_FILE_SIZE` | `10485760` (10MB) | `allow_upload()`에 `max_file_size`가 없을 때의 상한(바이트) |
| `UPLOAD_CHUNK_SIZE` | `65536` (64KB) | `allow_upload()`에 `chunk_size`가 없을 때의 청크 크기(바이트) |
| `UPLOAD_TOKEN_MAX_AGE` | `3600` | 업로드 토큰의 유효 기간(초). 버려진 청크 파일을 지우는 나이이기도 하다(`wireview_upload_gc`) |

## 모델 알림

| 키 | 기본값 | 뜻 |
|----|--------|----|
| `AUTO_BROADCAST` | `AutoBroadcast()` (모두 꺼짐) | 모델 저장·삭제를 채널로 알린다. `model`, `model_pk`, `related`, `m2m`, `senders`(알릴 모델의 `(app_label, ModelName)` 집합). **`senders`를 비우면 아무것도 알리지 않는다** — 플래그를 켰는데 비어 있으면 `wireview.W015`가 알린다. 설치되지 않은 모델을 적으면 기동 때 `ImproperlyConfigured`다. 채널 이름은 [호환성 정책](../COMPATIBILITY.md)의 "모델 채널 이름". **기동 시** |

`senders`에 적은 모델은 저장·삭제될 때마다 **그 모델 테이블의 모든 필드가** 직렬화되어 채널 레이어로 가고, 구독한
컴포넌트의 `mutation()`이 그것을 받는다. 민감한 필드가 있는 모델(`User`, `Session` 등)은 넣지 않는다. 그런 모델의
변경을 알려야 하면 필요한 필드만 담은 별도 모델을 만들어 그것을 적는다.

`mutation()`이 받은 `instance`는 **보통의 모델 인스턴스처럼 저장된다**(#153). `save()`·`asave()`는 모델의 `save()`
오버라이드를 거치고, `pre_save`·`post_save`는 `raw=False`로 나간다. 이미 있는 행으로 복원되므로 UPDATE를 먼저 한다.
알아 둘 것:

- **페이로드에 실린 필드를 모두 쓴다.** 페이로드는 시그널이 났을 때의 행이다. 그 뒤에 다른 곳에서 바뀐 컬럼도 페이로드의
  옛 값으로 덮인다. 바꾼 필드만 쓰려면 `await instance.asave(update_fields=["name"])`, 지금 값이 필요하면
  `await instance.arefresh_from_db()` 뒤에 고친다.
- **실리지 않은 필드는 deferred다.** Django 직렬화기는 그 모델 자신의 테이블만 쓴다 — 다중 테이블 상속에서 **부모 모델의
  필드**와 `serialize=False` 필드는 실리지 않는다. 그런 필드는 `QuerySet.only()`로 읽은 인스턴스처럼 남는다. 읽으면 DB를
  조회하므로 `mutation()`(이벤트 루프)에서 그냥 읽으면 `SynchronousOnlyOperation`이다 —
  `await instance.arefresh_from_db(fields=["name"])`로 먼저 읽는다. 저장은 실린 필드만 쓰고 부모 테이블은 건드리지 않는다.
  부모 행은 자식의 pk가 아니라 페이로드에 실린 부모 링크로 찾는다(자식이 pk를 따로 선언하면 둘은 다른 값이다). 링크가
  실리지 않은 조상(pk를 따로 둔 부모의 부모)의 키도 deferred로 남아 저장할 때 행에서 읽는다.
  인스턴스의 db alias(`_state.db`)는 라우터의 `db_for_write`가 고른 곳이다.
- **m2m은 쓰지 않는다.** 페이로드의 m2m pk 목록은 인스턴스에 실리지 않는다. 보통의 `save()`처럼 m2m은 그대로다.
- `DELETED`로 받은 인스턴스를 저장하면 행이 없으므로 다시 INSERT된다. deferred 필드가 있는 인스턴스(상속한 자식)는
  쓸 행이 없어 `DatabaseError`다.
- **저장도 알림을 낸다.** 그 모델이 `senders`에 있으면 수신자의 저장이 같은 채널로 다시 알려지고, 그 컴포넌트의
  `mutation()`이 또 불린다. 받을 때마다 무조건 저장하면 끝없이 돈다. 값이 다를 때만 저장하거나, 시그널을 내지 않는
  `QuerySet.update()`를 쓴다. 1.0.0rc4까지의 raw 저장도 시그널을 냈으므로 이 위험은 새것이 아니다.

**픽스처 로드는 알리지 않는다.** `loaddata`는 `raw=True`로 저장하고, 그 순간의 DB는 아직 일관되지 않을 수 있다.
`post_save`의 `raw=True`는 거르고, `loaddata`가 방금 raw로 저장한 객체에 이어서 채우는 m2m도 거른다(`m2m_changed`에는
`raw`가 없어서 그 객체에 표시를 남긴다). 같은 객체를 직접 `save_base(raw=True)`한 뒤 고친 m2m도 그 객체를 보통으로 다시
저장하기 전까지는 알리지 않는다.

m2m 변경(`m2m`)은 **바꾼 쪽의 모델**이 `senders`에 있을 때 알린다. 알림에 실리는 인스턴스가 그쪽이기 때문이다.
`user.groups.add(g)`는 `User`를, `group.user_set.add(u)`는 `Group`을 적어야 알린다. 어느 쪽에서 바꿔도 알리려면
두 모델을 모두 적는다.

## 개발 도구

| 키 | 기본값 | 뜻 |
|----|--------|----|
| `AUTO_GENERATE_STUBS` | `True` | `DEBUG`에서 컴포넌트 타입 스텁(`.pyi`)을 만든다 ([type-stubs](./type-stubs.md)). **기동 시** |
| `TELEMETRY` | `False` | 계측 시그널을 켠 채로 시작한다. 실행 중에는 `telemetry.enable()`·`disable()` ([telemetry](./telemetry.md)). **기동 시** |
| `DEBUG_SYNC_TRANSITIONS` | `False` | sync/async 전환이 겹치는지 감시한다 ([성능](../PERFORMANCE.md)). **기동 시** |
| `DEBUG_SYNC_TRANSITIONS_WARNING_THRESHOLD` | `2` | 이 깊이를 넘으면 경고 |
| `DEBUG_SYNC_TRANSITIONS_ERROR_THRESHOLD` | `3` | 이 깊이를 넘으면 오류 |
