# 내비게이션

컴포넌트가 브라우저 주소를 바꾸는 방법은 넷이다. 무엇이 다시 그려지고, 연결이 이어지는지가 다르다.

| 방법 | 주소창 | 새 페이지를 가져오나 | 연결 | 기록 |
|------|--------|----------------------|------|------|
| `await self.wire.redirect_to(to)` | 목적지 | 가져온다. 목적지의 컴포넌트가 새로 마운트된다 | 이 컴포넌트는 더 렌더하지 않는다(freeze) | 새 항목 |
| `await self.wire.push_to(to)` | 목적지 | 가져와 `<body>`를 바꾼다 | 같은 `live_session` 안이면 이어진다. 컴포넌트는 가져온 렌더로 다시 join하고 `params_changed()`가 돈다. 경계를 넘으면 전체 로드 | 새 항목 |
| `await self.wire.replace_to(to)` | 목적지 | 가져오지 않는다 | 이어진다. `params_changed()`가 돈다 | 지금 항목을 바꾼다 |
| `self.wire.params["k"] = v` | 쿼리만 | 가져오지 않는다 | 이어진다. `params_changed()`는 돌지 않는다 | 지금 항목을 바꾼다 |

- **`redirect_to`는 페이지 이동이다.** `BOOST_PAGES`가 켜져 있으면 전체 로드 없이, 꺼져 있으면 보통의
  로드로 간다. 부른 뒤에는 이 컴포넌트가 렌더를 보내지 않는다. 인가가 실패했을 때 로그인 페이지로 보내는
  용도가 이것이다([lifecycle-hooks](./lifecycle-hooks.md)).
- **`push_to`는 주소에 담긴 상태로 가는 이동이다.** 목록의 다음 쪽, 탭 전환. `BOOST_PAGES`와 상관없이 목적지를
  가져와 바꾸고, 컴포넌트는 그 렌더로 다시 join한다. 그래서 주소에 없고 이벤트로만 바꾼 상태는 가져온 페이지의
  값으로 돌아간다. 남겨야 하는 상태는 쿼리에 싣거나 `replace_to`를 쓴다.
- **`replace_to`는 주소만 고친다.** 필터를 바꿨지만 뒤로 가기에 남기고 싶지 않을 때.
- **`self.wire.params`에 쓰면** 이벤트가 끝난 뒤 쿼리 문자열이 그 값으로 바뀐다(`replaceState`).
  `params`는 페이지의 모든 컴포넌트가 함께 쓰는 dict다 — 한 컴포넌트가 쓴 키를 다른 컴포넌트도 본다.
  `.json`으로 끝나는 키는 값을 JSON으로 적고 읽는다(`self.wire.params["expanded.json"] = [1, 2]`).

## 뒤로 가기·앞으로 가기

`push_to`와 boost 이동이 남긴 기록 항목으로 돌아가면, 페이지는 그 주소를 **다시 가져와** 그린다. 떠날 때의 사본을
먼저 보여 주고, 가져온 페이지가 오면 그것으로 맞춘 뒤 컴포넌트가 그 렌더로 join한다. 그래서 화면은 언제나 주소와
맞는다.

- 쿼리에 담긴 상태(`params_changed()`가 읽는 것)는 돌아온다.
- 이벤트로만 바꾼 상태는 돌아오지 않는다. 그 주소를 새로 연 페이지의 값이다.
- `replace_to`와 `self.wire.params`가 고친 항목은 기록에 따로 남지 않으므로, 뒤로 가기는 그 앞 항목으로 간다.
- `live_session` 경계를 넘는 뒤로 가기·앞으로 가기는 전체 로드다([live_session](./live-session.md)).

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
`follow_push()`는 같은 인스턴스에 `params_changed()`만 돌린다. 브라우저의 `push_to`는 목적지를 가져와 다시 join하므로,
이벤트로만 바꾼 상태가 이동 뒤에 남는지는 `mount()` 테스트가 아니라 브라우저에서 확인한다.
