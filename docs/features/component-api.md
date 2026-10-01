# Component API

`Component`와 `LiveComponent`의 공개 멤버 전부다. 여기 없는 멤버는 밑줄이 없어도 **내부**이고,
마이너 릴리스에서 바뀔 수 있다([호환성 정책](../COMPATIBILITY.md)). 사용법은 표의 링크에 있다.

`tests/test_public_api.py`가 이 표와 클래스를 대조한다. 밑줄 없는 멤버를 새로 만들면 여기 적거나
아래 "내부" 목록에 넣어야 테스트가 통과한다.

## 필드

| 이름 | 뜻 |
|------|----|
| `id` | 페이지 안에서 고유한 컴포넌트 id |
| `user` | 연결의 사용자 (`AnonymousUser` 포함) |
| `session` | Django 세션의 읽기 전용 뷰 ([session](./session.md)) |
| `wire` | 내비게이션과 쿼리. 공개는 `params`, `redirect_to`, `replace_to`, `push_to`뿐이다 ([navigation](./navigation.md)) |
| `uploads` | 업로드 항목. 템플릿의 `this.uploads.<name>` ([external-uploads](./external-uploads.md)) |

선언한 필드는 상태다. JSON으로 직렬화되어야 하고 모델 인스턴스는 pk로 서명된다. 설정은 `class Meta:`에
둔다(README의 Meta 표).

## 오버라이드하는 것

wireview가 부르는 콜백이다. `get_subscriptions()`와 `new()` 말고는 모두 `async def`로 쓴다 —
sync로 쓰면 실행되지 않고 `manage.py check`가 `wireview.W002`로 알린다.

| 메서드 | 언제 |
|--------|------|
| `joined()` | 소켓에 연결되어 첫 렌더를 보내기 전 |
| `leaving()` | 컴포넌트가 떠날 때 (소켓이 닫힘, 페이지 이동, 부모가 뺌) |
| `params_changed(params, uri)` | URL 쿼리가 바뀌었을 때 |
| `mutation(channel, action, instance)` | 구독한 모델이 바뀌었을 때 (`AUTO_BROADCAST`). `instance`는 알림에 실려 온 값에서 복원한 것이다 — DB에서 다시 읽지 않고, 관계는 id만 있다. 같은 알림을 받는 컴포넌트마다 따로 복원하므로 고쳐도 다른 컴포넌트가 받는 값은 그대로다. 페이로드에 없는 필드(다중 테이블 상속의 부모 모델 필드 등)는 deferred라 읽으면 DB를 조회한다. 저장하면 보통의 저장이다(모델의 `save()`, `raw=False` 시그널). 무엇을 쓰는지는 [설정의 모델 알림](./settings.md#모델-알림) |
| `notification(channel, **kwargs)` | 구독한 채널로 브로드캐스트가 왔을 때 |
| `handle_async(name, result)` | `start_async()`가 끝났을 때. `result`는 끝난 `AsyncResult`(`ok`/`failed`) ([async-operations](./async-operations.md)) |
| `handle_hook_event(hook_id, event, payload)` | 클라이언트 훅이 `pushEvent`로 보냈을 때 ([hooks](./hooks.md)) |
| `get_subscriptions()` | 구독 채널을 상태에 따라 정할 때. 기본은 `Meta.subscriptions` |
| `new(**kwargs)` (classmethod) | 인스턴스를 만들 때. 페이지 렌더, join, 테스트의 `mount()` 모두 이것을 거친다. `wire`·`user`·`session`과 상태 필드를 키워드로 받고 `cls(**kwargs)`를 돌려줘야 한다 |
| `LiveComponent.update(**assigns)` | 부모가 새 값을 줄 때 ([live-component](./live-component.md)) |
| `LiveComponent.update_many(updates)` (classmethod) | 부모 렌더 한 번에 값이 바뀐 같은 클래스 자식 전부를 `[(component, assigns), ...]`로. 기본은 각자의 `update()` |

## 부르는 것

| 메서드 | 하는 일 |
|--------|---------|
| `skip_render()` | 이번 이벤트의 렌더를 건너뛴다 |
| `force_render()` | 바뀐 것이 없어도 렌더한다 |
| `await send_render()` | 지금 렌더를 보낸다 (긴 작업의 중간 진행률) |
| `freeze()` | 이후 렌더를 보내지 않는다 |
| `await destroy()` | 컴포넌트를 페이지에서 뺀다 |
| `await broadcast(channel, **kwargs)` | 채널로 보낸다. `joined()` 전이면 모았다가 보낸다 |
| `await push_event(event, payload=None, hook_id=None)` | 클라이언트 훅으로 보낸다 |
| `await push_js(js)` | `JS()` 명령을 실행한다 |
| `await push_title(title)` | 문서 제목을 바꾼다 |
| `await put_flash(flash_type, message, *, timeout=5000, dismissible=True)` / `await clear_flash(flash_id=None)` | 플래시 ([flash](./flash.md)) |
| `await focus_on(selector)` | 요소에 포커스 |
| `await scroll_into_view(element_id, *, behavior="auto", block="start", inline="nearest")` | 요소를 보이게 스크롤 |
| `await defer(f, *args, **kwargs)` | 지금 이벤트가 끝난 뒤 `f`를 부른다 |
| `await start_async(name, coro)` / `await cancel_async(name)` | 백그라운드 작업 ([async-operations](./async-operations.md)) |
| `await assign_async(coro, *, on_error=None)` | 결과를 `AsyncResult` 필드로 받는다 |
| `await stream(name, items, *, template=None, dom_id=None, limit=0)` | 스트림을 채우거나 다시 채운다 ([tutorial 06](../tutorials/06-streams-api.md)) |
| `await stream_insert(name, item, *, at=-1, template=None, dom_id=None, limit=0)` / `await stream_delete(name, dom_id)` | 스트림 항목 |
| `allow_upload(name, *, accept=None, max_entries=1, max_file_size=None, chunk_size=None, auto_upload=True, external=None)` | 업로드를 받는다 ([chunked-uploads](./chunked-uploads.md)) |
| `consume_uploads(name)` / `await cancel_upload(name, ref)` | 완료된 업로드를 꺼낸다 / 취소한다 |
| `attach_hook(name, stage, callback)` / `detach_hook(name, stage=None)` | 이 인스턴스에 수명주기 훅을 단다 ([lifecycle-hooks](./lifecycle-hooks.md)) |
| `await send_update(live_component_id, **assigns)` | 자식 LiveComponent에 값을 준다 |
| `await LiveComponent.send_to_parent(event, **kwargs)` | 부모의 핸들러를 부른다 |

## 내부

밑줄이 없지만 공개가 아닌 것: `LiveComponent.myself`. Pydantic `BaseModel`에서 온 멤버
(`model_dump` 등)는 Pydantic의 것이고 wireview가 약속하지 않는다.
