# 09. 테스트 가이드

wireview 컴포넌트의 효과적인 테스트 방법을 다룹니다.

## 학습 목표

- `mount()` 유틸리티 사용법
- 이벤트 핸들러 테스트
- 상태 검증
- 메시지와 내비게이션 테스트 (`assert_pushed_to`, `follow_redirect`, `follow_push`)
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
| `view.assert_pushed_to(...)` | 내비게이션 단언 (아래 참조) |
| `view.follow_redirect(...)` / `view.follow_push()` | 이동을 따라간다 |
| `view.stream_html(...)` | 스트림으로 나간 아이템 HTML |

전체 목록은 [기능 레퍼런스](../features/testing.md)에 있다.

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

## 내비게이션 테스트

### 리다이렉트 확인

```python
@pytest.mark.asyncio
async def test_redirect_after_save():
    view = await mount(XForm)

    await view.call("submit")

    view.assert_redirected_to("/success/")
    assert view.is_frozen  # 리다이렉트 후 freeze된다
```

`view.redirected_to`로 문자열을 직접 비교해도 되지만, 단언 헬퍼는 실패했을 때 **실제로 일어난
이동을 전부 나열해 준다.**

### push와 replace

```python
@pytest.mark.asyncio
async def test_paging_changes_the_url():
    view = await mount(XProductList)

    await view.call("next_page")

    view.assert_pushed_to("/products/", params={"page": "2"})
```

`params`는 통째로 비교한다. `"/products/?page=2"`처럼 URL에 쿼리를 붙여도 같은 뜻이고, 이때
순서는 상관없다.

### 이동하지 않았음을 확인

```python
@pytest.mark.asyncio
async def test_an_invalid_form_stays_put():
    view = await mount(XLogin)

    await view.call("login", username="wrong", password="wrong")

    assert view.component.error
    view.assert_no_navigation()
```

"이동했다"와 "여기로 이동했다"는 통과하는 테스트만 보면 구별되지 않는다. 이동하면 안 되는
경로에는 이 단언을 짝지어 둔다.

### 리다이렉트를 따라가기

```python
@pytest.mark.asyncio
async def test_login_lands_on_the_dashboard():
    view = await mount(XLogin)

    await view.call("login", username="admin", password="correct")

    landed = await view.follow_redirect(XDashboard)
    assert landed.component.username == "admin"
```

리다이렉트는 페이지 로드다. `follow_redirect()`는 대상 페이지의 컴포넌트를 새로 마운트하되,
대상 URL의 쿼리를 params로 넘기고 user·session을 이어 주며 **대상 페이지의 live_session을
URLconf에서 읽는다.** 그 경계가 이 사용자를 거절하면 여기서도 거절한다 — 서버가 컴포넌트를
그리지 않을 상황에서 테스트만 통과하는 일이 없도록.

### push를 따라가기

```python
@pytest.mark.asyncio
async def test_paging_reloads_the_page_of_products():
    view = await mount(XProductList)

    await view.call("next_page")        # wire.push_to("?page=2")
    await view.follow_push()            # 클라이언트가 하는 나머지 절반

    assert view.component.page == 2
```

`push_to()`는 절반이다. 나머지 절반은 클라이언트가 새 params를 서버에 알리는 것이고, 그것이
`params_changed()`를 돌린다. 경계를 넘는 push는 전체 페이지 로드라 `params_changed`가 아예
가지 않으므로, 그 경우 `follow_push()`는 콜백을 돌리는 대신 실패한다.

## Streams 테스트

### 스트림에 실린 것 확인

스트림 아이템은 `view.render()`에도 `view.dom_actions`에도 없다. 템플릿은 빈 컨테이너만
렌더하고 아이템 HTML은 별도 메시지로 간다.

```python
@pytest.mark.asyncio
async def test_stream_insert():
    view = await mount(XMessageList, room_id=1)
    view.clear_messages()          # joined()의 초기 stream()을 비운다

    await view.call("add_message", text="Hello")

    assert "Hello" in view.stream_html("messages")
    assert [op["op"] for op in view.stream_ops("messages")] == ["insert"]
```

`view.stream_items()`는 `{"id", "html"}` 목록을, `view.stream_ops()`는 원본 연산을 준다.
이름을 주지 않으면 모든 스트림이 대상이다.

**`clear_messages()`를 잊지 않는다.** `stream()`을 다시 부르는 핸들러(필터·정렬·페이지 전환)는
이전 메시지 위에 누적되므로, 비우지 않으면 방금 걸러 낸 아이템이 앞선 메시지에 남아 있다.

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

> **`django_db`는 async ORM 호출을 롤백하지 못한다.**
> `@pytest.mark.django_db`는 각 테스트를 트랜잭션으로 감싸고 끝에 롤백한다. 동기 ORM은
> 그대로 동작하지만, `acreate`·`asave`·`adelete`로 쓴 레코드는 그 트랜잭션 밖에서 커밋되어
> 다음 테스트에 그대로 보인다. 컴포넌트 핸들러는 async이므로 대부분의 컴포넌트 테스트가
> 여기에 해당한다.
>
> 그래서 위 예제들은 전역 개수가 아니라 **pk로 범위를 좁혀** 검사한다.
> `await Item.objects.acount() == 0` 같은 단언은 앞선 테스트가 남긴 레코드 때문에 깨진다.
> 격리가 꼭 필요하면 `@pytest.mark.django_db(transaction=True)`를 쓰되, 매 테스트마다
> 테이블을 비우므로 느려진다.

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

테스트 헬퍼의 전체 목록과 세부 규칙은 [기능 레퍼런스](../features/testing.md)에 있습니다.

더 많은 정보는 [README](../../README.md)와 [Architecture](../ARCHITECTURE.md) 문서를 참조하세요.

[← 이전: 08. File Uploads 심화](08-file-uploads.md) | [처음으로 →](README.md)
