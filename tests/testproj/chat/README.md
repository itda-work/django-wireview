# Chat App Example

A practical wireview example implementing a real-time chat application demonstrating WebSocket communication patterns.

## Features Demonstrated

### Real-time Communication
- **Streams API**: Efficient message list updates with `stream()` and `stream_insert()`
- **broadcast()**: Cross-component notifications for presence updates
- **notification()**: Hook for handling custom broadcast messages

### Component Patterns
- **Parent-Child Components**: XChatRoom contains XMessageList and XOnlineUsers
- **Dynamic Subscriptions**: `@property _subscriptions` for room-specific channels
- **Model Subscriptions**: Automatic updates on message creation

### Event Handling
- **Multiple Events**: Same input with `keypress.enter.prevent`, `input`, `blur`
- **Typing Indicators**: Debounced typing status broadcasts

## Structure

```
chat/
├── live.py               # Component definitions
├── models.py             # Room, Message models
├── views.py              # View functions
├── urls.py               # URL routing
└── templates/
    ├── base.html         # Base layout with styles
    ├── index.html        # Room list page
    ├── room.html         # Chat room wrapper
    └── chat/
        ├── room_component.html     # XChatRoom template
        ├── message_list.html       # XMessageList template
        ├── message_list_item.html  # Stream item template
        └── online_users.html       # XOnlineUsers template
```

## Components

### XChatRoom
Main container managing messages and presence.

```python
class XChatRoom(Component):
    _template_name = "chat/room_component.html"
    _subscriptions = {"chat.message"}

    room: Room
    username: str = "Anonymous"

    async def joined(self):
        # Broadcast presence to other users
        broadcast(f"room.{self.room.id}.presence", action="joined", username=self.username)

    async def send_message(self, content: str):
        if content.strip():
            await Message.objects.acreate(room=self.room, username=self.username, content=content)
        self.skip_render()  # mutation() handles the update

    async def mutation(self, channel, action, instance):
        if action == ModelAction.CREATED and instance.room_id == self.room.id:
            await self.stream_insert("messages", instance, at=-1)  # Append
```

### XMessageList
Message list using Streams for efficiency.

```python
class XMessageList(Component):
    _template_name = "chat/message_list.html"

    room: Room
    messages: list[Message] = []

    async def joined(self):
        messages = await Message.objects.filter(room=self.room)[:50]
        await self.stream("messages", list(reversed(messages)))
```

### XOnlineUsers
Presence tracking via custom broadcasts.

```python
class XOnlineUsers(Component):
    _template_name = "chat/online_users.html"

    room: Room
    online_users: dict[str, bool] = {}  # username -> is_typing

    @property
    def _subscriptions(self):
        return {f"room.{self.room.id}.presence"}

    async def notification(self, channel, **kwargs):
        action = kwargs.get("action")
        if action == "joined":
            self.online_users[kwargs["username"]] = False
            self.force_render()
```

## Key Patterns

### Streams for Lists
Use Streams for large lists to avoid memory issues:

```html
<!-- Template with wire-stream attribute -->
<ul wire-stream="messages">
  {% for item in messages %}
    {% include "chat/message_list_item.html" %}
  {% endfor %}
</ul>
```

```python
# Initial load
await self.stream("messages", list(messages))

# Add new item
await self.stream_insert("messages", new_message, at=-1)
```

### Cross-Component Communication
Use `broadcast()` and `notification()` for components that need to communicate:

```python
# In sender component
broadcast(f"room.{room_id}.presence", action="joined", username=user)

# In receiver component
@property
def _subscriptions(self):
    return {f"room.{room_id}.presence"}

async def notification(self, channel, **kwargs):
    # Handle the broadcast
    pass
```

## Running

```bash
cd tests
python manage.py migrate
python manage.py runserver
# Visit http://localhost:8000/chat/
```
