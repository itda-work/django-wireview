# 테스트 가이드

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
from wireview import mount
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

아래 예제는 앞선 튜토리얼의 컴포넌트를 테스트한다 — `XCounter`는 [Counter 컴포넌트](02-counter-component.md)의 완성본,
`XTodoList`·`XTodoItem`과 모델 `Item`은 [Todo 앱](03-todo-app.md), `XChatRoom`·`XMessageList`와 모델 `Room`은
[Chat 앱](04-chat-app.md), `XStatCard`·`XActivityFeed`와 모델 `Activity`는 [Dashboard](05-dashboard.md). 위의 `XCounter`처럼
각자의 앱에서 import한다. `XForm`·`XLogin`·`XDashboard`·`XProductList`는 설명을 위한 가상의 컴포넌트다.

DB를 읽거나 쓰는 컴포넌트는 `joined()`에서 이미 DB에 닿으므로, 그 테스트에는 `@pytest.mark.django_db`가 필요하다.
테스트나 핸들러가 async ORM으로 **쓰면** `@pytest.mark.django_db(transaction=True)`다 — 이유는
[모델과 함께 테스트](#모델과-함께-테스트)의 끝에 있다.

### MountedComponent API

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
async def test_set_to():
    """인자가 있는 핸들러"""
    view = await mount(XCounter, count=0)

    await view.call("set_to", value=5)

    assert view.component.count == 5
```

인자 이름은 핸들러의 매개변수 이름과 같아야 한다. `view.call()`은 핸들러가 받지 않는 인자를 버린다 —
폼의 다른 필드가 함께 실려 오는 클라이언트 이벤트와 같게. 그래서 `view.call("increment", amount=5)`처럼
없는 매개변수를 넘기면 오류 없이 무시되고 `increment()`만 돈다.

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
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_joined_loads_items():
    """비동기 데이터 로딩"""
    await Item.objects.acreate(text="Buy milk")

    view = await mount(XTodoList)

    # mount()가 joined()까지 호출한다
    assert len(view.component.items) > 0
```

## 상태 검증

### 단순 상태

```python
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_state_changes():
    item = await Item.objects.acreate(text="Buy milk")
    view = await mount(XTodoItem, item_id=item.id, text=item.text, completed=False)

    await view.call("toggle")

    assert view.component.completed is True
```

기본값이 없는 필드(`item_id`·`text`·`completed`)는 템플릿에서 넘기듯 `mount()`에도 모두 넘긴다.

### 복잡한 상태

03의 `add_item()`은 DB에 저장만 한다. 목록은 모델 브로드캐스트가 부르는 `mutation()`이 채운다.
`mount()`는 채널 레이어를 흉내 낼 뿐 모델 브로드캐스트를 컴포넌트에 전달하지 않으므로, 테스트가
`mutation()`을 직접 부른다. 채널 이름은 모델의 `label_lower`(`todo.item`)다.

```python
from wireview import ModelAction


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_list_state():
    view = await mount(XTodoList)

    await view.call("add_item", text="New item")

    # 브로드캐스트가 할 일을 대신한다
    item = await Item.objects.filter(text="New item").afirst()
    await view.component.mutation("todo.item", ModelAction.CREATED, item)

    # 리스트에 아이템 추가됨
    assert any(
        item.text == "New item"
        for item in view.component.items
    )
```

### 속성 검증

```python
@pytest.mark.django_db
@pytest.mark.asyncio
async def test_computed_property():
    view = await mount(XTodoList)

    # @property로 정의된 값
    assert view.component.active_count >= 0
```

## 메시지 테스트

`view.sent_messages`에는 클라이언트로 나간 원본 메시지가 모두 쌓인다. 04의 `send_message()` 한 번이면
스트림 삽입, 스크롤, 입력창을 비우는 JS 명령 세 개다. 그 모양은 wire 프로토콜이므로 직접 뒤지기보다
`stream_html()` 같은 헬퍼로 본다.

### 전송된 메시지 확인

```python
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_send_message():
    room = await Room.objects.acreate(name="general")
    view = await mount(XChatRoom, room_id=room.id, room_name=room.name, username="alice")

    await view.call("send_message", text="Hello!")

    # 새 메시지가 스트림으로 나갔다
    assert "Hello!" in view.stream_html("messages")
```

### 메시지 초기화

```python
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_multiple_messages():
    room = await Room.objects.acreate(name="general")
    view = await mount(XChatRoom, room_id=room.id, room_name=room.name, username="alice")

    await view.call("send_message", text="First")
    view.clear_messages()

    await view.call("send_message", text="Second")

    # 두 번째 호출이 보낸 것만 남는다
    html = view.stream_html("messages")
    assert "Second" in html
    assert "First" not in html
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

`push_to()`는 절반이다. `"?page=2"`처럼 같은 경로로 가면 나머지 절반은 클라이언트가 새 params를 서버에
알리는 것이고, 그것이 같은 인스턴스의 `params_changed()`를 돌린다. 이벤트로 바꾼 상태는 남는다.
다른 경로로 가면 브라우저는 그 페이지를 가져오고 컴포넌트가 새로 join하므로, `follow_push(Destination)`이
대상 컴포넌트를 새로 마운트해 돌려준다. `"/products/?page=2"`처럼 경로가 있는 목적지는
`mount(XProductList, path="/products/")`로 컴포넌트가 놓인 경로를 알려 줘야 둘을 가린다.

## Streams 테스트

### 스트림에 실린 것 확인

스트림 아이템은 `view.render()`에 없다. 템플릿은 빈 컨테이너만
렌더하고 아이템 HTML은 별도 메시지로 간다.

```python
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_stream_insert():
    room = await Room.objects.acreate(name="general")
    view = await mount(XMessageList, room_id=room.id)
    view.clear_messages()          # joined()의 초기 stream()을 비운다

    await view.call("add_message", sender="alice", text="Hello")

    assert "Hello" in view.stream_html("messages")
    assert [op["op"] for op in view.stream_ops("messages")] == ["insert"]
```

`view.stream_items()`는 `{"id", "html"}` 목록을, `view.stream_ops()`는 원본 연산을 준다.
이름을 주지 않으면 모든 스트림이 대상이다.

**`clear_messages()`를 잊지 않는다.** `stream()`을 다시 부르는 핸들러(필터·정렬·페이지 전환)는
이전 메시지 위에 누적되므로, 비우지 않으면 방금 걸러 낸 아이템이 앞선 메시지에 남아 있다.

### Stream 상태

```python
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_stream_state():
    for i in range(25):            # 한 번에 20개씩 읽으므로 그보다 많이
        await Activity.objects.acreate(user="alice", action="create", target=f"doc-{i}")

    view = await mount(XActivityFeed)

    # joined()에서 stream() 호출됨
    assert view.component.has_more is True

    view.clear_messages()
    await view.call("load_more")

    # 다음 묶음이 스트림 끝에 붙었다
    assert len(view.stream_items("activities")) > 0
    assert all(op["at"] == -1 for op in view.stream_ops("activities"))
```

스트림에 보낸 항목은 컴포넌트 상태에 남지 않는다. 무엇이 나갔는지는 필드가 아니라 `stream_items()`로 본다.

## Presence 테스트

Presence 알림은 클라이언트가 아니라 채널로 가는 브로드캐스트다. 그래서 `view.sent_messages`가 아니라
`view.presence_broadcasts`에 남는다. 항목마다 `kwargs`에 `action`과 사용자 정보가 있다.

### Presence 메시지

```python
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_presence_join():
    room = await Room.objects.acreate(name="general")
    view = await mount(XChatRoom, room_id=room.id, room_name=room.name, username="alice")

    # joined()에서 presence_join() 호출됨
    joins = [
        b["kwargs"] for b in view.presence_broadcasts
        if b["kwargs"]["action"] == "presence_join"
    ]
    assert joins[0]["username"] == "alice"
```

### 타이핑 표시

```python
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_typing_indicator():
    room = await Room.objects.acreate(name="general")
    view = await mount(XChatRoom, room_id=room.id, room_name=room.name, username="alice")

    await view.call("on_typing")

    typing = [
        b["kwargs"] for b in view.presence_broadcasts
        if b["kwargs"]["action"] == "presence_typing"
    ]
    assert typing[-1]["state"] == "typing"
```

## 픽스처 활용

### 공통 설정

```python
import pytest
from wireview import mount


@pytest.fixture
async def counter():
    """카운터 컴포넌트 픽스처"""
    return await mount(XCounter, count=0)


@pytest.fixture
async def todo_list(transactional_db):
    """데이터베이스와 함께 사용. async ORM으로 쓰므로 transactional_db"""
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


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_create_item():
    view = await mount(XTodoList)

    await view.call("add_item", text="New item")

    # DB에 저장되었는지 확인
    assert await Item.objects.filter(text="New item").aexists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_delete_item():
    # 테스트 아이템 생성
    item = await Item.objects.acreate(text="To delete")

    view = await mount(XTodoItem, item_id=item.id, text=item.text, completed=item.completed)

    await view.call("delete")

    # DB에서 삭제되었는지 확인
    assert not await Item.objects.filter(id=item.id).aexists()
```

> **async ORM으로 쓰는 테스트는 `django_db(transaction=True)`.**
> `@pytest.mark.django_db`는 각 테스트를 트랜잭션으로 감싸고 끝에 롤백한다. 그 트랜잭션은
> 테스트 스레드의 연결에만 걸린다. `acreate`·`asave`·`adelete`와 async 핸들러 안의 ORM 호출은
> 워커 스레드의 다른 연결에서 곧바로 커밋되므로, 쓴 레코드가 롤백되지 않고 다음 테스트에 남는다.
> 남은 레코드는 실행 순서에 따라 다음 테스트의 UNIQUE 충돌이나 개수 단언 실패로 드러난다.
> 컴포넌트 핸들러는 async이므로 DB에 쓰는 컴포넌트 테스트는 대부분 여기에 해당한다.
>
> `transaction=True`는 트랜잭션 대신 테스트가 끝날 때 테이블을 비운다. 그래서 async 쓰기도
> 남지 않는다. 비용은 크지 않다. SQLite와 모델 22개 기준으로 테스트 하나에 약 15ms다.
> 읽기만 하는 테스트(`test_computed_property`)는 그냥 `django_db`로 충분하다. 픽스처로 쓸 때는
> `db` 대신 `transactional_db`를 받는다.
>
> 그냥 `django_db`로 두고 pk로 범위를 좁혀 단언하는 방법도 있다. 그러나 그 테스트는 통과해도
> 남긴 행이 다른 테스트를 깨뜨린다.

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
import asyncio


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_error_state():
    view = await mount(XStatCard, stat_name="nonexistent")

    # assign_async()는 로딩 상태를 먼저 돌려주고, 작업은 뒤에서 돈다
    stat = view.component.stat
    assert stat.loading
    async with asyncio.timeout(2):
        while stat.loading:
            await asyncio.sleep(0.05)

    # AsyncResult가 실패 상태인지
    assert stat.failed is True
    assert stat.error is not None
```

`mount()`는 `joined()`가 돌아오면 끝나지만 `assign_async()`의 작업은 그때 아직 돌고 있다. 결과를 단언하기
전에 끝나기를 기다린다. 작업이 끝나면 같은 `AsyncResult` 객체의 상태가 바뀐다.

## 테스트 마커

### 마커 정의

```python
# conftest.py
def pytest_configure(config):
    config.addinivalue_line("markers", "unit: 단위 테스트")
    config.addinivalue_line("markers", "integration: DB를 쓰는 통합 테스트")
    config.addinivalue_line("markers", "slow: 느린 테스트")
```

`pytest.ini`의 `markers =` 항목에 적어도 같다. 등록하지 않은 마커는 경고가 나고, `--strict-markers`에서는
수집 오류다.

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
@pytest.mark.django_db(transaction=True)
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

[← 이전: File Uploads 심화](08-file-uploads.md) | [목차](README.md)
