# 세션 읽기

컴포넌트는 `self.session`으로 Django 세션을 읽는다. Phoenix가 `mount/3`의 두 번째 인자로
세션을 넘기는 자리다.

```python
from wireview import Component


class XCart(Component):
    _template_name = "shop/cart.html"

    coupon: str = ""

    @property
    def visitor(self) -> str:
        """익명 방문자 식별자."""
        return self.session.session_key or "anonymous"

    async def joined(self):
        self.coupon = self.session.get("coupon", "")
```

| 무엇 | 값 |
|------|-----|
| `self.session` | 세션 데이터의 **읽기 전용** 매핑. `["k"]`, `.get()`, `in`, 순회, `len()` |
| `self.session.session_key` | 쿠키에 든 세션 키. 세션이 아직 만들어지지 않았으면 `None` |
| `_on_mount` 훅의 세 번째 인자 | 같은 객체 ([라이프사이클 훅](./lifecycle-hooks.md)) |

템플릿에서도 그대로 읽는다.

```django
{% if this.session.coupon %}쿠폰 적용됨{% endif %}
```

## 읽기 전용인 이유

세션 쓰기는 **뷰에서** 한다. WebSocket에는 `Set-Cookie`를 실을 응답이 없고, Channels는
소켓 위에서 바뀐 세션을 저장하지 않는다. 쓸 수 있게 두면 저장된 것처럼 보이고 사라지는
코드가 된다. 그래서 쓰기 시도는 조용히 무시되지 않고 `TypeError`를 낸다.

```python
self.session["coupon"] = "X"   # TypeError: The session is read-only inside a component...
```

세션 키가 필요한 페이지는 뷰에서 세션을 만든다. 키가 없는 방문자는 쿠키가 없어
`session_key`가 `None`이고, 그 상태로는 WebSocket 연결에도 키가 없다.

```python
def product_detail(request, product_id):
    # 컴포넌트는 self.session으로 키를 읽는다. 세션을 만드는 건 뷰만 할 수 있다.
    if not request.session.session_key:
        request.session.create()
    return render(request, "rating/detail.html", {"product": get_object_or_404(Product, pk=product_id)})
```

실제 예제는 `examples/rating`(별점 1인 1회)과 `examples/quiz`(익명 응시자 식별)다.

## 클라이언트로 가지 않는다

`session`은 `_exclude_fields`의 기본값에 들어 있다. 서명된 `data-state`는 브라우저를
왕복하므로 세션 데이터가 거기 실리면 안 된다. `_exclude_fields`를 직접 덮어쓸 때는
기본값을 함께 넣는다.

```python
_exclude_fields = {"user", "wire", "session", "secret_field"}
```

빠뜨리면 조용히 새지 않고 직렬화가 `PydanticSerializationError`로 터진다.

## 스냅샷 시점

| 경로 | 언제 읽나 |
|------|-----------|
| WebSocket | 연결 시 한 번, 이벤트 루프 밖에서 읽어 둔다. 이후 접근은 dict 조회다 |
| HTTP 렌더 (`{% component %}`) | 처음 읽을 때. 세션을 보지 않는 페이지는 아무 비용도 치르지 않는다 |
| `mount()` | 주입한 값 그대로 |

WebSocket에서 미리 읽는 이유는 `SessionStore`가 첫 접근에 세션 백엔드를 조회하기 때문이다.
async 핸들러 안에서 그 조회가 일어나면 `SynchronousOnlyOperation`이다. `AuthMiddlewareStack`을
쓰면 `scope["user"]`를 만들면서 세션이 이미 메모리에 올라와 있으므로, 이 스냅샷은 보통 추가
비용이 없다.

**연결 수명 동안 스냅샷은 고정이다.** 다른 탭이나 뷰가 세션을 바꿔도 이미 열린 소켓의
`self.session`은 그대로다. 갱신이 필요하면 페이지를 새로 열거나, 세션이 아니라
[브로드캐스트](../tutorials/04-chat-app.md)로 알린다.

세션 미들웨어가 없는 프로젝트나 손으로 만든 컴포넌트에서는 빈 세션(`session_key`는 `None`)이다.

## 테스트

`mount()`에 세션을 주입한다.

```python
from wireview import mount


@pytest.mark.asyncio
async def test_the_coupon_comes_from_the_session():
    view = await mount(XCart, session={"coupon": "SUMMER"}, session_key="s1")

    assert view.component.coupon == "SUMMER"
    assert view.component.session.session_key == "s1"
```

| 인자 | 뜻 |
|------|-----|
| `session=` | 세션 데이터. dict, `SessionView`, 또는 Django `SessionStore` |
| `session_key=` | 세션 키. 데이터 없이 키만 주입할 수도 있다 |

`user`·`params`·`session`·`session_key`는 `mount()`가 쓰는 이름이라 컴포넌트 필드 이름과
겹치면 필드로 전달되지 않는다.

## 관련 문서

- [라이프사이클 훅](./lifecycle-hooks.md) — `_on_mount` 훅이 받는 세션
- [시스템 체크](./checks.md)
