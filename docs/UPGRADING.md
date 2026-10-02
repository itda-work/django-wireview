# 업그레이드 가이드

## 어디서 오나

쓰던 버전의 행에 적힌 절을 화살표 순서대로(오래된 변경부터) 읽는다. 문서는 최신 절이 위에 있으므로 아래에서
위로 올라가며 읽는 셈이다. 절마다 그 버전 사이에서 고칠 것만 적었다.

| 쓰던 버전 | 읽을 절 |
|-----------|---------|
| 0.4.x | [0.4에서 1.0으로](#04에서-10으로) → [0.5에서 0.6으로](#05에서-06으로) → [1.0.0rc1에서 1.0으로](#100rc1에서-10으로) → [1.0.0rc4에서 1.0으로](#100rc4에서-10으로) |
| 0.5.x | [0.5에서 0.6으로](#05에서-06으로) → [1.0.0rc1에서 1.0으로](#100rc1에서-10으로) → [1.0.0rc4에서 1.0으로](#100rc4에서-10으로) |
| 0.6.x, 0.7.x, 1.0.0rc1 | [1.0.0rc1에서 1.0으로](#100rc1에서-10으로) → [1.0.0rc4에서 1.0으로](#100rc4에서-10으로) |
| 1.0.0rc2, 1.0.0rc3 | [1.0.0rc1에서 1.0으로](#100rc1에서-10으로)의 [§9](#9-auto_broadcast는-senders에-적은-모델만-알린다-보안)와 [§10](#10-의존성-하한) → [1.0.0rc4에서 1.0으로](#100rc4에서-10으로) |
| 1.0.0rc4 | [1.0.0rc4에서 1.0으로](#100rc4에서-10으로) |

0.3 이하는 [CHANGELOG](../CHANGELOG.md)의 해당 절을 먼저 읽고 0.4 행을 따른다. 어느 행이든 마지막에
[버전 범위](#버전-범위)를 고친다.

> **보안.** 1.0 전 버전은 아래 보안 권고 중 하나 이상의 영향을 받는다. 해당하면 링크한 절의 할 일을 한다.
>
> - [GHSA-q2rr-5q2g-6xqp](https://github.com/itda-work/django-wireview/security/advisories/GHSA-q2rr-5q2g-6xqp)
>   (0.7.0 이하, 1.0.0rc1~1.0.0rc3): `AUTO_BROADCAST`의 플래그(`model`, `model_pk`, `related`, `m2m`) 중 하나라도
>   켜고 `senders`를 비워 두면, 켠 플래그에 따라 모든 모델의 저장·삭제(`model`·`model_pk`·`related`)나 모든 m2m
>   변경(`m2m`)이 모든 필드와 함께 채널 레이어로 방송됐다(`User`의 비밀번호 해시 포함). `m2m`만 켰어도 해당한다 —
>   `user.groups.add(g)` 한 번에 그 사용자가 양쪽 채널로 나갔다. 모델 알림을 쓰고 있었다면
>   [§9](#9-auto_broadcast는-senders에-적은-모델만-알린다-보안)를 읽는다.
> - [GHSA-8q8p-x4w4-p745](https://github.com/itda-work/django-wireview/security/advisories/GHSA-8q8p-x4w4-p745)
>   (1.0.0rc4 이하, 0.x 포함): `on_upload_complete`를 정의한 컴포넌트는 브라우저가 그 콜백을 이벤트로, 원하는
>   인자로 부를 수 있었다. [rc4→1.0 절](#100rc4에서-10으로)의 `on_upload_complete` 항목을 읽는다.
> - [GHSA-4v8p-p6p8-78pj](https://github.com/itda-work/django-wireview/security/advisories/GHSA-4v8p-p6p8-78pj)
>   (1.0.0rc3~1.0.0rc4): `{% wireview_toasts %}`가 있는 페이지는 방문자의 세션 키 원문을 그룹 이름으로 브로커에
>   보냈다. 서버 저장형 세션 백엔드와 Redis·NATS 같은 프로세스 밖 브로커 레이어를 썼다면 업그레이드하거나
>   태그를 빼고, 마지막 rc3·rc4 워커가 내려간 뒤 그 기간의 세션을 무효화한다. 백엔드마다 비울 것이 다르다.
>   [rc4→1.0 절](#100rc4에서-10으로)의 토스트 항목을 읽는다.

## 버전 범위

1.x는 [공개 API](./COMPATIBILITY.md)를 깨지 않으므로 상한은 다음 메이저다.

```toml
dependencies = ["django-wireview>=1.0,<2"]
```

릴리스 후보를 쓰던 프로젝트는 rc 하한(`>=1.0.0rc4,<1.1`)과 `pip install --pre`를 지운다. 1.0.0은 사전
릴리스가 아니므로 평소처럼 설치된다.

## 1.0.0rc4에서 1.0으로

- **`mutation()`이 받은 `instance`의 `save()`가 보통의 저장이 됐다**(**조용함**, #153). 전에는 Django 역직렬화기의 저장
  (`save_base(raw=True)`)이라 모델의 `save()` 오버라이드를 건너뛰고 시그널을 `raw=True`로 보냈다. 이제는 둘 다 정상으로
  돈다. m2m은 더 이상 페이로드 값으로 되돌리지 않는다(보통의 `save()`처럼 그대로 둔다). 받은 인스턴스를 저장하지 않으면 고칠 것이 없다. 저장한다면 [설정의 모델 알림](./features/settings.md#모델-알림)에서
  무엇을 쓰는지 확인한다 — 여전히 알림 때 페이로드에 실린 필드를 모두 쓴다. 다중 테이블 상속의 자식이면 부모 모델의
  필드는 이제 deferred다. 전에는 기본값(`''`·`None`)으로 오류 없이 읽혔으니, 그 값을 읽던 코드는 `await instance.arefresh_from_db(fields=["name"])`처럼
  읽을 필드를 적어 먼저 읽는다. 필드를 적지 않은 `arefresh_from_db()`는 deferred 필드를 건너뛴다.
- **픽스처 로드(`loaddata`)는 더 이상 모델 알림을 내지 않는다**(**조용함**). `raw=True` 저장과, `loaddata`가 그 객체에 이어서
  채우는 m2m을 거른다. 픽스처를 넣어 화면이 갱신되기를 기대하던 코드나 테스트는 행을 보통으로 저장하거나 알림을 직접 보낸다.
- **`on_upload_complete`는 이벤트 핸들러가 아니다**(**보안**,
  [GHSA-8q8p-x4w4-p745](https://github.com/itda-work/django-wireview/security/advisories/GHSA-8q8p-x4w4-p745)).
  `on_upload_complete(name, entry)`는 이제 `Component`가 가진 콜백이다. 전에는 프레임워크가 이름으로 찾기만 해서,
  이 메서드를 쓴 컴포넌트는 브라우저가 이벤트로 보낸 가짜 완료에도 콜백을 실행했다. 인자도 브라우저가 정했다 — 타입
  주석이 있으면 위조한 dict가 검증을 통과한 `UploadEntry`가 되어, `temp_path`는 서버가 읽을 수 있는 아무 파일을,
  `ref`·`client_name`은 아무 저장소 키를 가리킬 수 있었다. 1.0에서는 디스패처가 이 이름을 거절하고 `{% on %}`도
  바인딩하지 않으며, 세션만 이 콜백을 부른다. 올리기 전까지는 콜백의 `entry` 인자를 믿지 않는다 —
  `async for upload in self.consume_uploads(name)`으로 레지스트리의 완료된 항목만 쓰고 `entry.temp_path`를 직접
  열지 않는다. 오버라이드는 고칠 것이 없다 — 시그니처도 그대로고 업로드가 끝나면 전처럼 불린다. 테스트의
  `view.call("on_upload_complete", ...)`는 `AssertionError`를 내므로 `await view.component.on_upload_complete(name, entry)`로
  직접 부른다. sync로 쓴 오버라이드는 `wireview.W001` 대신 `wireview.W002`로 알린다(원래도 실행되지 않았다).
- **`{% on %}`은 클라이언트가 부를 수 없는 이름에 바인딩하지 않는다.** `{% on "click" "joined" %}`처럼 프레임워크 메서드나
  `_` 메서드에 바인딩하면(`JS().push("...")` 안도 같다) 렌더가 `AssertionError`로 멈춘다. 전에는 렌더는 되고 클릭이 서버
  로그 한 줄만 남긴 채 버려졌다. 그러니 원래 동작하던 바인딩은 없다. 사용자 메서드로 감싸 다른 이름으로 바인딩한다.
- **`{% on %}`이 클라이언트가 실행하지 않는 수정자를 렌더 때 `ValueError`로 거절한다.** `click.away`·`click.once`·
  `click.self`·`keydown.escape`처럼 클라이언트가 건너뛰던 이름, 인자가 없는 `keydown.key`·`input.debounce`·`click.throttle`,
  정수가 아닌 인자(`input.debounce.abc`)가 그렇다. 전에는 렌더되고 조용히 다른 이벤트에 반응했다(`keydown.escape`는 모든 키,
  `input.debounce`는 디바운스 없음). 템플릿을 렌더해 보면 바로 드러난다. Escape는 `esc` 또는 `key.escape`.
- **토스트 채널이 세션 키 원문을 브로커로 보내지 않는다**(**보안**,
  [GHSA-4v8p-p6p8-78pj](https://github.com/itda-work/django-wireview/security/advisories/GHSA-4v8p-p6p8-78pj)).
  1.0.0rc3·rc4의 `{% wireview_toasts %}`는 세션이 있는 모든 방문자를 `wireview.toast.session.<세션 키>` 그룹에
  구독시켰다 — 토스트를 보냈는지와 상관없이. 그룹 이름은 브로커로 간다(channels_redis는 Redis 키와 RDB/AOF 스냅샷,
  channels-nats는 구독 subject와 `/subsz?subs=1`·`/connz?subs=1` 모니터링). db·cache·cached_db·file 세션 백엔드에서
  세션 키는 곧 세션 쿠키라, 브로커와 그 로그·모니터링을 읽을 수 있는 쪽이 세션이 끝날 때까지 그 사용자로 행세할 수
  있었다. 1.0은 세션 키를 다이제스트로만 넣는다(`signed_cookies` 백엔드에서 join이 실패하던 결함도 함께 고쳐졌다).
  - 해당하면(위 버전, 그 태그가 있는 페이지, 서버 저장형 세션 백엔드, Redis·NATS 같은 프로세스 밖 브로커 레이어)
    1.0으로 올리고, **마지막 rc3·rc4 워커가 내려간 뒤에** 그 기간에 활성이던 세션을 무효화한다. 롤링 배포 동안 남은
    옛 워커는 계속 원문 키로 구독하므로 그 사이에 생긴 세션도 새로 유출된다. 모든 사용자가 다시 로그인한다. 백엔드마다 비울 것이 다르다.
    - `db`: 세션 테이블(`django_session`)
    - `cache`: 세션 캐시(`SESSION_CACHE_ALIAS`)
    - `cached_db`: 테이블과 캐시 둘 다. 테이블만 비우면 캐시 항목이 세션 만료까지(기본 2주) 남고, 캐시만 비우면
      테이블에서 다시 채운다.
    - `file`: `SESSION_FILE_PATH`의 세션 파일. 설정하지 않았다면 시스템 temp 디렉터리(`tempfile.gettempdir()`)에서
      `SESSION_COOKIE_NAME`(기본 `sessionid`)으로 시작하는 파일
  - `clearsessions`는 만료된 세션만 지우므로 소용없다. `SECRET_KEY`를 바꾸면서 옛 키를 `SECRET_KEY_FALLBACKS`에
    남기면 아무것도 무효화되지 않는다. 남기지 않고 바꾸면 모든 백엔드에서 로그인이 풀리지만, 비밀번호 재설정 링크·서명
    쿠키 같은 다른 서명도 함께 무효가 된다. 또 `cache`·`cached_db`에서는 로그인만 풀리고 세션에 든 다른 값은 그대로
    읽히므로, 세션에 민감한 값을 둔다면 위의 저장소를 비운다.
  - `wireview.toast.session.` 그룹 이름이 남았을 수 있는 기록을 점검해 지운다. 브로커의 덤프(Redis RDB/AOF)·로그·
    모니터링(NATS `/subsz?subs=1`·`/connz?subs=1`), 앱의 `wireview` 로거 DEBUG 출력(rc3·rc4는 구독마다 그룹 이름을
    남겼다), 텔레메트리 수신자(세션 키로 토스트를 보냈다면 `broadcast_published`의 `topic`(rc3·rc4)과
    `publish_failed`의 `target`(rc4))다. 세션을 무효화했다면
    남은 기록은 무해하다.
  - 올릴 수 없으면 레이아웃에서 `{% wireview_toasts %}`를 빼고, 그 배포가 모든 워커에 닿은 뒤 위와 같이 세션을
    무효화하고 기록을 점검한다. 태그를 빼도 이미 브로커·로그에 간 키는 세션이 끝날 때까지 유효하다. `InMemoryChannelLayer`는 프로세스 밖으로 보내지 않는다.
  - 1.0.0rc4 워커와 1.0 워커가 섞여 도는 롤링 배포 동안에는 서로 다른 버전의 워커 사이에서 세션 키 토스트가 닿지 않는다.
    토스트는 다시 오지 않는 일회성 메시지라 그동안의 것은 빠진다. 사용자로 보낸 토스트는 이름이 같아 영향이 없다.
- **`UploadStatus`·`AsyncState`·`PresenceState`는 `StrEnum`이다**(**조용함**). 템플릿의 `{{ entry.status }}`, `str()`,
  f-string이 `UploadStatus.UPLOADING` 대신 값(`uploading`)을 낸다. `==` 비교와 JSON은 그대로다. 옛 출력에 맞춘 CSS 클래스,
  로그 파싱, `"AsyncState.LOADING"` 같은 문자열 비교를 값으로 고친다.
- **스트림 연산은 그것을 보낸 컴포넌트 요소 안에서 `wire-stream` 컨테이너를 찾는다**(**조용함**). 전에는 페이지 전체에서
  이름으로 찾았다. 컨테이너를 그 컴포넌트의 루트 밖(레이아웃, 다른 컴포넌트)에 둔 페이지는 콘솔에
  `Stream container not found` 경고 하나만 남기고 항목이 붙지 않는다. 컨테이너를 스트림을 가진 컴포넌트의 템플릿 안으로
  옮긴다. 같은 이름의 스트림을 가진 두 컴포넌트가 이제 서로의 목록을 건드리지 않으므로, 그것을 피하려고 붙인 `dom_id`
  접두사는 없어도 된다.
- **렌더가 새로 그린 `{% component %}`는 join되고 `joined()`가 돈다**(**조용함**). 전에는 `{% if %}`로 다시 보이거나
  이벤트가 처음 그린 중첩 `{% component %}`는 서버에 인스턴스가 생기고 이벤트도 받았지만 `joined()`가 한 번도 돌지
  않았다. 이제 페이지가 그 컴포넌트를 join하므로 `joined()`가 돌고, 그 안의 `wire-viewport-*`도 관찰된다.
  - `joined()`가 돌지 않는 것을 우회하려고 `mount()`나 핸들러에서 같은 초기화를 되풀이했다면 걷어낸다. 이제 두 번 돈다.
  - LiveComponent 안의 `wire-viewport-*`는 그 LiveComponent의 핸들러를 부른다. 전에는 페이지의 루트 컴포넌트로 가서
    루트에 같은 이름의 핸들러가 있으면 그것이 불렸다. 자식의 센티널을 루트에서 받던 코드는 자식으로 옮긴다.
- **렌더가 지우거나 옮긴 포커스 칸은 `blur`·`focusout`·`change`를 보내지 않는다**(**조용함**). 브라우저는 포커스된 요소를
  지울 때 `blur`를(입력했으면 `change`를 먼저) 내고, 전에는 그것이 사용자의 이벤트로 서버에 갔다. 그래서 편집 칸을 숨기는
  핸들러 뒤에 `{% on "blur" "save_edit" %}`가 돌아 Escape가 저장이 되곤 했다. 칸을 지우는 렌더에 저장이 따라오기를
  기대했다면 칸을 지우는 핸들러에서 저장한다. 폼 피드백도 이 이벤트를 건드림으로 세지 않는다.
- **`mount()`·`call()`·`follow_push()`가 구독 이름과 브로드캐스트 이름을 채널 레이어처럼 검사한다.**
  `Meta.subscriptions`, `get_subscriptions()`, `broadcast()`에 `room:42`처럼 레이어가 거절하는 이름(영숫자·`-`·`_`·`.` 밖의
  문자)이 있으면 테스트가 `TypeError`로 실패한다. 실서버에서는 원래 join이나 브로드캐스트가 실패하던 이름이다.
  `room-42`처럼 바꾼다([테스트 헬퍼](./features/testing.md#mountedcomponent)).
- **생성된 `.pyi`가 `LiveComponent`를 `wireview`에서 import한다.** 커밋해 둔 LiveComponent 스텁은 다시 만들 때까지
  `wireview_stubs --check`에서 오래됐다고 실패한다. `python manage.py wireview_stubs`로 다시 만든다.
- **새 경고 `wireview.W018`.** 컴포넌트의 공개 메서드가 프레임워크(wireview·Pydantic)의 멤버와 이름이 같으면 알린다.
  그런 메서드는 원래 클라이언트가 부를 수 없었다 — Phoenix의 `phx-change="validate"`를 옮긴 `async def validate`가
  대표다. `manage.py check --fail-level WARNING`을 쓰는 CI는 첫 실행에서 멈출 수 있다. 메서드 이름을 바꾸고 바인딩도
  고친다([검사 목록](./features/checks.md#검사-목록)).
- **스타터로 만든 프로젝트의 `asgi.py`에 정적 파일 줄을 더한다.** 1.0 전 스타터의 `asgi.py`를 uvicorn으로 띄우면
  `wireview.min.js`가 404라 페이지는 그려지고 어떤 컴포넌트도 살아나지 않는다.
  [튜토리얼 01의 `asgi.py`](./tutorials/01-getting-started.md#asgipy-수정)처럼 `DEBUG`일 때 `ASGIStaticFilesHandler`로 감싼다. `runserver`(daphne)만 쓰면 고칠 것이 없다.
- **`public=False`와 함께 준 `name=`이 그 컴포넌트의 이름이 된다**(**조용함**). 등록된 클래스를 상속하면 `name=`이
  버려지고 로그·계측·서명 상태에 부모의 이름이 쓰였다. 계측이나 로그를 컴포넌트 이름으로 거르던 곳은 새 이름을 본다.
  `name=`이 없으면 그대로다.
- **`JS()` transition의 dict 형식에서 `to` 키가 빠졌다.** 클라이언트는 이 키를 읽은 적이 없다. 타입 검사기가 이제
  `{"to": ...}`를 거절하므로 키를 지운다. 실행 동작은 같다.
- **설치한 에이전트 스킬을 다시 설치한다.** `wireview_agent_setup`이 복사한 `.claude/skills/wireview`는 rc1의
  `handle_async`·`mount()` 계약을 말하고 `TypeError`를 내는 예시를 담고 있었고, 토스트와 `update_many()`를 몰랐다. `python manage.py wireview_agent_setup --force`로
  덮어쓴다.
- **문서 예시를 베껴 쓴 코드를 확인한다.** 1.0 전 문서의 예시 몇 개는 그대로 쓰면 결함이었고, 오류 없이 지나간다.
  - 함수 컴포넌트가 f-string으로 마크업을 만들어 인자를 이스케이프하지 않았다(XSS). `format_html`로 만든다.
  - LiveComponent·훅 가이드, README, 튜토리얼 07·08의 서버 헬퍼가 `_` 없이 이름 붙어 브라우저가 부를 수 있는 핸들러였다
    (`notify_user`는 아무 토스트나 띄웠다). `_`를 붙인다. `async def mount(self)`도 프레임워크가 부르지 않는 핸들러다 —
    초기화는 `joined()`에 둔다. `send_to_parent`가 부르는 부모 핸들러는 `_`를 붙일 수 없어(디스패처가 거절한다)
    브라우저도 아무 인자로 부를 수 있다. 그 핸들러 안에서 인자를 검증하고 권한을 확인한다.
  - 튜토리얼 02·03은 Escape와 Ctrl+Enter를 `keypress`에 묶어 한 번도 발화하지 않았다. `keydown`에 묶는다.
  - 튜토리얼 03·15는 `QuerySet.aupdate()`로 저장해 `post_save`가 없었고 다른 탭이 듣지 못했다. 인스턴스를 `asave()`한다.
    튜토리얼 15의 `reset_all`은 부모가 가진 합계도 옛 값으로 남겼다 — 자식의 `update()`는 부모에게 알리지 않으므로
    부모의 합계를 함께 고친다.
  - 튜토리얼 05·15는 컴포넌트를 모델 채널에 구독시키고 `AUTO_BROADCAST.senders`를 적지 않아 모델 알림이 한 번도 오지
    않았다. `AUTO_BROADCAST`가 아예 없으면 `wireview.W015`도 조용하다. 구독하는 모델을 `senders`에 적는다
    ([§9](#9-auto_broadcast는-senders에-적은-모델만-알린다-보안)).
  - 튜토리얼 03~05는 중첩 컴포넌트에 `id`를 주지 않아 부모가 렌더할 때마다 자식이 새로 만들어졌다. `id`를 준다.
  - 퀴즈 예제와 튜토리얼 13은 `{% class {...} %}`를 여러 줄에 걸쳐 써서 태그가 글자로 찍혔다. 한 줄에 쓴다.

## 1.0.0rc1에서 1.0으로

1.0이 공개 API를 굳히기 전에 모양을 한 번 더 정리했다(#119, 1.0.0rc2). 0.7이나 1.0.0rc1을 쓰던 프로젝트는 아래를 확인한다.
대부분은 `TypeError`·`ImportError`·`manage.py check`로 드러나고, 조용히 달라지는 것은 **조용함**으로 표시했다.
전체 목록은 [CHANGELOG](../CHANGELOG.md).

### 1. `handle_async`는 `AsyncResult`를 받는다

```python
# 전
async def handle_async(self, name, result):
    if result[0] == "ok":
        self.data = result[1]

# 후
async def handle_async(self, name, result):
    if result.ok:
        self.data = result.result      # 실패면 result.failed, result.error
```

옛 코드는 `TypeError`를 던지고, 컨슈머가 그 컴포넌트를 버리고 다시 join시킨다(로그에 남는다).

### 2. m2m 채널 이름 (**조용함**)

`AUTO_BROADCAST.m2m`의 알림은 이제 양쪽 행 모두 `<app_label>.<model>.<pk>.<field>`로 간다. 끝에 상대 pk가 붙은
`<...>.<field>.<pk>` 형식을 구독했다면 아무것도 받지 못한다. 접미사 없는 형식으로 바꾼다.

### 3. 테스트의 `mount()`

옵션은 키워드로만 받는다. `mount(Cls, user)`는 `mount(Cls, user=user)`로. 이름이 옵션과 같은 필드(`params` 등)는
`state={"params": ...}`로 준다. 필드 값은 전처럼 키워드로 줘도 된다 — `mount(Counter, count=3)`은 그대로 동작하고,
`state=`는 옵션과 이름이 겹칠 때만 쓴다. `view.dom_actions`와 `view.clear_dom_actions()`는 없어졌다(항상 비어 있었다).

### 4. 없어진 이름

| 옛 것 | 대신 |
|-------|------|
| `from wireview import send_notification`, `asend_notification` | `broadcast`, `abroadcast` |
| `from wireview import ComponentNotFound`, `list_function_components` | 없음 (내부) |
| `telemetry.span`, `telemetry.payload_size` | 없음 (내부) |
| `Component.dom()` | 없음 |
| `wireview.debounce()`, `wireview.throttle()` (브라우저) | `{% on "input.debounce.300" ... %}` 또는 직접 만든 타이머 |

### 5. 템플릿의 `{% upload_button %}`

```html
<!-- 전 -->
{% upload_button "images" class="btn" %}Select</button>
<!-- 후 -->
<button type="button" {% upload_button "images" %} class="btn">Select</button>
```

### 6. 브라우저 API (**조용함**)

- `wireview.send(el, name, args, "click")`처럼 넷째 인자로 이벤트 종류를 넘겼다면 `{eventType: "click"}`로 바꾼다.
  문자열은 무시되어 로딩 클래스가 붙지 않는다. 대상 LiveComponent는 `args._target` 대신 `{target: id}`.
- `wireview.dom.onBeforeElUpdated(cb)`는 콜백을 **더한다**. `null`을 넘겨 지우던 코드는 돌려받은 함수를 부른다.
- 업로드 이벤트는 `wireview:upload-progress` 등으로도 나간다. 옛 `upload:progress`도 2.0까지 나가므로 급하지 않다.

### 7. 그 밖의 시그니처

- `self.scroll_into_view(id, "smooth")` → `self.scroll_into_view(id, behavior="smooth")`
- `self.wire.redirect_to(to="/x")` → `self.wire.redirect_to("/x")`. `push_to`·`replace_to`도 같다.

### 8. 설정

`SYNC_TRANSITION_WARNING_THRESHOLD`·`SYNC_TRANSITION_ERROR_THRESHOLD`는
`DEBUG_SYNC_TRANSITIONS_WARNING_THRESHOLD`·`DEBUG_SYNC_TRANSITIONS_ERROR_THRESHOLD`로 바뀌었고
`TRANSPILER_CACHE_SIZE`는 없어졌다. 남아 있으면 `manage.py check`가 `wireview.W014`로 알린다.

### 9. `AUTO_BROADCAST`는 `senders`에 적은 모델만 알린다 (보안)

1.0.0rc4가 보안 권고 [GHSA-q2rr-5q2g-6xqp](https://github.com/itda-work/django-wireview/security/advisories/GHSA-q2rr-5q2g-6xqp)를
고친 변경이다.

`senders`를 비워 두면 전에는 모든 모델을 알렸고, 이제는 **아무것도 알리지 않는다.** 구독한 컴포넌트의
`mutation()`이 더는 불리지 않으므로, 알릴 모델을 적는다.

```python
# 전
"AUTO_BROADCAST": AutoBroadcast(model=True, model_pk=True)

# 후
"AUTO_BROADCAST": AutoBroadcast(model=True, model_pk=True, senders={("todo", "Item")})
```

플래그를 켜고 `senders`를 비워 두면 `manage.py check`가 `wireview.W015`로 알린다. 설치되지 않은 모델을 적으면
기동 때 `ImproperlyConfigured`다. `senders`에 적은 모델은 모든 필드가 채널 레이어로 직렬화되므로 `User`처럼
민감한 필드가 있는 모델은 넣지 않는다.

m2m 변경은 바꾼 쪽의 모델이 `senders`에 있을 때 알린다(**조용함**). `user.groups.add(g)`와 `group.user_set.add(u)`를
모두 알리려면 두 모델을 다 적는다. 상세는 [설정](./features/settings.md#모델-알림).

영향받는 버전에서 `model`, `model_pk`, `related`, `m2m` 중 하나라도 켜고 돌렸다면, 브로커(Redis·NATS)의
로그·모니터링·덤프에 남았을 수 있는 데이터를 점검하고 필요하면 비밀번호 변경을 검토한다. 보낼 필드를 모델마다 고르는 선택지는 1.1에서 더한다(#144).

### 10. 의존성 하한

1.0.0rc4부터 `channels>=4.2.1`, `pydantic>=2.7,!=2.9.0`이 필요하다. pydantic의 옛 버전은 Python 3.12에 설치되지
않거나 `import wireview`에서 실패했고, channels 4.2.1 미만은 channels-nats 레이어에서 실패했다(#132). 둘 중
하나를 옛 버전에 고정했다면 풀어 준다.

## 0.6에서 0.7, 1.0 릴리스 후보로

고칠 것이 없다. 0.7.0과 1.0.0rc1은 호환을 깨는 변경이 없다([CHANGELOG](../CHANGELOG.md)).

1.0.0rc1을 거쳐 1.0으로 올 때는 [1.0.0rc1에서 1.0으로](#100rc1에서-10으로)를 읽는다. rc2는 rc1과 호환되지
않는다. rc3는 pydantic 2.13 이상에서 import가 실패해 철회(yank)됐다(#127). 버전 범위는 [위](#버전-범위)처럼 적는다.

## 0.5에서 0.6으로

0.6은 1.0 전에 계약을 바로잡은 릴리스다. 대부분 결함 수정이지만 아래 넷은 코드나 테스트를 고쳐야 할 수 있다.
전체 목록은 [CHANGELOG](../CHANGELOG.md).

### 1. `temporary_assigns`는 `joined()`에서 불러온다 (**조용함**)

`Meta.temporary_assigns` 필드는 이제 서명 상태(`data-state`)에 실리지 않는다. 재접속으로 다시 join하면 그
필드는 기본값에서 시작한다. 템플릿 태그 인자로만 채우던 필드(`{% component "X" messages=... %}`)는 재접속 뒤
비므로, 불러오는 코드를 `joined()`로 옮긴다. 대신 초기화는 더 이상 변경이 아니어서, 목록과 무관한 렌더가 목록을 지우지 않는다
([temporary_assigns](./features/temporary-assigns.md)).

### 2. 중첩 컴포넌트 안의 훅은 그 컴포넌트의 것이다 (**조용함**)

부모가 먼저 join하면서 자식 컴포넌트 안의 훅까지 가져가던 결함을 고쳤다. 그래서 부모의 `push_event`가 자식
안의 훅에 닿던 코드는 이제 닿지 않는다. 훅을 가진 컴포넌트에서 보내거나, 훅을 부모 자신의 마크업으로 옮긴다
([훅](./features/hooks.md#훅-수명주기)).

### 3. 테스트: `call()`은 클라이언트가 부를 수 있는 것만 부른다

`MountedComponent.call()`이 `_`로 시작하는 메서드, 라이프사이클 메서드, 믹스인의 프레임워크 메서드를 부르면
`AssertionError`를 낸다. 메서드 자체를 시험하려던 테스트는 `await view.component.method(...)`로 직접 부른다.

### 4. 테스트: `view.wire.broadcasts`는 `view.broadcasts`로

`view.wire.broadcasts`·`view.wire.presence_broadcasts`는 `WireviewDeprecationWarning`을 내고 2.0에서 없어진다.

## 0.4에서 1.0으로

1.0 전에 API를 굳히면서 호환을 깨는 변경을 한 번에 모았다(#93). 아래 순서대로 하면 된다. 대부분은 틀리면
`TypeError`나 `manage.py check`의 경고로 드러나고, 조용히 달라지는 것은 따로 표시했다(**조용함**).

0.2 이하에서 바로 올린다면 하나 더: 그 버전의 `conn:comp:config:ref` 업로드 토큰은 0.5(#99)부터 받지 않는다. 롤링 배포
도중 옛 워커가 그린 페이지에서 진행 중이던 업로드는 403으로 실패하고, 페이지를 다시 열면 새 토큰으로 올라간다
([청크 업로드](./features/chunked-uploads.md)).

### 1. Django 5.2 이상

Django 5.0과 5.1은 Django의 지원이 끝나 지원 범위에서 빠졌다. 지원 범위는 Django가 보안 지원하는 버전과
Python 3.12 이상이다([호환성 정책](./COMPATIBILITY.md#지원-범위)).

함께 `channels>=4.2.1`, `pydantic>=2.7,!=2.9.0`이 필요하다. 1.0 rc까지 선언했던 하한 중 pydantic의 옛 버전은
설치되지 않거나 import에서 실패했고, channels 4.2.1 미만은 channels-nats 레이어에서 실패했다(#132). 둘 중 하나를
옛 버전에 고정했다면 풀어 준다.

### 2. import는 `wireview`에서

공개 API는 `wireview.__all__`뿐이다. 하위 모듈에서 import하던 이름은 모두 최상위에서 가져온다.

```python
# 전
from wireview.component import Component
from wireview.features.presence import PresenceMixin
from wireview.schemas import ModelAction
from wireview.testing import mount

# 후
from wireview import Component, ModelAction, PresenceMixin, mount
```

`wireview.component`는 `WireviewDeprecationWarning`을 내며 1.x 동안 동작하고 2.0에서 없어진다. 나머지 하위
모듈 경로는 약속이 아니므로 예고 없이 옮겨질 수 있다.

### 3. 컴포넌트 설정은 `class Meta:`로

밑줄 붙은 클래스 속성은 `class Meta:` 안의 키가 됐다. 옛 이름을 쓰면 새 위치를 알려 주는 `TypeError`가 난다.

```python
# 전
class Inbox(Component):
    _template_name = "mail/inbox.html"
    _subscriptions = {"mail"}
    _on_mount = [AuthHook]

# 후
class Inbox(Component):
    class Meta:
        template_name = "mail/inbox.html"
        subscriptions = {"mail"}
        on_mount = [AuthHook]
```

| 전 | 후 (`Meta` 키) |
|----|----------------|
| `_template_name` | `template_name` |
| `_subscriptions` | `subscriptions` |
| `_temporary_assigns` | `temporary_assigns` |
| `_exclude_fields` | `exclude_fields` |
| `_slots` | `slots` |
| `_on_mount` | `on_mount` |
| `_live_sessions` | `live_sessions` |
| `_presence_config` | `presence` |

- 하위 클래스는 자기 `Meta`에 적지 않은 키를 부모에게서 물려받는다.
- `exclude_fields`는 `user`·`wire`·`session`에 **더해진다**. 예전처럼 기본값을 함께 적을 필요가 없다.
- 상태에 따라 달라지는 구독은 `@property def _subscriptions`가 아니라 `get_subscriptions()`를 오버라이드한다.

```python
def get_subscriptions(self) -> set[str]:
    return {f"room.{self.room_id}"}
```

### 4. `deffer`는 `defer`

`self.deffer(self.handler, ...)`는 `self.defer(...)`다. `self.wire`에서 쓸 수 있는 것은 `params`,
`redirect_to`, `replace_to`, `push_to`뿐이다. flash, 제목, JS 명령은 `Component`의 메서드(`put_flash`,
`push_title`, `push_js`, `push_event`)로 한다.

### 5. 없어진 설정

`manage.py check`의 `wireview.W014`가 남은 키를 알려 준다.

| 설정 | 대신 |
|------|------|
| `STATE_ACCEPT_LEGACY` | 없음. v2 봉투 이전 상태를 가진 페이지는 한 번 새로 읽힌다 |
| `USE_HTML_DIFF` | 없음. diff는 항상 켜져 있다 |
| `USE_HMIN` | 없음. hmin은 diff 마커를 지워 매번 HTML 전체를 보내게 했다. 전송량이 문제면 WebSocket 압축을 재 보고 켠다 — 연결당 메모리 비용은 [배포 가이드](./DEPLOYMENT.md#권장-uvicorn--uvloop) |

### 6. Origin 검사 (**조용함**)

컨슈머가 소켓을 받기 전에 `Origin` 헤더의 호스트를 `ALLOWED_HOSTS`와 대조한다. **페이지와 소켓의 호스트가
다른 배포**(예: 페이지 `www.example.com`, 소켓 `ws.example.com`)는 페이지의 호스트도 `ALLOWED_HOSTS`에
넣어야 한다. 빠뜨리면 소켓이 403으로 거절되고, 서버 로그에 `Refusing a WebSocket`이 WARNING으로 남는다.
[배포 가이드](./DEPLOYMENT.md#websocket의-origin).

### 7. 테스트에서 설정 바꾸기

wireview는 설정을 쓰는 시점에 읽는다. `override_settings(WIREVIEW={...})`가 그대로 동작한다.
`wireview.settings`에 값을 대입하던 테스트(monkeypatch 포함)는 `AttributeError`가 나므로 `override_settings`로
바꾼다.

### 8. 달라진 동작 (**조용함**)

코드를 고칠 필요는 없지만 알아 둘 것들이다.

- **핸들러 예외는 연결을 끊지 않는다.** 그 컴포넌트만 이벤트 전 상태로 다시 join한다. 던지기 전에 DB에
  쓴 것은 남는다. `wireview:error` 이벤트로 사용자에게 알릴 수 있다([오류 처리](./features/errors.md)).
- **join에 실패한 요소는 지워지지 않는다.** `wireview-error` 클래스가 붙는다.
- **async 작업은 컴포넌트가 떠나면 취소된다.** 재연결 뒤에도 필요하면 `joined()`에서 다시 시작한다
  ([비동기 작업](./features/async-operations.md#작업의-수명)).
- **연결이 끊긴 동안** `.prevent`가 붙은 폼과 링크는 제출·이동하지 않고, 훅의 `pushEvent`는 버려진다
  ([CSP 문서의 라이브가 아닐 때](./features/csp.md)).
- **컴포넌트의 자체 템플릿 캐시가 없어졌다.** Django의 cached loader(기본으로 켜져 있다)가 맡는다.
  `TEMPLATES`의 `loaders`를 직접 적으면서 cached loader로 감싸지 않았다면, 렌더마다 템플릿을 다시 컴파일한다.
- **마커가 없는 HTML**(다른 템플릿 엔진)은 바뀌면 전체가 나간다. 토큰 diff는 없어졌다.

### 9. 브라우저 번들

서버와 번들은 버전을 협상하므로(`vsn`) 배포 중에 옛 번들로 열린 페이지도 깨지지 않는다. 옛 번들은 핸들러 예외에
예전처럼 소켓을 닫고 다시 연결한다. 새로고침하면 새 번들을 받는다.
