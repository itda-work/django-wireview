# 타입 스텁(.pyi) 자동 생성

컴포넌트의 `.pyi` 스텁을 생성해 IDE 자동완성과 mypy·pyright의 정적 검사를 받게 한다.

## 빠른 시작

모든 컴포넌트의 스텁을 만든다.

```bash
python manage.py wireview_stubs
```

## CLI 옵션

| 옵션 | 뜻 |
|------|-----|
| `--dry-run` | 파일을 쓰지 않고 미리 본다 |
| `--check` | CI 모드. 스텁이 낡았으면 exit 1 |
| `--output-dir=DIR` | 출력 디렉터리 (기본: 소스 옆) |
| `--app=NAME` | 앱 이름으로 거른다 (여러 번 지정 가능) |
| `-v 2` | 자세한 출력 |

### 예

```bash
# 무엇이 생성될지 미리 본다
python manage.py wireview_stubs --dry-run

# 스텁이 최신인지 확인한다 (CI용)
python manage.py wireview_stubs --check

# 특정 앱만
python manage.py wireview_stubs --app myapp

# 다른 디렉터리에 생성
python manage.py wireview_stubs --output-dir=./stubs

# 자세한 출력
python manage.py wireview_stubs -v 2
```

## 자동 생성

`DEBUG=True`이면 서버가 뜰 때 스텁이 다시 생성된다.

```python
# settings.py
WIREVIEW = {
    "AUTO_GENERATE_STUBS": True,  # 기본값: True (DEBUG 모드에서)
}
```

끄려면 `False`로 둔다.

```python
WIREVIEW = {
    "AUTO_GENERATE_STUBS": False,
}
```

## 생성 결과 예

이런 컴포넌트가 있으면

```python
# myapp/live.py
from wireview import Component

class Counter(Component):
    """A simple counter component."""
    _template_name = "counter.html"

    count: int = 0
    step: int = 1

    async def increment(self, amount: int = 1) -> None:
        """Increment the counter."""
        self.count += amount
```

이런 스텁(`myapp/live.pyi`)이 나온다.

```python
"""Auto-generated type stubs for wireview components.

DO NOT EDIT - regenerate with: python manage.py wireview_stubs
"""

from typing import Any, ClassVar
from wireview.component import Component

class Counter(Component):
    """A simple counter component."""

    _template_name: ClassVar[str]

    count: int
    step: int

    async def increment(self, amount: int = ...) -> None: ...

    # Wireview metadata for IDE support
    __wireview_attrs__: ClassVar[dict[str, dict[str, Any]]] = {
        'count': {'type': 'int', 'required': False, 'default': 0},
        'step': {'type': 'int', 'required': False, 'default': 1}
    }
    __wireview_handlers__: ClassVar[list[str]] = ['increment']
```

## 메타데이터 속성

스텁에는 IDE·LSP가 읽을 메타데이터가 함께 들어간다.

### `__wireview_attrs__`

컴포넌트 필드와 그 정보다.

```python
__wireview_attrs__ = {
    'count': {
        'type': 'int',       # 타입 애너테이션(문자열)
        'required': False,   # 필수 여부
        'default': 0,        # 기본값
    }
}
```

### `__wireview_handlers__`

이벤트 핸들러 메서드 이름 목록이다.

```python
__wireview_handlers__ = ['increment', 'decrement', 'reset']
```

## 지원하는 컴포넌트 종류

| 종류 | 설명 |
|------|------|
| `Component` | 일반 상태 컴포넌트 |
| `LiveComponent` | 독립 상태를 가진 중첩 컴포넌트 |
| `FunctionComponent` | 상태 없는 템플릿 함수 |

### LiveComponent

```python
class Counter(LiveComponent):
    """LiveComponent stub with proper inheritance."""

    _template_name: ClassVar[str]

    count: int

    async def increment(self) -> None: ...
    async def update(self, **assigns: Any) -> None: ...
```

### FunctionComponent

```python
button: FunctionComponent
"""Simple button component."""
```

## 동적 구독

`_subscriptions`를 `@property`로 정의했다면 스텁이 그 사실을 적어 둔다.

```python
class XTodoItem(Component):
    _template_name: ClassVar[str]
    # Note: _subscriptions is a dynamic property
    @property
    def _subscriptions(self) -> set[str]: ...
```

## CI 연동

`--check`로 스텁이 최신인지 확인한다.

```yaml
# GitHub Actions 예
- name: Check type stubs
  run: python manage.py wireview_stubs --check
```

종료 코드:

- `0` — 스텁이 최신이다
- `1` — 다시 생성해야 한다

## pre-commit 훅

`.pre-commit-config.yaml`에 넣는다.

```yaml
- repo: local
  hooks:
    - id: wireview-stubs
      name: Generate wireview type stubs
      entry: python manage.py wireview_stubs
      language: system
      pass_filenames: false
      files: '.*live\.py$'
```

## 문제 해결

### 일부 컴포넌트의 스텁이 안 생긴다

다음을 모두 만족해야 발견된다.

1. `Component._all` 또는 `LiveComponent._live_all`에 등록되어 있다
2. 라이브러리 경로(site-packages, venv) 밖에 있다
3. 소스 파일 경로가 유효하다

### 타입은 import되거나 `Any`가 된다

스텁은 타입 검사기가 모듈 대신 읽는 파일이라, 원본 모듈의 import가 스텁에는 없다. 그래서 생성기는
애너테이션을 **소스 텍스트로 복사하지 않고** 해석된 객체에서 다시 쓴다(`from __future__ import annotations`로
문자열이 된 애너테이션도 먼저 해석한다). 스텁이 읽는 이름은 모두 스텁 안에서 import된다.

| 애너테이션 | 스텁 |
|---|---|
| 내장 타입, `list[str]`, `dict[str, Any]`, `X \| None` | 그대로 (`t.List`·`t.Optional`도 이 모양으로) |
| `typing.Any`, `Literal[...]` | `from typing import ...` |
| `Callable`, `Awaitable` 등 | `from collections.abc import ...` |
| 다른 모듈의 클래스 (`datetime.date`, 다른 앱의 모델) | `from <모듈> import <이름>` |
| 같은 스텁에 선언되는 컴포넌트 | 이름 그대로 |
| 같은 모듈에만 있는 다른 클래스, TypeVar, 함수 안에서 정의한 클래스, 해석되지 않는 이름, 다른 import와 겹치는 이름 | `Any` |

마지막 줄은 원본보다 느슨하지만 틀리지 않는다. 같은 모듈의 모델을 정확한 타입으로 받고 싶으면 모델을
`models.py`처럼 다른 모듈에 두면 된다. 애너테이션이 없는 파라미터도 `Any`다.

`*args`, `**kwargs`, 키워드 전용(`*,`)과 위치 전용(`/`) 표시, `@classmethod`·`@staticmethod`는 원본대로
남는다. 생성된 스텁은 저장소의 테스트가 파싱되는지, 읽는 이름이 모두 바인딩되는지, import한 이름을 모두
쓰는지를 검사한다(`tests/test_stubs_valid.py`).
