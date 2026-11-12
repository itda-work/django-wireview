# 선언형 DX 제안 — URL 쿼리 바인딩, Django Form 통합, 핸들러 단위 인가 (#266)

> 2026-11-12. **제안 — 결정 아님.** 기준 커밋은 `docs/dx-proposals-266`의 a4645db(`main`과 같다). 줄 번호는 그 커밋의 것이다.
> 이 메모는 공개 API를 바꾸지 않는다. 코드 블록의 API 가운데 **"제안된 API, 아직 없음"이라고 적은 것은 지금 존재하지 않는다.**
> 채택 여부, 이름, 기본값은 저장소 소유자가 정한다(§6-3).
> **그 뒤:** #267이 §3-1의 예제(todo의 500, dashboard·rating·poll의 검사 없는 읽기)와 §5의 폼 문서 예를 고쳤다. §3-1의 표는
> 고치기 전의 것이다. §5-1의 "값이 사라지는가"는 초판이 틀렸고 #267이 브라우저로 확인해 고쳐 적었다.

## 0. 묻는 것과 답

**물음.** 앱이 지금 손으로 엮는 세 가지 — 주소의 쿼리 ↔ 필드, Django 폼 ↔ 상태, 핸들러마다의 인가 — 를 선언으로
쓸 수 있게 할 것인가. 한다면 어떤 모양으로, 보안 경계(`SECURITY.md` "wireview가 지키는 경계")를 어디서 열고 닫는가.

**답의 요약.**

| 기능 | 권고 | 한 줄 이유 |
|---|---|---|
| 핸들러 단위 인가 | **B: `@authorize(술어)` 데코레이터** | 열린 소켓의 권한 회수를 막는 일은 지금 핸들러마다 같은 `if`를 손으로 쓰는 것뿐이고(chat_lv2·lv3에 여섯 번), 빠뜨려도 아무 신호가 없다. 감싸는 함수 자체에 검사를 두면 디스패처·`wireview.testing` 두 길이 함께 지난다 |
| URL 쿼리 바인딩 | **B: `Meta.url_params`** | 예제 다섯 곳이 쿼리를 손으로 읽고 쓰며, 그중 둘은 잘못된 값에 실패한다(하나는 500). 타입 검증과 "잘못된 값은 기본값"을 한 곳이 하게 한다. 모델 필드를 막는 선언 시점 오류가 필요하다 |
| Django Form 통합 | **C: 레시피로 둔다 + 문서의 예를 고친다**, 그 뒤 다시 본다 | 앱 코드에 폼을 쓰는 곳이 문서 둘과 테스트 픽스처 하나뿐이다. 깨지는 입력 둘 중 mass assignment는 Django 폼과 `filter_parameters`가 이미 막고, 값이 사라지는 것은 문서의 예가 값을 상태에 두지 않아서다 |

구현 순서는 인가 → URL → (필요하면) 폼이다. 셋 다 diff 형태를 바꾸지 않으므로 `PROTOCOL_VERSION`(12, `wireview/core/rendered.py:67`)은
그대로다. 첫 PR은 인가 데코레이터 하나(모듈 하나 + 체크 하나 + 문서 한 절, §6-2).

## 1. 바탕: 지금 이미 있는 것

세 기능 모두 새 개념이 아니라 **이미 있는 길 위의 줄임말**이다. 그 길을 먼저 적는다.

| 길 | 위치 | 이 메모와의 관계 |
|---|---|---|
| `class Meta:` → `ComponentOptions` | `wireview/core/component.py:172-234` | 키 단위 상속, 모르는 키는 클래스를 만들 때 `TypeError`(`:220-224`). 새 선언의 첫 후보 자리 |
| 필드 대입 검증 | `model_config`의 `validate_assignment=True` (`component.py:335-342`) | `self.page = "abc"`(`page: int`)는 대입에서 `ValidationError`다. URL 바인딩이 이것을 그대로 쓸 수 있다 |
| 핸들러 판정 | `wireview/core/handlers.py:76` `is_client_callable` | 데코레이터가 붙은 메서드도 이 규칙을 지나야 한다 |
| 핸들러 감싸기 | `component.py:252-285` `_validate_handlers` | 사용자 핸들러를 `validate_call`로 감싼다. 새 데코레이터는 이것과 순서가 맞아야 한다 |
| 이벤트 인자 거르기 | `wireview/utils.py:160-171` `filter_parameters` | 핸들러가 받지 않는 키는 버린다. `**data`를 받으면 전부 넘긴다(`:161-163`) |
| 이벤트 디스패치 | `wireview/repository.py:1255-1297` | `handle_event` 훅(`:1284`) → 핸들러(`:1296`) |
| 테스트의 이벤트 | `wireview/testing.py:960-991` `call()` | 디스패처의 검사를 **복제해** 지난다(`:967-973`, `:980`) |
| params 듣기 | `component.py:702-711` `_handle_params` | `handle_params` 훅 → `params_changed()`. 모든 길이 여기를 지난다(#110) |
| 마운트 훅 | `component.py:713-826` `_mount` | 경계 훅 → `Meta.on_mount`. 마운트에서 **한 번** 돈다 |
| 연결의 사용자 | `wireview/consumer.py:106` `scope["user"]` | **connect 때의 사용자 객체**다. 그 뒤 DB에서 `is_staff`를 떼어도 `self.user.is_staff`는 그대로다(§4-1) |

순서도 정해져 있다(`docs/features/navigation.md:82-85`). 첫 HTTP 응답은 마운트 훅 → `params_changed()` → 렌더이고, join은
마운트 훅 → `joined()` → `params_changed()` → 첫 렌더다. **마운트 훅은 params보다 먼저 돈다** — §3-3의 모델 필드 금지가 여기서 나온다.

## 2. 비교 대상에서 확인한 것

공식 문서를 2026-11-12에 읽었다. 읽은 판과 읽지 못한 것을 함께 적는다. 문서에 없는 것을 기억으로 채우지 않았다.

**Livewire** (읽은 것은 4.x 문서다. 3.x 문서는 읽지 않았다)

- `#[Url]`의 옵션은 `as:`, `except:`, `keep:`, `history:`(기본은 `replaceState`, `history: true`면 `pushState`) —
  <https://livewire.laravel.com/docs/url>. 쿼리 값이 속성의 타입과 맞지 않을 때 무엇이 되는지, 쿼리를 신뢰할 수 없는
  입력으로 다루라는 경고는 **문서에 없다**.
- 폼 객체는 `class PostForm extends Form`, 속성에 `#[Validate(...)]`, 컴포넌트에 `public PostForm $form;`,
  템플릿은 `wire:model="form.title"` — <https://livewire.laravel.com/docs/forms>. 실시간 검증에 실패하면 "the property won't be
  updated on the server". 제출 검증 실패 뒤 입력이 화면에 남는지는 문서에 없다.
- 공개 속성은 "always treat public properties as user input"(properties 문서). 클라이언트가 쓸 수 없게 하려면 속성마다
  `#[Locked]`를 붙인다 — "Livewire will continue to make public properties freely mutable by default"
  (<https://livewire.laravel.com/docs/locked>). 모델을 담은 속성은 자동으로 잠긴다. **Livewire의 기본값은 "열림, 잠그기는
  opt-in"이다.** wireview는 반대(서명 상태, 클라이언트가 필드를 못 고침)이고 이 메모는 그것을 유지한다.
- 인가는 액션 안의 `$this->authorize('update', $post)`(Laravel 정책)다 — "Always authorize server-side"
  (<https://livewire.laravel.com/docs/actions>). **`#[Authorize]` 속성은 찾지 못했다** — 그 주소는 Quickstart를 돌려준다.
  있는지 없는지 확인 못 함.

**Phoenix LiveView** (hexdocs가 1.2.13으로 리다이렉트했다)

- `handle_params`의 params는 문자열 맵이고 "public information that can be set by the user". 형 변환은 없다.
  "you must use the callback to validate the user input" — 예는 허용 목록(`when sort_by in ~w(name company)`)에 없으면
  그대로 둔다(<https://phoenix-live-view.hexdocs.pm/live-navigation.html>). **잘못된 쿼리는 무시하고 기본 상태**가 Phoenix의 모양이다.
- 보안 모델: 이벤트마다 "you must always verify permissions on the server". 로그아웃·권한 회수는 `live_socket_id`
  토픽으로 `"disconnect"`를 방송해 소켓을 끊고, 다시 붙을 때 mount가 실패하게 한다
  (<https://phoenix-live-view.hexdocs.pm/security-model.html>). wireview의 `invalidate_authentication`이 같은 자리다.
  `on_mount`만으로 충분하다는 문장은 없고, 예의 이벤트도 scope를 다시 넘긴다.
- `attach_hook(:handle_event)`는 `{:halt, socket}`으로 그 단계를 멈춘다. **그것으로 인가하는 예는 문서에서 찾지 못했다.**
- 폼: `to_form(changeset, action: :validate)`. "The JavaScript client is always the source of truth for current input values",
  포커스된 입력은 덮어쓰지 않는다(<https://phoenix-live-view.hexdocs.pm/form-bindings.html>). Ecto `cast/3`의 허용 필드 목록이
  mass assignment를 막는다는 것은 이번에 읽은 문서에 없었다(Ecto 문서는 읽지 않았다 — 확인 못 함).

**django-unicorn** (<https://www.django-unicorn.com/docs/>)

- 쿼리 문자열 바인딩은 읽은 페이지에서 찾지 못했다.
- 폼은 컴포넌트 클래스 속성 `form_class = BookForm`, `self.validate()`·`self.is_valid()`, 템플릿의 `$validate`(validation 문서).
- "By default, all public attributes of the component are included in the context … and available to JavaScript"(views 문서).
  `_` 접두사, `Meta.exclude`, `Meta.javascript_exclude`가 있다. 제외한 필드에 클라이언트가 쓰는 것을 서버가 거절하는지는
  문서에 없다. 액션 단위 권한 데코레이터는 읽은 페이지에 없었다(decorators API 페이지는 읽지 않았다).

**Tetra** (<https://tetra.readthedocs.io/>, 입문과 튜토리얼 두 페이지만. tetraframework.com/docs는 404)

- `@public`을 붙인 메서드·속성만 브라우저에서 닿는다. 폼 컴포넌트, URL 바인딩, 인가 기능은 읽은 두 페이지에 없었다.
  참조 문서를 읽지 않았으므로 "없다"가 아니라 "확인 못 함"이다.

**정리.** 셋 중 어디에도 **"쿼리 → 타입 있는 필드, 잘못된 값은 기본값"을 문서로 약속하는 곳은 확인하지 못했다.** 인가는
모두 "핸들러 안에서 직접"이 공식 모양이고, 선언형 인가는 확인한 범위에서 없다. 이 메모의 인가 데코레이터는 비교 대상의
관례를 옮기는 것이 아니라 wireview의 사정(§4-1, 사용자 스냅샷) 때문에 제안하는 것이다.

## 3. URL 쿼리 바인딩

### 3-1. 지금 손으로 엮는 코드

| 파일:줄 | 읽기 | 쓰기 | 잘못된 값 |
|---|---|---|---|
| `examples/search/live.py:60-86` | `params_changed`에서 `params.get("q", "")` | `push_to(f"?{urlencode(...)}")` `:55`, `push_to("search:index")` `:58` | `str`이라 무엇이든 받는다. 바른 모양 |
| `examples/dashboard/live.py:65-66` | `joined()`에서 `wire.params.get("tab", "overview")` | `wire.params["tab"] = tab` `:75`, `["range"]` `:80` | 검증 없음. `?tab=<b>x`가 `active_tab`에 들어가 서명된다(템플릿이 아무 탭도 그리지 않을 뿐, 200). **뒤로 가기에 반응하지 않는다** — `params_changed`가 아니라 `joined()`에서 읽기 때문 |
| `examples/rating/live.py:53-57` | `joined()`에서 `int(...)`, `ValueError`면 넘김 | `:113` | 손으로 쓴 `try/except` |
| `examples/poll/live.py:41-45` | 같은 모양 | `:87` | 같은 `try/except` |
| `examples/todo/views.py:14` + `live.py:46` | 뷰가 `request.GET.get("showing", "all")`을 prop으로 | `live.py:106` | **`/todo?showing=bogus`는 `ValidationError`로 응답이 실패한다**(a4645db에서 테스트 클라이언트로 확인. `showing: Showing`이 `StrEnum`이다) |
| `docs/tutorials/03-todo-app.md:224-228` | `new()`에서 `kwargs.setdefault("filter", wire.params.get(...))` | `:237` | `str`이라 무엇이든 받는다 |

되풀이되는 모양은 **"필드 하나 = 쿼리 키 하나, 읽을 때 형 변환, 쓸 때 `str()`"**이다. 흔한 실수는 셋이다.

1. 읽는 자리가 셋으로 갈린다(`params_changed`, `joined()`, `new()`/뷰). `joined()`·`new()`에서 읽으면 같은 경로의
   `push_to`·뒤로 가기에 반응하지 않는다 — `navigation.md:87-98`이 권하는 자리는 `params_changed`뿐이다.
2. 형 변환과 잘못된 값 처리를 예제마다 따로 쓴다. 쓰지 않은 곳(todo)은 500, 쓴 곳은 `try/except` 네 줄.
3. 쓰는 쪽이 `wire.params[...]`(replaceState, 기록 없음)와 `push_to`(patch, 기록 있음) 중 무엇인지 예제마다 다르고
   이유가 적혀 있지 않다.

### 3-2. 후보

**A. 필드 주석의 메타데이터** — 제안된 API, 아직 없음

```python
# 제안된 API, 아직 없음
import typing as t
from pydantic import Field
from wireview import Component, Query

class ProductList(Component):
    page: t.Annotated[int, Field(ge=1), Query("p", history="push")] = 1
    sort: t.Annotated[t.Literal["name", "price"], Query()] = "name"
```

**B. `Meta.url_params`** — 제안된 API, 아직 없음

```python
# 전 (지금)
class ProductList(Component):
    page: int = 1
    sort: str = "name"

    async def params_changed(self, params, uri):
        try:
            self.page = max(1, int(params.get("page", "1")))
        except ValueError:
            self.page = 1
        sort = params.get("sort", "name")
        self.sort = sort if sort in ("name", "price") else "name"

    async def next_page(self):
        await self.wire.push_to(f"?page={self.page + 1}&sort={self.sort}")

# 후 (제안된 API, 아직 없음)
class ProductList(Component):
    class Meta:
        url_params = {"page": {"history": "push"}, "sort": {}}

    page: t.Annotated[int, Field(ge=1)] = 1
    sort: t.Literal["name", "price"] = "name"

    async def next_page(self):
        self.page += 1          # 핸들러가 끝나면 주소가 ?page=…&sort=… 로 (push)
```

`url_params`의 값은 키마다 `as`(쿼리 키 이름, 기본은 필드 이름), `history`(`"replace"` 기본 | `"push"`), `keep`(기본값이어도
주소에 남길지, 기본 False)다. Livewire의 `as:`·`history:`·`keep:`에 맞춘 이름이다.

**C. 레시피로 두기** — 지금 있는 것만으로 쓴다

```python
# 지금 쓸 수 있다
from pydantic import BaseModel, Field, ValidationError

class _Query(BaseModel):
    page: int = Field(1, ge=1)
    sort: t.Literal["name", "price"] = "name"

class ProductList(Component):
    page: int = 1
    sort: str = "name"

    async def params_changed(self, params, uri):
        try:
            q = _Query.model_validate(params)
        except ValidationError:
            q = _Query()          # 하나라도 틀리면 전부 기본값. 키마다 따로 하려면 필드마다 검증
        self.page, self.sort = q.page, q.sort
```

### 3-3. 동작의 규칙 (A·B 공통)

- **듣는 자리는 `_handle_params`(`component.py:702-711`) 하나다.** `handle_params` 훅 다음, `params_changed()` **앞에서**
  선언된 필드에 쿼리를 대입하고, 그 뒤 `params_changed()`를 부른다(앱이 파생 값을 계산할 수 있게). 그래서 첫 HTTP 응답,
  join, patch, 뒤로·앞으로 가기, sticky의 이동 알림, `follow_push()`가 모두 같은 규칙을 받는다. 서명 상태도 지금 규칙
  그대로다 — HTTP 응답의 `data-state`는 params를 듣기 전 상태이고 join이 다시 듣는다(`navigation.md:89-92`).
- **쓰는 자리는 핸들러가 끝난 뒤의 뒤처리 하나다.** 선언된 필드가 바뀌었으면 `history`에 따라 `wire.params`에 쓰거나
  (replace) 같은 경로의 `push_to`(push)를 낸다. 지금 메시지를 그대로 쓰므로 프로토콜은 바뀌지 않는다. 쿼리에서 들은 대입은
  "바뀜"으로 치지 않는다(다시 주소에 쓰면 고리가 된다).
- **깨지는 입력.**

  | 입력 | 일어나는 일 | 근거 |
  |---|---|---|
  | `?page=abc` | 그 필드만 기본값. 로그는 DEBUG. 렌더는 정상 | `validate_assignment`의 `ValidationError`를 키 단위로 받는다. Phoenix의 "허용 목록에 없으면 그대로"와 같은 결과 |
  | `?page=-1` | 타입만으로는 통과한다. **`Field(ge=1)` 같은 제약을 적어야 막힌다** | 제약은 Pydantic의 것을 그대로. 문서가 이것을 첫 예로 보인다 |
  | 선언하지 않은 키 | 아무 필드에도 가지 않는다. `wire.params`와 `params_changed()`에는 지금처럼 보인다 | 바인딩은 허용 목록이다 |
  | `?page=1&page=2` | 마지막 값(지금 `params`의 규칙) | 목록 필드는 첫 PR 범위 밖(§6-3 Q4) |
  | 같은 키를 두 컴포넌트가 선언 | 둘 다 같은 값을 듣는다. 쓰기는 나중 것이 이긴다 | `wire.params`는 페이지가 함께 쓰는 dict(`navigation.md:33-35`). W0xx로 알린다 |

- **보안 경계가 열리는 곳: 모델 필드.** 쿼리는 신뢰할 수 없는 입력인데 서명 상태의 필드로 들어온다. 서명 상태가
  지키는 것은 "클라이언트가 필드를 못 고친다"이고, 이 선언은 **그 필드에 한해 그것을 푼다.** 대부분은 괜찮다 —
  `page`·`sort`는 지금도 손으로 쿼리에서 받는다. 위험한 것은 앱이 **서명 상태라서 믿는** 필드다. chat_lv2의 경계 훅은
  방을 "URL이 아니라 컴포넌트의 서명 상태"에서 읽는다(`examples/chat_lv2/access.py:48-63`, `live-session.md:358-361`).
  그 `room`을 쿼리에 묶으면, 마운트 훅이 서명된 방으로 인가한 **뒤에** params가 방을 바꾼다(§1의 순서) — 인가를 건너뛴다.
  그래서 **모델 인스턴스·QuerySet·모델 목록 타입의 필드는 선언할 수 없다**(클래스를 만들 때 `TypeError`). 모델의 pk를
  쿼리로 받아야 하면 `int` 필드로 받고 핸들러·`params_changed`에서 인가하며 불러온다.
- **기본값이 여는 것.** 아무것도 선언하지 않으면 아무것도 열리지 않는다. 선언은 필드마다다(B는 키마다, A는 주석마다).
  "모든 필드를 쿼리로"는 두지 않는다.

### 3-4. 후보 비교

| | A. 필드 주석 | B. `Meta.url_params` | C. 레시피 |
|---|---|---|---|
| 이 저장소의 방식과 | 필드에 붙는 선언은 아직 없다. `temporary_assigns`·`exclude_fields`처럼 필드를 고르는 설정은 모두 Meta다 | 같다. 키 단위 상속도 그대로(`component.py:183-185`) | 새 것 없음 |
| 오타 | 붙은 자리가 곧 필드라 오타가 없다 | 없는 필드 이름은 클래스를 만들 때 `TypeError`(`extended`의 모르는 키 검사와 같은 자리) | 조용히 기본값 |
| 상속 | 필드를 다시 선언하면 주석이 사라진다 | 키 단위로 물려받는다. 하위 클래스가 `url_params`를 적으면 통째로 바뀐다(다른 Meta 키와 같다) | — |
| 편집기·타입 | pyright가 `Annotated`를 그대로 읽는다. `wireview_stubs`는 바꿀 것 없음 | `wireview_lsp`에 컴포넌트마다 `url_params`를 싣으면 확장이 템플릿의 `{% querystring %}` 키를 보완할 수 있다(후속) | — |
| 구현 크기 | 새 모듈 `wireview/core/url_params.py`(해석·대입·쓰기), `_handle_params`에 한 줄, 핸들러 뒤처리에 한 줄, `ComponentOptions`에 필드 하나, 체크 하나. A는 주석을 읽는 코드가 더 든다 | 같고 주석 해석이 없다 | 문서 한 절, 예제 셋 고치기 |
| 테스트 | `mount(X, params={"page": "abc"})` → `view.component.page == 1`. 쓰기는 `assert_replaced_to`·`assert_pushed_to`(`testing.md`)가 그대로 본다 | 같다 | 같다 |
| 프로토콜 | 없음 | 없음 | 없음 |

### 3-5. 조용한 실패와 체크

| 잘못 쓴 선언 | 잡는 곳 |
|---|---|
| 없는 필드 이름(B) | 클래스를 만들 때 `TypeError` |
| 모델·QuerySet 타입 필드 | 클래스를 만들 때 `TypeError` |
| `Meta.exclude_fields`·`temporary_assigns`에도 있는 필드 | 둘 다 말이 된다(서명하지 않고 매번 쿼리에서) — 오류 아님. 문서에 적는다 |
| `history`에 모르는 값 | 클래스를 만들 때 `TypeError` |
| 같은 페이지에 같은 쿼리 키를 다른 타입으로 선언한 두 클래스 | 페이지를 모르므로 클래스 단위로는 못 잡는다. 같은 `as`를 다른 타입으로 쓰는 클래스 쌍을 **W0xx(경고)**로 — 오탐이 있을 수 있어 경고 |
| 제약 없는 `int`(`?page=-1`) | 잡지 않는다. 앱의 값 범위는 앱이 안다. 문서의 첫 예가 `ge=1`을 보인다 |
| `params_changed`에서 같은 필드를 다시 손으로 대입 | 잡지 않는다(동작은 맞다 — 나중 대입이 이긴다) |

## 4. 핸들러 단위 인가

### 4-1. 지금 손으로 엮는 코드

| 파일:줄 | 무엇 |
|---|---|
| `examples/chat_lv2/live.py:161` | `send_message`: `if content and await ais_member(self.user, self.room):` |
| `examples/chat_lv2/live.py:182` | `on_typing`: `if await ais_member(...)` / `else: self.skip_render()` |
| `examples/chat_lv2/live.py:141`, `:237`, `:287` | 알림을 받은 컴포넌트가 다시 묻는다(이벤트가 아니라 구독을 놓기 위해) |
| `examples/chat_lv3/live.py:174`, `:195`, `:325`, `:351` | 같은 검사 넷. `:351`은 `_own_message`가 멤버십과 작성자를 함께 본다(객체 인가) |
| `docs/features/live-session.md:340-351` | "이미 마운트된 컴포넌트의 이벤트는 재인가하지 않는다" |
| `docs/features/lifecycle-hooks.md:379-389` | 셋째 칸 "이벤트 인가 — 핸들러 안 (직접 쓴다)" |

되풀이되는 모양: **"이 컴포넌트의 문맥(사용자, 서명된 객체)으로 같은 판정 함수를 다시 묻고, 아니면 조용히 아무것도
하지 않는다."** 판정 함수는 하나(`access.py`)인데 부르는 자리는 핸들러마다다.

흔한 실수 둘:

1. **새 핸들러에서 빠뜨린다.** chat_lv3의 README가 "새 핸들러도 같은 `is_member()`를 다시 묻는다"고 따로 적어야 했다
   (`examples/chat_lv3/README.md:12`). 빠뜨려도 정상 화면에서는 아무 차이가 없다.
2. **연결의 사용자 객체로 판정한다.** `self.user`는 connect 때 `scope["user"]`다(`consumer.py:106`). join 재확인
   (`wireview/session.py:1051-1079`)도 세션이 **이 pk를** 아직 인증하는지만 본다. 그래서 `self.user.is_staff`·
   `self.user.has_perm(...)`(Django가 객체에 권한을 캐시한다)는 **페이지를 연 뒤 회수된 권한을 보지 못한다.** 예제의
   `is_member`는 DB를 다시 물어서 맞다. 문서의 `RequireAdmin`(`lifecycle-hooks.md:262-270`)은 마운트 훅이라 괜찮지만,
   같은 판정을 이벤트에 옮기면 틀린다.

### 4-2. 후보

**A. `Meta.authorize`** — 클래스의 모든 핸들러 앞에서 — 제안된 API, 아직 없음

```python
# 제안된 API, 아직 없음
class Lv2ChatRoom(PresenceMixin, Component):
    class Meta:
        live_sessions = {"chat_lv2"}
        authorize = room_member        # async def room_member(component) -> bool
```

**B. `@authorize(술어)` 데코레이터** — 핸들러 하나에 — 제안된 API, 아직 없음

```python
# 전 (지금, examples/chat_lv2/live.py:147-168 줄임)
async def send_message(self, content: str):
    content = content.strip()
    if content and await ais_member(self.user, self.room):
        ...

# 후 (제안된 API, 아직 없음)
from wireview import authorize

async def room_member(component) -> bool:
    return await ais_member(component.user, component.room)   # 매번 DB를 묻는다

class Lv2ChatRoom(PresenceMixin, Component):
    @authorize(room_member)
    async def send_message(self, content: str):
        content = content.strip()
        if content:
            ...

    @authorize(room_member, otherwise="skip")
    async def on_typing(self):
        await self.presence_set_typing(True)
```

술어는 `async def (component) -> bool`. 거절의 모양 `otherwise`는 `"skip"`(기본: 핸들러를 부르지 않고 `skip_render()`,
지금 예제와 같은 조용한 거절)과 `"error"`(`PermissionDenied`를 던져 `_crashed` 경로 — 그 컴포넌트가 이벤트 전 상태로 다시
join, `docs/features/errors.md`) 둘이다. 객체 인가(남의 메시지 id)는 인자를 봐야 하므로 이 데코레이터의 일이 아니다 —
`_own_message` 같은 핸들러 안의 검사로 남는다(`SECURITY.md` "경계 밖의 것"과 같다).

**C. 레시피로 두기** — 지금 있는 `on_mount` + `attach_hook("handle_event")`로

```python
# 지금 쓸 수 있다
class RoomMembersOnEveryEvent:
    @staticmethod
    async def on_mount(component, params, session):
        async def check(event, kwargs):
            if await ais_member(component.user, component.room):
                return {"cont": True}
            component.skip_render()
            return {"halt": True}
        component.attach_hook("room_member", "handle_event", check)
        return {"cont": True}
```

### 4-3. 깨지는 입력: 권한이 회수된 뒤 열린 소켓으로 핸들러를 부른다

| | A | B | C |
|---|---|---|---|
| 회수 뒤의 호출 | 술어가 DB를 다시 물으면 막힌다 | 같다 | 같다 |
| 술어가 `self.user.is_staff`를 읽으면 | **막지 못한다**(스냅샷) | 같다 | 같다 |
| 막는 방법 | 문서와 도우미: `await fresh_user(component)` — 연결의 pk로 사용자 행을 다시 읽는 도우미(이벤트마다 쿼리 하나) — 제안된 API, 아직 없음. `has_perm`은 그 새 객체에 묻는다 | 같다 | 같다 |
| 빠뜨린 핸들러 | 없다(클래스 전체) | **있다** — 데코레이터를 안 붙이면 열린다 | 없다(훅이 모든 이벤트) |
| 술어가 던짐 | 거절로 친다(`_mount`가 예외를 거절로 치는 것과 같다, `component.py:750-757`) | 같다 | 훅의 예외는 `_crashed` |

**A·C의 "빠뜨림이 없다"는 반대로 넓다.** 모든 이벤트에 쿼리 하나가 붙고, 술어가 막아서는 안 되는 핸들러(닫기, 취소)도
막는다. 그래서 A에는 `@authorize(None)` 같은 빼기가 따라와야 하고, 그러면 A는 B의 기능을 다 가진 더 큰 API가 된다.

### 4-4. 후보 비교

| | A. `Meta.authorize` | B. `@authorize` | C. 레시피 |
|---|---|---|---|
| 이 저장소의 방식과 | Meta의 키. `on_mount`와 나란하다 | 지금 공개 데코레이터는 `@function_component`, `@session.view`(뷰에 붙는다)뿐이다. 핸들러에 붙는 것은 처음 | 있는 것만 |
| 어디서 검사하나 | 디스패처(`repository.py:1284` 옆)와 `testing.call`(`testing.py:980` 옆) **두 곳에 손으로** — 테스트 쪽이 디스패처를 복제하는 구조라서다 | **감싸는 함수 안.** 디스패처도 `testing.call`도 같은 함수를 부르므로 의무가 싱크에 있다(`CLAUDE.md` "새 의무는 진입점이 아니라 싱크에 단다"). 서버 코드가 그 메서드를 직접 불러도 검사한다 — 의도된 것이다 | 훅 단계는 두 곳 모두 이미 지난다 |
| `validate_call`과의 순서 | 무관 | `_validate_handlers`(`component.py:261-285`)가 데코레이터 **바깥**을 감싼다. 인자 검증 → 인가 순서다. 인가가 인자를 보지 않으므로 순서는 결과를 바꾸지 않는다. 데코레이터는 `functools.wraps`로 시그니처를 남겨 `filter_parameters`가 인자를 그대로 거른다 | — |
| 편집기·타입 | — | `ParamSpec`로 시그니처 보존, pyright가 그대로 읽는다. `wireview_lsp`에 "인가됨" 표시를 싣으면 확장이 `{% on %}` 보완에 표시할 수 있다(후속) | — |
| 테스트 | `mount(X, user=u)` 뒤 DB에서 권한을 떼고 `await view.call("send_message", content="x")` → 아무 일도 없음을 단언 | 같다. 추가로 `view.call`이 `otherwise="error"`면 `PermissionDenied`가 올라오는지 | 같다 |
| 구현 크기 | Meta 필드 하나 + 두 진입점 + 빼기 표시 | 모듈 하나(`wireview/core/authorize.py`, 30~60줄), `__init__`의 `_EXPORTS` 한 줄, 체크 하나, 문서 한 절 | 문서 한 절 |
| 프로토콜 | 없음 | 없음 | 없음 |

### 4-5. 조용한 실패와 체크

| 잘못 쓴 선언 | 잡는 곳 |
|---|---|
| 술어가 async가 아님 | 데코레이터를 적용할 때 `TypeError`(동기 ORM이 루프에서 `SynchronousOnlyOperation`으로 터지는 것을 미리) |
| 클라이언트가 부를 수 없는 메서드에 붙임(`_` 접두사, `joined` 같은 프레임워크 이름) | 붙여도 아무것도 지키지 않는다(부르는 쪽이 서버뿐). **W0xx**: `is_client_callable`이 아닌 이름에 붙은 `@authorize` |
| 데코레이터를 `@staticmethod`·`@classmethod` 바깥에 붙임 | 적용할 때 `TypeError` |
| 술어가 연결의 사용자 속성만 읽음(`is_staff`) | **잡지 못한다.** 문서의 첫 예를 DB 조회로, `fresh_user` 도우미를 둔다 |
| 한 클래스의 핸들러 일부에만 붙임 | 잡지 못한다(B의 본질). `wireview_lsp`·문서의 "이 클래스의 핸들러" 목록에 표시하는 것이 고작이다. 이것이 Q7의 질문이다 |

## 5. Django Form 통합

### 5-0. 지금 손으로 엮는 코드

| 파일:줄 | 무엇 |
|---|---|
| `docs/features/form-feedback.md:250-268` | `save(self, **data)` → `ContactForm(data)` → `is_valid()` → `self.errors = {f: list(e) ...}`. **`form_data: dict = {}`(`:254`)를 선언하고 쓰지 않는다** |
| `docs/features/form-feedback.md:273-292` | 템플릿의 `<input name="email">`에 `value=`가 없다 |
| `tests/testproj/formprobe/live.py:27-33` | 위 문서와 같은 코드(문서가 브라우저에서 도는 근거) |
| `docs/features/form-feedback.md:131-151` | Django 폼 없이 `_field_error`로 손 검증, 값은 필드에 대입 |

`examples/`에는 Django 폼을 쓰는 앱이 **하나도 없다**(`is_valid` 검색 0건). 튜토리얼에도 없다.

되풀이되는 모양은 "**`**data` → 폼 → 오류를 dict로**" 다섯 줄이다.

### 5-1. 깨지는 입력에서 지금 무엇이 일어나나

- **mass assignment(서명 상태에 없는 필드를 폼 값으로 보낸다).** 지금 문서의 모양에서는 일어나지 않는다. 핸들러가
  받는 키는 `filter_parameters`가 시그니처로 거르고(`utils.py:160-171`), `**data`로 받아도 Django `Form`은 자기 `fields`에
  없는 키를 읽지 않는다. 상태 필드는 핸들러가 대입한 것만 바뀐다. **위험해지는 것은 앱이 `**data`를 상태나 모델에 펼칠
  때다**: `for k, v in data.items(): setattr(self, k, v)`, `Model(**data)`, `ModelForm`의 `fields = "__all__"`(Django가 이미
  권하지 않는다), 그리고 `ModelForm(data, instance=...)`의 `instance`를 클라이언트가 보낸 id로 고르는 것. 이것들은 선언이
  막을 수 있는 것이 아니라 레시피가 "하지 말라"고 적을 것이다.
- **검증에 실패한 값이 다음 렌더에서 사라진다.** **사라진다**(#267에서 브라우저로 확인. 이 메모의 초판은 "보통의 렌더에서는
  사라지지 않는다"고 적었는데 틀렸다). 사용자가 고친 칸은 보통 렌더가 와도 값을 지키지만, **제출·change·Enter처럼 값을
  확정하는 동작에 답하는 렌더는 서버의 값을 쓴다**(`static/wireview/values.mjs`의 `keepsUserValue`, `committing`) — 할 일
  입력이 Enter 뒤 비워지는 것이 그 규칙이다. 문서의 예는 입력에 `value=`를 그리지 않으므로 서버의 값은 빈 값이고, 필수 칸을
  비운 채 다른 칸에 쓴 긴 글은 오류와 함께 돌아온 렌더에서 지워진다. 요소가 새로 그려질 때(다시 join, 재연결, `{% if %}` 안의
  폼)도 같다. 서버 상태에 값이 없기 때문이다. 문서의 `form_data`가 그것을 담으려던 자리로 보이는데 비어 있었다.
  #267이 문서의 예와 formprobe를 쓴 값을 상태(`values`)에 담는 모양으로 고쳤다.

### 5-2. 후보

**A. 상태 필드 타입 `FormState[F]`** — 제안된 API, 아직 없음

```python
# 제안된 API, 아직 없음
from wireview.forms import FormState

class XContactPage(Component):
    contact: FormState[ContactForm] = FormState()

    async def save(self, **data):
        if self.contact.bind(data):            # 폼의 fields에 있는 키만 담는다. 값·오류를 상태에 남긴다
            await send(self.contact.cleaned_data)
            await self.wire.redirect_to("/thank-you")
```

```django
{# 제안된 API, 아직 없음 — BoundField를 그대로 #}
{{ this.contact.form.email }}
<ul wire-feedback-for="email" class="errorlist wire-no-feedback">
  {% for e in this.contact.form.email.errors %}<li>{{ e }}</li>{% endfor %}
</ul>
```

서명 상태에는 `{"data": {폼 필드만}, "errors": {...}}`가 실린다(파일 필드 제외). 렌더는 그것으로 bound form을 만들어
`value=`를 그리므로 다시 그려져도 값이 남는다. `wire-feedback-for`는 그대로(필드 이름이 같다).

**B. `@form_handler(ContactForm)` 데코레이터** — 제안된 API, 아직 없음

```python
# 제안된 API, 아직 없음
@form_handler(ContactForm, errors="errors")
async def save(self, form: ContactForm):       # 유효할 때만 불린다. 아니면 self.errors에 넣고 끝
    await send(form.cleaned_data)
```

**C. 레시피로 두기 + 문서의 예를 고친다**

```python
# 지금 쓸 수 있다
class XContactPage(Component):
    values: dict[str, str] = {}
    errors: dict[str, list[str]] = {}

    async def save(self, **data):
        form = ContactForm(data)
        # 폼이 아는 필드만 상태에 둔다 — 나머지 키는 버린다
        self.values = {name: data.get(name, "") for name in form.fields}
        if form.is_valid():
            ...
        else:
            self.errors = {f: list(e) for f, e in form.errors.items()}
```

```django
<input name="email" value="{{ this.values.email }}">
```

### 5-3. 후보 비교

| | A. `FormState[F]` | B. `@form_handler` | C. 레시피 |
|---|---|---|---|
| mass assignment | 폼 필드만 담는다(구조로 막힌다) | 폼이 거른다. 상태에는 오류만 | 레시피가 `form.fields`로 거르라고 적는다 |
| 값이 사라짐 | 막는다(값이 서명 상태에) | **못 막는다**(값을 담지 않는다) | 막는다(`values`) |
| 서명 상태의 크기 | 폼 데이터 전부가 `data-state`에 실린다 — 긴 본문(`Textarea`)이면 프레임마다 커진다 | 작다 | 같다(A와) |
| 민감한 값 | `PasswordInput`의 값이 브라우저 상태에 남는다 — 서명은 되지만 **암호화되지 않는다.** 위젯이 `render_value=False`인 필드는 빼야 한다 | 문제없음 | 레시피가 비밀번호를 담지 말라고 적는다 |
| `ModelForm` | `instance`를 서명 상태의 모델 필드에서만 받게 해야 한다(별도 설계) | 같다 | 레시피 |
| `wire-auto-recover` | 겹친다 — 재연결 뒤에는 서명 상태에 이미 값이 있다 | 무관 | 겹친다 |
| 편집기·타입 | 제네릭 Pydantic 타입(`__get_pydantic_core_schema__`). `wireview_stubs`가 그 타입을 알아야 한다 | 단순 | 없음 |
| 구현 크기 | 새 모듈(타입, 직렬화, 렌더 때 bound form 캐시), 파일 필드·다중 값·접두사 규칙, `model_state`와의 관계. 가장 크다 | 모듈 하나 | 문서 두 곳 + formprobe |
| 프로토콜 | 없음 | 없음 | 없음 |

### 5-4. 조용한 실패와 체크

| 잘못 쓴 선언 | 잡는 곳 |
|---|---|
| A: 폼 클래스가 아닌 것을 `FormState[...]`에 | 클래스를 만들 때 `TypeError` |
| A: `PasswordInput`·`FileField`가 있는 폼 | 그 필드를 빼고 담는다. 빼는 것이 조용하므로 DEBUG 로그 |
| A·C: 템플릿 입력의 `name`이 폼 필드와 다름 | 값이 그 키로 오지 않아 "필수 항목" 오류가 뜬다 — 눈에 보이는 실패. 체크는 템플릿을 읽어야 해서(W019 수준의 걷기) 지금은 두지 않는다 |
| B: 데코레이터가 붙은 핸들러가 `form` 인자를 받지 않음 | 적용할 때 `TypeError` |
| C: `values`에 거르지 않고 `data`를 통째로 | 잡지 못한다. 레시피의 문장이 전부다 |

### 5-5. 왜 지금은 C인가

- 깨지는 입력 둘 중 하나(mass assignment)는 지금 모양에서 일어나지 않고, 다른 하나(값이 사라짐)는 문서의 예가 값을
  상태에 두지 않아서 생긴다. 고칠 것은 API보다 **문서의 예**다(`form-feedback.md:250-292`, formprobe).
- A는 서명 상태에 폼 데이터를 싣는 결정이다. 크기와 비밀번호 문제를 위젯 단위로 풀어야 하고, `ModelForm`의 `instance`를
  어디서 믿을지까지 정해야 한다. 쓰는 앱이 아직 없는 곳에서 그 결정을 하면 틀린 쪽으로 굳는다.
- **다시 볼 조건:** 예제나 사용자 이슈에서 같은 `values`/`errors` 쌍이 셋 이상 보이거나, `ModelForm` 수정 화면을 예제로
  넣게 되면 A를 다시 연다. 그때 `FormState`는 C의 `values`·`errors` 두 필드를 하나로 묶는 것에서 시작한다.

## 6. 권고와 순서

### 6-1. 권고

1. **인가: B(`@authorize`).** 검사가 감싸는 함수 안에 있어 디스패처와 `testing.call` 두 길이 함께 지난다 — 진입점에 손으로
   거는 의무가 하나도 늘지 않는다. A의 "빠뜨림 없음"은 모든 이벤트의 쿼리 비용과 빼기 표시를 들이고, C는 지금도 되지만
   훅의 클로저가 컴포넌트 상태를 읽는 방식이 처음 보는 사람에게 어렵다(그래서 C는 B의 문서에 "클래스 전체에 걸려면"으로
   함께 싣는다). 함께 둘 것: `fresh_user` 도우미(또는 그 레시피)와, `live-session.md:340-351`·`lifecycle-hooks.md:379-389`의
   "핸들러 안 (직접 쓴다)"를 데코레이터로 고치는 것. 예제는 chat_lv2의 `send_message`·`on_typing` 둘만 옮긴다(lv3의
   `_own_message`는 객체 인가라 그대로).
2. **URL: B(`Meta.url_params`).** 필드를 고르는 설정이 모두 Meta에 있고, 오타가 클래스를 만들 때 잡힌다. A는 상속에서
   주석이 사라지는 함정이 있다. C(레시피)만으로는 예제 다섯 곳의 "읽는 자리가 셋으로 갈린다"를 고치지 못한다 — 자리를
   정해 주는 것이 이 기능의 값이다. 모델 필드 금지는 필수다(§3-3).
3. **폼: C.** §5-5.

### 6-2. 순서와 의존

1. **첫 PR — 인가 데코레이터.** `wireview/core/authorize.py`, `_EXPORTS`, 체크 하나(W026 후보: 부를 수 없는 이름에 붙은
   `@authorize`), `docs/features/authorization.md`(또는 lifecycle-hooks의 절), chat_lv2 핸들러 둘, `tests/test_authorize.py`
   (회수 뒤 거절, 술어 예외 = 거절, `validate_call`과 함께, `view.call`과 디스패처가 같은 결과). 공개 API가 하나 늘어나므로
   `docs/COMPATIBILITY.md`의 목록과 `tests/test_public_api.py`도. 셋 중 다른 둘에 의존하지 않는다.
2. **둘째 — URL 바인딩.** `_handle_params`와 핸들러 뒤처리에 끼운다. 예제 다섯 곳(search·dashboard·rating·poll·todo)과
   튜토리얼 03·10·11을 옮기는 것은 따로 커밋한다 — todo의 500은 그 전에 버그로 고칠지 정한다(Q9). 인가와는 독립이지만,
   "쿼리로 받은 pk는 인가하고 불러온다"는 문장이 인가 문서를 가리키므로 뒤에 둔다.
3. **셋째 — 폼 레시피.** `form-feedback.md`의 예와 formprobe를 `values`를 담는 모양으로. 코드 변경 없음. 언제 해도 된다.

### 6-3. 저장소 소유자가 정할 것

1. **인가 데코레이터의 이름.** `authorize`(Livewire·Laravel의 메서드 이름과 같다)인가 `requires`·`guard`인가. `live_session`의
   `authorize=`와 이름이 겹친다 — 하나는 페이지, 하나는 핸들러다. 겹침을 받아들일지.
2. **거절의 기본 모양.** `"skip"`(조용히, 예제와 같다)인가 `"error"`(`PermissionDenied` → 다시 join, 눈에 보인다)인가.
3. **`fresh_user`를 공개 API로 둘지, 레시피로 둘지.** 둔다면 이벤트마다 쿼리 하나를 감수하는 opt-in이다.
4. **URL: 목록 필드(`?tag=a&tag=b` → `list[str]`)를 첫 판에 넣을지.** 지금 `params`는 마지막 값만 준다.
5. **URL: 잘못된 값의 처리.** 키 단위로 기본값(제안)인가, 하나라도 틀리면 `params_changed` 전체를 기본값인가, 오류인가.
6. **URL: `history`의 기본값.** `"replace"`(지금 `wire.params`와 같다, Livewire의 기본)인가 `"push"`(search 예제의 모양)인가.
7. **인가를 클래스 단위로도 줄지(후보 A를 나중에 더할지).** B만 있으면 "새 핸들러에 붙이는 것을 잊는다"가 남는다.
8. **URL 선언의 자리.** Meta(B, 권고)인가 필드 주석(A)인가 — 앞으로 필드에 붙는 선언을 들일지의 첫 판례가 된다.
9. **todo 예제의 `?showing=bogus` 500을 URL 기능 전에 버그로 고칠지.** 고친다면 뷰에서 `Showing` 값을 검사하는 두 줄이다.
10. **폼을 레시피로 두는 것에 동의하는지,** 아니면 `FormState`의 설계 메모를 따로 열지.

## 7. 확인하지 못한 것

- Livewire 3.x 문서(4.x만 읽었다), `#[Authorize]` 속성의 존재.
- Livewire `#[Url]`이 타입이 맞지 않는 쿼리 값을 어떻게 다루는지(문서에 없다).
- Ecto `cast/3`의 mass assignment 설명(Ecto 문서를 읽지 않았다). Phoenix `to_form/2` 절은 요약본만 읽었다.
- django-unicorn의 decorators API 페이지, 제외한 필드에 대한 클라이언트 쓰기를 서버가 거절하는지.
- Tetra의 참조 문서(입문·튜토리얼 두 페이지만 읽었다).
- 이 메모의 성능 언급(이벤트마다 쿼리 하나, 폼 데이터의 `data-state` 크기)은 측정하지 않았다.
