# LiveComponent 수명주기 — 지금 실제로 일어나는 일

> 2026-09-09. 코드를 읽고 재현해 확인한 사실과, 그로부터 나오는 설계 질문.
> 계기: `docs/design/live-session.md` 초안이 등록 시점을 반대로 적었고, 2라운드 리뷰가 그걸 잡았다.
> "지금 오류가 안 난다"와 "설계가 맞다"는 다른 문제이므로 별도로 적는다.

## 1. 확인된 동작

### 1-1. 자식은 부모 렌더 **도중** 만들어지고 그 자리에서 렌더된다

`{% live_component %}`(`templatetags/wireview.py:787-795`)는 부모 템플릿을 평가하는 중에

1. `repo.build_live_component(name, state, parent_id)`를 호출하고,
2. 반환된 자식을 즉시 `_render(repo)`해서 그 HTML을 부모 출력에 끼워 넣는다.

`build_live_component`(`repository.py:107-168`)는 그 안에서 클래스를 resolve하고, `_parent_id`를
세우고, **`repo.components`에 등록하고**, `_pending_live_components` 큐에 넣는다.

즉 자식은 **등록도 렌더도 `joined()` 이전에** 끝난다.

### 1-2. `joined()`는 자식의 HTML이 나간 **뒤**에 돈다

`command_join`(`consumer.py:96-116`)의 순서:

```
send_render(parent)                  # 자식 HTML이 이미 이 안에 있다
_flush_pending_live_components()     # 여기서 비로소 자식의 joined()
  └ repo.flush_pending_live_components()
      ├ 새 자식: wire.enter_pending_mode() → await joined()
      └ 기존 자식: await update(**changed_props)
  └ 자식마다 send_render(child) → wire.flush_pending()
```

결과로 따라오는 것들:

- 자식의 **첫 화면은 `joined()` 이전 상태**다. `joined()`에서 DB를 읽으면 화면이 두 번 그려진다.
- `joined()`가 "이 사용자는 보면 안 된다"고 판단해도 **HTML은 이미 나갔다.**
- 이벤트 경로(`command_user_event` 등)도 같은 모양이다: 렌더 → 큐 flush.

### 1-3. 새 자식과 기존 자식의 처리가 비대칭이다

| | 새 자식 | 기존 자식(prop 변경) |
|---|---|---|
| 호출 | `joined()` | `update(**changed_props)` |
| pending mode | `enter_pending_mode()` 진입 | **진입하지 않는다** |
| 결과 | `stream()`·`push_js()`가 렌더 뒤로 밀린다 | 렌더보다 먼저 나갈 수 있다 |

`changed_props`는 `key in existing.model_fields`로 거른다(`repository.py:135`). 필드가 아닌 prop은
조용히 버려지고, 이 접근은 Pydantic 2.11에서 인스턴스 경유 deprecation 경고 대상이다.

### 1-4. 재연결에서 자식 상태가 사라진다 (재현 확인)

클라이언트는 재연결 시 부모를 다시 join하면서 **모든 `[wireview-component]` 자손의 서명 상태**를
`children`으로 함께 보낸다(`wireview.js:946-959`). LiveComponent도 `wireview-component` 속성을
달고 있으므로 여기 포함된다.

그런데 `build_live_component`는 `repo.children`을 **보지 않는다.** 일반 `build()`에는 그 분기가
있고(`repository.py:91-95`), 이 경로에는 없다.

```python
repo.children = {"counter-1": ("ProbeCounter", unsign_state(signed_with_count_7))}
rebuilt = repo.build_live_component("ProbeCounter", {"id": "counter-1", "count": 0}, parent_id="p1")
assert rebuilt.count == 7
# AssertionError: reconnect reset the counter to 0
```

**두 경로의 차이를 정확히 적는다.** 일반 `build()`의 병합은 `state = child_state | state`라
**템플릿이 넘긴 prop이 복원된 값을 이긴다.** 실측:

| 필드 | 복원된 값 | 템플릿이 넘긴 값 | 결과 |
|---|---|---|---|
| `count` | 7 | 0 | **0** (prop이 이긴다) |
| `note` | `"kept"` | 넘기지 않음 | **`"kept"`** (복원된다) |

그러므로 "일반은 복원되고 LiveComponent는 안 된다"는 너무 거칠다. 정확히는

- **일반 중첩 컴포넌트**: 템플릿이 넘기지 **않는** 필드만 복원된다. 자식이 스스로 쌓은 내부 상태
  (펼침 여부, 선택, 필터)가 여기 해당한다.
- **LiveComponent**: 복원 자체가 없다. 템플릿이 넘기지 않는 필드는 **필드 기본값**으로 돌아간다.

클라이언트가 자식 상태를 서명해 보내는 것을 보면 복원이 의도였던 것 같은데, LiveComponent 경로는
그 값을 쓰지 않는다.

### 1-5. 서버에는 고아 정리가 없다

부모가 다시 렌더되면서 어떤 `{% live_component %}`가 사라져도 `repo.components`에서는 지워지지
않는다. `repo.remove()`를 부르는 것은 클라이언트의 `leave`뿐이고(`consumer.py:118-122`),
클라이언트는 DOM 스캔에서 사라진 id를 발견했을 때 그것을 보낸다(`wireview.js:158-172`).

따라서 서버 상태의 정리가 **클라이언트의 통지에 달려 있다.** 통지가 오지 않는 경로가 있으면
컴포넌트는 저장소에 남고, `myself` 대상 이벤트를 계속 받는다.

## 2. 설계 질문 — 지금 오류가 나지 않아도 물어야 하는 것

1. **자식의 첫 렌더가 `joined()`를 기다려야 하는가.** 지금은 두 번 그린다. 기다리게 하면 동기
   템플릿 렌더 안에서 async 훅을 부를 방법이 필요하다. 기다리지 않으면 `docs/design/live-session.md`가
   요구하는 "정책 통과 전에는 아무것도 내보내지 않는다"를 만족할 수 없다. 둘 중 하나는 바뀌어야 한다.
2. **재연결의 정답은 무엇인가.** 자식 상태를 복원해야 하는가(클라이언트는 이미 보내고 있다),
   아니면 부모가 준 prop이 진실이고 자식 상태는 파생인가. Phoenix의 LiveComponent는 부모의
   assigns에서 다시 mount된다 — 그 쪽을 따를 거면 클라이언트가 자식 상태를 보내는 것이 오히려
   잉여다. 어느 쪽이든 **지금은 둘 다 아니다**: 보내고, 무시한다.
3. **고아의 수명을 서버가 알아야 하는가.** 클라이언트 통지에 의존하는 현재 구조에서, 통지가
   유실되는 경로(연결 끊김, 스트림 컨테이너 교체, JS 명령으로 제거)를 열거해야 한다.
4. **pending mode 비대칭이 의도인가.** `update()` 경로만 렌더보다 먼저 부수효과를 낼 수 있다.
5. **`_pending_updates`의 필드 필터가 의도인가.** 필드가 아닌 prop을 조용히 버리는 것이 맞는지,
   아니면 `update()`가 받아서 판단해야 하는지.

## 3. 이 문서의 위치

여기는 **현황과 질문**만 담는다. 답은 각 이슈에서 정한다.

- 정책·인증 경계에서의 요구는 [live-session.md](./live-session.md) §3-5.
- 위 1-4(재연결 상태 손실)와 1-5(고아)는 별도 이슈로 뗀다.
