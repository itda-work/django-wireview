# Streams · Presence · 업로드

## Streams — 큰 리스트를 상태에 담지 않기

리스트를 컴포넌트 필드에 넣으면 매 이벤트마다 전체가 직렬화되어 왕복한다. Streams는
아이템을 개별 렌더해 DOM에 넣고 서버 메모리에는 남기지 않는다.

```python
class XChatRoom(Component):
    class Meta:
        template_name = "chat/room.html"

    async def joined(self):
        await self.stream("messages", Message.objects.filter(...)[:50], limit=50)

    async def send(self, text: str = ""):
        message = await Message.objects.acreate(text=text)
        await self.stream_insert("messages", message)          # at=0이면 맨 앞

    async def remove(self, message_id: int):
        await self.stream_delete("messages", f"messages-{message_id}")
```

- **QuerySet은 그대로 넘긴다**(슬라이스 포함). `stream()`이 `async for`로 읽는다. `list(qs)`·`reversed(qs)`·
  `[m for m in qs]`로 감싸면 그 자리에서 이벤트 루프 위의 동기 평가라 `SynchronousOnlyOperation`으로
  join이 실패한다. 순서는 `order_by`로 정한다. `stream_insert`에는 인스턴스 하나를 넘긴다.
- 컨테이너에 `wire-stream="messages"`를 단다.
- 아이템 템플릿 기본값은 `<컴포넌트 템플릿>_item.html`이고 `template=`으로 바꾼다.
- **아이템 템플릿 안에서 아이템은 `item`이다** (컴포넌트 자신은 `this`). 이름을 잘못 쓰면
  예외 없이 빈 칸으로 렌더된다.
- DOM id 기본값은 `{stream_name}-{item.pk}`이고 `dom_id=`로 바꾼다. `stream_delete`에는 이 **DOM id**를 넘긴다.
- 컨테이너는 스트림을 보낸 컴포넌트의 요소 안에서 찾는다. 스트림 이름은 컴포넌트 안에서만 고유하면 되지만, 항목의 DOM id는
  페이지에서 고유해야 하므로 같은 항목이 두 목록에 나오면 `dom_id`에 컴포넌트 id를 넣는다.
- `limit=N`이면 오래된 아이템이 DOM에서 자동으로 빠진다.
- 상세: https://github.com/itda-work/django-wireview/blob/main/docs/tutorials/06-streams-api.md

### 스트림에서 실제로 물리는 것

브라우저에서 확인한 것들이다. 단위 테스트는 여기까지 보지 못한다.

- **핸들러와 `mutation()` 양쪽에서 넣지 않는다.** 모델을 구독하고 있으면 저장 신호가 자기
  연결에도 돌아온다. 핸들러에서 한 번, `mutation()`에서 또 한 번 넣으면 목록에 같은 항목이
  둘 생긴다. 구독 중이라면 **삽입은 `mutation()` 한 곳에서만** 하고 핸들러는 저장만 한다.
- **같은 dom id로 다시 넣으면 제자리에서 갱신된다.** 항목 하나가 바뀌었을 때 `stream_delete` 후
  `stream_insert` 할 필요가 없다. 생성과 갱신을 한 줄로 처리한다.
- **필터·정렬 전환은 `stream()`을 다시 부르면 된다.** 상태를 바꿔 재렌더가 일어나도 스트림
  컨테이너의 내용은 보존된다 (`wire-stream` 컨테이너는 morph 대상에서 제외된다).

### 모두에게 같은 항목이면 `Broadcast`

피드·채팅처럼 새 항목을 구독한 **모든** 페이지에 넣을 때는 알림을 보내 각 연결의 `notification()`이 `stream_insert`하게
하지 말고 `Broadcast`로 보낸다. 항목을 발행하는 곳에서 한 번 렌더하고, 각 연결은 그 프레임에 자기 컴포넌트 id만 끼워 쓴다.
연결 1,000개에서 수백 ms가 수십 ms가 된다.

```python
from wireview import Broadcast, Component


class XFeed(Component):
    class Meta:
        template_name = "feed/feed.html"
        subscriptions = {"feed"}

    async def joined(self):
        await self.stream("items", Post.objects.order_by("-id")[:50])

    async def publish(self, text: str = ""):
        post = await Post.objects.acreate(text=text)
        await Broadcast(XFeed, "feed").stream_insert("items", post, at=0, limit=50).asend()
```

- **대상은 정확히 그 클래스**이고 그 토픽을 구독한 인스턴스만 받는다. 토픽이 `Meta.subscriptions`에 없으면 바로 `ValueError`.
- **항목 템플릿은 보는 사람을 읽을 수 없다.** `this`·`user`·`request`·`perms`·`csrf_token`을 읽으면 발행하는 곳에서
  `ImproperlyConfigured`. 컨텍스트는 `item`뿐이고 언어·시간대는 기본값이다. `{% on "click" "remove" pk=item.pk %}`는 된다
  (핸들러는 대상 클래스에서 확인), `myself=True`는 안 된다. 사람마다 다르면 토픽을 나누거나 알림을 쓴다.
- 보낼 수 있는 것은 `stream_insert`·`stream_delete`·`push_event`·`js`뿐이다. 필드는 바꿀 수 없다 — 개수 같은 필드는 알림으로.
- 동기 코드(신호 수신자)에서는 `.send()` — 커밋 뒤에 렌더하고 보낸다. 같은 항목을 `mutation()`에서도 넣지 않는다.
- `mount()`한 컴포넌트는 같은 프로세스의 Broadcast를 받으므로 `view.stream_html()`로 확인한다.
- 상세: https://github.com/itda-work/django-wireview/blob/main/docs/features/broadcast.md

## Presence — 접속자·타이핑 표시

`PresenceMixin`(자기 상태를 알리는 쪽)과 `PresenceTrackerMixin`(모아서 보여주는 쪽)을 조합한다.

```python
from wireview import PresenceMixin

class ChatInput(PresenceMixin, Component):
    def _presence_topic(self) -> str: return f"room.{self.room_id}"   # 채널 그룹 이름: 영숫자·-·_·. 만
    def _presence_user_id(self) -> str: return str(self.user.pk)
    def _presence_username(self) -> str: return self.user.username

    async def joined(self):
        await self.presence_join()

    async def leaving(self):
        await self.presence_leave()

    async def on_typing(self):
        await self.presence_set_typing(True)      # 서버가 타임아웃으로 자동 해제한다
```

상세: https://github.com/itda-work/django-wireview/blob/main/docs/tutorials/07-presence-api.md

## 파일 업로드

`joined()`에서 필드를 선언하고, 템플릿에 입력 UI를 놓고, 핸들러에서 소비한다.

```python
    async def joined(self):
        self.allow_upload(
            "images",
            accept=[".jpg", ".png"],
            max_entries=5,
            max_file_size=5 * 1024 * 1024,   # 기본값은 WIREVIEW["UPLOAD_MAX_FILE_SIZE"]
            auto_upload=True,
        )

    async def save(self):
        async for upload in self.consume_uploads("images"):
            path = await upload.save_to("uploads/images/")
```

```html
{% upload_input "images" class="hidden" id="image-input" %}
<div {% upload_drop_zone "images" %} class="drop-area">여기에 놓으세요</div>
{% for entry in this.uploads.images %}
  <div>{{ entry.client_name }} — {{ entry.progress }}%</div>
{% endfor %}
```

- 진행 중 항목은 `this.uploads.<name>`으로 읽는다. 취소는 `await self.cancel_upload(name, ref)`.
- 청크 업로드는 WebSocket이 아니라 HTTP 엔드포인트를 쓴다. 프로젝트 URLconf에
  `path("", include("wireview.urls"))`를 넣지 않으면 업로드만 조용히 404가 난다.
- 워커가 여럿이어도 스티키 라우팅은 필요 없다. 대신 워커들이 청크 저장소
  (`WIREVIEW["UPLOAD_TEMP_DIR"]`, 미설정이면 시스템 temp — 한 호스트면 이미 공유)와 서명 키를
  공유해야 한다. 여러 호스트라면 공유 볼륨을 지정하거나 external 업로드를 쓴다:
  https://github.com/itda-work/django-wireview/blob/main/docs/features/chunked-uploads.md
- S3·GCS 직행은 `external=` 콜백으로 presigned URL을 돌려준다:
  https://github.com/itda-work/django-wireview/blob/main/docs/features/external-uploads.md
- 상세: https://github.com/itda-work/django-wireview/blob/main/docs/tutorials/08-file-uploads.md
