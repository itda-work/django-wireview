# 09. 테스트 가이드

wireview 컴포넌트의 효과적인 테스트 방법을 다룹니다.

## 학습 목표

- `mount()` 유틸리티 사용법
- 이벤트 핸들러 테스트
- 상태 검증
- 메시지 및 리다이렉트 테스트
- Streams/Presence 테스트

## 테스트 환경 설정

### pytest 설정

`pytest.ini`:

```ini
[pytest]
DJANGO_SETTINGS_MODULE = myproject.settings
asyncio_mode = auto
python_files = test_*.py
python_classes = Test*
python_functions = test_*
```

### 의존성

```bash
pip install pytest pytest-asyncio pytest-django
```

## mount() 유틸리티

### 기본 사용법

```python
import pytest
from wireview.testing import mount
from myapp.live import XCounter


@pytest.mark.asyncio
async def test_counter_initial_state():
    """초기 상태 테스트"""
    view = await mount(XCounter)

    assert view.component.count == 0


@pytest.mark.asyncio
async def test_counter_with_initial_value():
    """초기값 지정 테스트"""
    view = await mount(XCounter, count=10)

    assert view.component.count == 10
```

### MountedView API

| 속성/메서드 | 설명 |
|-------------|------|
| `view.component` | 컴포넌트 인스턴스 |
| `view.call(handler, **kwargs)` | 이벤트 핸들러 호출 |
| `view.sent_messages` | 전송된 메시지 목록 |
| `view.redirected_to` | 리다이렉트 URL |
| `view.is_frozen` | freeze 상태 |
| `view.clear_messages()` | 메시지 초기화 |

## 이벤트 핸들러 테스트

### 기본 핸들러

```python
@pytest.mark.asyncio
async def test_increment():
    """increment 핸들러 테스트"""
    view = await mount(XCounter, count=0)

    await view.call("increment")

    assert view.component.count == 1


@pytest.mark.asyncio
async def test_increment_with_amount():
    """인자가 있는 핸들러"""
    view = await mount(XCounter, count=0)

    await view.call("increment", amount=5)

    assert view.component.count == 5
```

### 여러 호출

```python
@pytest.mark.asyncio
async def test_multiple_increments():
    """여러 번 호출"""
    view = await mount(XCounter, count=0)

    await view.call("increment")
    await view.call("increment")
    await view.call("increment")

    assert view.component.count == 3
```

### 비동기 핸들러

```python
@pytest.mark.asyncio
async def test_async_handler():
    """비동기 데이터 로딩"""
    view = await mount(XTodoList)

    # joined()가 자동으로 호출됨
    assert len(view.component.items) > 0
```

## 상태 검증

### 단순 상태

```python
@pytest.mark.asyncio
async def test_state_changes():
    view = await mount(XTodoItem, text="Buy milk", completed=False)

    await view.call("toggle")

    assert view.component.completed is True
```

### 복잡한 상태

```python
@pytest.mark.asyncio
async def test_list_state():
    view = await mount(XTodoList)

    await view.call("add_item", text="New item")

    # 리스트에 아이템 추가됨
    assert any(
        item.text == "New item"
        for item in view.component.items
    )
```

### 속성 검증

```python
@pytest.mark.asyncio
async def test_computed_property():
    view = await mount(XTodoList)

    # @property로 정의된 값
    assert view.component.active_count >= 0
```

## 메시지 테스트

### 전송된 메시지 확인

```python
@pytest.mark.asyncio
async def test_broadcast_message():
    view = await mount(XChatRoom, room_id=1, username="alice")

    await view.call("send_message", text="Hello!")

    # 메시지가 전송되었는지 확인
    assert len(view.sent_messages) > 0

    # 특정 메시지 확인
    messages = [m for m in view.sent_messages if m.get("text") == "Hello!"]
    assert len(messages) == 1
```

### 메시지 초기화

```python
@pytest.mark.asyncio
async def test_multiple_messages():
    view = await mount(XChatRoom, room_id=1, username="alice")

    await view.call("send_message", text="First")
    view.clear_messages()

    await view.call("send_message", text="Second")

    # 두 번째 메시지만
    assert len(view.sent_messages) == 1
```

## 리다이렉트 테스트

### 리다이렉트 확인

```python
@pytest.mark.asyncio
async def test_redirect_after_save():
    view = await mount(XForm)

    await view.call("submit")

    assert view.redirected_to == "/success/"
    assert view.is_frozen  # 리다이렉트 후 freeze됨
```

### 조건부 리다이렉트

```python
@pytest.mark.asyncio
async def test_redirect_on_error():
    view = await mount(XLogin)

    await view.call("login", username="wrong", password="wrong")

    # 실패 시 리다이렉트 없음
    assert view.redirected_to is None

    await view.call("login", username="admin", password="correct")

    # 성공 시 리다이렉트
    assert view.redirected_to == "/dashboard/"
```

## Streams 테스트

### Stream 작업 확인

```python
@pytest.mark.asyncio
async def test_stream_insert():
    view = await mount(XMessageList, room_id=1)

    await view.call("add_message", text="Hello")

    # stream_insert가 호출되었는지 확인
    stream_ops = [
        m for m in view.sent_messages
        if m.get("type") == "stream"
    ]
    assert len(stream_ops) > 0
```

### Stream 상태

```python
@pytest.mark.asyncio
async def test_stream_state():
    view = await mount(XActivityFeed)

    # joined()에서 stream() 호출됨
    assert view.component.has_more is True

    # load_more 호출
    await view.call("load_more")

    # 더 많은 아이템 로드됨
    assert len(view.component.activities) > 0
```

## Presence 테스트

### Presence 메시지

```python
@pytest.mark.asyncio
async def test_presence_join():
    view = await mount(XChatInput, room_id=1, username="alice")

    # joined()에서 presence_join() 호출됨
    presence_msgs = [
        m for m in view.sent_messages
        if m.get("action") == "presence_join"
    ]
    assert len(presence_msgs) > 0
```

### 타이핑 표시

```python
@pytest.mark.asyncio
async def test_typing_indicator():
    view = await mount(XChatInput, room_id=1, username="alice")

    await view.call("on_typing")

    typing_msgs = [
        m for m in view.sent_messages
        if m.get("action") == "presence_typing"
    ]
    assert len(typing_msgs) > 0
```

## 픽스처 활용

### 공통 설정

```python
import pytest
from wireview.testing import mount


@pytest.fixture
async def counter():
    """카운터 컴포넌트 픽스처"""
    return await mount(XCounter, count=0)


@pytest.fixture
async def todo_list(db):
    """데이터베이스와 함께 사용"""
    # 테스트 데이터 생성
    await Item.objects.acreate(text="Test item")
    return await mount(XTodoList)
```

### 픽스처 사용

```python
@pytest.mark.asyncio
async def test_with_fixture(counter):
    await counter.call("increment")
    assert counter.component.count == 1


@pytest.mark.asyncio
async def test_with_db(todo_list):
    assert len(todo_list.component.items) > 0
```

## 모델과 함께 테스트

### 데이터베이스 테스트

```python
import pytest
from myapp.models import Item


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_create_item():
    view = await mount(XTodoList)

    await view.call("add_item", text="New item")

    # DB에 저장되었는지 확인
    assert await Item.objects.filter(text="New item").aexists()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_delete_item():
    # 테스트 아이템 생성
    item = await Item.objects.acreate(text="To delete")

    view = await mount(XTodoItem, item_id=item.id, text=item.text)

    await view.call("delete")

    # DB에서 삭제되었는지 확인
    assert not await Item.objects.filter(id=item.id).aexists()
```

## 에러 테스트

### 예외 발생 확인

```python
@pytest.mark.asyncio
async def test_invalid_input():
    view = await mount(XForm)

    with pytest.raises(ValueError):
        await view.call("submit", email="invalid")
```

### 에러 상태 확인

```python
@pytest.mark.asyncio
async def test_error_state():
    view = await mount(XStatCard, stat_name="nonexistent")

    # AsyncResult가 실패 상태인지
    assert view.component.stat.failed is True
    assert view.component.stat.error is not None
```

## 테스트 마커

### 마커 정의

```python
# conftest.py
import pytest

pytest.mark.unit = pytest.mark.mark(name="unit")
pytest.mark.integration = pytest.mark.mark(name="integration")
pytest.mark.slow = pytest.mark.mark(name="slow")
```

### 마커 사용

```python
@pytest.mark.unit
@pytest.mark.asyncio
async def test_simple_logic():
    """단위 테스트"""
    view = await mount(XCounter)
    await view.call("increment")
    assert view.component.count == 1


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.asyncio
async def test_with_database():
    """통합 테스트"""
    view = await mount(XTodoList)
    await view.call("add_item", text="Test")
    assert await Item.objects.filter(text="Test").aexists()
```

### 선택적 실행

```bash
# 단위 테스트만
pytest -m unit

# 통합 테스트만
pytest -m integration

# 느린 테스트 제외
pytest -m "not slow"
```

## 커버리지

### 설정

```bash
pip install pytest-cov
```

### 실행

```bash
pytest --cov=myapp --cov-report=html
```

### 중요한 경로

테스트해야 할 것들:
- 모든 이벤트 핸들러
- 조건부 로직 (if/else)
- 에러 처리 경로
- 경계 조건 (빈 리스트, 최대값 등)

## 팁

### 1. 작은 단위로 테스트

```python
# 좋음: 하나의 동작만 테스트
async def test_increment_by_one():
    view = await mount(XCounter)
    await view.call("increment")
    assert view.component.count == 1

# 피하기: 여러 동작을 하나의 테스트에
async def test_everything():
    view = await mount(XCounter)
    await view.call("increment")
    await view.call("decrement")
    await view.call("set_to", value=100)
    # ...
```

### 2. 의미 있는 이름

```python
# 좋음
async def test_toggle_completed_changes_item_status():
    ...

# 피하기
async def test_toggle():
    ...
```

### 3. 독립적인 테스트

각 테스트는 다른 테스트에 의존하지 않아야 합니다.

## 다음 단계

축하합니다! wireview 튜토리얼을 모두 완료했습니다.

더 많은 정보는 [README](../../README.md)와 [Architecture](../ARCHITECTURE.md) 문서를 참조하세요.

[← 이전: 08. File Uploads 심화](08-file-uploads.md) | [처음으로 →](README.md)
