"""The boundaries the E2E pages live inside (#58).

Autodiscovered by ``WireviewConfig.ready()`` the way ``live.py`` is, which is
also what the real thing looks like: a project declares its sessions in one
module per app and never imports it by hand.
"""

from wireview import live_session

#: Staff only. The predicate is the whole policy: the view decorator runs it
#: before the page renders and the join runs the same one before it mounts.
staff = live_session("ls-staff", authorize=lambda ctx: ctx.user.is_staff)

#: A second boundary with no predicate. It exists so the E2E suite can navigate
#: between two *different* sessions, not only in and out of one.
members = live_session("ls-members", authorize=lambda ctx: ctx.user.is_authenticated)
