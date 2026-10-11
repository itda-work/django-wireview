"""A component that shows whose it is and draws a plain POST form (#209).

tests/test_socket_identity_e2e.py compares what it shows against what the
page around it -- rendered by Django for the same response -- shows, after
another tab of the same browser changed who is logged in.
"""

from wireview import Component


class IdentityCard(Component):
    class Meta:
        template_name = "identityprobe/card.html"

    clicks: int = 0

    async def bump(self):
        self.clicks += 1
