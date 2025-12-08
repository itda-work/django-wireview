# 10. Poll 앱 - 실시간 투표

이 튜토리얼에서는 실시간 투표 앱을 만들며 wireview의 렌더링 최적화를 학습합니다.

## 학습 목표

- `skip_render()` 최적화
- `{% class %}` 조건부 CSS 클래스
- `{% cond %}` 조건부 속성
- 모델 구독과 `mutation()` 훅
- `.wireview-loading` CSS 상태

## 완성 미리보기

실시간으로 투표 결과가 업데이트되는 투표 앱입니다:
- 옵션 클릭으로 투표
- 투표 수와 퍼센트 실시간 표시
- 다른 사용자의 투표도 실시간 반영
- URL로 투표 상태 저장

## 1. 모델 정의

`poll/models.py`:

```python
from django.db import models


class Poll(models.Model):
    """투표 질문"""
    question = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    @property
    def total_votes(self):
        """전체 투표 수"""
        return sum(option.votes for option in self.options.all())


class Option(models.Model):
    """투표 옵션"""
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="options")
    text = models.CharField(max_length=200)
    votes = models.PositiveIntegerField(default=0)

    @property
    def percentage(self):
        """퍼센트 계산"""
        total = self.poll.total_votes
        if total == 0:
            return 0
        return round((self.votes / total) * 100, 1)
```

## 2. AUTO_BROADCAST 설정

모델 변경을 자동으로 컴포넌트에 브로드캐스트하려면 `settings.py`에 설정이 필요합니다:

```python
from wireview.settings import AutoBroadcast

WIREVIEW = {
    "AUTO_BROADCAST": AutoBroadcast(
        model=True,      # 모델명 채널 활성화 (예: "option")
        model_pk=True,   # 모델명.pk 채널 활성화 (예: "option.5")
    ),
}
```

> **참고**: 이 설정이 없으면 `_subscriptions`를 지정해도 `mutation()`이 호출되지 않습니다.

## 3. 컴포넌트 정의

`poll/live.py`:

```python
from wireview.component import Component
from wireview.schemas import ModelAction

from .models import Option, Poll


class XPoll(Component):
    """실시간 투표 컴포넌트"""

    _template_name = "poll/poll.html"
    _subscriptions = {"option"}  # Option 모델 변경 구독

    poll: Poll
    voted_option_id: int | None = None  # 투표한 옵션 ID

    async def joined(self):
        """마운트 시 URL에서 투표 상태 복원"""
        if voted_id := self.wire.params.get("voted"):
            self.voted_option_id = int(voted_id)

    async def mutation(self, channel: str, action: ModelAction, instance: Option):
        """Option 변경 시 리렌더링"""
        if instance.poll_id == self.poll.id:
            self.force_render()

    async def vote(self, option_id: int):
        """투표 처리"""
        if self.voted_option_id is not None:
            self.skip_render()  # 이미 투표함
            return

        option = await Option.objects.aget(id=option_id)
        option.votes += 1
        await option.asave()

        self.voted_option_id = option_id
        self.wire.params["voted"] = str(option_id)  # URL에 저장
```

## 4. 핵심 개념: skip_render()

`skip_render()`는 불필요한 렌더링을 방지합니다:

```python
async def vote(self, option_id: int):
    if self.voted_option_id is not None:
        # 이미 투표한 경우, 렌더링 건너뛰기
        self.skip_render()
        return
```

`mutation()`이 모델 변경을 감지하고 자동으로 리렌더링하므로,
투표 후에는 `mutation()`에 맡깁니다.

## 5. 템플릿

`poll/templates/poll/poll.html`:

```html
{% load wireview %}

<div {% tag_header %} class="poll-card">
  <h2>{{ this.poll.question }}</h2>

  <div class="poll-options">
    {% for option in this.poll.options.all %}
      <div class="poll-option">
        <button
          type="button"
          {% class {'option-button': True, 'selected': voted_option_id == option.id} %}
          {% cond {'disabled': voted_option_id} %}
          {% on 'click' 'vote' option_id=option.id %}
        >
          <div class="progress-bar" style="width: {{ option.percentage }}%"></div>
          <div class="option-content">
            <span>{{ option.text }}</span>
            <span>{{ option.votes }} votes ({{ option.percentage }}%)</span>
          </div>
        </button>
      </div>
    {% endfor %}
  </div>

  <p>Total votes: <strong>{{ this.poll.total_votes }}</strong></p>
</div>
```

## 6. 핵심 템플릿 태그

### `{% class %}` - 조건부 CSS 클래스

```html
{% class {'option-button': True, 'selected': voted_option_id == option.id} %}
```

출력: `class="option-button selected"` (조건이 참인 것만)

### `{% cond %}` - 조건부 속성

```html
{% cond {'disabled': voted_option_id} %}
```

`voted_option_id`가 존재하면 `disabled` 출력

## 7. CSS 로딩 상태

wireview는 서버 요청 중 자동으로 `.wireview-loading` 클래스를 추가합니다:

```css
.option-button.wireview-loading {
  opacity: 0.7;
  pointer-events: none;
}
```

## 연습 문제

1. **복수 선택 투표**: 여러 옵션을 선택할 수 있도록 수정해보세요
2. **익명 투표**: 투표 후 결과만 보이도록 수정해보세요
3. **투표 마감**: `is_active=False`일 때 투표 버튼 비활성화

## 다음 단계

- [11. Rating 앱](./11-rating-app.md) - 별점 평가와 키보드 이벤트

---

[← 09. 테스트 가이드](./09-testing-components.md) | [목차](./README.md) | [11. Rating 앱 →](./11-rating-app.md)
