# 11. Rating 앱 - 별점 평가

> 동작하는 전체 코드: [examples/rating/](../../examples/rating/) — CI가 매번 돌리는 예제다.

이 튜토리얼에서는 별점 평가 시스템을 만들며 키보드 접근성과 URL 상태 관리를 학습합니다.

## 학습 목표

- `wire.params` URL 상태 저장
- 키보드 이벤트 (`.key.ArrowLeft`, `.key.ArrowRight`)
- 마우스 호버 이벤트
- `{% cond %}` 조건부 속성

## 완성 미리보기

인터랙티브 별점 입력:
- 클릭으로 별점 선택
- 키보드 화살표로 조절
- 호버 시 미리보기
- URL에 상태 저장

## 1. 모델 정의

`rating/models.py`:

```python
from django.db import models


class Product(models.Model):
    """평가 대상 상품"""
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    @property
    def average_rating(self):
        """평균 평점"""
        ratings = self.ratings.all()
        if not ratings:
            return 0
        return sum(r.score for r in ratings) / len(ratings)


class Rating(models.Model):
    """사용자 평점"""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="ratings")
    score = models.PositiveSmallIntegerField(choices=[(i, f"{i}점") for i in range(1, 6)])
    session_key = models.CharField(max_length=40)

    class Meta:
        unique_together = ["product", "session_key"]
```

## 2. AUTO_BROADCAST 설정

모델 구독을 위해 `settings.py`에 설정:

```python
from wireview.settings import AutoBroadcast

WIREVIEW = {
    "AUTO_BROADCAST": AutoBroadcast(
        model=True,      # "rating.rating" 채널 활성화
        model_pk=True,   # "rating.rating.{pk}" 채널 활성화
    ),
}
```

## 3. 컴포넌트 정의

`rating/live.py`:

```python
from wireview.component import Component
from wireview.schemas import ModelAction

from .models import Product, Rating


class XStarRating(Component):
    """인터랙티브 별점 입력"""

    _template_name = "rating/star_rating.html"
    _subscriptions = {"rating.rating"}  # rating 앱의 Rating 모델

    product: Product
    current_rating: int = 0  # 현재 평점
    hover_rating: int = 0    # 호버 중인 평점
    readonly: bool = False
    session_key: str = ""

    async def joined(self):
        """마운트 시 기존 평점 로드"""
        self.session_key = self.wire.session_key or "anonymous"

        # URL에서 복원
        if rating_param := self.wire.params.get("rating"):
            self.current_rating = int(rating_param)
            return

        # DB에서 로드
        existing = await Rating.objects.filter(
            product=self.product, session_key=self.session_key
        ).afirst()
        if existing:
            self.current_rating = existing.score

    async def set_hover(self, star: int):
        """호버 미리보기"""
        if self.readonly:
            self.skip_render()
            return
        self.hover_rating = star

    async def clear_hover(self):
        """호버 해제"""
        self.hover_rating = 0

    async def rate(self, score: int):
        """평점 저장"""
        if self.readonly or not 1 <= score <= 5:
            self.skip_render()
            return

        await Rating.objects.aupdate_or_create(
            product=self.product,
            session_key=self.session_key,
            defaults={"score": score},
        )

        self.current_rating = score
        self.hover_rating = 0
        self.wire.params["rating"] = str(score)  # URL에 저장

    async def adjust_rating(self, delta: int):
        """키보드로 조절"""
        if self.readonly:
            self.skip_render()
            return

        new_rating = max(1, min(5, (self.current_rating or 3) + delta))
        await self.rate(new_rating)
```

## 4. 템플릿

`rating/templates/rating/star_rating.html`:

```html
{% load wireview %}

<div {% tag_header %}>
  <div
    class="star-rating {% if readonly %}readonly{% endif %}"
    tabindex="{% if readonly %}-1{% else %}0{% endif %}"
    role="slider"
    aria-valuemin="1"
    aria-valuemax="5"
    aria-valuenow="{{ current_rating }}"
    {% if not readonly %}
      {% on 'keydown.key.ArrowRight' 'adjust_rating' delta=1 %}
      {% on 'keydown.key.ArrowLeft' 'adjust_rating' delta=-1 %}
      {% on 'mouseleave' 'clear_hover' %}
    {% endif %}
  >
    {% for i in "12345" %}
      {% with star_num=forloop.counter %}
        <span
          class="star {% if star_num <= hover_rating %}preview{% elif star_num <= current_rating %}filled{% endif %}"
          {% if not readonly %}
            {% on 'mouseenter' 'set_hover' star=star_num %}
            {% on 'click' 'rate' score=star_num %}
          {% endif %}
        >&#9733;</span>
      {% endwith %}
    {% endfor %}
  </div>

  <div class="rating-text">
    {% if current_rating %}
      Your rating: <strong>{{ current_rating }}</strong> star{{ current_rating|pluralize }}
    {% else %}
      Click to rate
    {% endif %}
  </div>

  {% if not readonly %}
    <div class="keyboard-hint">
      Use arrow keys to adjust rating
    </div>
  {% endif %}
</div>
```

## 5. 핵심 개념: 키보드 이벤트

### 특정 키 감지

```html
{% on 'keydown.key.ArrowRight' 'adjust_rating' delta=1 %}
{% on 'keydown.key.ArrowLeft' 'adjust_rating' delta=-1 %}
```

지원되는 키 이벤트:
- `keydown.key.Enter`
- `keydown.key.Escape`
- `keydown.key.ArrowUp/Down/Left/Right`
- `keypress.enter` (축약형)

### 마우스 이벤트

```html
{% on 'mouseenter' 'set_hover' star=star_num %}
{% on 'mouseleave' 'clear_hover' %}
```

## 6. wire.params - URL 상태 저장

```python
# 저장
self.wire.params["rating"] = str(score)

# 로드
if rating_param := self.wire.params.get("rating"):
    self.current_rating = int(rating_param)
```

URL이 `?rating=4`로 업데이트되어 새로고침해도 상태 유지됩니다.

## 7. 통계 컴포넌트

평균과 분포를 보여주는 `XRatingStats` 컴포넌트:

```python
class XRatingStats(Component):
    _template_name = "rating/rating_stats.html"
    _subscriptions = {"rating.rating"}

    product: Product

    async def mutation(self, channel, action, instance):
        if instance.product_id == self.product.id:
            self.force_render()
```

## 연습 문제

1. **반 별**: 0.5 단위 평점 지원
2. **평점 취소**: 같은 별 클릭 시 평점 취소
3. **애니메이션**: 별 선택 시 펄스 애니메이션

## 다음 단계

- [12. Live Search](./12-live-search.md) - 실시간 검색과 디바운스

---

[← 10. Poll 앱](./10-poll-app.md) | [목차](./README.md) | [12. Live Search →](./12-live-search.md)
