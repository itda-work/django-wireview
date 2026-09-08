# Streams · Presence · 업로드

## Streams — 큰 리스트를 상태에 담지 않기

리스트를 컴포넌트 필드에 넣으면 매 이벤트마다 전체가 직렬화되어 왕복한다. Streams는
아이템을 개별 렌더해 DOM에 넣고 서버 메모리에는 남기지 않는다.

```python
class XChatRoom(Component):
    _template_name = "chat/room.html"

    async def joined(self):
        await self.stream("messages", Message.objects.filter(...)[:50], limit=50)

    async def send(self, text: str = ""):
        message = await Message.objects.acreate(text=text)
        await self.stream_insert("messages", message)          # at=0이면 맨 앞

    async def remove(self, message_id: int):
        await self.stream_delete("messages", f"messages-{message_id}")
```

- 컨테이너에 `wire-stream="messages"`를 단다.
- 아이템 템플릿 기본값은 `<컴포넌트 템플릿>_item.html`이고 `template=`으로 바꾼다.
- DOM id 기본값은 `{stream_name}-{item.pk}`이고 `dom_id=`로 바꾼다. `stream_delete`에는 이 **DOM id**를 넘긴다.
- `limit=N`이면 오래된 아이템이 DOM에서 자동으로 빠진다.
- 상세: https://github.com/itda-work/django-wireview/blob/main/docs/tutorials/06-streams-api.md

## Presence — 접속자·타이핑 표시

`PresenceMixin`(자기 상태를 알리는 쪽)과 `PresenceTrackerMixin`(모아서 보여주는 쪽)을 조합한다.

```python
from wireview.features.presence import PresenceMixin

class ChatInput(PresenceMixin, Component):
    def _presence_topic(self) -> str: return f"room:{self.room_id}"
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
- S3·GCS 직행은 `external=` 콜백으로 presigned URL을 돌려준다:
  https://github.com/itda-work/django-wireview/blob/main/docs/features/external-uploads.md
- 상세: https://github.com/itda-work/django-wireview/blob/main/docs/tutorials/08-file-uploads.md
