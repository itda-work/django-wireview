"""A page that loses its connection after being live (#97).

tests/test_offline_e2e.py drives it. The form and the link bind server
handlers with ``.prevent``: while the socket is down they must not submit or
navigate, and nothing sent meanwhile -- a hook's pushEvent, which goes straight
to the socket (static/offlineprobe/hooks/pinger.js) -- may reach the server
after it is back.
"""

from wireview import Component


class OfflineBox(Component):
    class Meta:
        template_name = "offlineprobe/box.html"

    count: int = 0
    added: str = ""
    pings: int = 0

    async def bump(self, **_rest):
        self.count += 1

    async def add(self, item: str = "", **_rest):
        self.added = item

    recovered: str = ""
    city: str = ""

    async def handle_hook_event(self, hook_id, event, payload):
        if event == "ping":
            self.pings += 1
        if event == "echo":
            return {"echo": payload.get("n")}

    async def nudge(self, **_rest):
        await self.push_event("nudge", {"n": 1})

    async def recover(self, form_data: dict | None = None, **_rest):
        self.recovered = (form_data or {}).get("draft", "")

    async def set_city(self, city: str = "", **_rest):
        self.city = city
