# 05. Dashboard

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

# 상태
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

    _template_name = 'dashboard/stat_card.html'

    stat_name: str
    stat: AsyncResult = None

    async def joined(self):
        """컴포넌트 연결 시 데이터 로드 시작"""
        # assign_async로 비동기 작업 시작
        self.stat = await self.assign_async(self.load_stat())

    async def load_stat(self):
        """통계 데이터 로드 (시뮬레이션된 지연)"""
        # 실제 환경에서는 외부 API 호출 등
        await asyncio.sleep(0.5)  # 로딩 시뮬레이션
        return await Stat.objects.aget(name=self.stat_name)

    async def refresh(self):
        """수동 새로고침"""
        self.stat = await self.assign_async(self.load_stat())
```

### StatCard 템플릿

`dashboard/templates/dashboard/stat_card.html`:

```html
{% load wireview %}
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
stat = AsyncResult.loading()
stat = AsyncResult.success(value)
stat = AsyncResult.failure(exception)
```

## Part 3: 대시보드 메인 컴포넌트

### 탭 네비게이션

```python
from wireview.core.meta import WireviewMeta


class XDashboard(Component):
    """대시보드 메인 컴포넌트"""

    _template_name = 'dashboard/dashboard.html'

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
class XActivityFeed(Component):
    """활동 피드 - Streams + 무한 스크롤"""

    _template_name = 'dashboard/activity_feed.html'
    _subscriptions = {"dashboard-activity"}

    activities: list = []
    has_more: bool = True
    loading_more: bool = False

    async def joined(self):
        activities = await self._load_activities()
        await self.stream("activities", activities)
        self.has_more = len(activities) >= 20

    async def _load_activities(self, before_id: int | None = None, limit: int = 20):
        """활동 로드"""
        qs = Activity.objects.all()
        if before_id:
            qs = qs.filter(id__lt=before_id)
        return [a async for a in qs[:limit]]

    async def load_more(self):
        """더 불러오기"""
        if self.loading_more or not self.has_more:
            return

        self.loading_more = True

        # 마지막 아이템 ID 찾기
        last_id = None
        if self.activities:
            last_id = min(a.id for a in self.activities)

        new_activities = await self._load_activities(before_id=last_id)

        for activity in new_activities:
            await self.stream_insert("activities", activity, at=-1)
            self.activities.append(activity)

        self.has_more = len(new_activities) >= 20
        self.loading_more = False

    async def mutation(self, channel: str, action, instance):
        """새 활동 실시간 수신"""
        if action.name == "CREATED":
            await self.stream_insert("activities", instance, at=0)
            self.activities.insert(0, instance)
```

### ActivityFeed 템플릿

`dashboard/templates/dashboard/activity_feed.html`:

```html
{% load wireview %}
<div {% tag_header %} class="activity-feed">
  <h2>Recent Activity</h2>

  <ul wire-stream="activities" class="activity-list">
    {% for activity in activities %}
      {% include "dashboard/activity_item.html" %}
    {% endfor %}
  </ul>

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

`dashboard/templates/dashboard/activity_item.html`:

```html
<li id="activities-{{ activity.pk }}" class="activity-item">
  <div class="activity-icon {{ activity.action }}">
    {% if activity.action == 'create' %}➕
    {% elif activity.action == 'update' %}✏️
    {% elif activity.action == 'delete' %}🗑️
    {% elif activity.action == 'login' %}🔑
    {% endif %}
  </div>
  <div class="activity-content">
    <span class="user">{{ activity.user }}</span>
    <span class="action">{{ activity.get_action_display|lower }}</span>
    <span class="target">{{ activity.target }}</span>
  </div>
  <time class="activity-time">{{ activity.created_at|timesince }} ago</time>
</li>
```

## Part 5: 실시간 업데이트

### 통계 자동 갱신

```python
class XStatCard(Component):
    _template_name = 'dashboard/stat_card.html'
    _subscriptions = {"dashboard-stat"}

    stat_name: str
    stat: AsyncResult = None

    @property
    def _subscriptions(self):
        # 특정 통계만 구독
        return {f"dashboard-stat.{self.stat_name}"}

    async def mutation(self, channel: str, action, instance):
        """통계 업데이트 수신"""
        if instance.name == self.stat_name:
            # 로딩 없이 직접 업데이트
            self.stat = AsyncResult.success(instance)
```

### 수동 브로드캐스트

```python
from wireview import broadcast

# 어디서든 브로드캐스트 가능
async def update_stat(stat):
    await stat.asave()
    await broadcast(f"dashboard-stat.{stat.name}", action="updated", instance=stat)
```

## Part 6: 완성된 코드

### 전체 live.py

```python
from wireview import Component, AsyncResult
from wireview.core.meta import WireviewMeta
from wireview.auto_broadcast import ModelAction
from .models import Stat, Activity
import asyncio


class XDashboard(Component):
    """대시보드 메인 컴포넌트"""

    _template_name = 'dashboard/dashboard.html'

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

    _template_name = 'dashboard/stat_card.html'

    stat_name: str
    stat: AsyncResult = None

    @property
    def _subscriptions(self):
        return {f"dashboard-stat.{self.stat_name}"}

    async def joined(self):
        self.stat = await self.assign_async(self.load_stat())

    async def load_stat(self):
        await asyncio.sleep(0.3)  # 로딩 시뮬레이션
        return await Stat.objects.aget(name=self.stat_name)

    async def refresh(self):
        self.stat = await self.assign_async(self.load_stat())

    async def mutation(self, channel: str, action: ModelAction, instance):
        if instance.name == self.stat_name:
            self.stat = AsyncResult.success(instance)


class XActivityFeed(Component):
    """활동 피드 컴포넌트"""

    _template_name = 'dashboard/activity_feed.html'
    _subscriptions = {"dashboard-activity"}

    activities: list = []
    has_more: bool = True
    loading_more: bool = False

    async def joined(self):
        activities = await self._load_activities()
        await self.stream("activities", activities)
        self.has_more = len(activities) >= 20

    async def _load_activities(self, before_id: int | None = None, limit: int = 20):
        qs = Activity.objects.all()
        if before_id:
            qs = qs.filter(id__lt=before_id)
        return [a async for a in qs[:limit]]

    async def load_more(self):
        if self.loading_more or not self.has_more:
            return

        self.loading_more = True

        last_id = min((a.id for a in self.activities), default=None)
        new_activities = await self._load_activities(before_id=last_id)

        for activity in new_activities:
            await self.stream_insert("activities", activity, at=-1)
            self.activities.append(activity)

        self.has_more = len(new_activities) >= 20
        self.loading_more = False

    async def mutation(self, channel: str, action: ModelAction, instance):
        if action == ModelAction.CREATED:
            await self.stream_insert("activities", instance, at=0)
            self.activities.insert(0, instance)
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
