# 01. 시작하기

이 튜토리얼에서는 django-wireview를 설치하고 첫 번째 실시간 컴포넌트를 만들어봅니다.

## 학습 목표

- django-wireview 설치 및 설정
- ASGI와 Django Channels 이해
- 첫 번째 컴포넌트 생성

## 전제 조건

- Python 3.10 이상
- Django 4.2 이상 프로젝트
- 기본적인 Django 지식

## 1. 설치

### 패키지 설치

```bash
pip install django-wireview
```

이 명령은 다음 의존성을 함께 설치합니다:
- `channels` - Django Channels (WebSocket 지원)
- `pydantic` - 데이터 검증 및 상태 관리

### (권장) Redis 설치

개발 환경에서는 InMemory channel layer를 사용할 수 있지만, 실제 브로드캐스팅을 테스트하려면 Redis가 필요합니다:

```bash
pip install channels-redis
```

## 2. Django 설정

### settings.py 수정

```python
INSTALLED_APPS = [
    'wireview',      # wireview를 먼저 추가
    'channels',      # channels도 추가
    'django.contrib.admin',
    'django.contrib.auth',
    # ... 나머지 앱들
    'myapp',
]

# ASGI 애플리케이션 설정
ASGI_APPLICATION = 'myproject.asgi.application'

# Channel Layer 설정 (개발용 InMemory)
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer"
    }
}

# 프로덕션에서는 Redis 사용:
# CHANNEL_LAYERS = {
#     "default": {
#         "BACKEND": "channels_redis.core.RedisChannelLayer",
#         "CONFIG": {
#             "hosts": [("127.0.0.1", 6379)],
#         },
#     },
# }
```

### asgi.py 수정

`myproject/asgi.py` 파일을 다음과 같이 수정합니다:

```python
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

import django
django.setup()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from wireview.urls import websocket_urlpatterns

application = ProtocolTypeRouter({
    'http': get_asgi_application(),
    'websocket': AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
})
```

**중요**: `django.setup()`이 import 전에 호출되어야 합니다.

## 3. 첫 번째 컴포넌트 만들기

### 앱 구조

```
myapp/
├── __init__.py
├── live.py              # 컴포넌트 정의
├── templates/
│   └── myapp/
│       └── hello.html   # 컴포넌트 템플릿
├── views.py
└── urls.py
```

### 컴포넌트 정의 (live.py)

`myapp/live.py` 파일을 생성합니다:

```python
from wireview.component import Component


class XHello(Component):
    """첫 번째 wireview 컴포넌트"""

    _template_name = 'myapp/hello.html'

    name: str = "World"

    async def change_name(self, name: str):
        """이름 변경 이벤트 핸들러"""
        self.name = name
```

**핵심 포인트:**
- `Component`를 상속합니다
- `_template_name`으로 템플릿 경로를 지정합니다
- Pydantic 스타일로 상태(필드)를 정의합니다
- 이벤트 핸들러는 `async def`로 정의합니다

### 템플릿 작성 (hello.html)

`myapp/templates/myapp/hello.html` 파일을 생성합니다:

```html
{% load wireview %}
<div {% tag_header %}>
  <h1>Hello, {{ name }}!</h1>

  <input
    type="text"
    name="name"
    value="{{ name }}"
    {% on "input.debounce.300" "change_name" %}
  >
</div>
```

**핵심 포인트:**
- `{% load wireview %}` - wireview 템플릿 태그 로드
- `{% tag_header %}` - 컴포넌트 루트 요소에 필수 속성 추가
- `{% on "event" "handler" %}` - 이벤트를 핸들러에 바인딩
- `.debounce.300` - 300ms 디바운스로 타이핑 중 과도한 요청 방지

### 페이지 템플릿 (index.html)

`myapp/templates/myapp/index.html` 파일을 생성합니다:

```html
{% load wireview %}
<!DOCTYPE html>
<html>
<head>
    <title>Wireview Hello</title>
    {% wireview_header %}
</head>
<body>
    <h1>My First Wireview App</h1>

    {% component 'XHello' %}

    <!-- 초기값을 전달할 수도 있습니다 -->
    {% component 'XHello' name="Django" %}
</body>
</html>
```

**핵심 포인트:**
- `{% wireview_header %}` - 필요한 JavaScript 로드 (~10KB)
- `{% component 'ComponentName' %}` - 컴포넌트 렌더링
- kwargs로 초기 상태 전달 가능

### 뷰 및 URL 설정

`myapp/views.py`:

```python
from django.shortcuts import render


def index(request):
    return render(request, 'myapp/index.html')
```

`myapp/urls.py`:

```python
from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
]
```

`myproject/urls.py`:

```python
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('myapp.urls')),
]
```

## 4. 실행

### 개발 서버 실행

```bash
python manage.py runserver
```

`http://localhost:8000`에 접속하면:
1. "Hello, World!" 메시지가 표시됩니다
2. 입력 필드에 이름을 입력하면 실시간으로 인사말이 바뀝니다
3. 페이지 새로고침 없이 UI가 업데이트됩니다

## 5. 동작 원리

### 초기 렌더링
1. Django 뷰가 `index.html`을 렌더링
2. `{% component 'XHello' %}`가 컴포넌트를 서버 사이드 렌더링
3. HTML이 클라이언트에 전송됨

### WebSocket 연결
1. 페이지 로드 후 JavaScript가 WebSocket 연결 설정
2. 컴포넌트가 백엔드에 "join" 메시지 전송
3. `joined()` 메서드 호출 (정의된 경우)

### 이벤트 처리
1. 사용자가 입력 필드에 타이핑
2. `input` 이벤트가 300ms 디바운스 후 발생
3. `change_name` 핸들러가 WebSocket으로 호출
4. 서버에서 상태 업데이트 및 새 HTML 렌더링
5. 변경된 부분만 클라이언트로 전송 (HTML diff)
6. DOM이 효율적으로 업데이트됨 (morphdom)

## 6. 디버깅

브라우저 콘솔에서 디버그 모드를 활성화할 수 있습니다:

```javascript
// 디버그 로깅 활성화
wireview.debug.enable()

// 연결 상태 확인
wireview.debug.status()

// 등록된 컴포넌트 목록
wireview.debug.components()
```

## 다음 단계

축하합니다! 첫 번째 wireview 컴포넌트를 만들었습니다.

다음 튜토리얼에서는 Counter 컴포넌트를 만들며 이벤트 핸들링과 상태 관리를 더 자세히 배워봅니다.

[다음: 02. Counter 컴포넌트 →](02-counter-component.md)
