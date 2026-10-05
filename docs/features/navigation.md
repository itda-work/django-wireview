# 내비게이션

컴포넌트가 브라우저 주소를 바꾸는 방법은 넷이다. 무엇이 다시 그려지고, 연결이 이어지는지가 다르다.
`push_to`·`replace_to`는 **목적지의 경로가 지금 페이지의 경로와 같은지**에 따라 둘로 갈린다(#169).
쿼리나 조각만 바꾸면 같은 경로다.

| 방법 | 같은 경로 | 다른 경로 | 기록 |
|------|-----------|-----------|------|
| `await self.wire.push_to(to)` | **patch.** 가져오지 않는다. 연결과 인스턴스가 그대로이고 `params_changed()`가 돈다 | **navigate.** 가져와 `<body>`를 바꾸고, 컴포넌트는 그 렌더로 새로 join한다. `live_session` 경계를 넘으면 전체 로드 | 새 항목 |
| `await self.wire.replace_to(to)` | patch (`push_to`와 같다) | navigate (`push_to`와 같다) | 지금 항목을 바꾼다 |
| `await self.wire.redirect_to(to)` | 가져온다. 목적지의 컴포넌트가 새로 마운트된다 | 같다 | 새 항목 |
| `self.wire.params["k"] = v` | 쿼리만 바꾼다. 가져오지 않고 `params_changed()`도 돌지 않는다 | — | 지금 항목을 바꾼다 |

Phoenix LiveView에 대면 같은 경로의 `push_to`가 `push_patch`, 다른 경로의 `push_to`와 `redirect_to`가
`push_navigate`다. `replace_to`는 그 둘의 `replace: true`다.

- **같은 경로의 `push_to`는 주소에 담긴 상태로 가는 이동이다.** 목록의 다음 쪽, 탭 전환. 클라이언트는
  `pushState`만 하고 새 params를 서버에 알린다. 페이지의 컴포넌트(그 LiveComponent도)는 같은 인스턴스로
  `params_changed()`를 받으므로, 이벤트로 바꾼 상태는 그대로 남는다. **지금 주소와 똑같은 주소로 가는
  `push_to`는 기록 항목을 만들지 않는다** — 그 항목의 뒤로 가기는 아무것도 바꾸지 않았을 것이다.
  `params_changed()`는 그래도 돈다. Phoenix의 `push_patch`와 같다(#170).
- **다른 경로의 `push_to`는 다른 페이지로 가는 이동이다.** boost 링크를 누른 것과 같다. `BOOST_PAGES`와
  상관없이 목적지를 가져와 바꾸고, 컴포넌트는 그 렌더로 새로 join한다. 경계를 넘는 이동은 전체 로드다.
  목적지의 params는 **그 페이지가 화면에 놓인 뒤에** 서버에 간다. 새 페이지의 컴포넌트는 join에서 받고
  (`joined()` 뒤, 첫 렌더 앞), 이동을 건너온 sticky 컴포넌트와 그 안에 그린 컴포넌트는 `params_changed()`로
  받는다. 떠나는 페이지의 컴포넌트는 목적지의 params를 듣지 않는다(#170).
- **`replace_to`는 기록 항목을 늘리지 않는 `push_to`다.** 필터를 바꿨지만 뒤로 가기에 남기고 싶지 않을 때.
  다른 경로면 그 자리에서 목적지를 가져온다 — 주소창이 이 페이지를 그리지 않은 뷰를 가리키는 일이 없다.
- **`redirect_to`는 언제나 페이지 이동이다.** 같은 경로여도, 조각만 다른 주소(`"#top"`)여도 가져온다.
  지금 주소와 똑같으면 기록 항목을 늘리지 않는다. `BOOST_PAGES`가 켜져 있으면 전체
  로드 없이, 꺼져 있으면 보통의 로드로 간다. 부른 뒤에는 이 컴포넌트가 렌더를 보내지 않는다. 인가가
  실패했을 때 로그인 페이지로 보내는 용도가 이것이다([lifecycle-hooks](./lifecycle-hooks.md)).
- **`self.wire.params`에 쓰면** 이벤트가 끝난 뒤 쿼리 문자열이 그 값으로 바뀐다(`replaceState`).
  `params`는 페이지의 모든 컴포넌트가 함께 쓰는 dict다 — 한 컴포넌트가 쓴 키를 다른 컴포넌트도 본다.
  `.json`으로 끝나는 키는 값을 JSON으로 적고 읽는다(`self.wire.params["expanded.json"] = [1, 2]`).

### 페이지를 다시 가져와야 하는 페이지

patch는 컴포넌트에만 새 params를 알린다. **컴포넌트 밖의 템플릿이 `request.GET`을 읽는 페이지**(쿼리로
제목이나 사이드바를 고르는 레이아웃)는 patch 뒤에 그 부분이 옛 쿼리로 남는다. 그런 페이지에서는
`push_to` 대신 `redirect_to`를 쓴다. 같은 경로여도 페이지를 가져와 그 부분까지 다시 그린다. 쿼리를 읽는
부분을 컴포넌트로 옮기면 patch로 충분하다.

## 첫 응답과 join

쿼리가 있는 주소(`/search/?q=장고`)를 열면 `params_changed()`가 두 번 돈다. 첫 HTTP 응답에서 한 번,
WebSocket join에서 한 번이다. Phoenix가 dead render와 connected mount에서 `handle_params`를 한 번씩 부르는 것과
같다(#170, #177).

| 언제 | 순서 |
|------|------|
| 첫 HTTP 응답 | 마운트 훅(`Meta.on_mount`, `live_session`) → `params_changed()` → 렌더. `joined()`는 돌지 않는다 |
| WebSocket join | 마운트 훅 → `joined()` → `params_changed()` → 첫 렌더. 첫 응답이 그린 상태가 아니라 마운트 상태에서 시작한다 |

- **같은 params로 두 번 돌아도 같은 상태가 되어야 한다.** 필드는 params에서 다시 계산한다. 메일 발송, 카운터
  증가, 감사 로그처럼 한 번만 일어나야 하는 일은 `params_changed()`가 아니라 이벤트 핸들러에 둔다.
- **join은 첫 응답이 그린 상태가 아니라 마운트 상태에서 params를 다시 듣는다.** 첫 응답의 HTML은
  `params_changed()`가 만든 상태를 그리지만, `data-state`에는 그 전, 마운트 직후의 상태가 서명된다. 그래서 join은
  params를 듣기 전의 컴포넌트에서 출발해 `params_changed()`를 처음부터 다시 돈다. Phoenix의 connected mount가 dead
  render의 assigns를 물려받지 않는 것과 같다. 같은 params로 다시 계산하므로 join의 첫 렌더는 첫 응답과 같은 화면이다.
  - 단, params 밖의 입력(시각·난수·그 사이 바뀐 DB 행)에 따라 달라지면 join의 첫 렌더가 화면을 바꾼다.
  - `if q == self.q: return`처럼 자기 필드와 비교해 일찍 돌아오는 가드를 두어도 된다. join이 받는 `self.q`는
    아직 기본값이다.
  - 서명 상태에 실리지 않는 필드(`Meta.temporary_assigns`, `Meta.exclude_fields`)도 join의 `params_changed()`가
    다시 채운다.
  - 쿼리가 없는 페이지는 아무것도 달라지지 않는다. 들은 것이 없으니 서명하는 상태가 곧 그린 상태다.
- 첫 응답이 이미 쿼리를 반영해 그리므로 JavaScript 없는 브라우저와 검색엔진도 결과를 본다
  ([JavaScript 없는 첫 렌더](./dead-view.md)).
- 쿼리가 없으면 어느 쪽도 부르지 않는다.
- 페이지의 모든 컴포넌트가 듣는다. 루트, 그 안의 `{% component %}`, 슬롯 안의 컴포넌트, 함수 컴포넌트가 그린
  컴포넌트, sticky 컴포넌트, LiveComponent 모두 같다. 마운트 훅이 거절한 컴포넌트는 듣지 않는다.
- 첫 응답에는 연결이 없다. 거기서 `start_async`·`assign_async`로 시작한 작업은 렌더 전에 취소된다 — 페이지는
  로딩 상태로 그려지고 join의 `params_changed()`가 작업을 다시 시작한다(join은 마운트 상태에서 시작하므로
  가드가 있어도 그렇다). Phoenix도 연결 전에는 비동기 작업을
  시작하지 않는다. `push_js`·`push_event`·플래시·`push_to`처럼 클라이언트에 보내는 명령은 갈 곳이 없어 버려진다.
  `redirect_to`는 `<meta http-equiv="refresh">`로 나간다.
- 첫 응답의 `params_changed()`가 예외를 던지면 그 응답이 실패한다. 마운트 훅이 던질 때와 같다. LiveComponent의
  것은 소켓에서처럼 로그로 남고 자식은 그대로 그려진다.

회귀 테스트는 `tests/test_http_params.py`, 경로 × 거절 사유 표는 `tests/test_live_session_contract.py`다.

## 뒤로 가기·앞으로 가기

컴포넌트는 언제나 주소와 맞는다. 컴포넌트 밖의 템플릿은 patch 뒤에 옛 쿼리로 남을 수 있다(위 절). 맞추는 방법은
둘이다.

- **지금 화면에 있는 페이지가 만든 항목으로 가면 patch다.** 같은 경로의 `push_to`·`replace_to`가 남긴
  항목, 그리고 이 페이지가 처음 놓인 항목이 그렇다. 가져오지 않고 새 params로 `params_changed()`만 돈다.
  쿼리에 담긴 상태는 돌아오고, 이벤트로 바꾼 상태는 지금 인스턴스의 값 그대로다.
- **다른 페이지가 만든 항목으로 가면 그 주소를 다시 가져온다.** 다른 경로, boost로 가져온 다른 페이지,
  새로고침하기 전의 페이지가 남긴 항목이 그렇다. 떠날 때의 사본이 있으면 먼저 보여 주고, 가져온 페이지가
  오면 그것으로 맞춘 뒤 컴포넌트가 그 렌더로 join한다. 사본은 boost 이동이 떠난 항목에만 있다. patch가 만든
  항목에는 없으므로, 그 항목으로 돌아가면 가져온 페이지가 올 때까지 지금 화면이 남는다. 이벤트로만 바꾼 상태는
  그 주소를 새로 연 페이지의 값이다.
- **페이지를 가져오는 동안에는 어느 것도 patch가 아니다.** 화면에는 떠나는 페이지나 뒤로 가기가 그린 사본이
  있을 뿐, 항목을 만든 페이지가 아니다. 그 사이의 뒤로·앞으로 가기는 그 주소를 가져오고, 떠나는 페이지의
  컴포넌트가 보낸 `push_to`도 가져온다. patch인지는 주소창이 아니라 화면에 있는 페이지의 경로로 판단하고, 상대
  URL은 `pushState`·`fetch`처럼 문서의 기준 URL(`<base href>`)로 푼다.
- **조각만 다른 항목 사이의 뒤로·앞으로 가기는 브라우저의 것이다.** `<a href="#section">`이 만든 항목이 그렇다.
  가져오지 않고 `params_changed()`도 돌지 않는다. Phoenix도 경로와 쿼리가 같은 popstate는 무시한다(#170).
- `live_session` 경계를 넘는 뒤로 가기·앞으로 가기는 전체 로드다([live_session](./live-session.md)).
- 가져오기가 네트워크 오류로 실패하면 그 주소를 보통의 페이지 로드로 연다. GET이 아닌 폼은 다시 보내지 않고,
  다른 출처로 리다이렉트된 폼도 그렇다([boost](./boost.md#가져오기가-실패하면)).

어느 항목이 "이 페이지가 만든 것"인지는 클라이언트가 `history.state`에 남긴 페이지 표식(`wireviewPage`)으로
가린다. 페이지가 로드되거나 boost 이동이 착지할 때마다 새 표식이 생기므로, 새로고침이나 다른 페이지를
거친 뒤에는 옛 항목이 모두 가져오기로 돌아간다. 판단은 `wireview/static/wireview/navigation.mjs`에 있다.

회귀 테스트는 `tests/test_history_e2e.py`와 `tests/test_live_session_e2e.py`다.

## `to`

`django.shortcuts.redirect()`와 같다. URL 경로(`"/items/"`), 쿼리나 조각만(`"?page=2"`, `"#top"` — 지금
경로에 붙는다), URL 이름과 그 인자, `get_absolute_url()`이 있는 모델 인스턴스.

```python
await self.wire.push_to("?page=2")
await self.wire.redirect_to("items:detail", pk=item.pk)   # reverse("items:detail", kwargs={"pk": ...})
await self.wire.redirect_to(item)                          # item.get_absolute_url()
```

키워드 인자는 모두 URL 역참조로 간다. 이후 릴리스가 이 메서드들에 옵션을 더하면 키워드 전용 이름으로 더한다.
URL 패턴의 인자 이름이 그 옵션과 겹치면 `reverse()`의 결과를 넘긴다: `redirect_to(reverse("x", kwargs={...}))`.

## 클라이언트에서

`wireview.visit(url, {replace})`는 링크를 누른 것과 같다([boost](./boost.md)). `JS().navigate(url, replace=)`는
같은 이동을 `{% on %}` 한 줄로 건다.

## 테스트

`view.assert_pushed_to()`·`assert_replaced_to()`·`assert_redirected_to()`로 단언하고, `follow_push()`·
`follow_redirect()`로 클라이언트가 할 일을 이어서 한다([testing](./testing.md)).
`follow_push()`는 브라우저와 같은 판단을 한다. 같은 경로면 같은 인스턴스에 `params_changed()`를 돌리고,
다른 경로면 `follow_push(Destination)`으로 목적지를 새로 마운트한다. 경로가 있는 목적지를 판단하려면
컴포넌트가 놓인 경로를 `mount(..., path="/items/")`로 알려 준다. 둘이 같은 결과를 내는지는
`tests/test_history_e2e.py::test_follow_push_and_the_browser_agree`가 본다.
