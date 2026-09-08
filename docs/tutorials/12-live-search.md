# 12. Live Search - 실시간 검색

> 동작하는 전체 코드: [examples/search/](../../examples/search/) — CI가 매번 돌리는 예제다.

이 튜토리얼에서는 실시간 검색 기능을 만들며 디바운스와 키보드 내비게이션을 학습합니다.

## 학습 목표

- `.debounce` 이벤트 수정자
- `focus_on()` 포커스 관리
- `push_js(JS())` 클라이언트 명령
- 키보드 내비게이션
- 로딩 상태 표시

## 완성 미리보기

검색어 입력 시 실시간으로 결과가 표시됩니다:
- 입력 디바운스 (300ms)
- 화살표 키로 결과 탐색
- Enter로 선택
- Escape로 닫기

## 1. 모델 정의

`search/models.py`:

```python
from django.db import models


class Book(models.Model):
    """검색 대상 책"""
    title = models.CharField(max_length=200)
    author = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=50, blank=True)
    published_year = models.PositiveSmallIntegerField(null=True)
```

## 2. 컴포넌트 정의

`search/live.py`:

```python
from django.db.models import Q

from wireview.component import Component
from wireview.js import JS

from .models import Book


class XLiveSearch(Component):
    """실시간 검색 컴포넌트"""

    _template_name = "search/live_search.html"

    query: str = ""
    results: list[Book] = []
    selected_index: int = -1  # 현재 선택된 결과
    is_open: bool = False     # 드롭다운 표시 여부
    selected_book: Book | None = None

    async def search(self, q: str):
        """검색 실행 (디바운스됨)"""
        self.query = q
        self.selected_index = -1

        if len(q) < 2:
            self.results = []
            self.is_open = False
            return

        self.results = list(
            await Book.objects.filter(
                Q(title__icontains=q) |
                Q(author__icontains=q) |
                Q(description__icontains=q)
            )[:10]
        )
        self.is_open = len(self.results) > 0

    async def navigate(self, direction: int):
        """화살표 키로 결과 탐색"""
        if not self.results:
            self.skip_render()
            return

        max_index = len(self.results) - 1
        if self.selected_index == -1:
            self.selected_index = 0 if direction == 1 else max_index
        else:
            new_index = self.selected_index + direction
            if new_index < 0:
                self.selected_index = max_index
            elif new_index > max_index:
                self.selected_index = 0
            else:
                self.selected_index = new_index

    async def select_result(self, index: int):
        """결과 선택"""
        if 0 <= index < len(self.results):
            self.selected_book = self.results[index]
            self.is_open = False
            self.query = self.selected_book.title

    async def select_current(self):
        """현재 선택 항목 확정 (Enter)"""
        if self.selected_index >= 0:
            await self.select_result(self.selected_index)
        elif len(self.results) == 1:
            await self.select_result(0)

    async def close_dropdown(self):
        """드롭다운 닫기 (Escape)"""
        self.is_open = False
        self.selected_index = -1

    async def clear(self):
        """검색 초기화"""
        self.query = ""
        self.results = []
        self.is_open = False
        self.selected_book = None

        # JS로 input 초기화 및 포커스
        await self.push_js(
            JS()
            .set_value(f"#{self.id} input[name=q]", "")
            .focus(f"#{self.id} input[name=q]")
        )
```

## 3. 템플릿

`search/templates/search/live_search.html`:

```html
{% load wireview %}

<div {% tag_header %}>
  <div class="search-wrapper">
    <div class="search-input-group">
      <input
        type="text"
        name="q"
        class="search-input"
        placeholder="Search books..."
        value="{{ query }}"
        autocomplete="off"
        {% on 'input.debounce.300' 'search' %}
        {% on 'keydown.key.ArrowDown.prevent' 'navigate' direction=1 %}
        {% on 'keydown.key.ArrowUp.prevent' 'navigate' direction=-1 %}
        {% on 'keydown.key.Enter.prevent' 'select_current' %}
        {% on 'keydown.key.Escape' 'close_dropdown' %}
      />
      {% if query %}
        <button type="button" {% on 'click' 'clear' %}>Clear</button>
      {% endif %}
    </div>

    {% if is_open %}
      <div class="dropdown">
        {% for book in results %}
          <div
            {% class {'dropdown-item': True, 'highlighted': forloop.counter0 == selected_index} %}
            {% on 'click' 'select_result' index=forloop.counter0 %}
          >
            <div class="book-title">{{ book.title }}</div>
            <div class="book-author">by {{ book.author }}</div>
          </div>
        {% endfor %}
      </div>
    {% endif %}
  </div>

  {% if selected_book %}
    <div class="selected-book">
      <h3>{{ selected_book.title }}</h3>
      <p>by {{ selected_book.author }}</p>
    </div>
  {% endif %}

  <div class="keyboard-hint">
    <kbd>&uarr;</kbd> <kbd>&darr;</kbd> Navigate &bull;
    <kbd>Enter</kbd> Select &bull;
    <kbd>Esc</kbd> Close
  </div>
</div>
```

## 4. 핵심 개념: 디바운스

### `.debounce` 수정자

```html
{% on 'input.debounce.300' 'search' %}
```

입력 후 300ms 동안 추가 입력이 없을 때만 `search` 호출합니다.
빠른 타이핑 시 불필요한 서버 요청을 방지합니다.

### 디바운스 vs 쓰로틀

- **디바운스**: 마지막 이벤트 후 지정 시간 경과 시 실행
- **쓰로틀**: 지정 시간마다 최대 1회 실행

```html
{% on 'input.debounce.300' 'search' %}   <!-- 입력 멈춘 후 300ms -->
{% on 'scroll.throttle.100' 'on_scroll' %} <!-- 100ms마다 최대 1회 -->
```

## 5. JS() 명령어

`push_js()`로 클라이언트에 JavaScript 명령을 보냅니다:

```python
from wireview.js import JS

await self.push_js(
    JS()
    .set_value(f"#{self.id} input", "")  # input 값 비우기
    .focus(f"#{self.id} input")           # 포커스 이동
)
```

### 주요 JS 명령어

```python
JS().set_value(selector, value)    # input 값 설정
JS().focus(selector)               # 포커스 이동
JS().show(selector)                # 요소 표시
JS().hide(selector)                # 요소 숨김
JS().toggle(selector)              # 토글
JS().add_class(selector, "class")  # 클래스 추가
JS().remove_class(selector, "cls") # 클래스 제거
```

## 6. 키보드 내비게이션 패턴

```html
{% on 'keydown.key.ArrowDown.prevent' 'navigate' direction=1 %}
{% on 'keydown.key.ArrowUp.prevent' 'navigate' direction=-1 %}
```

`.prevent`는 기본 동작(스크롤)을 방지합니다.

## 연습 문제

1. **검색 하이라이트**: 검색어를 결과에서 강조 표시
2. **최근 검색어**: 최근 검색어 기록 표시
3. **카테고리 필터**: 카테고리별 필터링 추가

## 다음 단계

- [13. Quiz 앱](./13-quiz-app.md) - 상태 머신과 mutation

---

[← 11. Rating 앱](./11-rating-app.md) | [목차](./README.md) | [13. Quiz 앱 →](./13-quiz-app.md)
