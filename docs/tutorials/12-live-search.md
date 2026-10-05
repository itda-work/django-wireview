# Live Search - 실시간 검색

> 동작하는 전체 코드: [examples/search/](../../examples/search/) — `make test`가 함께 돌리고, 릴리스 게이트(CI)가 태그마다 다시 돌리는 예제다.

이 튜토리얼에서는 실시간 검색 기능을 만들며 디바운스, 주소에 남는 검색어, 키보드 내비게이션을 학습합니다.

## 학습 목표

- `.debounce` 이벤트 수정자
- `push_to()`와 `params_changed()`로 검색어를 주소에 남기기
- `focus_on()` 포커스 관리
- `push_js(JS())` 클라이언트 명령
- 키보드 내비게이션
- 로딩 상태 표시

## 완성 미리보기

검색어 입력 시 실시간으로 결과가 표시됩니다:
- 입력 디바운스 (300ms)
- 검색어가 주소(`/search/?q=장고`)에 남아 새로고침·공유한 링크·뒤로 가기에도 같은 결과
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
from urllib.parse import urlencode

from django.db.models import Q

from wireview import Component, JS

from .models import Book


class XLiveSearch(Component):
    """실시간 검색 컴포넌트"""

    class Meta:
        template_name = "search/live_search.html"

    query: str = ""
    results: list[Book] = []
    selected_index: int = -1  # 현재 선택된 결과
    is_open: bool = False     # 드롭다운 표시 여부
    selected_book: Book | None = None

    async def search(self, q: str):
        """검색어를 주소에 남긴다 (디바운스됨). 검색은 params_changed()가 한다"""
        q = q.strip()
        if q:
            await self.wire.push_to(f"?{urlencode({'q': q})}")
        else:
            # 빈 검색어는 ?q= 없는 이 페이지의 주소로
            await self.wire.push_to("search:index")

    async def params_changed(self, params, uri):
        """주소의 검색어로 결과를 낸다"""
        q = params.get("q", "")
        self.query = q
        self.selected_index = -1

        if len(q) < 2:
            self.results = []
            self.is_open = False
            return

        # QuerySet은 await할 수 없다. async for로 모은다
        self.results = [
            book
            async for book in Book.objects.filter(
                Q(title__icontains=q) |
                Q(author__icontains=q) |
                Q(description__icontains=q)
            )[:10]
        ]
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
        self.selected_book = None
        # 검색어와 결과는 params_changed()가 비운다
        await self.wire.push_to("search:index")

        # JS로 input 초기화 및 포커스
        await self.push_js(
            JS()
            .set_value(f"#{self.id} input[name=q]", "")
            .focus(f"#{self.id} input[name=q]")
        )
```

`results`와 `selected_book`은 모델 인스턴스를 그대로 담는다. 서명 상태에는 pk(목록)만 실리고, 다시 join할 때
타입 표기를 따라 다시 읽힌다(상세는 [Todo 앱](03-todo-app.md)).

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

## 5. 검색어를 주소에 남기기

검색 결과가 컴포넌트 상태에만 있으면 새로고침하면 사라지고, 주소를 보내 줘도 받은 사람은 빈 검색창을 봅니다.
검색어를 주소의 쿼리(`?q=장고`)에 두면 주소가 곧 검색입니다.

### 핸들러는 주소만 바꾼다

`search`는 결과를 계산하지 않습니다. `push_to("?q=…")`로 주소를 바꿀 뿐이고, 결과는 `params_changed()`가
냅니다. 같은 경로로 가는 `push_to`는 **patch**입니다. 페이지를 가져오지 않고 연결과 인스턴스가 그대로이며,
클라이언트가 주소창을 바꾼 뒤 새 params를 서버에 알리면 `params_changed()`가 돕니다
([내비게이션](../features/navigation.md)).

결과를 내는 길을 `params_changed()` 하나로 두는 이유는 검색어가 들어오는 길이 넷이기 때문입니다.

| 언제 | 무엇이 `params_changed()`를 부르나 |
|------|-----------------------------------|
| 입력 | `search`의 `push_to`가 patch로 |
| 새로고침, 공유한 링크 | 첫 HTTP 응답이 렌더 전에, 이어서 그 페이지의 join이 `joined()` 뒤에 |
| 뒤로·앞으로 가기 | 이 페이지가 만든 항목이면 patch로, 새로고침 전의 항목이면 그 주소를 가져온 페이지의 join으로 |
| Clear | `clear`의 `push_to`가 patch로 |

`search`가 직접 결과를 계산하면 입력할 때는 맞고 새로고침이나 뒤로 가기에서만 다른 일이 일어납니다.
하나의 길로 모으면 넷이 같은 결과를 냅니다.

첫 HTTP 응답도 렌더 전에 `params_changed()`를 부릅니다. `?q=`가 있는 주소를 열면 서버가 결과까지 그려
보내므로, JavaScript 없는 브라우저와 검색엔진도 결과를 봅니다. join에서 한 번 더 돌기 때문에 `params_changed()`는
같은 검색어로 두 번 돌아도 같은 화면을 내야 합니다. 여기서는 검색어에서 결과를 다시 계산할 뿐이라 그렇습니다
([첫 응답과 join](../features/navigation.md#첫-응답과-join)).

### 인코딩과 빈 검색어

쿼리 문자열은 `urlencode`로 만듭니다. `f"?q={q}"`라고 쓰면 `&`나 `#`이 들어간 검색어는 거기서 잘리고
`+`는 공백이 됩니다. 클라이언트는 받은 쿼리를 풀어서 보내므로 `params["q"]`는 입력한 그대로의 문자열입니다.

빈 검색어는 `?q=`를 남기지 않고 페이지의 주소(`search:index`, 즉 `/search/`)로 갑니다. 경로가 지금 페이지와
같으므로 이것도 patch이고, `params_changed()`가 빈 params를 받아 결과를 비웁니다.

### `push_to`, `replace_to`, `self.wire.params`

주소의 쿼리를 바꾸는 방법은 셋이고, 기록과 `params_changed()`에서 갈립니다.

| 방법 | 기록 | `params_changed()` |
|------|------|--------------------|
| `await self.wire.push_to("?q=…")` | 새 항목 | 돈다 |
| `await self.wire.replace_to("?q=…")` | 지금 항목을 바꾼다 | 돈다 |
| `self.wire.params["q"] = q` | 지금 항목을 바꾼다 | 돌지 않는다 |

이 예제는 `push_to`를 씁니다. 디바운스가 끝날 때마다, 즉 입력을 멈춘 검색어마다 기록 항목이 생기고,
뒤로 가기는 바로 전 검색어로 돌아갑니다. 검색어를 고쳐 가며 결과를 비교하는 화면에 맞습니다.

뒤로 가기가 검색창에 들어오기 전 페이지로 바로 가야 한다면 `replace_to`로 바꿉니다. 한 줄만 다르고
나머지는 같습니다. 주소는 언제나 지금 검색어를 가리키므로 새로고침과 링크 공유는 그대로 됩니다.

`self.wire.params`에 쓰는 것도 기록을 늘리지 않지만 `params_changed()`가 돌지 않습니다. 결과를 핸들러가
직접 계산해야 하므로 결과를 내는 길이 다시 둘이 됩니다. 이미 그린 상태를 주소에 비춰 두기만 할 때 씁니다.

### 테스트

`mount(params=...)`가 주소에 쿼리가 있는 페이지의 join과 같습니다. 입력은 `call()` 뒤에 `follow_push()`로
클라이언트가 할 나머지 절반을 합니다. `/search/`로 가는 push를 patch로 따라가려면 컴포넌트가 놓인 경로를
`path=`로 알려 줍니다([테스트 헬퍼](../features/testing.md#push를-따라가기)).

```python
view = await mount(XLiveSearch, params={"q": "파이썬"})    # 새로고침·공유한 링크
assert [book.title for book in view.component.results] == ["파이썬 입문"]

view = await mount(XLiveSearch, path="/search/")
await view.call("search", q="장고 실전")
view.assert_pushed_to(params={"q": "장고 실전"})
await view.follow_push()                                  # 브라우저가 하는 patch
assert view.component.wire.params == {"q": "장고 실전"}
```

브라우저에서 입력 → 새로고침 → 뒤로 가기를 도는 E2E는 `examples/search/tests.py`의 `TestQueryInTheAddress`입니다.

## 6. JS() 명령

`push_js()`로 클라이언트에 JavaScript 명령을 보냅니다:

```python
from wireview import JS

await self.push_js(
    JS()
    .set_value(f"#{self.id} input", "")  # input 값 비우기
    .focus(f"#{self.id} input")           # 포커스 이동
)
```

### 주요 JS 명령

```python
JS().set_value(selector, value)    # input 값 설정
JS().focus(selector)               # 포커스 이동
JS().show(selector)                # 요소 표시
JS().hide(selector)                # 요소 숨김
JS().toggle(selector)              # 토글
JS().add_class(selector, "class")  # 클래스 추가
JS().remove_class(selector, "cls") # 클래스 제거
```

## 7. 키보드 내비게이션 패턴

```html
{% on 'keydown.key.ArrowDown.prevent' 'navigate' direction=1 %}
{% on 'keydown.key.ArrowUp.prevent' 'navigate' direction=-1 %}
```

`.prevent`는 기본 동작(스크롤)을 방지합니다.

## 연습 문제

1. **검색 하이라이트**: 검색어를 결과에서 강조 표시
2. **최근 검색어**: 최근 검색어 기록 표시
3. **카테고리 필터**: 카테고리를 `?category=`로 주소에 함께 남기기 (`params_changed()`에서 둘 다 읽는다)

## 다음 단계

- [Quiz 앱](./13-quiz-app.md) - 상태 머신과 mutation

---

[← 이전: Todo 앱](03-todo-app.md) | [목차](README.md) | [다음: Quiz 앱 →](13-quiz-app.md)
