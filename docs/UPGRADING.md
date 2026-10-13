# 업그레이드 가이드

쓰던 django-wireview 버전에서 지금 버전으로 올릴 때 고칠 것을 버전 구간별로 모았다. 동작하던 코드가 오류가 되는 변경(깨짐),
조용히 동작이 바뀌는 변경, 보안 조치가 필요한 변경은 그렇게 표시했다.

## 어디서 오나

쓰던 버전의 행에 적힌 절을 화살표 순서대로(오래된 변경부터) 읽는다. 문서는 최신 절이 위에 있으므로 아래에서
위로 올라가며 읽는 셈이다. 절마다 그 버전 사이에서 고칠 것만 적었다.

| 쓰던 버전 | 읽을 절 |
|-----------|---------|
| 0.4.x | [0.4에서 1.0으로](#04에서-10으로) → [0.5에서 0.6으로](#05에서-06으로) → [1.0.0rc1에서 1.0으로](#100rc1에서-10으로) → [1.0.0rc4에서 1.0으로](#100rc4에서-10으로) → [1.0에서 1.1로](#10에서-11로) → [1.1에서 1.2로](#11에서-12로) → [1.2에서 1.3으로](#12에서-13으로) → [1.3에서 1.4로](#13에서-14로) → [1.4에서 1.5로](#14에서-15로) → [1.5에서 1.6으로](#15에서-16으로) |
| 0.5.x | [0.5에서 0.6으로](#05에서-06으로) → [1.0.0rc1에서 1.0으로](#100rc1에서-10으로) → [1.0.0rc4에서 1.0으로](#100rc4에서-10으로) → [1.0에서 1.1로](#10에서-11로) → [1.1에서 1.2로](#11에서-12로) → [1.2에서 1.3으로](#12에서-13으로) → [1.3에서 1.4로](#13에서-14로) → [1.4에서 1.5로](#14에서-15로) → [1.5에서 1.6으로](#15에서-16으로) |
| 0.6.x, 0.7.x, 1.0.0rc1 | [1.0.0rc1에서 1.0으로](#100rc1에서-10으로) → [1.0.0rc4에서 1.0으로](#100rc4에서-10으로) → [1.0에서 1.1로](#10에서-11로) → [1.1에서 1.2로](#11에서-12로) → [1.2에서 1.3으로](#12에서-13으로) → [1.3에서 1.4로](#13에서-14로) → [1.4에서 1.5로](#14에서-15로) → [1.5에서 1.6으로](#15에서-16으로) |
| 1.0.0rc2, 1.0.0rc3 | [1.0.0rc1에서 1.0으로](#100rc1에서-10으로)의 [§9](#9-auto_broadcast는-senders에-적은-모델만-알린다-보안)와 [§10](#10-의존성-하한) → [1.0.0rc4에서 1.0으로](#100rc4에서-10으로) → [1.0에서 1.1로](#10에서-11로) → [1.1에서 1.2로](#11에서-12로) → [1.2에서 1.3으로](#12에서-13으로) → [1.3에서 1.4로](#13에서-14로) → [1.4에서 1.5로](#14에서-15로) → [1.5에서 1.6으로](#15에서-16으로) |
| 1.0.0rc4 | [1.0.0rc4에서 1.0으로](#100rc4에서-10으로) → [1.0에서 1.1로](#10에서-11로) → [1.1에서 1.2로](#11에서-12로) → [1.2에서 1.3으로](#12에서-13으로) → [1.3에서 1.4로](#13에서-14로) → [1.4에서 1.5로](#14에서-15로) → [1.5에서 1.6으로](#15에서-16으로) |
| 1.0.x | [1.0에서 1.1로](#10에서-11로) → [1.1에서 1.2로](#11에서-12로) → [1.2에서 1.3으로](#12에서-13으로) → [1.3에서 1.4로](#13에서-14로) → [1.4에서 1.5로](#14에서-15로) → [1.5에서 1.6으로](#15에서-16으로) |
| 1.1.x | [1.1에서 1.2로](#11에서-12로) → [1.2에서 1.3으로](#12에서-13으로) → [1.3에서 1.4로](#13에서-14로) → [1.4에서 1.5로](#14에서-15로) → [1.5에서 1.6으로](#15에서-16으로) |
| 1.2.x | [1.2에서 1.3으로](#12에서-13으로) → [1.3에서 1.4로](#13에서-14로) → [1.4에서 1.5로](#14에서-15로) → [1.5에서 1.6으로](#15에서-16으로) |
| 1.3.x | [1.3에서 1.4로](#13에서-14로) → [1.4에서 1.5로](#14에서-15로) → [1.5에서 1.6으로](#15에서-16으로) |
| 1.4.x | [1.4에서 1.5로](#14에서-15로) → [1.5에서 1.6으로](#15에서-16으로) |
| 1.5.x | [1.5에서 1.6으로](#15에서-16으로) |

> **깨짐(1.6).** 1.6부터 컴포넌트·함수 컴포넌트·스트림·`Broadcast` 항목의 템플릿은 Django 템플릿 엔진만 그린다.
> Jinja2 같은 다른 엔진이 찾는 함수 컴포넌트·항목 템플릿은 1.5까지 동작했지만 이제 첫 렌더가 오류다. 어느 행이든
> 1.6으로 올리면 [1.5에서 1.6으로의 깨짐](#깨짐)을 먼저 읽는다.

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
> - [GHSA-pv9v-gqcj-f42x](https://github.com/itda-work/django-wireview/security/advisories/GHSA-pv9v-gqcj-f42x)
>   (0.3.0~1.0.0rc4): 첫 화면(HTTP 렌더)에서 함수 컴포넌트의 템플릿 안에 쓴 `{% component %}`에는 페이지의
>   `live_session` `on_mount` 훅이 돌지 않았다. 그 훅으로 컴포넌트마다 거절하던 앱은 거절했어야 할 컴포넌트의
>   마크업과 서명 상태를 내보냈고, 그 토큰은 새 소켓에서 누구나 join할 수 있었다.
>   [rc4→1.0 절](#100rc4에서-10으로)의 함수 컴포넌트 항목을 읽는다.

## 버전 범위

1.x는 [공개 API](./COMPATIBILITY.md)를 깨지 않으므로 상한은 다음 메이저다.

```toml
dependencies = ["django-wireview>=1.5,<2"]
```

하한은 쓰는 기능이 처음 나온 버전이다. `AUTO_BROADCAST.senders`의 매핑은 1.1부터, 테스트의 `mount(path=...)`·
`follow_push(Destination)`와 `wireview:navigation-failed`는 1.2부터, `Broadcast`·`Meta.shared_render`·`wireview:before-navigate`는 1.3부터, 테스트의 `MountedComponent.queries()`는 1.4부터, `Meta.lazy_properties`는 1.5부터, `stream(..., at=, reset=False)`와
`stream_insert(..., update_only=True)`(컴포넌트와 `Broadcast` 둘 다), presence 트래커의 `presence_users_in(topic)`, pytest 플러그인 `wireview`는 1.6부터다.

릴리스 후보를 쓰던 프로젝트는 rc 하한(`>=1.0.0rc4,<1.1`)과 `pip install --pre`를 지운다. 1.x 릴리스는 사전
릴리스가 아니므로 평소처럼 설치된다.

## 1.5에서 1.6으로

주제별로 묶었다. 괄호 안의 번호가 그 변경의 이슈다.

### 깨짐

- **깨짐: 컴포넌트 템플릿은 Django 템플릿 엔진만 그린다(#210).** 컴포넌트·LiveComponent·함수 컴포넌트·스트림·`Broadcast`
  항목의 템플릿을 Jinja2 같은 다른 엔진이 찾으면(`TEMPLATES`에서 그 엔진이 `DjangoTemplates`보다 앞에 있을 때) 첫 렌더가
  어느 클래스·템플릿·엔진인지 적은 `ImproperlyConfigured`로 실패하고, `manage.py check`가 `wireview.W022`로 먼저 알린다.
  다른 엔진이 찾았지만 컴파일하지 못한 템플릿(Django 태그를 만난 Jinja2의 `TemplateSyntaxError`)도 W022가 그 엔진을 알린다.
  - 컴포넌트·LiveComponent 자기 템플릿: 1.5에서도 그 엔진이 그렸지만 `{% tag_header %}`도 라이브 렌더의 마커도 없어
    페이지가 join하지 못했다. 동작하던 것이 깨지지는 않는다.
  - 함수 컴포넌트와 스트림·`Broadcast` 항목 템플릿: 1.5에서는 그 엔진이 그렸고 이벤트 뒤의 라이브 렌더에서도 그려졌다
    (`Broadcast`는 1.3부터). **이제 오류다.**
  - 옮기는 법: `manage.py check`의 W022가 가리키는 템플릿을 `DjangoTemplates` 엔진의 `DIRS`나 앱 디렉터리로 옮긴다 —
    다른 엔진의 `DIRS`도 같은 디렉터리를 보면 그 엔진이 찾지 못하는 곳으로 옮기거나 `TEMPLATES`에서 `DjangoTemplates`를
    앞에 둔다. 그리고 문법을 Django 템플릿으로 바꾼다 — 예를 들어 `{% for %}`의 `{% else %}`는 `{% empty %}`로, 템플릿
    안의 함수 호출은 함수 컴포넌트가 돌려주는 값이나 항목에 미리 계산해 넣는다. 다시 `manage.py check`로 W022가 없는지
    본다. 페이지 템플릿은 그대로 둬도 된다([호환성](./COMPATIBILITY.md#지원-범위)).
- **깨짐: Presence 프로듀서는 id를 주어 그린다(#212).** `PresenceMixin`을 쓰는 컴포넌트를 id 없이 그리면
  (`{% component 'ChatRoom' room=room %}`, `{% component_block %}`도) 그 태그가 어느 클래스인지와 고치는 법을 적은
  `TemplateSyntaxError`로 실패한다 — 페이지의 첫 렌더에서. 1.5에서는 동작했다. `manage.py check`의 `wireview.W023`이
  렌더가 불러올 템플릿에서 클래스 이름을 따옴표로 적은 태그를 미리 알린다 — 전부는 아니다(이름을 변수로 고르는 태그와
  체크가 찾지 못하는 템플릿은 첫 렌더가 알린다, [범위](./features/checks.md)). 테스트의 `mount()`도 id 없는 프로듀서는 `presence_join()`에서
  `ImproperlyConfigured`로 실패한다. 트래커(`PresenceTrackerMixin`만)는 그대로다.
  - 왜: Presence는 탭마다, 그 안에서 프로듀서마다 기록하고(아래 [Presence](#presence)) 프로듀서의 항목을 그 id로
    가리킨다. id는 같은 탭의 프로듀서 둘을 가르고, 재연결·다시 그리기에서 같은 항목을 잇는다. 프레임워크가 지어 주는
    id(`rx-…`)는 그릴 때마다 새것이고, sticky의 기본 id는 클래스에 하나뿐이다.
  - 옮기는 법: `manage.py check`의 W023이 가리키는 태그와 클래스 이름을 변수로 고르는 태그에 페이지 안에서 고유한 id를
    준다 — 방마다 하나면 `id="room-"|concat:room.id`, 한 페이지에 하나면 `id="composer"`. 테스트는
    `mount(ChatRoom, id="room", ...)`.
  - 배포 전에 열린 페이지: 1.5가 id 없이 그려 서명한 프로듀서의 상태는 1.6의 join을 통과한다 — 서명 상태는 그 id를
    템플릿이 주었는지 프레임워크가 지었는지 싣지 않는다. 그 태그를 다시 지나는 렌더(새로고침의 HTTP 렌더, 그 프로듀서를 그린
    컴포넌트의 다시 그리기)는 막으므로, 그 페이지는 그때 오류를 만난다.

### 보안

- **고쳐짐: 컴포넌트 템플릿의 `{% csrf_token %}`이 토큰을 그린다(#206).** 1.5까지는 첫 HTTP 렌더에서도 라이브 렌더에서도
  빈 값이라, 컴포넌트 안의 일반 POST 폼은 403을 받았다. 그래서 그 뷰에 `csrf_exempt`를 붙여 두었다면 떼고 폼에
  `{% csrf_token %}`을 둔다 — 그 예외는 그 뷰의 CSRF 보호를 통째로 끈 것이다. 무엇이 어디서 오는지는
  [dead view](./features/dead-view.md#폼의-csrf-토큰).
  - 조용히 달라짐: 컴포넌트 템플릿이 `{% csrf_token %}`을 그리는 페이지는 CSRF 쿠키를 받는다. 페이지 템플릿에서처럼
    응답에 `Set-Cookie`(`CSRF_USE_SESSIONS`면 세션 쓰기)와 `Vary: Cookie`가 붙는다. 그 페이지를 공용 캐시에 두었다면
    이제 사용자마다 나뉜다. 1.5에서는 그 태그가 아무것도 하지 않았으므로 쿠키도 없었다.
  - 컴포넌트에 `csrf_token`이라는 필드나 property가 있으면 지금처럼 그것이 템플릿의 `csrf_token`이다.
  - 조용히 달라짐: 테스트의 `mount()`도 쿠키를 가진 페이지처럼 그 폼에 `csrfmiddlewaretoken` 입력을 그린다. 값은
    `mount()`마다 다르므로, 그런 컴포넌트의 HTML을 통째로 비교하던 단언은 그 입력을 빼고 비교한다
    ([테스트의 주의사항](./features/testing.md#주의사항)).
  - 보안 조치: uvicorn을 `--ws-per-message-deflate false` 없이 띄우고 있다면 붙인다. 컴포넌트 렌더의 토큰은 연결마다
    하나라, 압축한 소켓에서는 다른 사용자가 정한 텍스트와 한 압축 문맥에 거듭 실린다(BREACH). 이유는
    [배포](./DEPLOYMENT.md#권장-uvicorn--uvloop), 토큰이 언제 다시 실리는지는
    [dead view](./features/dead-view.md#폼의-csrf-토큰).
- **고쳐짐: `Meta.shared_render`가 끼워 넣은 템플릿에서 보는 사람을 읽는 클래스를 공유하지 않는다(#208).** 1.3부터 1.5까지의
  공유 범위 판정(`wireview.W019`와 같은 것)은 컴포넌트의 템플릿 파일 하나만, 그것도 태그의 본문 일부만 보았다.
  그래서 `{% include %}`한 템플릿, `{% extends %}`의 부모, `takes_context`로 `user`를 넘겨받는 inclusion 태그의
  템플릿에서 `user`·`session`·`request`·`perms`·`messages`를 읽는 클래스가 공유 대상이었다. 자기 파일 안이라도
  `{% blocktranslate %}`의 본문(`{% blocktranslate %}{{ user }}{% endblocktranslate %}`)과, 본문을
  `child_nodelists`로 알리지 않는 서드파티 블록 태그의 본문, 인자를 자기 객체로 감싸는 태그(django-classytags류)의
  인자, 본문을 토큰째 가져가 렌더 때 컴파일하는 태그의 본문, wireview 자신의 `{% cond %}`·`{% class %}`의 식
  (`{% class {'staff': user.is_staff} %}`)에서 읽는 것도 그랬다. `VERIFY_SHARED_RENDER`가 꺼진 운영(`DEBUG = False`의 기본)에서는 같은 브로드캐스트를
  처리한 연결들이 **처음 렌더한 연결의 사용자로 그린 화면**을 받았다 — 다른 사람의 이름·권한별 화면이 보였을 수 있다.
  1.6은 렌더가 닿는 템플릿(`{% include %}`(`"파일#조각"` — Django 6의 것과 django-template-partials의 것 — 포함),
  `{% extends %}`의 부모, 함수 컴포넌트와 inclusion 태그의 템플릿)을 모두 걷고, 템플릿마다 소스를 파서로 다시 읽어
  태그가 어디에 두든 그 본문과 인자를, 태그가 토큰째 가져간 `{{ }}`와 `{% cond %}`·`{% class %}`의 식도 본다. 그리고 이름이 변수인 `{% include part %}`·`{% extends base %}`·
  `{% func kind %}`처럼 렌더 전에는 알 수 없는 템플릿을 불러오면 공유하지 않는다. 범위는 [shared_render](./features/shared-render.md#범위).
  - 보안 조치: `shared_render`를 선언한 클래스가 있으면 1.6에서 `manage.py check`를 돌린다. 새로 나온
    `wireview.W019`의 클래스가 이전 운영에서 다른 사람의 화면을 그렸을 수 있는 클래스다(이유에 `through <파일>`이
    붙는다). 그 화면이 민감했다면 그 기간의 노출을 따로 판단한다.
  - W019가 없다고 안전한 것은 아니다. property, 템플릿 태그·필터의 파이썬 코드(`takes_context` 태그가 컨텍스트에서
    꺼내는 것, 태그가 파서를 거치지 않고 스스로 만든 노드·변수), 다른 이름으로 받은 컴포넌트(`{% with viewer=this %}`)는 체크가 보지 못한다. 그런 클래스는 사용자 둘로
    브로드캐스트해 보는 테스트로 확인한다([잡지 못하는 것](./features/shared-render.md#잡지-못하는-것)).
  - 조용히 달라짐: 그런 클래스는 이제 연결마다 렌더한다. 결과는 맞아지지만 공유로 아끼던 시간은 없어진다 — 브로드캐스트
    한 번의 렌더 수가 연결 수만큼 는다. 보는 사람을 읽는 부분을 선언하지 않은 다른 컴포넌트로 빼면 다시 공유된다.
  - 조용히 달라짐: 이름이 변수인 include·extends·`{% func %}`를 쓰는 클래스도 공유하지 않는다. 공유하려면 이름을
    문자열 리터럴로 쓴다.

- **고쳐짐: 다른 탭이 신분을 바꾼 뒤의 boost 이동이 새 소켓을 연다(#209).** 1.5까지는 같은 브라우저의 다른 탭이
  로그인·로그아웃·다른 사용자로 로그인한 뒤 이 탭이 boost 이동하면, 새 페이지의 Django 부분은 새 신분이고 컴포넌트는
  소켓 핸드셰이크 때의 `user`(와 CSRF 시크릿)로 join했다. 이제 모든 `data-state`가 그 페이지의 신분 지문을 싣고, 다른
  연결에서 join하면 서버가 `renew`로 답해 페이지가 지금의 쿠키로 새 소켓을 연다. 할 일은 없다. 설명은
  [live_session](./features/live-session.md#다른-탭이-신분을-바꾼-뒤의-이동).
  - 조용히 달라짐: `data-state`가 40자쯤 길어진다(경계 밖 페이지도). 그리고 HTTP 렌더가 상태를 서명할 때 요청의
    세션을 읽는다 — 세션 쿠키가 있는데 그 전에 아무도 세션(`request.user` 포함)을 읽지 않은 페이지는 세션 백엔드
    조회가 한 번 늘고, 응답에 `Vary: Cookie`가 붙는다(Django의 `SessionMiddleware`가 읽힌 세션에 붙인다). 그런 페이지를
    `cache_page`나 CDN 같은 공유 캐시에 두었다면 이제 쿠키마다 나뉘거나 캐시되지 않는다. 세션 쿠키가 없는 요청(첫 방문,
    로그인하지 않은 방문자)은 세션을 건드리지 않고 익명으로 서명하므로 1.5와 같다.
  - 주의: 컴포넌트가 있는 페이지를 `Vary`를 무시하는 캐시(직접 짠 캐시, `Vary`를 지우는 CDN 설정)에 두면 그 HTML의
    `data-state`는 첫 요청자의 신분을 싣는다. 다른 방문자는 소켓을 한 번 더 열고, 로그인이 다르면 새로고침한다.
    그런 캐시에는 로그인한 사용자의 페이지를 두지 않는다.
  - 조용히 달라짐: 그 경우 훅은 `disconnected()`·`reconnected()`를 받고 `wire-auto-recover` 폼은 복구 이벤트를 보낸다 — 재연결과
    같은 길이다. 경계 안의 같은 경우는 전체 새로고침이었는데 이제 새 소켓이다.
  - 조용히 달라짐: `telemetry.join_rejected`의 `reason`에 `identity`가 더해졌다(닫힌 집합).
  - 조용히 달라짐: 새 소켓에서도 로그인이 다른 페이지는 전체 새로고침한다(`reload`, 사유 `identity`) — 다른 탭이 로그인한
    뒤 끊긴 소켓이 다시 붙을 때(배포), sticky 컴포넌트, 뒤로 가기가 보여 준 옛 본문이 그렇다. 1.5에서는 옛 사용자의
    상태가 소켓의 사용자 아래에서 복원됐다.
  - 점검: 세션·CSRF 쿠키의 `SESSION_COOKIE_PATH`·`CSRF_COOKIE_PATH`·도메인이 `/__wireview__`를 덮지 않으면 로그인한
    사용자의 컴포넌트는 마운트되지 않는다. 30초 넘게 간격을 둔 이동마다 소켓을 두 번 더 열고 한 번 더 새로고침하며,
    컴포넌트는 HTTP 렌더 그대로 흐리게 남아 클릭을 받지 않는다(새로고침 쿨다운이 그 사이를 막는다). CSRF 쿠키만 닿지
    않으면 이동마다 소켓을 한 번 더 연다. 어느 쪽이든 서버 로그에 연결마다 경고("do not reach the socket")가 한 번 남고
    (핸드셰이크에 그 쿠키가 실려 왔으면 쿠키보다 오래된 페이지의 정상 경로라 INFO 한 줄이다),
    `manage.py check`가 경로를 `wireview.W021`로 알린다(도메인은 보지 못한다). 그 설정에서는 원래 컴포넌트가 익명으로
    그려졌다 — 쿠키가 소켓에 닿게 고친다. `FORCE_SCRIPT_NAME`으로 서브 경로에 배포해도 소켓은 호스트의 루트
    `/__wireview__`에 열린다. 서브 경로 배포는 지원하지 않는다([호환성](./COMPATIBILITY.md)) — 쿠키 경로만 `/`로 바꾼다고
    지원되는 것이 아니다. 같은 호스트의 다른 앱과 나누려고 쿠키를 좁혀 두었다면, `/`로 넓힌 쿠키는 그 앱들에도 실린다.
  - 점검: WebSocket 라우트를 `AuthMiddlewareStack`(또는 `SessionMiddlewareStack`)으로 감싸지 않았다면 소켓에는 세션도
    사용자도 없다. 그 연결은 신분을 비교하지 않으므로 1.5처럼 동작하지만, 컴포넌트의 `user`는 늘 익명이다.
    `manage.py check`가 `wireview.W020`으로 알린다.
  - 알아 둘 것: 첫 방문에 CSRF 쿠키가 없었고 다음 이동의 페이지가 `{% csrf_token %}`을 그리면, 그 이동에서 소켓을 한 번
    다시 연다. 서명 키를 돌린 배포(`SIGNING_KEY_FALLBACKS`)는 다시 열지 않는다.

### 중첩 컴포넌트

- **조용히 달라짐: 그린 쪽이 더는 그리지 않는 중첩 컴포넌트는 그 렌더와 함께 은퇴한다.** `{% component %}`로 그린 컴포넌트를
  그린 쪽의 렌더가 더는 그리지 않으면, 이제 그 렌더가 끝날 때 브로드캐스트·패치·작업의 결과가 닿지 않고 구독이 빠진다(#204).
  전에는 페이지의 `leave`가 올 때까지 남아 있었다. id 없이 그린 것은 그린 쪽이 다시 그릴 때마다
  새 인스턴스이므로, 옛 것이 그 사이 알림을 받던 것이 멈춘다. 페이지가 그 렌더를 보기 전에 보낸 이벤트(방금 브로드캐스트가
  다시 그린 목록의 행을 누른 클릭)는 그대로 옛 인스턴스의 핸들러를 돌린다 — 그 render는 화면에 가지 않는다. `leaving()`과
  작업 취소는 1.5와 같이 페이지의 `leave`가 올 때다. 그래서 Presence는 1.5처럼 `leave`까지 그 컴포넌트를 센다.
  `leave`가 오지 않는 요소(`wire-update="ignore"` 안)는 은퇴하고 10초가 지난 뒤 끝나는 첫 렌더가 정리한다 — 그런 렌더가
  없으면 연결의 끝까지 남는다. 1.5에서는 언제나 연결의 끝까지 남아 그동안 센 것이 이제 그 렌더에서 끝난다. `{% if %}`로 숨겼다 다시 보인 컴포넌트는
  id가 있어도 언제나 새로 시작한다 — 전에는 페이지의 `leave`보다 다시 보이는 렌더가 먼저 오면 옛 상태(그 안의
  LiveComponent 포함)로 돌아왔다. 숨겨도 상태를 지켜야 하면 `{% if %}` 대신 그대로 그리고 CSS(`hidden`)로 감춘다
  ([설계 메모](./design/live-component-ownership.md#3-6-그린-쪽이-더는-그리지-않는-중첩-컴포넌트-204)).
- **조용히 달라짐: 인스턴스 없는 id로 온 이벤트에 답하고, `wire-update="ignore"` 안에 id 없이 그린 컴포넌트는 다시
  join한다.** 이 연결에 인스턴스가 없는 id로 온 이벤트(떠났거나 마운트가 거절된 것)는 전에 아무 답도 받지 않아 버튼의
  로딩 표시가 남았다. 이제 `gone`(프로토콜 `vsn` 9)으로 답하고, 옛 번들에는 핸들러가 없는 이벤트처럼 `diff: null`인
  render로 답한다. `wireview:error`는 나지 않는다
  ([프로토콜](./implementation/wire-protocol.md#3-outbound-세션--브라우저)).
  `wire-update="ignore"` 안에 id 없이 그린 컴포넌트가 이 길을 탄다: 페이지는 그 요소를 붙들지만, 그린 쪽의 렌더는
  새 인스턴스를 그리고 페이지의 것을 은퇴시킨다(#204). 그 요소의 다음 이벤트는 옛 인스턴스가 아직 남아 있으면 그
  핸들러를 돌리고(그 render는 화면에 가지 않는다), 남아 있지 않으면 핸들러 없이 `gone`으로 답받는다. 옛 인스턴스는
  은퇴하고 10초가 지난 뒤 끝나는 첫 렌더가 정리한다 — 렌더가 없으면 연결의 끝까지 남는다. 어느 쪽이든 로딩 표시가
  풀리고 페이지가 그 요소를 그 상태(`data-state`)로 다시 join해 다음 이벤트부터 새 인스턴스가 답한다. `joined()`도
  다시 돈다. 전에는 옛 인스턴스가 그대로 답했다. 그 안의 컴포넌트에는 id를 준다 — 같은 id로 매번
  그리면 같은 인스턴스가 이어진다([훅](./features/hooks.md#렌더가-건드리지-않게-wire-updateignore)).
  핸들러가 있는 중첩 컴포넌트를 목록처럼 자주 다시 그리는 곳에 둘 때도 id를 주는 것이 낫다: id 없이 그린 행은
  렌더마다 새 인스턴스라, 그 행에서 시작한 상태는 다음 렌더에 사라진다.
- **조용히 달라짐: 부모의 렌더가 새로 그린 중첩 컴포넌트는 `joined()` 뒤에야 알림을 받는다.** `{% component %}`로 새로
  그린 인스턴스는 그 렌더부터 연결에 있고 페이지의 join은 그 렌더를 적용한 뒤에 온다 — 보통 한 왕복 뒤, 숨은 탭은
  렌더를 적용하지 않으므로 다시 보일 때까지(몇 분, 몇 시간). 그 사이에 구독한 채널로 온 알림이 전에는
  `joined()`보다 먼저 `notification()`·`mutation()`을 돌렸고, 그 결과는 곧이어 오는 join이 페이지가 가진 상태로 덮어써
  화면에서 사라졌다(#207). 이제 그 알림은 인스턴스에 모였다가 `joined()`와 join의 렌더 뒤에 온 순서대로 넘어간다.
  그 컴포넌트가 그리는 LiveComponent도 같다. 같은 사이에 온 URL 변경(`params_changed()`)은 건너뛴다 — join이
  `joined()` 뒤에 그때의 쿼리로 부른다. `joined()`에서 초기화하는 상태를 `notification()`에서 읽어도 된다.
  모이는 양에는 끝이 있다: 1000개를 넘도록 페이지가 join하지 않는 인스턴스는(숨은 탭이 오래 그렇게 있거나,
  `wire-update="ignore"` 안에 새로 그린 `{% component %}`처럼 페이지가 join하지 않는 것) `leaving()` 없이 은퇴하고
  경고 로그가 남는다. 시간으로는 은퇴시키지 않는다. 전에는 그런 인스턴스가 연결이 끝날 때까지 알림마다
  `notification()`을 돌렸다. 은퇴한 뒤에 join이 오면 페이지가 가진 상태로 새로 시작하고 모였던 알림은 받지 않는다 —
  화면이 그 사이의 알림을 반영해야 한다면 `joined()`에서 현재 값을 읽는다.

### Presence

- **조용히 달라짐: Presence는 탭마다 기록한다.** 같은 사용자가 탭을 여럿 열었으면 마지막 탭이 떠날 때에야
  다른 사람의 목록에서 빠진다(#202). 정확히는 프로듀서마다(탭 + 컴포넌트 id) 기록하므로, 프로듀서가 둘인 페이지가
  하나를 숨겨(`presence_leave()`) 다른 하나가 입장해 있으면 그 사용자는 목록에 남는다. 그래서 프로듀서에는 id가
  있어야 한다(위 [깨짐](#깨짐), [프로듀서는 id로 그린다](./tutorials/07-presence-api.md#프로듀서는-id로-그린다)). 전에는 탭 하나가 떠나면 남은 탭이 있어도 빠졌고, 한 탭의 입력 상태가 다른 탭의
  것을 덮었다. `presence_users`는 그대로 사용자마다 한 항목이고, 탭 중 하나라도 입력 중이면 입력 중이다
  ([합치는 규칙](./tutorials/07-presence-api.md#탭이-여럿인-사용자)). 트래커 템플릿은 고칠 것이 없다.
  - 페이지는 불러올 때 만든 이름을 소켓 URL의 `tab`으로 보내고 재연결해도 같은 이름을 쓴다. 그래서 서버가
    `leaving()` 없이 끝나도(daphne·Granian의 종료, 크래시) 다른 프로세스로 재연결한 탭의 입장이 남은 항목을 덮고, 그
    탭이 닫힐 때 빠진다. **재연결하지 않은 탭의 항목은 남는다** — 1.5까지도 있던 한계다. 어느 서버도 종료할 때 퇴장을
    보장하지 않는다(아래 [연결의 끝](#연결의-끝)). 덜 놓치려면 소켓을 닫아 `leaving()`을 돌리는 uvicorn으로
    드레인한다([SIGTERM 드레인 절차](./DEPLOYMENT.md#sigterm-드레인-절차)).
  - **롤링 배포 중에는** 새 프로세스가 옛 프로세스의 메시지(`presence_ref`가 없다)를 그 사용자의 탭 하나로
    받는다 — 옛 프로세스에 붙은 탭들은 예전처럼 하나로 셈해지고, 새 프로세스에 붙은 그 사용자의 탭은 그것과 따로
    남는다. 옛 프로세스는 새 키를 읽지 않으므로 옛 프로세스가 그리는 목록은 모든 프로세스가 올라갈 때까지
    사용자 단위 그대로다. 오류는 나지 않는다. 옛 프로세스에서는 트래커가 sync 요청에 답하므로, 그 프로세스의 트래커만
    있는 페이지는 새 프로세스의 목록에 올라 닫혀도 내려가지 않는다 — 그 목록의 트래커가 다시 join하면(새로고침)
    사라진다. 옛 `wireview.min.js`로 열린 페이지(이름을 보내지 않는다)는 연결을
    탭으로 셈한다 — 새로고침하면 새 번들로 바뀐다.
  - `notification()`을 오버라이드해 presence 메시지를 직접 읽던 컴포넌트는 키를 더 받는다: 입장·퇴장·입력·sync
    응답의 `presence_ref`(탭과 프로듀서의 컴포넌트 id)와 `presence_speaker`(그 메시지를 보낸 프로듀서 인스턴스의 이름, 인스턴스마다 새것), sync 요청의
    `requester_ref`(요청한 트래커의 탭). 내부 이름 `_presence_registry`는 채널 → 사용자 → 프로듀서 → 항목이 됐다
    (트래커 자기 항목은 탭 키). 트래커의 자기 항목은 같은 탭 프로듀서의 항목과 따로 있어, 프로듀서가 떠나도 자기 목록에서
    자기가 빠지지 않는다. 그것을 직접 읽던 코드(테스트 포함)는
    `presence_get_user()`·`presence_users`·`presence_users_in()`을 쓴다.
  - 프로듀서를 페이지가 숨겼다 곧 다시 보이면 다시 보인 것은 같은 id의 새 인스턴스이고, 숨긴 것과 같은 항목을 쓴다.
    숨긴 것의 퇴장은 그 은퇴가 끝날 때(아래 [중첩 컴포넌트](#중첩-컴포넌트)) 오므로 다시 보인 것의 입장보다 늦을 수
    있다. `presence_speaker`가 인스턴스마다 다르므로 그 늦은 퇴장은 버려지고 사용자는 목록에 남는다. 할 일은 없다.
- **조용히 달라짐: `leaving()`이 퇴장하지 못한 프로듀서는 프레임워크가 퇴장시킨다**(#212). 입장해 있는 프로듀서의
  마지막 `leaving()`이 `presence_leave()` 전에 예외를 던졌거나 그것을 부르지 않았으면, 그 뒤 퇴장 메시지를 보내고
  입력 타이머를 멈춘다. 전에는 그 사용자가 탭을 닫아도 다른 사람의 목록에 남았다. `leaving()`이 이미 퇴장시켰으면
  다시 보내지 않는다. 묘비로 남은 동안이 아니라 마지막 `leaving()` 뒤다([중첩 컴포넌트](#중첩-컴포넌트)). 연결이 끝날 때
  `leaving()`이 시한을 넘겼거나 퇴장을 발행하던 도중에 끊긴 것도 같다(아래 [연결의 끝](#연결의-끝), #211).
  `leaving()`에서 일부러 퇴장하지 않아 연결이 끝난 뒤에도 목록에 남기던 코드는 없어야 정상이지만, 있다면 이제 빠진다.
  `mount()`는 `leaving()`을 부르지 않으므로 테스트에서 달라지는 것은 없다.
- **조용히 달라짐: 다른 사람의 목록에 올리는 것은 프로듀서다.** 나중에 들어온 트래커의 sync 요청에 답하는 것이
  트래커가 아니라 입장해 있는 프로듀서(`PresenceMixin`)다(#202). 그래서 프로듀서와 트래커가 형제 루트로 따로 join되는
  페이지, 트래커가 예외 뒤에 다시 join된 페이지, sticky 프로듀서가 boost 이동으로 옮겨 간 페이지도 나중에 온 사람의
  목록에 오른다.
  - 트래커(`PresenceTrackerMixin`)만 있고 `presence_join()`을 부르는 프로듀서가 없는 페이지는 이제 다른 사람의
    목록에 오르지 않는다. 그런 페이지는 들어왔다고도 떠났다고도 알리지 않는데, 나중에 들어온 사람의 sync 요청에
    답해서 그 사람의 목록에만 올랐고 닫혀도 내려가지 않았다. 보는 사람도 목록에 올리려면 그 페이지에
    `presence_join()`·`presence_leave()`를 부르는 컴포넌트를 둔다.
  - 프로듀서의 연결은 `presence_join()`부터 `presence_leave()`까지 그 프로듀서의 presence 채널을 듣는다.
    `get_subscriptions()`가 무엇을 돌려주든 그렇고, 컴포넌트의 `get_subscriptions()`·`notification()`은 앱이 쓴
    그대로다. 거기서 들은 것은 프레임워크가 처리한다 — 프로듀서의 `notification()`은
    전처럼 그 메시지를 받지 않고, 렌더도 없다. 코드를 고칠 일은 없다. 그 채널을 직접 구독하던 프로듀서는 전처럼
    `notification()`이 받는다.
- **조용히 달라짐: 토픽 여럿을 듣는 트래커는 토픽마다 기록한다.** 한 방에서 떠난 사용자가 다른 방에 남아 있으면
  그대로 목록에 있다(#202). 전에는 방 하나의 퇴장이 그 사용자를 통째로 지웠다. `presence_users`와 그것을 세는
  속성은 모든 토픽을 합쳐 사용자마다 한 번이고, 토픽 하나만 보려면 새 메서드 `presence_users_in(topic)`을 쓴다
  ([여러 토픽 구독](./tutorials/07-presence-api.md#여러-토픽-구독)).

### 연결의 끝

- **조용히 달라짐: 연결이 끝날 때의 `leaving()`은 모두 합쳐 5초 안에 끝나야 한다(#211).** 서버가 앱을 취소했거나
  앱이 예외로 끝나 `disconnect`가 오지 않은 연결도 이제 끝에 한 번 정리된다 — 1.5까지는 PatchHub의 항목·채널 그룹·
  `start_async` 작업·presence가 프로세스에 남았다. 그 정리가 `leaving()`에 막히지 않도록:
  - 한 연결의 `leaving()`들에 합쳐 5초(`wireview.session.LEAVING_SECONDS`)를 준다. 넘으면 돌던 것을 취소하고 남은 것은
    부르지 않는다(경고 로그). 오래 걸리는 정리(외부 API 호출, 큰 쓰기)는 `leaving()`에서 기다리지 말고 작업 큐로 넘긴다.
    페이지 이동·부모가 뺀 컴포넌트의 `leaving()`에는 시한이 없다.
  - `joined()`에서 `presence_join()`한 프로듀서가 연결이 끝날 때까지 `presence_leave()`하지 않았으면(`leaving()`이
    던졌거나 시한을 넘겼거나 — 퇴장을 발행하던 도중이었어도 — 부르지 않았으면) 프레임워크가 대신 떠난다. 그래서
    다른 탭의 목록에 퇴장이 간다.
  - daphne의 close timeout에서도 `leaving()`과 `connection_closed`가 돈다(1.5까지는 돌지 않았다). daphne의 종료에서는
    `leaving()`이 시작되지만 끝까지 돈다는 보장은 없다 — daphne는 먼저 끝난 연결 하나만 기다리고 내려간다. uvicorn도
    `--timeout-graceful-shutdown`을 넘기면 기다리지 않는다. **종료할 때의 presence 퇴장과 `leaving()`의 마지막 쓰기에
    의존하지 않는다** — 잃으면 안 되는 쓰기는 핸들러에서 한다.
    `disconnect` 없이 끝난 연결의 `connection_closed`는 `code`가 `None`이다 — 닫힘 코드로 지표를 나누면 그 값을 둔다.
    언제 그런 끝이 오는지와 드레인 순서는 [배포](./DEPLOYMENT.md#sigterm-드레인-절차).

### 스트림과 무한 스크롤

- **조용히 달라짐: `wire-viewport-*`가 더 자주 불린다.** 자기 스크롤바가 있는 상자(`overflow-y: auto`) 안의 바인딩은
  1.5에서 창 가운데를 지나야만 불렸다(#199). 이제 상자를 그쪽으로 스크롤하면 불리고, 창이 그쪽으로 움직여도 불린다.
  "지나침"은 실제로 넘치는 가장 가까운 상자의, 창에 보이는 부분에서 잰다. `overflow-x: hidden`만 둔 래퍼처럼 넘치지
  않는 조상은 상자가 아니라 창으로 친다. 상자 안의 무한 스크롤이 불리지 않아 이를 **우회하는 JS 훅**(상자의 `scroll`을
  들어 `pushEvent`하던 것)을 두었다면 지운다 — 두면 같은 스크롤에 핸들러가 두 번 불린다
  ([무한 스크롤](./tutorials/06-streams-api.md#무한-스크롤-wire-viewport-top--wire-viewport-bottom)).
- **프로토콜 10과 롤링 배포.** `stream_insert(..., update_only=True)`와 항목 여럿인 `stream(..., reset=False)`는
  프로토콜 10의 형태다(#199). 옛 번들(`vsn` 9 이하)로 열린 페이지에는 다르게 간다. 새로고침하면 새 번들로 바뀐다.
  - `update_only` 삽입은 보내지 않는다. 그 항목을 보이던 옛 페이지는 다시 join할 때까지 고치기 전 내용을 보인다 — 자리는
    맞고 낡았을 뿐이다.
  - `stream(..., reset=False)`는 `at`이 -1이 아니면 서버가 항목을 거꾸로 보내 옛 클라이언트에서도 순서가 맞는다. 단
    `at`이 길이를 넘으면 그 항목들이 거꾸로 선다.
  - 배포가 섞인 동안 옛 프로세스에 붙은 페이지는 새 프로세스가 발행한 `update_only` 삽입을 보통 삽입으로 받아, 그 항목을
    불러오지 않은 목록의 `at` 자리에 넣을 수 있다. 옛 프로세스는 프레임에 붙은 최소 버전을 읽지 않는다. 배포가 끝난 뒤
    다시 join하면 바로잡힌다([wire-protocol §7](./implementation/wire-protocol.md#7-버전)).
- **읽어 붙이는 목록은 QuerySet을 `stream(..., reset=False)`에 넘긴다.** 핸들러가 목록을 읽어 `stream_insert`를
  반복하면, 읽는 동안 발행된 Broadcast delete나 `update_only` 수정이 먼저 쓰이고 뒤에 나가는 삽입이 그것을 되돌린다 —
  지운 항목이 되살아난다(#199). 1.5와 같고, `stream_insert`는 1.6에서도 패치를 잡지 않는다. 1.6의
  `stream(queryset, reset=False)`는 reset처럼 읽기 전부터 그 컴포넌트의 Broadcast 패치를 잡아 두고 삽입이 소켓에 쓰일 때
  놓는다. 비용도 reset과 같다. 잡힌 패치는 `stream()`을 부른 핸들러(`joined()`·`notification()`·`handle_async` 포함)가
  끝나고 그 연산이 쓰일 때 나가므로, 그 뒤에 오래 걸리는 일이 있으면 그만큼 늦는다. 그런 일은 `start_async`로 넘기거나
  `stream()`을 핸들러 끝에 둔다. 그동안 잡힌 프레임이 1,000개(`QUEUE_LIMIT`)를 넘으면 연결이 닫히고(1013) 페이지가
  다시 join한다([Broadcast — 순서와 유실](./features/broadcast.md#순서와-유실)).

### 템플릿

- **고쳐짐: 컴포넌트 템플릿의 `{% querystring %}`이 동작한다(#210).** 1.5까지는 첫 HTTP 렌더에서도 라이브 렌더에서도
  `AttributeError`로 실패했다(라이브에서는 join이 실패했다). 이제 지금 페이지의 쿼리 — 첫 렌더는 요청의 것, 라이브 렌더는
  페이지가 마지막으로 알린 주소의 것 — 에서 시작한다. 쿼리를 필드로 옮겨 링크를 만들던 우회는 그대로 둬도 된다.
  설명은 [내비게이션](./features/navigation.md#컴포넌트-템플릿의-querystring-태그).
  - 달라짐: 컴포넌트 템플릿을 그리는 컨텍스트에 `context.request`가 생겼다. `GET`만 가진 객체다. 1.5에서
    `getattr(context, "request", None)`으로 요청이 없음을 알아채고 넘어가던 서드파티 태그가 이제 그 객체를 받아
    `request.user`·`request.path` 같은 다른 속성을 읽으면 이유를 적은 `AttributeError`로 실패한다 — 첫 HTTP 렌더에서
    바로 보인다. 그런 태그에는 컴포넌트가 읽은 값을 인자로 넘긴다. `AttributeError`를 잡는 `{% url %}`은 1.5와 같다.
  - 달라짐: 그런 태그가 `GET`을 읽으면 `Broadcast` 항목 템플릿(언제나)과 검사가 켜진 공유 렌더(`VERIFY_SHARED_RENDER`,
    기본은 `DEBUG`)에서 이제 `BroadcastRenderError`·`SharedRenderError`다. 1.5에서는 요청이 없는 것으로 알고 넘어갔다.
    그 자리의 쿼리는 보는 사람마다 다르다 — 항목에는 `item`으로, 공유 렌더에는 필드로 넘긴다. 검사가 꺼진 운영의 공유
    렌더에는 `context.request`가 없어 1.5와 같다.
  - `shared_render`를 선언한 클래스의 템플릿에 페이지의 쿼리를 읽는 `{% querystring %}`이 있으면 `wireview.W019`가 알리고
    공유하지 않는다. 1.5에서는 그 템플릿이 렌더되지 않았으므로 새로 공유를 잃는 `{% querystring %}`은 없다. Django 5.2의
    `{% querystring None page=2 %}`도 읽음으로 본다.
### JS 명령

- **조용히 달라짐: `JS()` 명령이 바꾼 것이 렌더 뒤에도 남는다(#219).** 명령이 더하거나 뺀 클래스, 바꾼 속성, 보이거나
  숨긴 상태는 그 요소를 다시 그리는 렌더가 템플릿대로 되돌리지 않는다 — Phoenix LiveView와 같다. 1.5에서는 그 요소를
  건드리는 다음 렌더가 지웠다. 그 지움에 기대던 코드를 찾는다:
  - `JS().add_class("#btn", "loading").push("save")`처럼 응답까지만 보이려던 클래스는 이제 응답 뒤에도 남는다.
    [로딩 클래스](./features/optimistic-ui.md#로딩-클래스)(`.wireview-click-loading` 등)와 `wire-disabled-with`로 바꾼다.
  - 템플릿이 필드로 그리는 클래스·속성·`hidden`을 명령으로도 바꾸면, 명령 뒤로는 그 필드의 변화가 그 부분에 보이지
    않는다. 한쪽으로 정한다([서버의 상태와 겹칠 때](./features/js-commands.md#서버의-상태와-겹칠-때)).
- **달라짐: 선택자가 맞는 요소 전부에 적용된다(#219).** 1.5에서는 첫 요소에만 적용됐다. `focus`·`focus_first`는
  그대로 첫 요소다. 여러 요소와 맞는 선택자를 쓰면서 첫 요소만 바뀌기를 기대했다면 선택자를 좁힌다.

### 테스트

- **조용히 달라짐: 테스트의 `mount()`가 같은 프로세스의 알림을 받는다.** 구독한 채널에 온 `abroadcast()`·`broadcast()`·
  `self.broadcast()`·`atoast()`·auto-broadcast의 모델 변경이 페이지처럼 `notification()`·`mutation()`을 돌리고
  렌더한다(#201). 알림을 보낸 뒤 `view.component.notification(...)`을 직접 부르던 테스트는 이제 받는 메서드가 두 번
  돈다 — 직접 부르기를 지운다. Presence 프로듀서와 트래커를 함께 마운트한 테스트는 서로의 입장·퇴장·sync를
  페이지처럼 듣는다 — `presence_broadcasts`를 꺼내 트래커의 `notification()`에 넘기던 것도 지운다. 렌더도 함께 돌므로 템플릿이 없는 테스트용 컴포넌트는 `TemplateDoesNotExist`로
  실패하고, 그 렌더는 `render_diff()`와 같은 경로라 pytest-django에서는 `django_db` 표시가 없는 테스트가 DB 접근
  오류로 실패한다. `sent_messages`를 통째로 비교하던 테스트는 받은 쪽의 `render`와 메일이 더해질 수 있다
  ([알림 받기](./features/testing.md#알림-받기)).
  - **알림 뒤의 `render_diff()`가 `None`일 수 있다.** 받은 쪽의 렌더가 페이지처럼 기준선을 옮긴다.
    1.5에서는 `mount()` → `abroadcast(...)` → `render_diff()`가 전체 렌더(곧 `None`이 아님)였지만, 이제 알림이 그린
    것은 `sent_messages`의 `render`에 있고 그 뒤의 `render_diff()`는 알림 뒤의 변화만 본다. "브로드캐스트 뒤
    `render_diff() is not None`"이나 diff에 새 값이 있는지를 단언하던 테스트는 `sent_messages`의 `render`나
    `render()`의 HTML로 단언한다. 상태를 바꾸지 않는 알림은 `render`를 남기지 않는다(페이지에도 가지 않는다).
    `render_diff()`로 기준선을 잡은 뒤 `call()`한 이벤트가 있으면, 알림은 세션처럼 그 이벤트의 렌더를 먼저 해
    `render`로 남긴다 — 이벤트가 바꾼 것은 그 `render`에 있고 알림의 것에 섞이지 않는다.
  - **받는 쪽의 예외가 발행한 쪽에서 올라온다.** 알림을 받는 컴포넌트를 마운트해 둔 테스트에서
    그 `notification()`·`mutation()`이나 렌더가 던지면, 발행한 `abroadcast()`, `self.broadcast()`를 부른 다른
    컴포넌트의 `call()`, auto-broadcast라면 행을 쓴 `acreate()`·`save()`가 그 예외로 실패한다. 1.5에서는 받지
    않았으므로 통과했다. 받는 메서드를 고치거나, 그 테스트에서 받는 컴포넌트를 마운트하지 않는다.
  - **자기가 듣는 채널에 보내는 컴포넌트는 자기 알림을 받는다.** 핸들러나 `joined()`의
    `self.broadcast()`가 자기 구독 채널로 가면 그 컴포넌트의 `notification()`도 돈다 — 브라우저와 같다. 핸들러에서
    목록에 한 번 넣고 `notification()`에서 또 넣는 컴포넌트는 테스트에서 항목이 둘이 된다(브라우저에서도 둘이었다).
    `self.broadcast()`는 기록만 하지 않고 같은 프로세스의 채널 레이어로도 나가므로 같은 프로세스의 소켓도 받는다.
  - **마운트한 컴포넌트의 `self.broadcast()`가 테스트 설정의 채널 레이어로 나간다.** 1.5에서는 `view.broadcasts`에
    기록만 했다. 테스트 설정의 `CHANNEL_LAYERS`가 redis·nats를 가리키는데 그 서버가 떠 있지 않으면, 그것을 부른
    `call()`이 레이어의 연결 오류로 실패한다 — 실제 연결도 그렇게 실패하므로 삼키지 않는다. 단위 테스트 설정은
    InMemory 레이어(`channels.layers.InMemoryChannelLayer`)이거나 `CHANNEL_LAYERS` 없이 둔다. 테스트 설정이 개발
    서버와 같은 redis를 가리키면 테스트의 `self.broadcast()`가 그 서버에 열린 페이지에도 간다.
- **조용히 달라짐: 핸들러의 메일이 핸들러가 끝난 뒤 `sent_messages`에 들어간다.** 연결처럼, `call()`·`joined()`·알림
  처리 중에 보낸 스트림 연산·`push_event`·JS·내비게이션은 그것이 끝날 때 기록되고, 그 사이에 온 `Broadcast` 패치는
  그 앞에 든다(#205). 핸들러가 도는 동안 `sent_messages`를 들여다보던 코드는 아직 비어 있는 목록을 본다. 패치와
  핸들러의 연산 순서를 단언하던 테스트는 브라우저가 받는 순서로 고친다
  ([핸들러의 메일과 Broadcast 패치의 순서](./features/testing.md#핸들러의-메일과-broadcast-패치의-순서)).
- **조용히 달라짐: 다른 경로로 `follow_push(Destination)`한 뒤 원래 인스턴스는 언다.** 브라우저에서 떠난 페이지의
  컴포넌트처럼 `view.is_frozen`이 참이고 알림·`Broadcast`를 더 받지 않는다. 같은 `live_session` 안의 sticky
  컴포넌트만 남는다. 이동한 뒤에도 원래 `view`로 `call()`을 이어 가던 테스트는 목적지 컴포넌트로 옮긴다.
- **새로: pytest 플러그인 `wireview`.** wireview를 설치하면 pytest가 진입점(`pytest11`)으로 읽는다. 테스트가 끝나면
  그 테스트가 마운트한 컴포넌트가 듣기를 멈춘다. 모듈·클래스·세션 범위 픽스처와 unittest의 `setUpClass`가
  마운트한 것은 그 픽스처가 정리될 때 멈추므로, 그것을 함께 쓰는 테스트는 1.5처럼 모두 `Broadcast`를 받는다.
  끄려면 `-p no:wireview`. 플러그인을 읽으면 패키지 `wireview`가 import되고 pytest가 그 패키지의 `assert`를
  다시 쓴다(assertion rewriting) — 이것까지 피하려면 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`
  ([알림 받기](./features/testing.md#알림-받기)).

## 1.4에서 1.5로

- **조용히 달라짐: `queries()`가 이벤트 루프 위에서 그린 템플릿의 마운트를 센다.** async 테스트나 async 뷰가 루프에서
  템플릿을 바로 그리면 `{% component %}`는 마운트를 풀 스레드에서 돌린다. 1.4에서는 그 스레드에 `queries()` 블록이
  넘어가지 않아 마운트 훅(`on_mount`)·`params_changed()`의 SQL이 빠졌다(#193). 이제 블록이 그것도 센다. 그런 테스트가
  `q.count`의 상한을 단언했다면 마운트의 쿼리만큼 늘 수 있다 — 숫자를 고치거나, 마운트를 따로 단언한다
  ([렌더 SQL](./features/render-queries.md#테스트에서-단언하기)).

## 1.3에서 1.4로

- **첫 HTTP 렌더가 요청의 DB 연결을 닫지 않는다.** 1.3에서는 `ATOMIC_REQUESTS`(또는 `transaction.atomic()`)
  안에서 렌더한 페이지의 `params_changed()`·마운트 훅이 `stream()`을 부르거나 Channels의 `database_sync_to_async`를
  기다리면, 또는 async property가 그렇게 하면, 뷰가 렌더 전에 한 쓰기가 예외 없이 롤백되고 `on_commit`이 사라졌다
  (#190). 응답은 200이었다. 이제 그 쓰기가 커밋된다. 같은 수정으로 **첫 HTTP 렌더의 `stream()`·`stream_insert()`·
  `stream_delete()`는 아무것도 하지 않는다** — 받을 소켓이 없어 전에도 버려지던 연산이므로 화면은 같고, 항목 템플릿과
  넘긴 목록(QuerySet)을 이제 읽지 않을 뿐이다. 이 함정을 피하려고 `joined()`에서 플래그를 세우고 소켓이 없으면
  `stream()`을 건너뛰던 코드가 있으면 지워도 된다. 첫 화면의 목록은 전처럼 `wire-stream` 안의 `{% for %}`가 그린다
  ([JavaScript 없는 첫 렌더](./features/dead-view.md#첫-렌더의-훅과-데이터베이스)).
- **조용히 달라짐: 개발 서버가 `BASE_DIR/.wireview/render-queries/`에 파일을 쓴다.** `DEBUG`가 켜져 있으면 렌더마다
  실행한 SQL과 그것을 부른 템플릿 줄을 JSON 줄로 남긴다. VS Code 확장이 그것을 읽어 인라인 힌트로 보인다(#188).
  운영(`DEBUG` 꺼짐)과 `wireview.testing`을 import한 테스트 프로세스는 쓰지 않는다. 스타터 프로젝트의 `.gitignore`는
  이미 `.wireview/`를 뺀다. 직접 만든 프로젝트라면 `.wireview/`를 `.gitignore`에 더한다. 다른 곳에 두려면
  `DEBUG_RENDER_QUERIES_DIR`에 경로를, 끄려면 `False`를 준다
  ([렌더 SQL](./features/render-queries.md#편집기로-보내기)).

## 1.2에서 1.3으로

- **조용히 달라짐: 첫 HTTP 렌더도 `params_changed()`를 부른다.** 쿼리가 있는 주소(`/search/?q=장고`)를 열면 서버는
  컴포넌트를 마운트한 뒤 그 쿼리로 `params_changed()`를 부르고 나서 그린다. Phoenix의 dead render가 mount →
  handle_params → render인 것과 같고, 1.2부터 join이 하던 순서다. 1.2까지 첫 응답은 쿼리가 없는 것처럼 그려지고
  join 뒤에야 채워졌으므로, JavaScript 없는 브라우저와 검색엔진은 빈 화면을 보았다. 루트, 그 안의 `{% component %}`,
  슬롯 안의 컴포넌트, 함수 컴포넌트가 그린 컴포넌트, sticky 컴포넌트, LiveComponent 모두 같다. `on_mount` 훅이 거절한
  컴포넌트는 듣지 않는다. 고칠 것은 셋이다.
  - **`params_changed()`는 이제 한 페이지 로드에 두 번 돈다.** HTTP 렌더에서 한 번, join에서 한 번이다. Phoenix의
    `handle_params`도 그렇다. 같은 params로 두 번 돌아도 결과가 같아야 한다 — 필드를 params에서 다시 계산하고, 메일 발송·
    카운터 증가·로그 기록 같은 부수 효과는 핸들러로 옮긴다. join은 첫 응답이 그린 상태가 아니라 마운트 상태에서
    params를 다시 듣는다 — HTML은 `params_changed()`가 만든 상태를 그리지만 `data-state`에는 그 전의 상태가 서명된다.
    그래서 `if q == self.q: return`처럼 자기 필드와 비교하는 가드가 있어도 join의 `params_changed()`는 처음부터 돈다.
    HTTP 렌더에서 시작한 `assign_async()`·`start_async()`는 연결이 없어 돌기 전에 취소되고, 페이지는 로딩 상태로
    그려진 뒤 join이 다시 시작한다. `Meta.temporary_assigns`·`Meta.exclude_fields`에 둔 결과도 join이 다시 채운다.
  - HTTP 렌더에서는 `joined()`가 돌지 않는다. `params_changed()`가 `joined()`에서 준비한 것(`allow_upload()`,
    `joined()`에서 채운 필드)에 기대고 있었다면, 그것이 없을 때도 돌게 한다. 쿼리로 정해지는 것은 `params_changed()`에서,
    연결이 있어야 하는 일은 `joined()`에서 한다.
  - `params_changed()`가 예외를 던지면 그 페이지의 응답이 실패한다. `on_mount` 훅이 던질 때와 같다. LiveComponent의 것은
    소켓에서처럼 로그로 남고 자식은 그대로 그려진다.
  ([JavaScript 없는 첫 렌더](./features/dead-view.md), [내비게이션](./features/navigation.md#첫-응답과-join))
- **퍼센트 인코딩 없이 온 쿼리를 Django처럼 읽는다.** HTTP 렌더는 쿼리를 `request.META["QUERY_STRING"]`에서 직접 풀었으므로,
  WSGI 서버가 받은 `?q=파이썬`(인코딩하지 않은 UTF-8)이 깨진 글자로 `params`와 `on_mount` 훅에 갔다. 이제
  `request.GET`에서 읽는다. 깨진 값을 되돌리던 코드가 있으면 지운다. 같은 변경이 둘을 더 바꾼다.
  - **빈 값이 남는다.** `?q=`나 `?flag`를 열면 HTTP 렌더의 params가 이제 `{"q": ""}`·`{"flag": ""}`다. 전에는 빠졌다.
    join(브라우저의 `URLSearchParams`)이 늘 그렇게 읽었으므로 두 쪽이 같아졌다. `on_mount` 훅이 받는 `params`도 같고,
    빈 값만 있는 주소에서도 `params_changed()`가 돈다. `"q" in params`로 검색어가 있는지 보던 코드는 값이 비었는지도 본다.
  - **컨텍스트의 `request`에 `.GET`이 있어야 한다.** 템플릿을 직접 렌더하는 테스트가 `request`에 `META`만 가진 가짜
    객체를 넣었다면 이제 `AttributeError`다. `RequestFactory().get(...)`로 만든 요청을 넣는다.
- **브로드캐스트 메시지에 `message_id`가 붙는다.** `abroadcast()`·`broadcast()`·컴포넌트의 `self.broadcast()`·Presence·모델 알림이 채널 레이어에 보내는 메시지에
  키가 하나 늘었다. 같은 메시지를 받은 연결들이 그것을 알아보고 `Meta.shared_render` 컴포넌트의 렌더를 함께 쓰게
  하려는 것이다([shared_render](./features/shared-render.md)). 앱이 할 일은 없다. 다만 `group_send`를 모의 객체로 바꾸고
  메시지 딕셔너리를 통째로 비교하던 테스트는 이제 실패한다. `unittest.mock.ANY`로 그 키를 받거나 필요한 키만 본다.
  1.2 프로세스와 섞여 돌아도 된다 — 옛 프로세스는 이 키를 읽지 않고, 새 프로세스는 키가 없는 메시지를 공유 없이 처리한다.
- **`manage.py wireview_lsp`가 메타데이터 형식 2.0을 낸다.** 컴포넌트의 `methods`에는 컴포넌트 자신의 코드가 정의한
  메서드만 남고, pydantic과 wireview가 정의한 메서드는 `inherited_methods`에 이름만, 최상위 `framework_methods`에 한
  번 적힌다. testproj의 출력이 6.9MB에서 0.5MB로 줄었다. VS Code 확장은 1.1과 2.0을 똑같이 읽으므로 할 일이 없다.
  그 JSON을 직접 읽는 도구가 있으면 `inherited_methods`를 `framework_methods`에서 찾아 펼친다
  ([편집기 지원](./features/editor-support.md#1x에서-20으로)).

## 1.1에서 1.2로

- **조용히 달라짐: 같은 경로로 가는 `push_to`는 페이지를 다시 가져오지 않는다.** 쿼리나 조각만 바꾸는 `push_to`
  (`"?page=2"`, 또는 지금 페이지와 경로가 같은 URL)는 Phoenix의 `push_patch`처럼 동작한다. 주소창과 기록 항목만
  바뀌고, 페이지의 컴포넌트(LiveComponent 포함)는 같은 인스턴스로 `params_changed()`를 받는다. 1.1까지는 그 URL을
  가져와 `<body>`를 바꾸고 모든 컴포넌트가 그 렌더로 다시 join했으므로, 이벤트로만 바꾼 상태가 처음 값으로
  돌아갔다. 이제는 남는다. 그렇게 생긴 항목 사이의 뒤로·앞으로 가기도 가져오지 않고 `params_changed()`만 돈다.
  다른 경로로 가는 `push_to`, 다른 페이지나 새로고침 전의 페이지가 남긴 항목으로 가는 뒤로 가기는 전처럼 가져온다.
  고칠 것은 둘이다.
  - **컴포넌트 밖의 템플릿이 `request.GET`을 읽는 페이지**(쿼리로 제목·사이드바·레이아웃을 고르는 페이지)는 patch
    뒤에 그 부분이 옛 쿼리로 남는다. 그 이동을 `redirect_to`로 바꾼다. `redirect_to`는 같은 경로여도 페이지를
    가져온다. 쿼리를 읽는 부분을 컴포넌트로 옮겨 `params_changed()`에서 읽어도 된다.
  - 다시 join되는 것에 기대던 컴포넌트, 즉 `push_to` 뒤에 상태가 처음 값으로 돌아가길 바라던 컴포넌트는
    `params_changed()`에서 그 상태를 직접 되돌린다.
  ([내비게이션](./features/navigation.md))
- **조용히 달라짐: 다른 경로로 가는 `replace_to`는 그 페이지를 가져온다.** 1.1까지는 주소창만 그 경로로 바꾸고
  옛 페이지를 그대로 두었으므로, 주소창이 이 화면을 그리지 않은 뷰를 가리켰다. 이제는 기록 항목을 늘리지 않는
  `push_to`와 같다: 같은 경로면 patch, 다른 경로면 그 자리에서 가져와 컴포넌트가 새로 join한다. 다른 경로로
  `replace_to`하면서 상태가 남길 바랐다면 같은 경로의 쿼리로 바꾼다.
- **조용히 달라짐(테스트): `mount(params=...)`와 `follow_push()`가 브라우저와 같게 동작한다.**
  - `mount(params={"page": "2"})`는 `joined()` 뒤에 `params_changed()`를 그 값으로 부른다. 쿼리가 있는 페이지의
    join이 하는 일이다. 1.1까지는 부르지 않았으므로, 그것을 손으로 부르던 테스트는 그 줄을 지운다(두 번 돈다).
  - `follow_push()`는 같은 경로면 전처럼 같은 인스턴스에 `params_changed()`를 돌린다. 다른 경로면 브라우저처럼
    대상을 새로 마운트하므로 그 컴포넌트를 넘긴다: `landed = await view.follow_push(Destination)`. 경계를 넘는
    push에 실패하던 것도 이제 대상을 마운트한다(그 경계가 사용자를 거절하면 실패한다).
  - `"/items/?page=2"`처럼 경로가 있는 목적지는 컴포넌트가 놓인 경로를 알아야 둘을 가린다:
    `mount(XList, path="/items/")`. 없으면 `follow_push()`는 1.1처럼 같은 인스턴스에 `params_changed()`를
    돌리고 `WireviewDeprecationWarning`을 낸다. 2.0에서는 실패하므로 경고가 나는 테스트에 `path=`를 더한다.
  ([testing](./features/testing.md#push를-따라가기))
- **`mount(path=...)`는 페이지의 경로다.** 1.1에는 이 옵션이 없어서 `path=`가 같은 이름의 필드 값으로
  들어갔다. 이제 `path`라는 필드가 있는 컴포넌트에 `path=`를 주면 `TypeError`가 난다. 필드 값은
  `state={"path": "/a/b/"}`로 준다. `state=`에 필드를 두면 `path=`는 페이지의 경로로 함께 줄 수 있다.
  `follow_redirect()`·`follow_push(Destination)`의 키워드는 1.1처럼 그대로 필드다(`path="/docs/"`도).
- **조용히 달라짐: join은 첫 렌더 앞에서 `params_changed()`를 부르고, LiveComponent도 처음 그려질 때 그것을 받는다.**
  Phoenix의 mount → handle_params → render 순서다. 1.1까지는 join의 렌더가 먼저 나가고 `params_changed()`가 그
  뒤에 돌아 렌더가 둘이었고, LiveComponent는 첫 params를 받지 못했다. 이제 순서는 부모 `joined()` → 부모
  `params_changed()` → 자식 `joined()` → 자식 `params_changed()`다. 고칠 것은 둘이다.
  - LiveComponent의 `params_changed()`가 patch 때만 돈다고 보고 쓴 코드, 예를 들어 같은 값을 부모가 props로도 넘기고
    `joined()`에서도 쿼리를 읽는 자식은 이제 처음에도 `params_changed()`를 듣는다. 두 번 하는 일이 있으면 하나로 줄인다.
  - `params_changed()`가 예외를 던지면 join이 렌더 없이 `error` 하나로 실패한다(1.1은 render 뒤에 `error`).
    `wireview:error`(`during: "join"`)는 전처럼 한 번 온다.
  ([LiveComponent](./features/live-component.md#수명주기))
- **조용히 달라짐: 다른 페이지로 가는 이동에서 떠나는 페이지의 컴포넌트는 목적지의 params를 듣지 않는다.**
  1.1까지는 가져온 응답이 경계 검사를 통과하자마자, 새 페이지를 그리기 전에 페이지의 모든 컴포넌트가 목적지의
  params로 `params_changed()`를 받았다. 곧 떠날 컴포넌트가 다른 페이지의 쿼리로 일했다. 이제 params는 새 페이지가
  화면에 놓인 뒤 간다. 새 페이지의 컴포넌트는 join에서, 이동을 건너온 컴포넌트는 `params_changed()`로
  받는다(주소마다 한 번, 뒤로 가기가 사본을 먼저 그려도 한 번). 이동을 건너온 것은 sticky 컴포넌트와 그 안에
  `{% component %}`로 그린 컴포넌트, 그리고 그것들의 LiveComponent다. 떠나는 컴포넌트가 떠날 때 할 일은
  `params_changed()`가 아니라 `leaving()`에 둔다. 와이어 프로토콜은 `vsn` 7이 되었고 클라이언트가 새 명령
  `navigated`를 보낸다. 롤링 배포 중의 옛 번들은 전처럼 동작하고 새 서버가 받는다.
  ([boost](./features/boost.md#이동을-건너-살아남기-sticky))
- **조용히 달라짐: 지금 주소와 똑같은 주소로 가는 `push_to`는 기록 항목을 만들지 않는다.** `params_changed()`는
  전처럼 돈다. 1.1은 같은 항목을 하나 더 만들었고, 그 항목에서 뒤로 가기를 누르면 아무것도 바뀌지 않았다. 지금
  주소로 가는 boost 링크·`redirect_to`·GET 폼도 브라우저처럼 지금 항목에서 다시 가져온다.
- **조용히 달라짐: boost는 같은 페이지 안의 조각 링크(`<a href="#section">`)를 가로채지 않는다.** 1.1은 그 링크에서
  같은 페이지 전체를 다시 가져왔다. 이제 브라우저가 스크롤하고 기록 항목을 만들며, 그 항목 사이의 뒤로·앞으로
  가기는 가져오지도 `params_changed()`를 부르지도 않는다. 조각 링크를 눌러 페이지를 다시 그리게 하던 곳은
  `{% on "click" %}` 핸들러나 `redirect_to`로 바꾼다.
- **가져오기가 네트워크 오류로 실패하면 그 주소를 boost 없이 연다.** 1.1에서는 처리되지 않은 rejection이 나고
  주소창과 화면이 어긋났다. GET이 아닌 폼은 다시 보내지 않고 `wireview:navigation-failed`를 보낸다. 오프라인에서
  보낸 폼을 사용자에게 알리려면 그 이벤트를 듣는다([boost](./features/boost.md#가져오기가-실패하면)).
- **boost 폼(GET이 아닌 것)은 `no-cors` 모드로 보낸다.** 같은 사이트 안의 리다이렉트는 전처럼 따라가 그린다.
  다른 출처로 가는 리다이렉트(결제, SSO)는 따라갈 수 없다 — 1.1은 처리되지 않은 rejection으로 멈췄고, 이제는
  `wireview:navigation-failed`가 `answered: true`로 온다. 서버는 폼을 이미 처리했다. 그런 폼에서는 `wire-boost`를 뺀다.
  boost는 폼에 헤더를 더하지 않으므로 서버가 받는 요청은 같다(`Sec-Fetch-Mode`만 `no-cors`).
- **조용히 달라짐: boost 폼의 method를 브라우저처럼 읽는다.** `get`·`post`·`dialog` 말고는 GET이다. 1.1은
  `method="put"`(`delete`, `patch`, 버튼의 `formmethod`도)을 그 method 그대로 fetch로 보냈다 — 브라우저라면 쿼리를 단
  GET으로 보냈을 폼이다. 이제 boost도 GET 이동으로 보낸다. PUT 같은 요청을 받던 뷰는 `{% on "submit.prevent" %}`
  핸들러에서 처리하거나 POST로 받는다([boost](./features/boost.md#폼은-스스로-청한다-wire-boost)).
- **중지된 boost 이동은 화면의 페이지에 머문다.** 가져오기가 `AbortError`로 끝나면(Firefox의 중지) 주소창을 화면의
  페이지로 되돌린다. 1.1은 주소창만 새 주소로 둔 채 옛 페이지를 남겼다. 뒤로·앞으로 가기는 주소창이 그대로이고,
  그린 캐시 사본이 착지하거나 사본이 없으면 그 항목을 boost 없이 연다([boost](./features/boost.md#가져오기가-실패하면)).

## 1.0에서 1.1로

- **새 경고 `wireview.W017`.** `AUTO_BROADCAST`의 플래그 중 하나를 켜고 `senders`가 사용자 모델(`AbstractBaseUser`)을
  비밀번호 해시가 실리게 알리거나 세션 모델(`AbstractBaseSession`)을 알리면 경고한다. 동작은 1.0과 같다 — 그 값은 1.0에서도
  저장할 때마다 채널 레이어로 나가고 있었다. `manage.py check --fail-level WARNING`을 쓰는 CI는 첫 실행에서 멈출 수 있다.
  사용자 모델은 `senders`를 매핑으로 바꿔 `password` 없이 보낼 필드를 적고, 세션 모델은 `senders`에서 뺀다
  ([검사 목록](./features/checks.md#검사-목록), [설정의 모델 알림](./features/settings.md#모델-알림)). 이미 나간 값은
  [§9](#9-auto_broadcast는-senders에-적은-모델만-알린다-보안)처럼 브로커의 로그·모니터링·덤프에서 점검한다.
- **`senders` 매핑은 1.0.0rc4 이하 프로세스가 모두 내려간 뒤에 켠다.** 집합으로 적은 `senders`는 전처럼 모든 필드를
  보내므로, 매핑을 쓰지 않으면 고칠 것이 없다. 1.0 프로세스는 페이로드에 없는 필드를 deferred로 두므로 1.0과 1.1이 섞인
  롤링 배포에서 매핑을 켜도 된다. 1.0.0rc4 이하 프로세스는 같은 브로커에서 그 알림을 받으면 빠진 필드를 기본값으로
  채운 인스턴스를 오류 없이 `mutation()`에 넘긴다 — 그 인스턴스를 저장하면 행이 기본값으로 덮인다. 그런 프로세스가
  남아 있으면 모두 올린 뒤 다음 배포에서 매핑을 켠다([배포](./DEPLOYMENT.md#업그레이드-auto_broadcast의-필드-목록)).
  매핑으로 받은 인스턴스에서 적지 않은 필드는 deferred이고, `()`(pk만)로 받은 인스턴스는 저장하지 않는다
  ([설정의 모델 알림](./features/settings.md#모델-알림)).

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
- **함수 컴포넌트 템플릿 안의 `{% component %}`가 첫 화면에서도 페이지의 것이다**(**보안**,
  [GHSA-pv9v-gqcj-f42x](https://github.com/itda-work/django-wireview/security/advisories/GHSA-pv9v-gqcj-f42x)).
  HTTP 렌더에서 함수의 템플릿이 그린 `{% component %}`는 요청이 없는 따로의 저장소에 그려졌다. 그래서
  `self.user`가 익명이고 쿼리 파라미터가 비었으며, 페이지의 `live_session(..., on_mount=[...])` 훅이 그 컴포넌트에
  돌지 않았다. 훅이 거절했어야 할 컴포넌트의 마크업과 서명 상태(`data-state`)가 첫 응답에 나갔다 — 서명이지
  암호화가 아니라 모든 필드가 읽힌다. 그 상태는 경계 없이(`s=""`) 서명되어, 새 WebSocket에서 `authorize`·인증 세대·
  세션 훅 없이 join되고 핸들러를 부를 수 있었다(`STATE_MAX_AGE`, 기본 14일 동안 누구나).
  - 해당하는 경우: 페이지의 `on_mount` 훅으로 컴포넌트 단위 인가를 하고, 훅이 거절할 컴포넌트를 함수 컴포넌트의
    템플릿 안에서(함수 안 함수 포함) 그리며, 그 컴포넌트가 `Meta.live_sessions`를 선언하지 않았다. 선언한 컴포넌트는
    오히려 제 경계의 페이지에서 거절되었는데, 1.0에서는 그려진다.
  - 1.0에서는 함수의 템플릿이 페이지와 같은 저장소(요청의 user·query·session·`live_session`)로 그리므로 고칠 것이
    없다. 다만 같은 저장소라 id도 페이지 전체에서 고유해야 한다 — 페이지와 함수의 템플릿이 같은 id를 다른 클래스로
    쓰면 요청이 `StateMismatch`로 실패한다.
  - 올릴 수 없으면 세션 훅이 막아야 하는 컴포넌트를 함수의 템플릿이 아니라 페이지·컴포넌트 템플릿에 직접 쓰거나
    그 컴포넌트에 `Meta.live_sessions`를 선언하고, 핸들러에서 `self.user`를 다시 검사한다.
  - 그 기간에 발행된 서명 상태는 `STATE_MAX_AGE`가 지나면 만료된다. 바로 무효화하려면 `WIREVIEW["SIGNING_KEY"]`
    (없으면 `SECRET_KEY`)를 바꾸고 옛 키를 `SIGNING_KEY_FALLBACKS`(없으면 `SECRET_KEY_FALLBACKS`)에 남기지 않는다.
    그 키로 만든 다른 서명 — 모든 페이지의 상태와 업로드 토큰, `SECRET_KEY`라면 Django의 로그인과 비밀번호 재설정
    링크 — 도 함께 무효가 된다.
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
- **`leaving()`은 `joined()`가 돈 인스턴스만 받는다**(**조용함**). 페이지가 join하기 전에 떠난 중첩 `{% component %}`와
  부모의 렌더가 `joined()`를 부르기 전에 사라진 LiveComponent는 이제 `leaving()` 없이 지워진다. 전에는 받았다 — 같은
  id의 페이지로 boost 이동하면 루트의 join 렌더가 만든 LiveComponent가 `joined()`보다 `leaving()`을 먼저 받았다.
  `joined()`에서 등록하고 `leaving()`에서 해제하는 코드(Presence, 카운터)는 고칠 것이 없다 — 이제 짝이 맞는다.
  `leaving()`에서 `on_mount` 훅이나 `new()`가 얻은 것을 놓았다면, join 전에 떠난 인스턴스에서는 그 일이 빠진다. 얻는
  일을 `joined()`로 옮긴다. 비동기 작업은 전처럼 취소된다.
- **초기화된 temporary assign 때문에 남던 부분 가운데 다시 그려지는 것이 있다**(**조용함**). 핸들러가 다시 대입하기
  전까지 그 필드만 읽은 블록·루프는 마지막 모습으로 남는다([Temporary Assigns](./features/temporary-assigns.md#다음-렌더에서)).
  1.0에서는 아래 경우 그 부분을 초기화된 값으로 다시 그려, 무관한 렌더에서 목록이 화면에서 빠진다.
  - 부분이 그린 중첩 `{% component %}`가 그 뒤 스스로 렌더해 화면에 다른 것을 그렸다. 서명 상태에 실리지 않는
    temporary assign이나 `exclude_fields`만 바뀌었어도, join의 답이 `joined()`가 바꾼 것을 그렸어도 그렇다. 전에는 그 컴포넌트가 화면에서 바뀌기 전 모습으로
    돌아갔고, 상태가 바뀐 경우에는 재연결하면 옛 상태로 join했다.
  - 블록이 그리는 `{% render_slot %}`의 fill이 바뀌었다. 전에는 옛 fill이 남았다.
  - 블록 안의 `{% include %}` 템플릿이 그리지 않은 가지에서 다른 필드를 읽거나, 이름을 렌더 때 정하거나(`{% include tpl %}`),
    찾을 수 없다. 전에는 그 템플릿 안을 보지 않아 다른 필드의 옛 값이 남았다.

  바뀌지 않은 표시용 행과 고정된 fill은 전처럼 남는다. 스스로 바뀌는 행의 목록을 남기려면 행을 `{% live_component %}`로
  그린다 — LiveComponent는 남는 부분 안에서도 남고 스스로 렌더한다. 상태가 필요 없는 행은 `{% func %}`나 인라인 마크업으로
  그린다. 그 밖의 경우는 목록을 그 블록 밖에 두거나 다른 필드를 블록 밖으로 옮긴다.
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
  [시작하기 튜토리얼의 `asgi.py`](./tutorials/01-getting-started.md#asgipy-수정)처럼 `DEBUG`일 때 `ASGIStaticFilesHandler`로 감싼다. `runserver`(daphne)만 쓰면 고칠 것이 없다.
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
  - LiveComponent·훅 가이드, README, Presence API 심화·File Uploads 심화 튜토리얼의 서버 헬퍼가 `_` 없이 이름 붙어 브라우저가 부를 수 있는 핸들러였다
    (`notify_user`는 아무 토스트나 띄웠다). `_`를 붙인다. `async def mount(self)`도 프레임워크가 부르지 않는 핸들러다 —
    초기화는 `joined()`에 둔다. `send_to_parent`가 부르는 부모 핸들러는 `_`를 붙일 수 없어(디스패처가 거절한다)
    브라우저도 아무 인자로 부를 수 있다. 그 핸들러 안에서 인자를 검증하고 권한을 확인한다.
  - Counter 컴포넌트·Todo 앱 튜토리얼은 Escape와 Ctrl+Enter를 `keypress`에 묶어 한 번도 발화하지 않았다. `keydown`에 묶는다.
  - Todo 앱·LiveComponent 튜토리얼은 `QuerySet.aupdate()`로 저장해 `post_save`가 없었고 다른 탭이 듣지 못했다. 인스턴스를 `asave()`한다.
    LiveComponent 튜토리얼의 `reset_all`은 부모가 가진 합계도 옛 값으로 남겼다 — 자식의 `update()`는 부모에게 알리지 않으므로
    부모의 합계를 함께 고친다.
  - Dashboard·LiveComponent 튜토리얼은 컴포넌트를 모델 채널에 구독시키고 `AUTO_BROADCAST.senders`를 적지 않아 모델 알림이 한 번도 오지
    않았다. `AUTO_BROADCAST`가 아예 없으면 `wireview.W015`도 조용하다. 구독하는 모델을 `senders`에 적는다
    ([§9](#9-auto_broadcast는-senders에-적은-모델만-알린다-보안)).
  - Todo 앱·Chat 앱·Dashboard 튜토리얼은 중첩 컴포넌트에 `id`를 주지 않아 부모가 렌더할 때마다 자식이 새로 만들어졌다. `id`를 준다.
  - 퀴즈 예제와 Quiz 앱 튜토리얼은 `{% class {...} %}`를 여러 줄에 걸쳐 써서 태그가 글자로 찍혔다. 한 줄에 쓴다.

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
로그·모니터링·덤프에 남았을 수 있는 데이터를 점검하고 필요하면 비밀번호 변경을 검토한다. 보낼 필드를 모델마다 고르는 선택지는 1.1에서 더했다([1.0에서 1.1로](#10에서-11로), #144).

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
