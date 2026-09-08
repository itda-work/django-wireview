# 테스트

`mount()`로 WebSocket 없이 컴포넌트를 띄운다. 이것이 wireview 앱 테스트의 기본형이다.

```python
import pytest
from wireview import mount

from myapp.live import XCounter


@pytest.mark.asyncio
async def test_increment():
    view = await mount(XCounter, amount=0)      # joined()까지 실행된다
    await view.call("inc")
    assert view.component.amount == 1
```

`mount(component_class, user=None, params=None, **initial_state)`.
`user`를 주면 인증된 사용자로, `params`를 주면 URL 쿼리 파라미터가 있는 상태로 뜬다.

## MountedComponent가 주는 것

| 속성 · 메서드 | 무엇 |
|---|---|
| `await view.call("handler", **kwargs)` | 핸들러 호출. 클라이언트가 보내는 것과 같은 경로 |
| `view.component` | 컴포넌트 인스턴스. 상태를 직접 검사한다 |
| `view.render()` | 렌더된 HTML 문자열 |
| `view.sent_messages` | 클라이언트로 나간 메시지 목록 |
| `view.dom_actions` | 스트림·DOM 조작 목록 |
| `view.redirected_to` | 리다이렉트 대상 URL (없으면 `None`) |
| `view.is_frozen` | `freeze()` 여부 |
| `view.wire.broadcasts` | 이 컴포넌트가 낸 브로드캐스트 |
| `view.clear_messages()` / `view.clear_dom_actions()` | 다음 단계 전에 비운다 |

`ComponentTestCase`를 상속하면 pytest·unittest 클래스 안에서 같은 유틸을 쓸 수 있다.

## 무엇을 테스트하나

- **핸들러의 상태 전이**: 이벤트 → 필드 값. 가장 값싸고 가장 많이 잡는다.
- **권한**: 남의 객체 id를 넘겼을 때 거부되는지. 클라이언트 인자는 신뢰할 수 없다.
- **렌더 결과**: `view.render()`에 기대하는 텍스트·클래스가 있는지.
- **브로드캐스트**: 알림을 보내야 하는 핸들러가 실제로 보냈는지 (`view.wire.broadcasts`).

브라우저가 실제로 필요한 것(idiomorph 갱신, 업로드 진행률, JS Hook)만 Playwright E2E로 남긴다.

## 컴포넌트 밖의 방어선

테스트 이전에 `manage.py check`가 조용한 실패를 잡는다 — 핸들러가 async가 아님, 컴포넌트
이름 충돌, JS 미로드, 다중 프로세스에서 깨지는 InMemory 레이어. CI에 넣어 둔다.

상세: https://github.com/itda-work/django-wireview/blob/main/docs/tutorials/09-testing-components.md
