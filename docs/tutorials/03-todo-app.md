# 03. Todo 앱

> 동작하는 전체 코드: [examples/todo/](../../examples/todo/) — CI가 매번 돌리는 예제다.

이 튜토리얼에서는 완전한 Todo 앱을 만들며 실제 애플리케이션 개발 패턴을 학습합니다.

## 학습 목표

- Django 모델과의 연동
- CRUD 작업 구현
- 모델 구독과 실시간 업데이트
- 중첩 컴포넌트
- 필터링과 URL 상태

## 완성 앱 미리보기

완성된 앱은 다음 기능을 제공합니다:
- Todo 아이템 추가/수정/삭제
- 완료 상태 토글
- 필터링 (전체/활성/완료)
- 실시간 동기화 (다른 탭에서의 변경 반영)
- 남은 아이템 개수 표시

## Part 1: 기본 설정

### 모델 정의

`todo/models.py`:

```python
from django.db import models


class Item(models.Model):
    """Todo 아이템 모델"""

    text = models.CharField(max_length=200)
    completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.text
```

```bash
python manage.py makemigrations todo
python manage.py migrate
```

### 기본 컴포넌트

`todo/live.py`:

```python
from wireview import Component
from .models import Item


class XTodoList(Component):
    """Todo 리스트 메인 컴포넌트"""

    class Meta:
        template_name = 'todo/todo_list.html'

    items: list[Item] = []

    async def joined(self):
        """컴포넌트가 연결되면 아이템 로드"""
        self.items = [item async for item in Item.objects.all()]
```

`items`에는 모델 인스턴스가 그대로 들어간다. 상태는 서명된 `data-state`로 페이지에 실리는데, 모델 인스턴스는
어디에 있든(단일 필드, `list`·`tuple`·`set`, `dict`의 값, `AsyncResult`의 결과) **pk로 서명되고**, 다시 join할 때
필드의 타입 표기(`list[Item]`)를 따라 다시 읽힌다. 목록은 쿼리 한 번이고 순서를 지킨다. 상태에 남는 것은 행의
정체(pk)뿐이라 재join 때는 그 순간의 행을 읽는다 — 그 사이 삭제된 행은 목록에서 빠지고, 단일 필드(`item: Item | None`)는
`None`이 된다. 그래서 타입 표기가 필요하다: 그냥 `list`라고 적으면 무엇을 다시 읽을지 몰라 pk가 그대로 남는다.

### 템플릿

`todo/templates/todo/todo_list.html`:

```html
{% load wireview %}
<div {% tag_header %} class="todo-app">
  <h1>Todo List</h1>

  <ul class="todo-items">
    {% for item in items %}
      <li class="{% if item.completed %}completed{% endif %}">
        {{ item.text }}
      </li>
    {% empty %}
      <li class="empty">No items yet</li>
    {% endfor %}
  </ul>
</div>
```

## Part 2: CRUD 구현

### 아이템 추가

컴포넌트에 추가:

```python
class XTodoList(Component):
    class Meta:
        template_name = 'todo/todo_list.html'

    items: list[Item] = []
    new_item_text: str = ""

    async def joined(self):
        self.items = [item async for item in Item.objects.all()]

    async def add_item(self, text: str):
        """새 아이템 추가"""
        text = text.strip()
        if not text:
            return

        item = await Item.objects.acreate(text=text)
        self.items.insert(0, item)
        self.new_item_text = ""  # 입력 필드 초기화
```

템플릿 수정:

```html
{% load wireview %}
<div {% tag_header %} class="todo-app">
  <h1>Todo List</h1>

  <form class="add-form">
    <input
      type="text"
      name="text"
      placeholder="What needs to be done?"
      value="{{ new_item_text }}"
      {% on "keypress.enter.prevent" "add_item" %}
    >
    <button type="button" {% on "click" "add_item" %}>Add</button>
  </form>

  <ul class="todo-items">
    {% for item in items %}
      <li class="{% if item.completed %}completed{% endif %}">
        {{ item.text }}
      </li>
    {% empty %}
      <li class="empty">No items yet</li>
    {% endfor %}
  </ul>
</div>
```

### 완료 상태 토글

```python
async def toggle_item(self, item_id: int):
    """아이템 완료 상태 토글"""
    item = await Item.objects.aget(id=item_id)
    item.completed = not item.completed
    await item.asave()

    # 로컬 상태 업데이트
    for i, it in enumerate(self.items):
        if it.id == item_id:
            self.items[i] = item
            break
```

```html
<li class="{% if item.completed %}completed{% endif %}">
  <input
    type="checkbox"
    {% cond {'checked': item.completed} %}
    {% on "change" "toggle_item" item_id=item.id %}
  >
  <span>{{ item.text }}</span>
</li>
```

### 아이템 삭제

```python
async def delete_item(self, item_id: int):
    """아이템 삭제"""
    await Item.objects.filter(id=item_id).adelete()
    self.items = [it for it in self.items if it.id != item_id]
```

```html
<li class="{% if item.completed %}completed{% endif %}">
  <input
    type="checkbox"
    {% cond {'checked': item.completed} %}
    {% on "change" "toggle_item" item_id=item.id %}
  >
  <span>{{ item.text }}</span>
  <button class="delete" {% on "click" "delete_item" item_id=item.id %}>×</button>
</li>
```

## Part 3: 필터링

### 필터 상태 추가

```python
from wireview import WireviewMeta


class XTodoList(Component):
    class Meta:
        template_name = 'todo/todo_list.html'

    items: list[Item] = []
    new_item_text: str = ""
    filter: str = "all"  # all, active, completed

    @classmethod
    def new(cls, wire: WireviewMeta, **kwargs):
        # URL에서 필터 상태 복원
        kwargs.setdefault("filter", wire.params.get("filter", "all"))
        return cls(wire=wire, **kwargs)

    async def joined(self):
        await self._load_items()

    async def set_filter(self, filter: str):
        """필터 변경"""
        self.filter = filter
        self.wire.params["filter"] = filter
        await self._load_items()

    async def _load_items(self):
        """필터에 따라 아이템 로드"""
        qs = Item.objects.all()

        if self.filter == "active":
            qs = qs.filter(completed=False)
        elif self.filter == "completed":
            qs = qs.filter(completed=True)

        self.items = [item async for item in qs]

    @property
    def active_count(self) -> int:
        """활성(미완료) 아이템 개수"""
        return sum(1 for item in self.items if not item.completed)
```

### 필터 UI

```html
{% load wireview %}
<div {% tag_header %} class="todo-app">
  <h1>Todo List</h1>

  <form class="add-form">
    <input
      type="text"
      name="text"
      placeholder="What needs to be done?"
      {% on "keypress.enter.prevent" "add_item" %}
    >
  </form>

  <ul class="todo-items">
    {% for item in items %}
      <li class="{% if item.completed %}completed{% endif %}">
        <input
          type="checkbox"
          {% cond {'checked': item.completed} %}
          {% on "change" "toggle_item" item_id=item.id %}
        >
        <span>{{ item.text }}</span>
        <button class="delete" {% on "click" "delete_item" item_id=item.id %}>×</button>
      </li>
    {% empty %}
      <li class="empty">No items</li>
    {% endfor %}
  </ul>

  <footer class="todo-footer">
    <span class="count">{{ this.active_count }} items left</span>

    <div class="filters">
      <button
        {% class {'active': filter == 'all'} %}
        {% on "click" "set_filter" filter="all" %}
      >All</button>
      <button
        {% class {'active': filter == 'active'} %}
        {% on "click" "set_filter" filter="active" %}
      >Active</button>
      <button
        {% class {'active': filter == 'completed'} %}
        {% on "click" "set_filter" filter="completed" %}
      >Completed</button>
    </div>
  </footer>
</div>
```

## Part 4: 모델 구독

다른 탭이나 사용자의 변경 사항을 실시간으로 반영합니다.

### Auto Broadcast 설정

`settings.py`:

```python
from wireview import AutoBroadcast

WIREVIEW = {
    "AUTO_BROADCAST": AutoBroadcast(
        model=True,
        model_pk=True,
        senders={("todo", "Item")},  # 알릴 모델. 비우면 아무것도 알리지 않는다
    ),
}
```

### 구독 설정

```python
from wireview import ModelAction


class XTodoList(Component):
    class Meta:
        template_name = 'todo/todo_list.html'
        # 모델 변경 구독 ({app_label}.{model_name} 형식)
        subscriptions = {"todo.item"}

    items: list[Item] = []
    filter: str = "all"

    async def mutation(self, channel: str, action: ModelAction, instance):
        """모델 변경 시 호출"""
        if action == ModelAction.CREATED:
            # 현재 필터에 맞으면 추가
            if self._should_show(instance):
                self.items.insert(0, instance)

        elif action == ModelAction.UPDATED:
            # 기존 아이템 업데이트 또는 필터에 따라 추가/제거
            for i, item in enumerate(self.items):
                if item.id == instance.id:
                    if self._should_show(instance):
                        self.items[i] = instance
                    else:
                        self.items.pop(i)
                    return

            # 아이템이 목록에 없지만 이제 표시해야 하는 경우
            if self._should_show(instance):
                self.items.insert(0, instance)

        elif action == ModelAction.DELETED:
            self.items = [it for it in self.items if it.id != instance.id]

    def _should_show(self, item) -> bool:
        """현재 필터에서 아이템을 표시해야 하는지"""
        if self.filter == "all":
            return True
        elif self.filter == "active":
            return not item.completed
        else:  # completed
            return item.completed
```

이제 두 개의 브라우저 탭을 열고 한쪽에서 아이템을 추가하면 다른 쪽에서도 실시간으로 반영됩니다.

## Part 5: 중첩 컴포넌트

각 Todo 아이템을 별도의 컴포넌트로 분리합니다.

### XTodoItem 컴포넌트

```python
class XTodoItem(Component):
    """개별 Todo 아이템 컴포넌트"""

    class Meta:
        template_name = 'todo/todo_item.html'

    item_id: int
    text: str
    completed: bool
    editing: bool = False
    edit_text: str = ""

    # 이 아이템의 변경만 구독 ({app_label}.{model_name}.{pk} 형식)
    def get_subscriptions(self) -> set[str]:
        return {f"todo.item.{self.item_id}"}

    async def toggle(self):
        """완료 상태 토글"""
        self.completed = not self.completed
        await Item.objects.filter(id=self.item_id).aupdate(completed=self.completed)

    async def start_edit(self):
        """편집 모드 시작"""
        self.editing = True
        self.edit_text = self.text
        # 인자는 CSS 선택자다. 포커스는 이 핸들러의 렌더가 입력 칸을 그린 뒤에 적용된다
        await self.focus_on(f"#edit-{self.item_id}")

    async def save_edit(self, text: str):
        """편집 저장"""
        text = text.strip()
        if text:
            self.text = text
            await Item.objects.filter(id=self.item_id).aupdate(text=text)
        self.editing = False

    async def cancel_edit(self):
        """편집 취소"""
        self.editing = False

    async def delete(self):
        """아이템 삭제"""
        await Item.objects.filter(id=self.item_id).adelete()
        await self.destroy()  # 컴포넌트 제거

    async def mutation(self, channel: str, action: ModelAction, instance):
        """다른 곳에서 변경된 경우"""
        if action == ModelAction.UPDATED:
            self.text = instance.text
            self.completed = instance.completed
        elif action == ModelAction.DELETED:
            await self.destroy()
```

### XTodoItem 템플릿

`todo/templates/todo/todo_item.html`:

```html
{% load wireview %}
<li {% tag_header %} class="todo-item {% if completed %}completed{% endif %} {% if editing %}editing{% endif %}">
  {% if editing %}
    <input
      id="edit-{{ item_id }}"
      type="text"
      name="text"
      value="{{ edit_text }}"
      {% on "keypress.enter.prevent" "save_edit" %}
      {% on "keydown.key.escape" "cancel_edit" %}
      {% on "blur" "save_edit" %}
    >
  {% else %}
    <input
      type="checkbox"
      {% cond {'checked': completed} %}
      {% on "change" "toggle" %}
    >
    <span {% on "dblclick" "start_edit" %}>{{ text }}</span>
    <button class="delete" {% on "click" "delete" %}>×</button>
  {% endif %}
</li>
```

### XTodoList에서 사용

```python
class XTodoList(Component):
    class Meta:
        template_name = 'todo/todo_list.html'
        subscriptions = {"todo.item"}

    items: list[Item] = []
    filter: str = "all"

    # ... 기존 코드 ...
```

```html
{% load wireview %}
<div {% tag_header %} class="todo-app">
  <h1>Todo List</h1>

  <form class="add-form">
    <input
      type="text"
      name="text"
      placeholder="What needs to be done?"
      {% on "keypress.enter.prevent" "add_item" %}
    >
  </form>

  <ul class="todo-items">
    {% for item in items %}
      {% component 'XTodoItem' item_id=item.id text=item.text completed=item.completed %}
    {% empty %}
      <li class="empty">No items</li>
    {% endfor %}
  </ul>

  <footer class="todo-footer">
    <span class="count">{{ this.active_count }} items left</span>
    <!-- 필터 버튼들 -->
  </footer>
</div>
```

## Part 6: 완성된 코드

### 최종 live.py

```python
from wireview import Component, WireviewMeta, ModelAction
from .models import Item


class XTodoList(Component):
    """Todo 리스트 메인 컴포넌트"""

    class Meta:
        template_name = 'todo/todo_list.html'
        subscriptions = {"todo.item"}

    items: list[Item] = []
    filter: str = "all"

    @classmethod
    def new(cls, wire: WireviewMeta, **kwargs):
        kwargs.setdefault("filter", wire.params.get("filter", "all"))
        return cls(wire=wire, **kwargs)

    async def joined(self):
        await self._load_items()

    async def add_item(self, text: str):
        text = text.strip()
        if not text:
            return
        await Item.objects.acreate(text=text)
        # mutation에서 목록 업데이트 처리

    async def set_filter(self, filter: str):
        self.filter = filter
        self.wire.params["filter"] = filter
        await self._load_items()

    async def clear_completed(self):
        await Item.objects.filter(completed=True).adelete()
        # mutation에서 목록 업데이트 처리

    async def mutation(self, channel: str, action: ModelAction, instance):
        if action == ModelAction.CREATED:
            if self._should_show(instance):
                self.items.insert(0, instance)
        elif action == ModelAction.UPDATED:
            await self._load_items()  # 간단하게 전체 리로드
        elif action == ModelAction.DELETED:
            self.items = [it for it in self.items if it.id != instance.id]

    async def _load_items(self):
        qs = Item.objects.all()
        if self.filter == "active":
            qs = qs.filter(completed=False)
        elif self.filter == "completed":
            qs = qs.filter(completed=True)
        self.items = [item async for item in qs]

    def _should_show(self, item) -> bool:
        if self.filter == "all":
            return True
        elif self.filter == "active":
            return not item.completed
        return item.completed

    @property
    def active_count(self) -> int:
        # 지금 보이는 목록 안의 미완료 개수 (Completed 필터에서는 0)
        return sum(1 for item in self.items if not item.completed)

    @property
    def has_completed(self) -> bool:
        return any(item.completed for item in self.items)


class XTodoItem(Component):
    """개별 Todo 아이템 컴포넌트"""

    class Meta:
        template_name = 'todo/todo_item.html'

    item_id: int
    text: str
    completed: bool
    editing: bool = False

    def get_subscriptions(self) -> set[str]:
        return {f"todo.item.{self.item_id}"}

    async def toggle(self):
        self.completed = not self.completed
        await Item.objects.filter(id=self.item_id).aupdate(completed=self.completed)

    async def start_edit(self):
        self.editing = True
        await self.focus_on(f"#edit-{self.item_id}")

    async def save_edit(self, text: str):
        text = text.strip()
        if text and text != self.text:
            self.text = text
            await Item.objects.filter(id=self.item_id).aupdate(text=text)
        self.editing = False

    async def cancel_edit(self):
        self.editing = False

    async def delete(self):
        await Item.objects.filter(id=self.item_id).adelete()
        await self.destroy()

    async def mutation(self, channel: str, action: ModelAction, instance):
        if action == ModelAction.UPDATED:
            self.text = instance.text
            self.completed = instance.completed
        elif action == ModelAction.DELETED:
            await self.destroy()
```

### 최종 템플릿

`todo/templates/todo/todo_list.html`:

```html
{% load wireview %}
<section {% tag_header %} class="todoapp">
  <header class="header">
    <h1>todos</h1>
    <input
      class="new-todo"
      name="text"
      placeholder="What needs to be done?"
      autofocus
      {% on "keypress.enter.prevent" "add_item" %}
    >
  </header>

  {% if items %}
  <section class="main">
    <ul class="todo-list">
      {% for item in items %}
        {% component 'XTodoItem' item_id=item.id text=item.text completed=item.completed %}
      {% endfor %}
    </ul>
  </section>

  <footer class="footer">
    <span class="todo-count">
      <strong>{{ this.active_count }}</strong>
      item{{ this.active_count|pluralize }} left
    </span>

    <ul class="filters">
      <li>
        <a
          href="#"
          {% class {'selected': filter == 'all'} %}
          {% on "click.prevent" "set_filter" filter="all" %}
        >All</a>
      </li>
      <li>
        <a
          href="#"
          {% class {'selected': filter == 'active'} %}
          {% on "click.prevent" "set_filter" filter="active" %}
        >Active</a>
      </li>
      <li>
        <a
          href="#"
          {% class {'selected': filter == 'completed'} %}
          {% on "click.prevent" "set_filter" filter="completed" %}
        >Completed</a>
      </li>
    </ul>

    {% if this.has_completed %}
    <button class="clear-completed" {% on "click" "clear_completed" %}>
      Clear completed
    </button>
    {% endif %}
  </footer>
  {% endif %}
</section>
```

`todo/templates/todo/todo_item.html`:

```html
{% load wireview %}
<li {% tag_header %} class="{% if completed %}completed{% endif %} {% if editing %}editing{% endif %}">
  <div class="view">
    <input
      class="toggle"
      type="checkbox"
      {% cond {'checked': completed} %}
      {% on "change" "toggle" %}
    >
    <label {% on "dblclick" "start_edit" %}>{{ text }}</label>
    <button class="destroy" {% on "click" "delete" %}></button>
  </div>
  {% if editing %}
  <input
    id="edit-{{ item_id }}"
    class="edit"
    name="text"
    value="{{ text }}"
    {% on "keypress.enter.prevent" "save_edit" %}
    {% on "keydown.key.escape" "cancel_edit" %}
    {% on "blur" "save_edit" %}
  >
  {% endif %}
</li>
```

## 연습 문제

1. **우선순위 추가**: 아이템에 우선순위(높음/중간/낮음)를 추가하고 정렬하기
2. **마감일 추가**: 아이템에 마감일을 추가하고 기한 지난 항목 강조하기
3. **드래그 앤 드롭**: 아이템 순서를 드래그로 변경하기 (도전 과제)

## 다음 단계

Todo 앱을 통해 실제 애플리케이션의 CRUD, 모델 구독, 중첩 컴포넌트를 학습했습니다.

다음 튜토리얼에서는 디바운스와 JS 명령으로 입력하는 대로 결과가 바뀌는 검색을 만듭니다.

[← 이전: 11. Rating 앱](11-rating-app.md) | [목차](README.md) | [다음: 12. Live Search →](12-live-search.md)
