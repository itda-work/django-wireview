# 15. LiveComponent - 중첩 컴포넌트

> 동작하는 전체 코드: [examples/livecomp/](../../examples/livecomp/) — CI가 매번 돌리는 예제다.

이 튜토리얼에서는 LiveComponent를 사용하여 독립적인 상태를 가진 중첩 컴포넌트를 만드는 방법을 학습합니다.

## 학습 목표

- LiveComponent vs Component 차이점 이해
- 부모-자식 컴포넌트 통신
- `myself=True` 이벤트 타겟팅
- 실용적인 대시보드 예제

## 전제 조건

- [02. Counter 컴포넌트](02-counter-component.md) 완료
- [05. Dashboard](05-dashboard.md) 기본 지식

---

## Part 1: LiveComponent란?

### Component vs LiveComponent

| 특성 | Component | LiveComponent |
|-----|-----------|---------------|
| 상태 | 독립적 | 독립적 |
| WebSocket | 자체 연결 | 부모와 공유 |
| 렌더링 | 페이지 레벨 | 부모 내부 |
| 이벤트 타겟 | 자동 | `myself=True` 필요 |
| 용도 | 페이지 컴포넌트 | 재사용 가능 위젯 |

### 언제 사용하나요?

**LiveComponent 사용**:
- 여러 인스턴스가 필요한 재사용 가능 위젯
- 부모와 통신이 필요한 자식 컴포넌트
- 독립적인 상태를 가진 대시보드 카드

**일반 Component 사용**:
- 페이지 레벨 컴포넌트
- 단일 인스턴스만 필요한 경우
- 완전히 독립적인 기능

---

## Part 2: 기본 사용법

### 2.1 LiveComponent 정의

`widgets/live.py`:

```python
from wireview import LiveComponent


class Counter(LiveComponent):
    """재사용 가능한 카운터 위젯."""

    _template_name = "widgets/counter.html"

    # 상태
    count: int = 0
    label: str = "Count"

    async def increment(self):
        """증가 버튼 핸들러."""
        self.count += 1

    async def decrement(self):
        """감소 버튼 핸들러."""
        self.count -= 1
```

### 2.2 템플릿 작성

`templates/widgets/counter.html`:

```html
{% load wireview %}

<div {% live_tag_header %} class="counter-widget">
  <h4>{{ label }}</h4>
  <div class="counter-controls">
    <button {% on "click" "decrement" myself=True %}>-</button>
    <span class="count">{{ count }}</span>
    <button {% on "click" "increment" myself=True %}>+</button>
  </div>
</div>
```

**핵심 포인트**:
- `{% live_tag_header %}`: LiveComponent 전용 헤더 (data-parent 포함)
- `myself=True`: 이벤트를 이 LiveComponent로 타겟팅

### 2.3 부모에서 사용

`dashboard/live.py`:

```python
from wireview import Component


class Dashboard(Component):
    _template_name = "dashboard/dashboard.html"

    title: str = "My Dashboard"
```

`templates/dashboard/dashboard.html`:

```html
{% load wireview %}

<div {% tag_header %}>
  <h1>{{ title }}</h1>

  <div class="widgets">
    {% live_component "Counter" id="counter-1" label="방문자" count=100 %}
    {% live_component "Counter" id="counter-2" label="주문" count=42 %}
    {% live_component "Counter" id="counter-3" label="매출" count=1234 %}
  </div>
</div>
```

**중요**: 각 LiveComponent에는 고유한 `id`가 필요합니다.

---

## Part 3: 부모-자식 통신

### 3.1 자식 → 부모 (send_to_parent)

자식이 부모에게 이벤트를 보내는 패턴입니다.

`widgets/live.py`:

```python
class Counter(LiveComponent):
    _template_name = "widgets/counter.html"

    count: int = 0
    label: str = "Count"

    async def increment(self):
        self.count += 1
        # 부모에게 알림
        await self.send_to_parent(
            "counter_changed",
            counter_id=self.id,
            count=self.count
        )
```

`dashboard/live.py`:

```python
class Dashboard(Component):
    _template_name = "dashboard/dashboard.html"

    title: str = "My Dashboard"
    total: int = 0

    async def counter_changed(self, counter_id: str, count: int):
        """자식 Counter가 변경되면 호출됨."""
        # 총합 재계산
        self.total = sum(
            c.count for c in self.wire.repo.get_live_components(self.id)
        )
```

### 3.2 부모 → 자식 (send_update)

부모가 자식의 상태를 업데이트하는 패턴입니다.

```python
class Dashboard(Component):
    _template_name = "dashboard/dashboard.html"

    async def reset_all(self):
        """모든 카운터를 0으로 리셋."""
        for counter in self.wire.repo.get_live_components(self.id):
            await self.send_update(counter.id, count=0)

    async def set_counter(self, counter_id: str, value: int):
        """특정 카운터 값 설정."""
        await self.send_update(counter_id, count=value)
```

---

## Part 4: 실습 - 대시보드 만들기

### 4.1 프로젝트 구조

```
myapp/
├── live.py
├── templates/
│   └── myapp/
│       ├── dashboard.html
│       └── stat_counter.html
└── urls.py
```

### 4.2 모델

`myapp/models.py`:

```python
from django.db import models


class Stat(models.Model):
    """통계 데이터."""
    name = models.CharField(max_length=100, unique=True)
    value = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.name}: {self.value}"
```

### 4.3 LiveComponent 구현

`myapp/live.py`:

```python
from wireview import Component, LiveComponent
from .models import Stat


class StatCounter(LiveComponent):
    """통계 카운터 위젯 - DB와 동기화."""

    _template_name = "myapp/stat_counter.html"
    _subscriptions = {"myapp.stat"}  # DB 변경 구독

    stat_name: str
    value: int = 0
    is_loading: bool = False

    async def joined(self):
        """초기 데이터 로드."""
        await self._load_stat()

    async def _load_stat(self):
        """DB에서 통계 로드."""
        try:
            stat = await Stat.objects.aget(name=self.stat_name)
            self.value = stat.value
        except Stat.DoesNotExist:
            self.value = 0

    async def increment(self, amount: int = 1):
        """값 증가 및 DB 저장."""
        self.is_loading = True
        await self.skip_render()  # 로딩 표시

        stat, _ = await Stat.objects.aget_or_create(
            name=self.stat_name,
            defaults={"value": 0}
        )
        stat.value += amount
        await stat.asave()

        self.value = stat.value
        self.is_loading = False

        # 부모에게 알림
        await self.send_to_parent("stat_updated", name=self.stat_name, value=self.value)

    async def mutation(self, channel, action, instance):
        """다른 곳에서 DB가 변경되면 업데이트."""
        if instance.name == self.stat_name:
            self.value = instance.value


class StatsDashboard(Component):
    """통계 대시보드."""

    _template_name = "myapp/dashboard.html"

    stats: list[str] = ["visitors", "orders", "revenue"]
    last_updated: str = ""

    async def stat_updated(self, name: str, value: int):
        """자식에서 통계가 업데이트되면 호출."""
        from datetime import datetime
        self.last_updated = f"{name}: {value} (at {datetime.now():%H:%M:%S})"

    async def reset_all(self):
        """모든 통계 리셋."""
        for stat_name in self.stats:
            counter_id = f"stat-{stat_name}"
            await self.send_update(counter_id, value=0)

        # DB도 리셋
        await Stat.objects.filter(name__in=self.stats).aupdate(value=0)
```

### 4.4 템플릿

`templates/myapp/stat_counter.html`:

```html
{% load wireview %}

<div {% live_tag_header %} class="stat-card {% if is_loading %}loading{% endif %}">
  <h3>{{ stat_name|title }}</h3>
  <div class="stat-value">{{ value }}</div>
  <div class="stat-actions">
    <button {% on "click" "increment" amount=-1 myself=True %}
            {% if is_loading %}disabled{% endif %}>
      -1
    </button>
    <button {% on "click" "increment" myself=True %}
            {% if is_loading %}disabled{% endif %}>
      +1
    </button>
    <button {% on "click" "increment" amount=10 myself=True %}
            {% if is_loading %}disabled{% endif %}>
      +10
    </button>
  </div>
</div>
```

`templates/myapp/dashboard.html`:

```html
{% load wireview %}

<div {% tag_header %} class="stats-dashboard">
  <header>
    <h1>Statistics Dashboard</h1>
    <button {% on "click" "reset_all" %}>Reset All</button>
  </header>

  {% if last_updated %}
    <p class="last-updated">Last update: {{ last_updated }}</p>
  {% endif %}

  <div class="stats-grid">
    {% for stat_name in stats %}
      {% live_component "StatCounter" id="stat-"|add:stat_name stat_name=stat_name %}
    {% endfor %}
  </div>
</div>
```

### 4.5 스타일

```css
.stats-dashboard {
  max-width: 800px;
  margin: 0 auto;
  padding: 20px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 20px;
}

.stat-card {
  background: white;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
  transition: opacity 0.2s;
}

.stat-card.loading {
  opacity: 0.6;
}

.stat-value {
  font-size: 2.5rem;
  font-weight: bold;
  text-align: center;
  margin: 20px 0;
}

.stat-actions {
  display: flex;
  gap: 10px;
  justify-content: center;
}

.stat-actions button {
  padding: 8px 16px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  background: #007bff;
  color: white;
}

.stat-actions button:disabled {
  background: #ccc;
  cursor: not-allowed;
}
```

---

## Part 5: 고급 패턴

### 5.1 조건부 LiveComponent

```html
{% if show_advanced_stats %}
  {% live_component "AdvancedStatCounter" id="advanced-stats" %}
{% endif %}
```

### 5.2 동적 ID 생성

```html
{% for item in items %}
  {% live_component "ItemWidget" id="item-"|add:item.pk item_id=item.pk %}
{% endfor %}
```

### 5.3 update() 콜백 활용

```python
class Counter(LiveComponent):
    count: int = 0
    previous_count: int = 0

    async def update(self, **assigns):
        """부모가 props를 변경할 때 호출."""
        self.previous_count = self.count
        await super().update(**assigns)

        # 변경 감지
        if self.count != self.previous_count:
            await self.on_count_changed()

    async def on_count_changed(self):
        """count가 변경되면 호출."""
        print(f"Count changed: {self.previous_count} → {self.count}")
```

---

## 요약

### 핵심 포인트

1. **LiveComponent 정의**: `LiveComponent` 상속
2. **템플릿 헤더**: `{% live_tag_header %}` 사용
3. **이벤트 타겟팅**: `myself=True` 필수
4. **부모에서 사용**: `{% live_component "Name" id="unique-id" %}`
5. **자식→부모 통신**: `await self.send_to_parent("event", **kwargs)`
6. **부모→자식 통신**: `await self.send_update("child-id", **kwargs)`

### 라이프사이클

```
1. 부모 렌더링
2. {% live_component %} → LiveComponent 생성
3. 부모 렌더 완료
4. LiveComponent.joined() 호출
5. LiveComponent 렌더링
6. (re-render 시) LiveComponent.update(**changed_props) 호출
```

### 주의사항

- 각 LiveComponent에는 고유한 `id` 필요
- 이벤트에 `myself=True` 빠뜨리면 부모로 전달됨
- `send_to_parent`는 부모의 메서드를 직접 호출

---

## 다음 단계

- [docs/features/live-component.md](../features/live-component.md) - 상세 레퍼런스
- [05. Dashboard](05-dashboard.md) - AsyncResult 활용
- [04. Chat 앱](04-chat-app.md) - Streams와 Presence

---

## 완성 코드

전체 예제는 [examples/livecomp/](../../examples/livecomp/)에 있습니다.
