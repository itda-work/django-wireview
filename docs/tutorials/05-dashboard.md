# 05. Dashboard

> 동작하는 전체 코드: [examples/dashboard/](../../examples/dashboard/) — CI가 매번 돌리는 예제다.

이 튜토리얼에서는 대시보드를 만들며 AsyncResult와 복합 컴포넌트 패턴을 학습합니다.

## 학습 목표

- AsyncResult로 비동기 데이터 로딩 처리
- 로딩/성공/에러 상태 UI
- 복합 컴포넌트 구성
- Streams와 페이지네이션 조합
- URL 상태로 탭/필터 관리

## 완성 앱 미리보기

- 여러 통계 카드 (각각 비동기 로딩)
- 탭 네비게이션
- 활동 피드 (무한 스크롤)
- 실시간 업데이트

## Part 1: 기본 설정

### 모델 정의

`dashboard/models.py`:

```python
from django.db import models


class Stat(models.Model):
    """통계 데이터"""
    name = models.CharField(max_length=100)
    value = models.IntegerField(default=0)
    change = models.FloatField(default=0)  # 변화율 (%)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name}: {self.value}"


class Activity(models.Model):
    """활동 로그"""
    ACTION_CHOICES = [
        ('create', 'Created'),
        ('update', 'Updated'),
        ('delete', 'Deleted'),
        ('login', 'Logged in'),
    ]

    user = models.CharField(max_length=100)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    target = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user} {self.action} {self.target}"
```

## Part 2: AsyncResult - 비동기 로딩 상태

### 개념

AsyncResult는 비동기 작업의 상태를 추적합니다:

```python
from wireview import AsyncResult
result.loading  # 로딩 중
result.ok       # 성공
result.failed   # 실패
result.done     # 완료 (성공 또는 실패)

# 값
result.result        # 성공 시 결과값
result.error         # 실패 시 예외
result.error_message # 에러 메시지
```

### StatCard 컴포넌트

`dashboard/live.py`:

```python
from wireview import Component, AsyncResult
from .models import Stat
import asyncio


class XStatCard(Component):
    """통계 카드 - AsyncResult 사용"""

    class Meta:
        template_name = 'dashboard/stat_card.html'
        exclude_fields = {"stat"}

    stat_name: str
    stat: AsyncResult = None

    async def joined(self):
        """컴포넌트 연결 시 데이터 로드 시작"""
        # assign_async로 비동기 작업 시작
        self.stat = await self.assign_async(self._load_stat())

    async def _load_stat(self):
        """통계 데이터 로드 (시뮬레이션된 지연)"""
        # 실제 환경에서는 외부 API 호출 등
        await asyncio.sleep(0.5)  # 로딩 시뮬레이션
        return await Stat.objects.aget(name=self.stat_name)

    async def refresh(self):
        """수동 새로고침"""
        self.stat = await self.assign_async(self._load_stat())
```

`_load_stat`은 `_`로 시작한다. 밑줄 없는 메서드는 클라이언트가 이벤트로 부를 수 있는 핸들러가 된다.

`stat`은 `Meta.exclude_fields`로 서명 상태에서 뺀다. 성공하면 모델 인스턴스를, 실패하면 예외를 담는데 서명
상태는 JSON이라 둘 다 담지 못한다. 빠진 필드는 연결 동안 서버의 인스턴스에만 있고, 다시 연결하면 `joined()`가
새로 불러온다. 뒤에 나오는 `XDashboard`의 `stats`(모델 인스턴스 목록)도 같은 이유로 뺀다.

### StatCard 템플릿

`dashboard/templates/dashboard/stat_card.html`:

`intcomma` 필터는 `django.contrib.humanize`에 있다. `INSTALLED_APPS`에 추가하고 템플릿에서 `humanize`를 로드한다.

```html
{% load wireview humanize %}
<div {% tag_header %} class="stat-card">
  {% if stat.loading %}
    <div class="loading">
      <div class="spinner"></div>
      <span>Loading...</span>
    </div>

  {% elif stat.ok %}
    <div class="stat-content">
      <h3 class="stat-name">{{ stat.result.name }}</h3>
      <div class="stat-value">{{ stat.result.value|intcomma }}</div>
      <div class="stat-change {% if stat.result.change >= 0 %}positive{% else %}negative{% endif %}">
        {% if stat.result.change >= 0 %}+{% endif %}{{ stat.result.change }}%
      </div>
    </div>
    <button class="refresh-btn" {% on "click" "refresh" %}>
      Refresh
    </button>

  {% elif stat.failed %}
    <div class="error">
      <span class="error-icon">⚠️</span>
      <span class="error-message">{{ stat.error_message }}</span>
      <button {% on "click" "refresh" %}>Retry</button>
    </div>
  {% endif %}
</div>
```

### AsyncResult 메서드

```python
# 결과 변환
mapped = stat.map(lambda s: s.value * 2)

# 기본값으로 가져오기
value = stat.get_or(default_value)

# 결과 또는 예외 발생
value = stat.get_or_raise()

# 직접 상태 설정
stat = AsyncResult.loading_state()
stat = AsyncResult.success(value)
stat = AsyncResult.failure(exception)
```

## Part 3: 대시보드 메인 컴포넌트

### 탭 네비게이션

```python
from wireview import WireviewMeta


class XDashboard(Component):
    """대시보드 메인 컴포넌트"""

    class Meta:
        template_name = 'dashboard/dashboard.html'
        exclude_fields = {"stats"}

    active_tab: str = "overview"
    stats: list = []

    @classmethod
    def new(cls, wire: WireviewMeta, **kwargs):
        # URL에서 탭 상태 복원
        kwargs.setdefault("active_tab", wire.params.get("tab", "overview"))
        return cls(wire=wire, **kwargs)

    async def joined(self):
        self.stats = [s async for s in Stat.objects.all()]

    async def set_tab(self, tab: str):
        """탭 변경"""
        self.active_tab = tab
        self.wire.params["tab"] = tab
```

### 대시보드 템플릿

`dashboard/templates/dashboard/dashboard.html`:

```html
{% load wireview %}
<div {% tag_header %} class="dashboard">
  <header class="dashboard-header">
    <h1>Dashboard</h1>
    <nav class="tabs">
      <button
        {% class {'active': active_tab == 'overview'} %}
        {% on "click" "set_tab" tab="overview" %}
      >Overview</button>
      <button
        {% class {'active': active_tab == 'activity'} %}
        {% on "click" "set_tab" tab="activity" %}
      >Activity</button>
      <button
        {% class {'active': active_tab == 'settings'} %}
        {% on "click" "set_tab" tab="settings" %}
      >Settings</button>
    </nav>
  </header>

  <main class="dashboard-content">
    {% if active_tab == 'overview' %}
      <section class="stats-grid">
        {% for stat in stats %}
          {% component 'XStatCard' stat_name=stat.name %}
        {% endfor %}
      </section>

    {% elif active_tab == 'activity' %}
      {% component 'XActivityFeed' %}

    {% elif active_tab == 'settings' %}
      <section class="settings">
        <p>Settings content here...</p>
      </section>
    {% endif %}
  </main>
</div>
```

## Part 4: Activity Feed - Streams + 페이지네이션

### ActivityFeed 컴포넌트

```python
from wireview import ModelAction
from .models import Activity


class XActivityFeed(Component):
    """활동 피드 - Streams + 무한 스크롤"""

    class Meta:
        template_name = 'dashboard/activity_feed.html'
        # 자동 브로드캐스트 채널: {app_label}.{model_name}
        subscriptions = {"dashboard.activity"}

    # 스트림 항목은 상태에 두지 않는다. 다음 페이지의 기준점만 기억한다
    oldest_id: int | None = None
    has_more: bool = True
    loading_more: bool = False

    async def joined(self):
        activities = await self._load_activities()
        await self.stream("activities", activities)
        self._advance(activities)

    async def _load_activities(self, before_id: int | None = None, limit: int = 20):
        """활동 로드 (id 역순)"""
        qs = Activity.objects.order_by('-id')
        if before_id is not None:
            qs = qs.filter(id__lt=before_id)
        return [a async for a in qs[:limit]]

    def _advance(self, activities: list, limit: int = 20):
        """불러온 페이지로 기준점과 has_more를 갱신"""
        if activities:
            self.oldest_id = activities[-1].id
        self.has_more = len(activities) >= limit

    async def load_more(self):
        """더 불러오기"""
        if self.loading_more or not self.has_more:
            return

        self.loading_more = True

        new_activities = await self._load_activities(before_id=self.oldest_id)
        for activity in new_activities:
            await self.stream_insert("activities", activity, at=-1)
        self._advance(new_activities)

        self.loading_more = False

    async def mutation(self, channel: str, action: ModelAction, instance):
        """새 활동 실시간 수신"""
        if action == ModelAction.CREATED:
            await self.stream_insert("activities", instance, at=0)
```

`stream()`은 `template=`이 없으면 `{컴포넌트 템플릿}_item.html`, 여기서는 `dashboard/activity_feed_item.html`을
항목 템플릿으로 쓴다. 그 템플릿은 항목을 `item`으로 받는다.

### ActivityFeed 템플릿

`dashboard/templates/dashboard/activity_feed.html`:

```html
{% load wireview %}
<div {% tag_header %} class="activity-feed">
  <h2>Recent Activity</h2>

  <ul wire-stream="activities" class="activity-list"></ul>

  {% if has_more %}
    <div class="load-more">
      <button
        {% on "click" "load_more" %}
        {% class {'loading': loading_more} %}
        {% cond {'disabled': loading_more} %}
      >
        {% if loading_more %}
          Loading...
        {% else %}
          Load More
        {% endif %}
      </button>
    </div>
  {% endif %}
</div>
```

`dashboard/templates/dashboard/activity_feed_item.html`:

```html
<li id="activities-{{ item.pk }}" class="activity-item">
  <div class="activity-icon {{ item.action }}">
    {% if item.action == 'create' %}➕
    {% elif item.action == 'update' %}✏️
    {% elif item.action == 'delete' %}🗑️
    {% elif item.action == 'login' %}🔑
    {% endif %}
  </div>
  <div class="activity-content">
    <span class="user">{{ item.user }}</span>
    <span class="action">{{ item.get_action_display|lower }}</span>
    <span class="target">{{ item.target }}</span>
  </div>
  <time class="activity-time">{{ item.created_at|timesince }} ago</time>
</li>
```

## Part 5: 실시간 업데이트

### 통계 자동 갱신

`Stat`이 저장되면 자동 브로드캐스트가 `dashboard.stat` 채널에 알린다(설정은 [03. Todo 앱](03-todo-app.md)의
Auto Broadcast). 카드는 이름으로 구분되므로 모델 채널을 구독하고 `mutation()`에서 자기 통계만 고른다.

```python
class XStatCard(Component):
    class Meta:
        template_name = 'dashboard/stat_card.html'
        exclude_fields = {"stat"}
        subscriptions = {"dashboard.stat"}

    stat_name: str
    stat: AsyncResult = None

    async def mutation(self, channel: str, action: ModelAction, instance):
        """통계 업데이트 수신"""
        if instance.name == self.stat_name:
            # 로딩 없이 직접 업데이트
            self.stat = AsyncResult.success(instance)
```

### 수동 브로드캐스트

모델 저장 없이 값이 바뀌는 경우(외부 집계 등)에는 직접 알린다. `broadcast()`/`abroadcast()`가 보낸 알림은
`mutation()`이 아니라 `notification()`으로 온다. 인자는 채널 레이어를 지나므로 모델 인스턴스가 아니라 JSON
직렬화 가능한 값을 넘긴다. async 코드에서는 `abroadcast()`, 동기 코드에서는 `broadcast()`를 쓴다.

```python
from wireview import abroadcast


async def refresh_stat(name: str):
    await abroadcast("dashboard-stats", name=name)
```

```python
class XStatCard(Component):
    class Meta:
        template_name = 'dashboard/stat_card.html'
        exclude_fields = {"stat"}
        subscriptions = {"dashboard.stat", "dashboard-stats"}

    # ...

    async def notification(self, channel: str, **kwargs):
        if kwargs.get("name") == self.stat_name:
            await self.refresh()
```

## Part 6: 완성된 코드

### 전체 live.py

```python
from wireview import Component, AsyncResult, WireviewMeta, ModelAction
from .models import Stat, Activity
import asyncio


class XDashboard(Component):
    """대시보드 메인 컴포넌트"""

    class Meta:
        template_name = 'dashboard/dashboard.html'
        exclude_fields = {"stats"}

    active_tab: str = "overview"
    stats: list = []

    @classmethod
    def new(cls, wire: WireviewMeta, **kwargs):
        kwargs.setdefault("active_tab", wire.params.get("tab", "overview"))
        return cls(wire=wire, **kwargs)

    async def joined(self):
        self.stats = [s async for s in Stat.objects.all()]

    async def set_tab(self, tab: str):
        self.active_tab = tab
        self.wire.params["tab"] = tab


class XStatCard(Component):
    """통계 카드 컴포넌트"""

    class Meta:
        template_name = 'dashboard/stat_card.html'
        exclude_fields = {"stat"}
        subscriptions = {"dashboard.stat"}

    stat_name: str
    stat: AsyncResult = None

    async def joined(self):
        self.stat = await self.assign_async(self._load_stat())

    async def _load_stat(self):
        await asyncio.sleep(0.3)  # 로딩 시뮬레이션
        return await Stat.objects.aget(name=self.stat_name)

    async def refresh(self):
        self.stat = await self.assign_async(self._load_stat())

    async def mutation(self, channel: str, action: ModelAction, instance):
        if instance.name == self.stat_name:
            self.stat = AsyncResult.success(instance)


class XActivityFeed(Component):
    """활동 피드 컴포넌트"""

    class Meta:
        template_name = 'dashboard/activity_feed.html'
        subscriptions = {"dashboard.activity"}

    oldest_id: int | None = None
    has_more: bool = True
    loading_more: bool = False

    async def joined(self):
        activities = await self._load_activities()
        await self.stream("activities", activities)
        self._advance(activities)

    async def _load_activities(self, before_id: int | None = None, limit: int = 20):
        qs = Activity.objects.order_by('-id')
        if before_id is not None:
            qs = qs.filter(id__lt=before_id)
        return [a async for a in qs[:limit]]

    def _advance(self, activities: list, limit: int = 20):
        if activities:
            self.oldest_id = activities[-1].id
        self.has_more = len(activities) >= limit

    async def load_more(self):
        if self.loading_more or not self.has_more:
            return

        self.loading_more = True

        new_activities = await self._load_activities(before_id=self.oldest_id)
        for activity in new_activities:
            await self.stream_insert("activities", activity, at=-1)
        self._advance(new_activities)

        self.loading_more = False

    async def mutation(self, channel: str, action: ModelAction, instance):
        if action == ModelAction.CREATED:
            await self.stream_insert("activities", instance, at=0)
```

### CSS 예제

```css
.dashboard {
  padding: 2rem;
  max-width: 1200px;
  margin: 0 auto;
}

.tabs {
  display: flex;
  gap: 1rem;
  margin-bottom: 2rem;
}

.tabs button {
  padding: 0.5rem 1rem;
  border: none;
  background: #f0f0f0;
  cursor: pointer;
  border-radius: 4px;
}

.tabs button.active {
  background: #007bff;
  color: white;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 1.5rem;
}

.stat-card {
  background: white;
  border-radius: 8px;
  padding: 1.5rem;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
  min-height: 150px;
}

.stat-card .loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100px;
}

.spinner {
  width: 30px;
  height: 30px;
  border: 3px solid #f0f0f0;
  border-top-color: #007bff;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.stat-value {
  font-size: 2.5rem;
  font-weight: bold;
}

.stat-change {
  font-size: 0.9rem;
}

.stat-change.positive { color: green; }
.stat-change.negative { color: red; }

.activity-list {
  list-style: none;
  padding: 0;
}

.activity-item {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1rem;
  border-bottom: 1px solid #eee;
}

.activity-icon {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f0f0f0;
  border-radius: 50%;
}

.activity-content {
  flex: 1;
}

.activity-time {
  color: #999;
  font-size: 0.9rem;
}

.load-more {
  text-align: center;
  padding: 1rem;
}

.load-more button.loading {
  opacity: 0.7;
}
```

## 연습 문제

1. **차트 컴포넌트**: Chart.js를 통합해 통계 시각화
2. **날짜 필터**: 활동 피드에 날짜 범위 필터 추가
3. **검색**: 활동 피드 검색 기능
4. **알림**: 새 활동 발생 시 브라우저 알림

## 다음 단계

Dashboard를 통해 AsyncResult, 복합 컴포넌트, Streams + 페이지네이션을 학습했습니다.

다음은 심화 튜토리얼에서 각 API를 더 깊이 다룹니다.

[← 이전: 04. Chat 앱](04-chat-app.md) | [다음: 06. Streams API 심화 →](06-streams-api.md)
