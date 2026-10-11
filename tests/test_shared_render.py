"""``Meta.shared_render``: one render for the connections handling the same broadcast (#176, stage 2).

docs/design/broadcast-fanout.md §4-A and §7 say what is shared and what is
not. In short, these hold:

- the render is shared only within one message, and only between instances of
  the same class with the same id, fields, language and time zone
- a field that holds a model instance or a QuerySet keeps the render the
  connection's own: the same pk may carry other attributes on another connection
- each connection still diffs against its own page, and its ``data-state`` is
  its own token: what a page boundary signs differs between viewers
- the frames are the bytes an unshared render sends
- a class out of scope does not share, and ``wireview.W019`` says why
- with ``VERIFY_SHARED_RENDER`` a render that reads the viewer raises, and a
  render taken from another connection is rendered again and compared
- the token is signed off the event loop, by the leader and the takers alike
- what the process keeps of a message goes after a while, and within a size
"""

import asyncio
import json
import re
import typing as t
from collections import Counter
from uuid import uuid4

import django
import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.template import TemplateDoesNotExist, TemplateSyntaxError
from django.test import override_settings
from django.utils import translation
from pydantic import BaseModel, ConfigDict, Field, computed_field
from testproj.bookmarks.models import Bookmark
from testproj.outbound import RecordingOutbound
from testproj.waiting import eventually
from testproj.wireview_setting import set_wireview

from wireview import Component, LiveComponent, function_component, live_session
from wireview import testing as wireview_testing
from wireview.checks import check_shared_render
from wireview.core import live_session as live_session_module
from wireview.core import shared_render
from wireview.core.meta import WireviewMeta
from wireview.core.rendered import PROTOCOL_VERSION, Rendered
from wireview.core.session import SessionView
from wireview.core.state import sign_state, unsign_envelope
from wireview.session import WireviewSession

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

BOARD = (
    "{% load wireview %}<section {% tag_header %}><h1>{{ headline }}</h1><p>page {{ page }}</p>"
    "<ul>{% for item in items %}<li>{{ item }}</li>{% endfor %}</ul>"
    "{% if page %}<b>{{ page }}</b>{% endif %}</section>"
)
TEMPLATES = {
    "sh/board.html": BOARD,
    "sh/user.html": "{% load wireview %}<p {% tag_header %}>{{ headline }} for {{ this.user.username }}</p>",
    "sh/user_property.html": "{% load wireview %}<p {% tag_header %}>{{ greeting }}</p>",
    "sh/request.html": "{% load wireview %}<p {% tag_header %}>{{ headline }} {{ request.path }}</p>",
    "sh/csrf.html": "{% load wireview %}<form {% tag_header %}>{% csrf_token %}{{ headline }}</form>",
    "sh/messages.html": "{% load wireview %}<p {% tag_header %}>{% for m in messages %}{{ m }}{% endfor %}</p>",
    "sh/viewer.html": "{% load wireview %}<p {% tag_header %}>{{ headline }} seen by {{ viewer }}</p>",
    "sh/temp.html": "{% load wireview %}<p {% tag_header %}>{% for r in rows %}{{ r }}{% endfor %}</p>",
    "sh/nested.html": '{% load wireview %}<div {% tag_header %}>{% component "ShInner" id="inner" %}</div>',
    "sh/inner.html": "{% load wireview %}<span {% tag_header %}>{{ headline }}</span>",
    "sh/plain.html": "{% load wireview %}<p {% tag_header %}>{{ headline }}</p>",
    "sh/child.html": "{% load wireview %}<i {% live_tag_header %}>{{ headline }}</i>",
    "sh/include.html": '{% load wireview %}<p {% tag_header %}>{{ headline }}{% include "sh/partial.html" %}</p>',
    "sh/partial.html": "<i>{{ request.path }}</i>",
    "sh/session_property.html": "{% load wireview %}<p {% tag_header %}>{{ visits }}</p>",
    "sh/twice.html": "{% load wireview %}<p {% tag_header %}>{{ headline }}</p><p {% tag_header %}></p>",
    "sh/doc.html": "{% load wireview %}<p {% tag_header %}>{{ headline }} {{ doc.title }}</p>",
    "sh/marks.html": "{% load wireview %}<p {% tag_header %}>{% for b in marks %}{{ b.title }}{% endfor %}</p>",
    "sh/counted.html": "{% load wireview %}<p {% tag_header %}>{{ headline }} {{ rows }}</p>",
    "sh/hidden.html": "{% load wireview %}<p {% tag_header %}>{{ headline }} {{ viewer }}</p>",
    "sh/if_include.html": '{% load wireview %}<p {% tag_header %}>{{ headline }}{% include "sh/if_part.html" %}</p>',
    "sh/if_part.html": "{% if request.user.is_staff and headline %}staff{% endif %}",
    "sh/alias.html": "{% load wireview %}<p {% tag_header %}>{% with viewer=this %}{{ viewer.user }}{% endwith %}</p>",
    "sh/if_alias.html": (
        "{% load wireview %}<p {% tag_header %}>"
        "{% with viewer=this %}{% if viewer.user.is_staff and headline %}staff{% endif %}{% endwith %}</p>"
    ),
    "sh/if_property.html": "{% load wireview %}<p {% tag_header %}>{% if staff and headline %}staff{% endif %}</p>",
    "sh/filter_arg.html": "{% load wireview %}<p {% tag_header %}>{{ headline|default:request.path }}</p>",
    "sh/firstof.html": "{% load wireview %}<p {% tag_header %}>{% firstof headline user.username %}</p>",
    # The templates a render reaches beyond its own (#208)
    "sh/inc_user.html": '{% load wireview %}<p {% tag_header %}>{{ headline }}{% include "sh/user_part.html" %}</p>',
    "sh/user_part.html": "<i>{{ user.username }}</i>",
    "sh/ext_user.html": '{% extends "sh/user_base.html" %}{% block body %}{{ headline }}{% endblock %}',
    "sh/user_base.html": (
        "{% load wireview %}<p {% tag_header %}>{% block body %}{% endblock %} for {{ user.username }}</p>"
    ),
    "sh/func_csrf.html": '{% load wireview %}<p {% tag_header %}>{{ headline }}{% func "sh_form" %}</p>',
    "sh/func_block_csrf.html": (
        '{% load wireview %}<p {% tag_header %}>{{ headline }}{% func_block "sh_form" %}x{% endfunc %}</p>'
    ),
    "sh/form.html": '<form method="post">{% csrf_token %}{{ label }}</form>',
    "sh/label.html": "<b>{{ label }}</b>",
    "sh/func_component.html": '{% load wireview %}<p {% tag_header %}>{{ headline }}{% func "sh_holder" %}</p>',
    "sh/holder.html": '{% load wireview %}<b>{% component "ShInner" id="held" %}</b>',
    "sh/deep.html": '{% load wireview %}<p {% tag_header %}>{{ headline }}{% include "sh/deep_1.html" %}</p>',
    "sh/deep_1.html": '{% extends "sh/deep_2.html" %}',
    "sh/deep_2.html": '{% block b %}{% include "sh/user_part.html" %}{% endblock %}',
    "sh/inc_plain.html": '{% load wireview %}<p {% tag_header %}>{{ headline }}{% include "sh/plain_part.html" %}</p>',
    "sh/plain_part.html": '<i>{{ headline }}</i>{% if again %}{% include "sh/plain_part.html" %}{% endif %}',
    "sh/inc_var.html": "{% load wireview %}<p {% tag_header %}>{{ headline }}{% include part %}</p>",
    "sh/ext_var.html": "{% extends base %}",
    "sh/func_var.html": "{% load wireview %}<p {% tag_header %}>{{ headline }}{% func kind %}</p>",
    "sh/func_unknown.html": '{% load wireview %}<p {% tag_header %}>{{ headline }}{% func "sh_nowhere" %}</p>',
    "sh/inc_missing.html": '{% load wireview %}<p {% tag_header %}>{{ headline }}{% include "sh/nowhere.html" %}</p>',
    "sh/card_tag.html": "{% load wireview sh_tags %}<p {% tag_header %}>{{ headline }}{% sh_card %}</p>",
    "sh/card.html": "<b>{{ user.username }}</b>",
    "sh/func_plain.html": (
        '{% load wireview %}<p {% tag_header %}>{{ headline }}{% func "sh_label" %}{% func "sh_inline" %}</p>'
    ),
    # Shapes that once read past the check: one file's partials, a string's Origin, a tag's own body
    "sh/partials.html": (
        "{% load wireview %}<p {% tag_header %}>{{ headline }}"
        '{% include "sh/parts.html#safe" %}{% include "sh/parts.html#viewer" %}</p>'
    ),
    "sh/parts.html": (
        "{% partialdef safe %}<i>s</i>{% endpartialdef %}"
        "{% partialdef viewer %}<b>{{ user.username }}</b>{% endpartialdef %}"
    ),
    "sh/partials_whole.html": (
        "{% load wireview %}<p {% tag_header %}>{{ headline }}"
        '{% include "sh/parts_whole.html#safe" %}{% include "sh/parts_whole.html" %}</p>'
    ),
    "sh/parts_whole.html": "{% partialdef safe %}<i>s</i>{% endpartialdef %}<b>{{ user.username }}</b>",
    "sh/blocktrans.html": (
        "{% load wireview i18n %}<p {% tag_header %}>{{ headline }}"
        "{% blocktranslate %} for {{ user }}{% endblocktranslate %}</p>"
    ),
    "sh/wrap.html": (
        "{% load wireview sh_tags %}<p {% tag_header %}>{{ headline }}"
        '{% sh_wrap %}{% include "sh/user_part.html" %}{% endsh_wrap %}</p>'
    ),
    "sh/wrap_direct.html": (
        "{% load wireview sh_tags %}<p {% tag_header %}>{{ headline }}{% sh_wrap %}{{ user }}{% endsh_wrap %}</p>"
    ),
    "sh/anon_cards.html": (
        "{% load wireview sh_tags %}<p {% tag_header %}>{{ headline }}{% sh_safe_card %}{% sh_viewer_card %}</p>"
    ),
    "sh/sub_card.html": "{% load wireview sh_tags %}<p {% tag_header %}>{{ headline }}{% sh_sub_card %}</p>",
    "sh/debug.html": "{% load wireview %}<p {% tag_header %}>{{ headline }}{% debug %}</p>",
    # A field named as the request's: the component's own template reads the field, another tag's does not
    "sh/request_card_tag.html": (
        "{% load wireview sh_tags %}<p {% tag_header %}>{{ request }}{% sh_request_card %}</p>"
    ),
    "sh/request_card.html": "<i>{{ request.path }}</i>",
    "sh/request_func.html": '{% load wireview %}<p {% tag_header %}>{{ request }}{% func "sh_request" %}</p>',
    # Shapes the attribute walk missed (#208, round 2): django-template-partials' partials on Django 5.2,
    # a chain of namesakes, a body in an object or four containers down, an argument in a value object
    "sh/proxy.html": (
        "{% load wireview %}<p {% tag_header %}>{{ headline }}"
        '{% include "sh/proxy_parts.html#safe" %}{% include "sh/proxy_parts.html#viewer" %}</p>'
    ),
    "sh/proxy_parts.html": (
        "{% load sh_partials %}{% partialdef safe %}<i>s</i>{% endpartialdef %}"
        "{% partialdef viewer %}<b>{{ user.username }}</b>{% endpartialdef %}"
    ),
    "sh/page_parts.html": (
        "{% load sh_partials %}<nav>{{ user.username }}</nav>"
        "{% partialdef row %}<i>{{ headline }}</i>{% endpartialdef %}"
        "{% partialdef linked %}<i>{% partial named %}</i>{% endpartialdef %}"
        "{% partialdef named %}{{ user.username }}{% endpartialdef %}"
    ),
    "sh/history.html": (
        '{% load wireview %}<p {% tag_header %}>{{ headline }}{% include "sh/hq.html" %}{% include "sh/hn.html" %}</p>'
    ),
    "sh/hq.html": '{% extends "sh/hn.html" %}{% block b %}q1{% endblock %}',
    "sh/hn.html": '{% extends "sh/hq.html" %}{% block b %}n1{% endblock %}',
    "sh/slot.html": (
        "{% load wireview sh_tags %}<p {% tag_header %}>{{ headline }}"
        '{% sh_slot %}<b>{% include "sh/user_part.html" %}</b>{% endsh_slot %}</p>'
    ),
    "sh/branches.html": (
        "{% load wireview sh_tags %}<p {% tag_header %}>{{ headline }}"
        "{% sh_deep %}<b>{{ user.username }}</b>{% endsh_deep %}</p>"
    ),
    "sh/show.html": (
        "{% load wireview sh_tags %}<p {% tag_header %}>{{ headline }}<b>{% sh_show user.username %}</b></p>"
    ),
    "sh/variable.html": (
        "{% load wireview sh_tags %}<p {% tag_header %}>{{ headline }}<b>{% sh_variable user.username %}</b></p>"
    ),
    # Shapes the recording parser missed (#208, round 3): wireview's own {% cond %} and {% class %}, which
    # hold their expression as text; a partial that renders a macro parsed outside it; a body taken as tokens
    "sh/cond.html": "{% load wireview %}<p {% tag_header %}>{{ headline }}<b>{% cond {user.username: True} %}</b></p>",
    "sh/class.html": (
        "{% load wireview %}<p {% tag_header %}>{{ headline }}<b {% class {this.user.username: True} %}></b></p>"
    ),
    "sh/macro.html": '{% load wireview %}<p {% tag_header %}>{{ headline }}{% include "sh/macro_parts.html#row" %}</p>',
    "sh/macro_parts.html": (
        ("{% load sh_tags %}" if django.VERSION >= (6, 0) else "{% load sh_partials sh_tags %}")
        + "{% sh_macro who %}<b>{{ user.username }}</b>{% endsh_macro %}"
        "{% partialdef row %}[{% sh_use who %}]{% endpartialdef %}"
    ),
    "sh/later.html": (
        "{% load wireview sh_tags %}<p {% tag_header %}>{{ headline }}"
        "{% sh_later %}<b>{{ user.username }}</b>{% endsh_later %}</p>"
    ),
    "sh/fresh.html": (
        "{% load wireview sh_tags %}<p {% tag_header %}>{{ headline }}"
        "{% sh_fresh %}<b>{{ user.username }}</b>{% endsh_fresh %}</p>"
    ),
    "sh/comment.html": (
        "{% load wireview %}<p {% tag_header %}>{{ headline }}{% comment %}{{ user.username }}{% endcomment %}</p>"
    ),
    "sh/cond_broken.html": "{% load wireview %}<p {% tag_header %}>{{ headline }}<b>{% cond {user: } %}</b></p>",
    "sh/later_unread.html": (
        "{% load wireview sh_tags %}<p {% tag_header %}>{{ headline }}{% sh_later %}{{ a b }}{% endsh_later %}</p>"
    ),
    "sh/alias_parts.html": (
        "{% load sh_partials sh_tags %}{% sh_alias row %}<b>{{ user.username }}</b>{% endsh_alias %}"
        "{% partialdef row %}<i>s</i>{% endpartialdef %}"
    ),
}
#: The next loader's namesakes: ``sh/hn.html`` there is what the chain ``hn -> hq -> hn`` finds last
NAMESAKES = {
    "sh/hq.html": "[q2 {% block b %}{% endblock %}]",
    "sh/hn.html": "[n2 <b>{{ user.username }}</b>{% block b %}{% endblock %}]",
}

STORE: dict[str, t.Any] = {}
RENDERS: Counter[str] = Counter()
VIEWERS: dict[str, str] = {}


def _reset_store() -> None:
    STORE.clear()
    STORE.update(headline="hello", items=["a", "b", "c"])
    RENDERS.clear()
    VIEWERS.clear()


class _Headline:
    """Counts the renders of each class: every render reads ``headline`` once."""

    @property
    def headline(self) -> str:
        RENDERS[type(self).__name__] += 1
        if STORE.get("raise_once") == type(self).__name__:
            del STORE["raise_once"]
            raise RuntimeError("the first render of this message fails")
        return STORE["headline"]


class ShBoard(_Headline, Component):
    class Meta:
        template_name = "sh/board.html"
        subscriptions = {"sh-board"}
        shared_render = True

    page: int = 0

    @property
    def items(self) -> list[str]:
        return STORE["items"]

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass

    async def turn(self, to: int):
        self.page = to


class ShPlainBoard(ShBoard):
    """The same board without the declaration: what an unshared render sends."""

    class Meta:
        shared_render = False


class ShUser(_Headline, Component):
    class Meta:
        template_name = "sh/user.html"
        subscriptions = {"sh-board"}
        shared_render = True

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


class ShUserProperty(_Headline, Component):
    class Meta:
        template_name = "sh/user_property.html"
        shared_render = True

    @property
    def greeting(self) -> str:
        return f"hi {self.user}"


class ShSessionProperty(_Headline, Component):
    class Meta:
        template_name = "sh/session_property.html"
        shared_render = True

    @property
    def visits(self) -> int:
        return self.session.get("visits", 0)


class ShInclude(_Headline, Component):
    """Reads the request in an included template."""

    class Meta:
        template_name = "sh/include.html"
        shared_render = True


class ShRequest(_Headline, Component):
    class Meta:
        template_name = "sh/request.html"
        shared_render = True


class ShCsrf(_Headline, Component):
    class Meta:
        template_name = "sh/csrf.html"
        shared_render = True


class ShMessages(_Headline, Component):
    """Has a field named like a context processor's: the field wins, nothing raises."""

    class Meta:
        template_name = "sh/messages.html"
        shared_render = True

    messages: list[str] = ["one", "two"]


class ShViewer(_Headline, Component):
    """Wrongly declared: what it shows depends on the connection, through no watched name."""

    class Meta:
        template_name = "sh/viewer.html"
        subscriptions = {"sh-board"}
        shared_render = True

    @property
    def viewer(self) -> str:
        return VIEWERS.get(self.wire.channel_name or "", "?")

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


class ShTemporary(_Headline, Component):
    class Meta:
        template_name = "sh/temp.html"
        subscriptions = {"sh-board"}
        temporary_assigns = {"rows"}
        shared_render = True

    rows: list[str] = []

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        self.rows = ["x"]


class ShInner(_Headline, Component):
    class Meta:
        template_name = "sh/inner.html"


class ShNested(_Headline, Component):
    class Meta:
        template_name = "sh/nested.html"
        subscriptions = {"sh-board"}
        shared_render = True

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


class ShChild(_Headline, LiveComponent):
    class Meta:
        template_name = "sh/child.html"
        shared_render = True


class ShBounded(_Headline, Component):
    class Meta:
        template_name = "sh/plain.html"
        live_sessions = {"sh-room"}
        shared_render = True


class ShSlotted(_Headline, Component):
    class Meta:
        template_name = "sh/plain.html"
        slots = {"body": {}}
        shared_render = True


class ShTwice(_Headline, Component):
    class Meta:
        template_name = "sh/twice.html"
        subscriptions = {"sh-board"}
        shared_render = True

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


class ShDoc(_Headline, Component):
    """A row typed as one: out of scope before any render."""

    class Meta:
        template_name = "sh/doc.html"
        subscriptions = {"sh-board"}
        shared_render = True

    doc: Bookmark | None = None

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


class ShAnyDoc(ShDoc):
    """A row in a field whose type does not say so: the render tells."""

    class Meta:
        shared_render = True

    doc: t.Any = None


class ShMarks(_Headline, Component):
    """A QuerySet in a field whose type does not say so: in scope, and its state's JSON queries."""

    class Meta:
        template_name = "sh/marks.html"
        subscriptions = {"sh-board"}
        shared_render = True

    marks: t.Any = None

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


class ShCounted(_Headline, Component):
    """Shared, with a computed field in its state that queries: every token is signed off the loop."""

    class Meta:
        template_name = "sh/counted.html"
        subscriptions = {"sh-board"}
        shared_render = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rows(self) -> int:
        return Bookmark.objects.count()

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


class ShHidden(_Headline, Component):
    """A field pydantic leaves out of its dumps, which the render reads all the same."""

    class Meta:
        template_name = "sh/hidden.html"
        subscriptions = {"sh-board"}
        shared_render = True

    viewer: str = Field("", exclude=True)

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


REDIRECTED: set[str] = set()


class ShLeaving(_Headline, Component):
    class Meta:
        template_name = "sh/plain.html"
        subscriptions = {"sh-board"}
        shared_render = True

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        if self.wire.channel_name in REDIRECTED:
            await self.wire.redirect_to("/elsewhere/")


class ShIfInclude(_Headline, Component):
    """Reads the request where ``{% if a and b %}`` swallows what the read raises."""

    class Meta:
        template_name = "sh/if_include.html"
        shared_render = True


class ShAlias(_Headline, Component):
    """Reads the user under another name, which the system check cannot tell from a field."""

    class Meta:
        template_name = "sh/alias.html"
        shared_render = True


class ShIfAlias(_Headline, Component):
    """The same where ``{% if a and b %}`` swallows what the read raises."""

    class Meta:
        template_name = "sh/if_alias.html"
        shared_render = True


class ShIfProperty(_Headline, Component):
    """The same through a property that reads the user and swallows what that raises itself."""

    class Meta:
        template_name = "sh/if_property.html"
        shared_render = True

    @property
    def staff(self) -> bool:
        try:
            return bool(self.user.is_staff)
        except Exception:
            return False


class ShFilterArg(_Headline, Component):
    class Meta:
        template_name = "sh/filter_arg.html"
        shared_render = True


class ShFirstOf(_Headline, Component):
    class Meta:
        template_name = "sh/firstof.html"
        shared_render = True


@function_component(name="sh_form", template="sh/form.html")
def _sh_form():
    return {"label": "send"}


@function_component(name="sh_label", template="sh/label.html")
def _sh_label():
    return {"label": "tag"}


@function_component(name="sh_holder", template="sh/holder.html")
def _sh_holder():
    return {}


@function_component(name="sh_inline")
def _sh_inline():
    return "<s>inline</s>"


@function_component(name="sh_request", template="sh/request_card.html")
def _sh_request():
    return {"request": None}


class _Shared(_Headline, Component, public=False):
    class Meta:
        subscriptions = {"sh-board"}
        shared_render = True

    async def notification(self, channel: str, **kwargs: t.Any) -> None:
        pass


class ShIncUser(_Shared):
    class Meta:
        template_name = "sh/inc_user.html"


class ShExtUser(_Shared):
    class Meta:
        template_name = "sh/ext_user.html"


class ShFuncCsrf(_Shared):
    class Meta:
        template_name = "sh/func_csrf.html"


class ShFuncBlockCsrf(_Shared):
    class Meta:
        template_name = "sh/func_block_csrf.html"


class ShFuncComponent(_Shared):
    class Meta:
        template_name = "sh/func_component.html"


class ShDeep(_Shared):
    class Meta:
        template_name = "sh/deep.html"


class ShIncPlain(_Shared):
    class Meta:
        template_name = "sh/inc_plain.html"


class ShFuncPlain(_Shared):
    class Meta:
        template_name = "sh/func_plain.html"


class ShCardTag(_Shared):
    class Meta:
        template_name = "sh/card_tag.html"


class ShIncVar(_Shared):
    class Meta:
        template_name = "sh/inc_var.html"

    part: str = "sh/user_part.html"


class ShExtVar(_Shared):
    class Meta:
        template_name = "sh/ext_var.html"

    base: str = "sh/user_base.html"


class ShFuncVar(_Shared):
    class Meta:
        template_name = "sh/func_var.html"

    kind: str = "sh_form"


class ShFuncUnknown(_Shared):
    class Meta:
        template_name = "sh/func_unknown.html"


class ShIncMissing(_Shared):
    class Meta:
        template_name = "sh/inc_missing.html"


class ShPartials(_Shared):
    class Meta:
        template_name = "sh/partials.html"


class ShPartsWhole(_Shared):
    class Meta:
        template_name = "sh/partials_whole.html"


class ShBlocktrans(_Shared):
    class Meta:
        template_name = "sh/blocktrans.html"


class ShWrap(_Shared):
    class Meta:
        template_name = "sh/wrap.html"


class ShWrapDirect(_Shared):
    class Meta:
        template_name = "sh/wrap_direct.html"


class ShAnonCards(_Shared):
    class Meta:
        template_name = "sh/anon_cards.html"


class ShSubCard(_Shared):
    class Meta:
        template_name = "sh/sub_card.html"


class ShDebug(_Shared):
    class Meta:
        template_name = "sh/debug.html"


class ShProxy(_Shared):
    class Meta:
        template_name = "sh/proxy.html"


class ShHistory(_Shared):
    class Meta:
        template_name = "sh/history.html"


class ShSlot(_Shared):
    class Meta:
        template_name = "sh/slot.html"


class ShBranches(_Shared):
    class Meta:
        template_name = "sh/branches.html"


class ShShow(_Shared):
    class Meta:
        template_name = "sh/show.html"


class ShVariable(_Shared):
    class Meta:
        template_name = "sh/variable.html"


class ShCond(_Shared):
    class Meta:
        template_name = "sh/cond.html"


class ShClass(_Shared):
    class Meta:
        template_name = "sh/class.html"


class ShMacro(_Shared):
    class Meta:
        template_name = "sh/macro.html"


class ShLater(_Shared):
    class Meta:
        template_name = "sh/later.html"


class ShFresh(_Shared):
    class Meta:
        template_name = "sh/fresh.html"


class ShComment(_Shared):
    class Meta:
        template_name = "sh/comment.html"


class ShCondBroken(_Shared):
    class Meta:
        template_name = "sh/cond_broken.html"


class ShLaterUnread(_Shared):
    class Meta:
        template_name = "sh/later_unread.html"


class ShRequestCard(_Shared):
    class Meta:
        template_name = "sh/request_card_tag.html"

    request: str = "a field"


class ShRequestFunc(_Shared):
    class Meta:
        template_name = "sh/request_func.html"

    request: str = "a field"


#: Template partials (``{% partialdef %}``) came with Django 6.0
partials = pytest.mark.skipif(django.VERSION < (6, 0), reason="template partials are Django 6.0's")
#: Before 6.0, django-template-partials' loader names a partial; from 6.0 the engine does, before any loader
package_partials = pytest.mark.skipif(django.VERSION >= (6, 0), reason="the package's loader shape is Django 5.2's")


@pytest.fixture(autouse=True)
def _templates():
    _reset_store()
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "loaders": [
                        # A locmem loader that also answers "file#name" as django-template-partials' does
                        ("testproj.template_partials.Loader", TEMPLATES),
                        ("django.template.loaders.locmem.Loader", NAMESAKES),
                    ],
                    "context_processors": [
                        "django.template.context_processors.request",
                        "django.contrib.auth.context_processors.auth",
                    ],
                    "libraries": {"sh_tags": "testproj.inclusion_tags", "sh_partials": "testproj.template_partials"},
                },
            }
        ]
    ):
        shared_render._SCOPE.clear()
        yield
    shared_render._SCOPE.clear()


def signed(cls: type[Component], *, id: str = "board", user=None, page_session=None, **fields: t.Any) -> str:
    return sign_state(
        cls(id=id, user=user or AnonymousUser(), wire=WireviewMeta(params={}, live_session=page_session), **fields)
    )


class Page:
    """One browser tab: a session with a component joined, and what it was sent."""

    def __init__(self, session: WireviewSession, outbound: RecordingOutbound, id: str) -> None:
        self.session = session
        self.outbound = outbound
        self.id = id

    @classmethod
    async def open(cls, component: type[Component] = ShBoard, *, user=None, id: str = "board", **fields) -> "Page":
        outbound = RecordingOutbound()
        session = WireviewSession(outbound, user=user or AnonymousUser(), channel_name=f"sh-{uuid4().hex[:8]}")
        await session.start(session=SessionView(), vsn=PROTOCOL_VERSION)
        await session.handle_message(
            {
                "command": "join",
                # Off the loop: a state that holds a QuerySet or a computed field queries
                "payload": {"name": component._name, "state": await sync_to_async(signed)(component, id=id, **fields)},
            }
        )
        assert outbound.renders(), f"{component._name} did not join"
        outbound.commands.clear()
        return cls(session, outbound, id)

    @property
    def component(self) -> Component:
        return self.session.repo.get(self.id)

    def frames(self) -> list[str]:
        return [json.dumps(payload, sort_keys=True) for command, payload in self.outbound.commands]

    def html(self) -> str:
        last = self.component.wire._last_rendered
        assert last is not None
        return last.to_html()


async def broadcast(pages: list[Page], message_id: str | None, **kwargs: t.Any) -> None:
    data: dict[str, t.Any] = {"channel": "sh-board", "kwargs": kwargs}
    if message_id is not None:
        data["message_id"] = message_id
    await asyncio.gather(*(page.session.notification(dict(data)) for page in pages))


# -- the option -------------------------------------------------------------------------------


@pytest.mark.unit
def test_the_option_is_off_unless_declared_and_inherited_by_subclasses():
    class ShOff(Component, public=False):
        class Meta:
            template_name = "sh/plain.html"

    class ShOnSub(ShBoard, public=False):
        pass

    assert ShOff._meta.shared_render is False
    assert ShBoard._meta.shared_render is True
    assert ShOnSub._meta.shared_render is True
    assert ShPlainBoard._meta.shared_render is False


# -- sharing ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_connections_handling_one_message_render_once():
    pages = [await Page.open() for _ in range(5)]
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert RENDERS["ShBoard"] == 1
    for page in pages:
        (render,) = page.outbound.renders()
        assert render["id"] == "board" and render["diff"] == {"1": "news"}
        assert "<h1>news</h1>" in page.html()


@pytest.mark.asyncio
async def test_a_message_without_an_id_renders_on_every_connection():
    """A message from a publisher before #176, or from another process's older code."""
    pages = [await Page.open() for _ in range(3)]
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, None)

    assert RENDERS["ShBoard"] == 3
    assert all(page.outbound.renders()[0]["diff"] == {"1": "news"} for page in pages)


@pytest.mark.asyncio
async def test_each_message_renders_again():
    pages = [await Page.open() for _ in range(3)]
    RENDERS.clear()

    STORE["headline"] = "first"
    await broadcast(pages, "m-1")
    STORE["headline"] = "second"
    await broadcast(pages, "m-2")

    assert RENDERS["ShBoard"] == 2
    assert all("<h1>second</h1>" in page.html() for page in pages)


@pytest.mark.asyncio
async def test_other_fields_render_apart():
    pages = [await Page.open(page=n % 2) for n in range(6)]
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert RENDERS["ShBoard"] == 2, "one render for page 0, one for page 1"
    for page in pages:
        shown = page.component.page  # type: ignore[attr-defined]
        assert f"<p>page {shown}</p>" in page.html()


@pytest.mark.asyncio
async def test_another_id_renders_apart():
    pages = [await Page.open(id="board-a"), await Page.open(id="board-a"), await Page.open(id="board-b")]
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert RENDERS["ShBoard"] == 2
    assert [page.outbound.renders()[0]["id"] for page in pages] == ["board-a", "board-a", "board-b"]


@pytest.mark.asyncio
async def test_another_language_renders_apart():
    pages = [await Page.open() for _ in range(4)]
    RENDERS.clear()

    async def handle(page: Page, language: str) -> None:
        with translation.override(language):
            await page.session.notification({"channel": "sh-board", "kwargs": {}, "message_id": "m-1"})

    await asyncio.gather(*(handle(page, ("en", "ko")[n % 2]) for n, page in enumerate(pages)))

    assert RENDERS["ShBoard"] == 2


@pytest.mark.asyncio
async def test_an_undeclared_class_renders_on_every_connection():
    pages = [await Page.open(ShPlainBoard) for _ in range(3)]
    RENDERS.clear()

    await broadcast(pages, "m-1")

    assert RENDERS["ShPlainBoard"] == 3


@pytest.mark.asyncio
async def test_a_connection_whose_page_shows_another_render_gets_its_own_diff():
    """Shared is the render, not the diff: each connection diffs against what its page shows."""
    ahead, behind = await Page.open(), await Page.open()
    STORE["headline"] = "one"
    await ahead.session.notification({"channel": "sh-board", "kwargs": {}, "message_id": "m-0"})
    ahead.outbound.commands.clear()
    RENDERS.clear()

    STORE["items"] = ["a", "b", "c", "d"]
    await broadcast([ahead, behind], "m-1")

    assert RENDERS["ShBoard"] == 1
    assert "1" not in ahead.outbound.renders()[0]["diff"], "the headline is already on that page"
    assert behind.outbound.renders()[0]["diff"]["1"] == "one"
    assert _without_state(ahead.html()) == _without_state(behind.html()), "each signs its own state"


@pytest.mark.asyncio
async def test_the_frames_are_the_bytes_an_unshared_render_sends(monkeypatch):
    """Each step of a run, shared on five connections and unshared on five more: the same frames.

    The clock is fixed so the state tokens, which carry a timestamp, are alike too.
    """
    monkeypatch.setattr("time.time", lambda: 1_800_000_000.0)
    shared = [await Page.open(page=n % 2) for n in range(5)]
    plain = [await Page.open(page=n % 2) for n in range(5)]
    monkeypatch.setattr(shared_render, "declared", lambda component: component.wire.channel_name in sharing)
    sharing = {page.session.channel_name for page in shared}

    steps: list[t.Callable[[], None]] = [
        lambda: STORE.update(headline="news"),
        lambda: STORE.update(items=["c", "a"]),
        lambda: None,
        lambda: STORE.update(items=[], headline="<b>&</b>"),
        lambda: STORE.update(items=["x"] * 3),
    ]
    for n, step in enumerate(steps):
        step()
        await broadcast(shared, f"m-{n}")
        await broadcast(plain, f"p-{n}")
        if n == 2:  # a user event in between: the next shared render diffs against it
            for page in (shared[0], plain[0]):
                await page.session.handle_message(
                    {
                        "command": "user_event",
                        "payload": {"id": "board", "command": "turn", "implicit_args": {}, "explicit_args": {"to": 7}},
                    }
                )

    for mine, theirs in zip(shared, plain, strict=True):
        assert mine.frames() == theirs.frames()
        assert mine.html() == theirs.html()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_each_connection_signs_its_own_state(monkeypatch):
    """A page boundary signs who is looking: the shared render holds every connection's own token."""
    saved = dict(live_session_module._REGISTRY)
    room = live_session("sh-room-tokens")
    try:
        users = [await get_user_model().objects.acreate(username=f"sh-{n}-{uuid4().hex[:6]}") for n in range(3)]
        pages = [await Page.open(user=user) for user in users]
        for page in pages:
            page.component.wire.live_session = room
            page.component.wire._state_token = None  # issued again, under the boundary
        RENDERS.clear()
        STORE["headline"] = "news"

        await broadcast(pages, "m-1")

        assert RENDERS["ShBoard"] == 1
        tokens = set()
        for page, user in zip(pages, users, strict=True):
            token = _state_token(page)
            tokens.add(token)
            envelope = unsign_envelope(token, "ShBoard")
            assert envelope.live_session == "sh-room-tokens"
            assert envelope.auth == live_session_module.auth_fingerprint(user, page.component.session)
            assert f'data-state="{token}"' in page.html()
        assert len(tokens) == 3
    finally:
        live_session_module._REGISTRY.clear()
        live_session_module._REGISTRY.update(saved)


def _without_state(html: str) -> str:
    return re.sub(r'data-state="[^"]*"', 'data-state=""', html)


def _state_token(page: Page) -> str:
    html = page.html()
    start = html.index('data-state="') + len('data-state="')
    return html[start : html.index('"', start)]


@pytest.mark.asyncio
async def test_a_refused_component_takes_no_part():
    """What a failed join left is not rendered; the others share as ever."""
    pages = [await Page.open() for _ in range(3)]
    refused = pages[0]
    refused.session.repo.join_failed("board", [])
    assert refused.session.repo.refused("board")
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert refused.outbound.renders() == []
    assert RENDERS["ShBoard"] == 1
    assert all(page.outbound.renders()[0]["diff"] == {"1": "news"} for page in pages[1:])


@pytest.mark.asyncio
async def test_when_the_first_render_fails_the_others_render_on_their_own():
    pages = [await Page.open() for _ in range(4)]
    RENDERS.clear()
    STORE.update(headline="news", raise_once="ShBoard")

    await broadcast(pages, "m-1")

    errors = [page for page in pages if any(command == "error" for command, _ in page.outbound.commands)]
    assert len(errors) == 1, "the connection whose render raised is the one that crashed"
    fine = [page for page in pages if page not in errors]
    assert all(page.outbound.renders()[0]["diff"] == {"1": "news"} for page in fine)
    assert RENDERS["ShBoard"] == 2, "the failed render and one more the others shared"


@pytest.mark.asyncio
async def test_the_store_forgets_a_message_after_a_while(monkeypatch):
    """By a timer: nothing is held once the traffic stops, without a next message to clean up."""
    monkeypatch.setattr(shared_render, "KEEP_SECONDS", 0.05)
    pages = [await Page.open(page=n) for n in range(3)]
    await broadcast(pages, "m-1")
    store = shared_render._store()
    assert list(store.messages) == ["m-1"] and store.renders == 3 and store.size > 0

    await eventually(lambda: not store.messages)
    assert store.renders == 0 and store.size == 0


@pytest.mark.asyncio
async def test_the_store_keeps_renders_within_a_size(monkeypatch):
    """Connections whose fields are all their own share nothing: what they leave is bounded."""
    pages = [await Page.open(page=n) for n in range(4)]
    store = shared_render._store()
    await broadcast(pages, "m-0")
    one = store.size // 4
    monkeypatch.setattr(shared_render, "KEEP_BYTES", one * 10)
    for n in range(1, 6):
        await broadcast(pages, f"m-{n}")
        assert store.size <= one * 10
    assert list(store.messages) == ["m-4", "m-5"], "the oldest go first"
    assert store.renders == 8

    monkeypatch.setattr(shared_render, "KEEP_RENDERS", 5)
    await broadcast(pages, "m-6")
    assert list(store.messages) == ["m-6"] and store.renders == 4


@pytest.mark.asyncio
async def test_a_message_dropped_while_its_render_is_under_way_is_not_counted(monkeypatch):
    pages = [await Page.open() for _ in range(2)]
    store = shared_render._store()
    monkeypatch.setattr(shared_render, "KEEP_SECONDS", 0)  # gone as soon as the loop turns
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert all(page.outbound.renders()[0]["diff"] == {"1": "news"} for page in pages)
    assert store.size == 0 and store.renders == 0 and not store.messages


@pytest.mark.unit
def test_a_shared_render_is_never_changed_by_a_connection():
    shared = shared_render.Shared.parse(
        f'<p id="x" data-state="<!--$0-->{shared_render.STATE_SLOT}<!--/$0-->"><!--$B1-->'
        f"<b><!--$2-->x<!--/$2--></b><!--/$B1--></p>"
    )
    assert shared is not None and shared.state_path == (0,)
    one = shared.with_state("token-1")
    two = shared.with_state("token&2")
    assert one.dynamic[0] == "token-1" and two.dynamic[0] == "token&amp;2"
    assert shared.rendered.dynamic[0] == shared_render.STATE_SLOT
    assert one.dynamic[1] is shared.rendered.dynamic[1], "what is not on the path to the token is shared"
    assert one.static is shared.rendered.static


@pytest.mark.unit
def test_a_slot_inside_a_block_is_found_and_one_in_a_loop_is_not_shared():
    inside = shared_render.Shared.parse(
        f'<!--$B0--><p data-state="<!--$1-->{shared_render.STATE_SLOT}<!--/$1-->"><!--$2-->y<!--/$2--></p><!--/$B0-->'
    )
    assert inside is not None and inside.state_path == (0, 0)
    assert isinstance(inside.with_state("t").dynamic[0], Rendered)
    looped = shared_render.Shared.parse(
        f"<!--$C0--><!--$I1--><p><!--$2-->{shared_render.STATE_SLOT}<!--/$2--></p><!--/$I1--><!--/$C0-->"
    )
    assert looped is None


# -- out of scope -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_temporary_assigns_put_a_class_out_of_scope(caplog):
    pages = [await Page.open(ShTemporary) for _ in range(3)]
    RENDERS.clear()

    await broadcast(pages, "m-1")

    assert all("<p" in page.html() and ">x</p>" in page.html() for page in pages)
    assert "ShTemporary declares Meta.shared_render, but its renders are not shared" in caplog.text


@pytest.mark.asyncio
async def test_a_template_that_draws_a_component_renders_on_every_connection():
    pages = [await Page.open(ShNested) for _ in range(3)]
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert RENDERS["ShNested"] == 3
    assert all("news" in page.html() for page in pages)


@pytest.mark.asyncio
async def test_a_template_that_signs_twice_renders_on_every_connection(caplog):
    """W019 cannot see two tag headers; the render can, and it keeps to its own."""
    caplog.set_level("WARNING", "wireview")
    pages = [await Page.open(ShTwice) for _ in range(3)]
    assert "its render draws another component or holds data-state more than once" in caplog.text
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    # The first join found out and rendered again; the class renders on its own from then on
    assert RENDERS["ShTwice"] == 3
    assert shared_render.STATE_SLOT not in "".join(page.html() for page in pages)
    assert all(page.html().count('data-state="') == 2 for page in pages)


@pytest.mark.unit
def test_the_system_check_names_every_reason():
    found = {message.obj: message for message in check_shared_render(None)}
    assert set(found) >= {ShTemporary, ShNested, ShChild, ShBounded, ShSlotted, ShUser, ShRequest, ShCsrf}
    assert {ShBoard, ShPlainBoard, ShViewer, ShMessages, ShTwice, ShInner}.isdisjoint(found)
    assert all(message.id == "wireview.W019" for message in found.values())
    assert "temporary_assigns" in found[ShTemporary].msg
    assert "component" in found[ShNested].msg
    assert "LiveComponent" in found[ShChild].msg
    assert "live_sessions" in found[ShBounded].msg
    assert "Meta.slots" in found[ShSlotted].msg
    assert "this.user" in found[ShUser].msg
    assert "request" in found[ShRequest].msg
    assert "csrf_token" in found[ShCsrf].msg


@pytest.mark.unit
def test_a_field_named_like_a_context_processor_is_not_reported():
    """``messages`` is a field of ShMessages: the template reads the field, not the request's."""
    assert all(message.obj is not ShMessages for message in check_shared_render(None))


# -- verification -----------------------------------------------------------------------------


@pytest.fixture
def verify(monkeypatch):
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("component", "name"),
    [(ShUserProperty, "user"), (ShSessionProperty, "session"), (ShAlias, "user")],
)
async def test_a_render_that_reads_the_viewer_raises(verify, component, name):
    """What the system check cannot see -- a property, a name for the component -- the render catches."""
    mounted = await wireview_testing.mount(component, id="board")
    with pytest.raises(shared_render.SharedRenderError, match=f"its render read '{name}'"):
        await mounted.render_diff()


@pytest.mark.asyncio
@pytest.mark.parametrize("component", [ShUser, ShRequest, ShCsrf, ShInclude, ShIfInclude])
async def test_a_template_that_reads_the_viewer_is_out_of_scope(verify, component):
    """What the system check sees keeps the class's renders its own: nothing to catch, nothing shared."""
    assert not shared_render.declared(component(id="board", user=AnonymousUser(), wire=WireviewMeta(params={})))
    mounted = await wireview_testing.mount(component, id="board")
    assert await mounted.render_diff() is not None


@pytest.mark.asyncio
async def test_a_field_named_like_the_request_is_read_as_the_field(verify):
    mounted = await wireview_testing.mount(ShMessages, id="board")
    diff = json.dumps(await mounted.render_diff())
    assert '["one"], ["two"]' in diff


@pytest.mark.asyncio
async def test_without_verification_the_viewer_is_read_as_ever(monkeypatch):
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=False)
    mounted = await wireview_testing.mount(ShUserProperty, id="board")
    assert "hi " in json.dumps(await mounted.render_diff())


@pytest.mark.asyncio
async def test_wireview_testing_verifies_unless_told_not_to():
    """pytest runs with DEBUG off; the test helpers watch all the same."""
    mounted = await wireview_testing.mount(ShUserProperty, id="board")
    with pytest.raises(shared_render.SharedRenderError):
        await mounted.render_diff()


@pytest.mark.asyncio
async def test_debug_turns_verification_on(settings, monkeypatch):
    settings.DEBUG = True
    assert shared_render.verifying()
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=False)
    assert not shared_render.verifying()


@pytest.mark.asyncio
async def test_a_render_taken_from_another_connection_that_differs_raises(verify, caplog):
    """ShViewer shows something of the connection through no watched name: two renders tell."""
    pages = [await Page.open(ShViewer) for _ in range(3)]
    for n, page in enumerate(pages):
        VIEWERS[page.session.channel_name] = f"viewer-{n}"  # type: ignore[index]
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    crashed = [page for page in pages if any(command == "error" for command, _ in page.outbound.commands)]
    assert len(crashed) == 2, "every connection that took the render found its own differ"
    assert "renders it otherwise than the connection whose render it would take" in caplog.text


@pytest.mark.asyncio
async def test_verification_passes_a_render_that_is_the_same(verify):
    pages = [await Page.open() for _ in range(3)]
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert RENDERS["ShBoard"] == 3, "the leader's render and each follower's own, compared"
    assert all(page.outbound.renders()[0]["diff"] == {"1": "news"} for page in pages)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_watched_fields_come_back_after_the_render(verify):
    user = await get_user_model().objects.acreate(username=f"sh-back-{uuid4().hex[:6]}")
    page = await Page.open(user=user)
    await broadcast([page], "m-1")
    assert page.component.user == user
    assert isinstance(page.component.session, SessionView)


# -- what keeps a render the connection's own -------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("component", [ShDoc, ShAnyDoc])
async def test_a_model_instance_is_never_shared(component):
    """The key could name the row's pk alone: one connection's unsaved edit went to the other's page."""
    mark = await Bookmark.objects.acreate(title="saved title", url="https://example.com/")
    try:
        a, b = await Page.open(component, doc=mark.pk), await Page.open(component, doc=mark.pk)
        a.component.doc = mark  # type: ignore[attr-defined]
        b.component.doc = await Bookmark.objects.aget(pk=mark.pk)  # type: ignore[attr-defined]
        a.component.doc.title = "A's unsaved draft"  # type: ignore[attr-defined]
        RENDERS.clear()

        await a.session.notification({"channel": "sh-board", "kwargs": {}, "message_id": "m-1"})
        await b.session.notification({"channel": "sh-board", "kwargs": {}, "message_id": "m-1"})

        assert RENDERS[component.__name__] == 2
        assert "unsaved draft" in a.html()
        assert "saved title" in b.html() and "unsaved draft" not in b.html()
    finally:
        await Bookmark.objects.filter(pk=mark.pk).adelete()


@pytest.mark.unit
def test_the_system_check_names_a_field_typed_as_a_row():
    found = {message.obj: message for message in check_shared_render(None)}
    assert "doc" in found[ShDoc].msg and "model instances or QuerySets" in found[ShDoc].msg
    assert ShAnyDoc not in found, "its type does not say so: the render finds out"


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        Bookmark(pk=1, title="x"),
        Bookmark.objects.none(),
        [1, {"a": Bookmark(pk=1)}],
        {"rows": (Bookmark(pk=1),)},
    ],
)
def test_a_value_that_holds_a_row_has_no_key(value):
    component = ShAnyDoc(id="board", user=AnonymousUser(), wire=WireviewMeta(params={}), doc=value)
    assert shared_render.key(component) is None


@pytest.mark.unit
@pytest.mark.parametrize(("one", "other"), [(1, True), ([1], (1,)), ({"1": 1}, {1: 1}), ("2026-10-06", None)])
def test_values_that_render_otherwise_have_other_keys(one, other):
    import datetime

    if other is None:
        other = datetime.date(2026, 10, 6)
    keys = [
        shared_render.key(ShAnyDoc(id="board", user=AnonymousUser(), wire=WireviewMeta(params={}), doc=value))
        for value in (one, other)
    ]
    assert None not in keys and keys[0] != keys[1]


@pytest.mark.unit
def test_the_extras_of_a_nested_model_are_in_the_key():
    """An extra="allow" model renders its extras ({{ prefs.theme }}): two viewers' extras must not share."""

    class Prefs(BaseModel):
        model_config = ConfigDict(extra="allow")
        name: str = "x"

    keys = [
        shared_render.key(
            ShAnyDoc(id="board", user=AnonymousUser(), wire=WireviewMeta(params={}), doc=Prefs(theme=theme))
        )
        for theme in ("dark", "light")
    ]
    assert None not in keys and keys[0] != keys[1]


@pytest.mark.asyncio
async def test_a_field_pydantic_excludes_is_in_the_key():
    """``Field(exclude=True)`` keeps a value out of model_dump_json, not out of the render."""
    a, b = await Page.open(ShHidden), await Page.open(ShHidden)
    a.component.viewer = "alice"  # type: ignore[attr-defined]
    b.component.viewer = "bob"  # type: ignore[attr-defined]
    RENDERS.clear()

    await broadcast([a, b], "m-1")

    assert RENDERS["ShHidden"] == 2
    assert "alice" in a.html() and "bob" in b.html()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("message_id", [None, "m-1"])
async def test_a_queryset_field_signs_off_the_loop(message_id):
    """Its state's JSON queries the ids: signed on the loop, every render raised SynchronousOnlyOperation."""
    await Bookmark.objects.acreate(title="one", url="https://example.com/")
    try:
        mounted = await wireview_testing.mount(ShMarks, id="board", marks=await sync_to_async(Bookmark.objects.all)())
        assert "one" in json.dumps(await mounted.render_diff())

        pages = [await Page.open(ShMarks) for _ in range(2)]
        for page in pages:
            page.component.marks = await sync_to_async(Bookmark.objects.all)()  # type: ignore[attr-defined]
        await broadcast(pages, message_id)
        for page in pages:
            assert not any(command == "error" for command, _ in page.outbound.commands)
            assert "one" in page.html()
            assert '"ids":' not in page.html()
    finally:
        await Bookmark.objects.all().adelete()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_connections_that_take_a_render_sign_off_the_loop():
    """A computed field in the state queries: the takers' tokens too are signed in a trip."""
    await Bookmark.objects.acreate(title="one", url="https://example.com/")
    try:
        pages = [await Page.open(ShCounted) for _ in range(3)]
        RENDERS.clear()

        await broadcast(pages, "m-1")

        assert RENDERS["ShCounted"] == 1
        for page in pages:
            assert not any(command == "error" for command, _ in page.outbound.commands)
            assert unsign_envelope(_state_token(page), "ShCounted").state["rows"] == 1
    finally:
        await Bookmark.objects.all().adelete()


@pytest.mark.asyncio
async def test_a_component_its_receiver_redirected_takes_no_render():
    """Frozen by its own receiver: nothing more is drawn, a taken render as little as its own."""
    leader, follower = await Page.open(ShLeaving), await Page.open(ShLeaving)
    REDIRECTED.clear()
    REDIRECTED.add(follower.session.channel_name)  # type: ignore[arg-type]
    STORE["headline"] = "news"
    try:
        await leader.session.notification({"channel": "sh-board", "kwargs": {}, "message_id": "m-1"})
        await follower.session.notification({"channel": "sh-board", "kwargs": {}, "message_id": "m-1"})
    finally:
        REDIRECTED.clear()

    assert leader.outbound.renders()[0]["diff"] == {"1": "news"}
    assert follower.outbound.renders() == []
    assert leader.component.wire.template_evaluated


@pytest.mark.asyncio
async def test_a_connection_that_takes_a_render_evaluated_the_template():
    pages = [await Page.open() for _ in range(2)]
    STORE["headline"] = "news"
    RENDERS.clear()

    await broadcast(pages, "m-1")

    assert RENDERS["ShBoard"] == 1
    assert all(page.component.wire.template_evaluated for page in pages)


@pytest.mark.asyncio
async def test_a_component_broadcast_is_shared(monkeypatch):
    """``self.broadcast()``, as the tutorials and Presence publish, names its message as abroadcast() does."""
    published: list[dict[str, t.Any]] = []

    class Broker:
        async def publish(self, channel: str, message: dict[str, t.Any]) -> None:
            published.append(message)

    pages = [await Page.open() for _ in range(3)]
    pages[0].component.wire.broker = Broker()  # type: ignore[assignment]
    await pages[0].component.broadcast("sh-board", n=1)
    await pages[0].component.broadcast("sh-board", n=2)
    (first, second) = published
    assert first["message_id"] != second["message_id"]
    assert first["kwargs"] == {"n": 1} and first["type"] == "notification" and first["channel"] == "sh-board"
    RENDERS.clear()
    STORE["headline"] = "news"

    await asyncio.gather(*(page.session.notification(dict(first)) for page in pages))

    assert RENDERS["ShBoard"] == 1


# -- what the checks see ----------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("component", "name"), [(ShIfAlias, "user"), (ShIfProperty, "user")])
async def test_a_read_the_template_swallows_raises_after_the_render(verify, component, name):
    """``{% if a and b %}`` turns any exception of an operand into False, and a property may catch its own:
    the read is written down first, and raises once the render is over."""
    mounted = await wireview_testing.mount(component, id="board")
    with pytest.raises(shared_render.SharedRenderError, match=f"its render read '{name}'"):
        await mounted.render_diff()


@pytest.mark.asyncio
async def test_a_swallowed_read_is_not_held_against_the_next_render(verify, monkeypatch):
    mounted = await wireview_testing.mount(ShIfAlias, id="board")
    with pytest.raises(shared_render.SharedRenderError):
        await mounted.render_diff()
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=False)
    assert await mounted.render_diff() is not None


@pytest.mark.unit
def test_the_system_check_reads_filter_arguments_and_tag_variables():
    found = {message.obj: message for message in check_shared_render(None)}
    assert "request" in found[ShFilterArg].msg
    assert "user" in found[ShFirstOf].msg
    assert "request" in found[ShIfInclude].msg, "an included template's condition as well"
    assert ShIfAlias not in found and ShAlias not in found, "another name for the component is the render's to catch"


@pytest.mark.asyncio
async def test_the_connections_that_take_a_render_sign_in_few_trips(monkeypatch):
    """A trip per taker cost more on the loop than the signature: those waiting together sign in one."""
    trips: list[int] = []
    sign_each = shared_render._sign_each

    def counted(components):
        trips.append(len(components))
        return sign_each(components)

    monkeypatch.setattr(shared_render, "_sign_each", counted)
    pages = [await Page.open() for _ in range(6)]
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert sum(trips) == 5, "every connection but the one that rendered"
    assert len(trips) < 5
    tokens = {_state_token(page) for page in pages}
    assert all(unsign_envelope(token, "ShBoard") for token in tokens)


@pytest.mark.asyncio
async def test_a_token_that_fails_to_sign_fails_its_own_connection(monkeypatch):
    from wireview.core import state

    pages = [await Page.open() for _ in range(3)]
    failing = pages[2].component
    sign = state.sign_state

    def sign_state(component):
        if component is failing:
            raise RuntimeError("this one cannot be signed")
        return sign(component)

    monkeypatch.setattr(state, "sign_state", sign_state)
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    errors = [page for page in pages if any(command == "error" for command, _ in page.outbound.commands)]
    assert errors == [pages[2]]
    assert all(page.outbound.renders()[0]["diff"] == {"1": "news"} for page in pages[:2])


# -- the templates a render reaches (#208) ----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("component", [ShIncUser, ShExtUser, ShDeep, ShCardTag])
async def test_a_viewer_read_in_another_template_reaches_no_other_viewer(monkeypatch, component):
    """Unverified, as in production: one connection's render of ``{{ user.username }}`` went to the other.

    The name is read in an included template, an extended parent, both a few levels down, or the
    template of an inclusion tag.
    """
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=False)
    model = get_user_model()
    ann = await model.objects.acreate(username=f"sh-ann-{uuid4().hex[:6]}")
    bob = await model.objects.acreate(username=f"sh-bob-{uuid4().hex[:6]}")
    a, b = await Page.open(component, user=ann), await Page.open(component, user=bob)
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast([a, b], "m-1")

    assert ann.username in a.html() and bob.username not in a.html()
    assert bob.username in b.html() and ann.username not in b.html()
    assert RENDERS[component.__name__] == 2, "each connection renders its own"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "component",
    [
        pytest.param(ShPartials, marks=partials),
        pytest.param(ShPartsWhole, marks=partials),
        ShBlocktrans,
        ShWrap,
        ShAnonCards,
        ShSubCard,
    ],
)
async def test_no_shape_of_template_reads_past_the_check(monkeypatch, component):
    """Shapes the first walk missed, each of which put one viewer's name on another's page.

    Two partials of one file (``parts.html#a``, then ``#b`` or the whole file) share their
    file's Origin; two templates made from strings share ``<unknown source>``; the body of
    ``{% blocktranslate %}`` holds tokens, not expressions; a block tag may keep its body
    under any name; an inclusion tag's node may be a subclass of Django's.
    """
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=False)
    assert shared_render.out_of_scope(component)
    model = get_user_model()
    ann = await model.objects.acreate(username=f"sh-ann-{uuid4().hex[:6]}")
    bob = await model.objects.acreate(username=f"sh-bob-{uuid4().hex[:6]}")
    a, b = await Page.open(component, user=ann), await Page.open(component, user=bob)
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast([a, b], "m-1")

    assert ann.username in a.html() and bob.username not in a.html()
    assert bob.username in b.html() and ann.username not in b.html()
    assert RENDERS[component.__name__] == 2, "each connection renders its own"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "component",
    [
        pytest.param(ShProxy, marks=package_partials),
        ShHistory,
        ShSlot,
        ShBranches,
        ShShow,
        ShVariable,
        ShCond,
        ShClass,
        ShMacro,
        ShLater,
        ShFresh,
    ],
)
async def test_no_tag_or_partial_keeps_what_it_reads_from_the_check(monkeypatch, component):
    """Shapes the walk of the node tree missed, each of which put one viewer's name on another's page.

    The check read what a tag left on its node, and guessed where: a body in an object of the
    tag's own or four containers down, an argument in a value object, were not found. It reads
    what the parser made instead. A partial on Django 5.2 is django-template-partials', of no class
    of Django's; a template that extends another finds its parent by the chain it came through.

    Then what the parser made missed (round 3): wireview's ``{% cond %}`` and ``{% class %}`` hold
    their expression as text and tell its names (``context_names``); a partial whose tag renders a
    macro parsed outside it; a body a tag takes as tokens, to compile at render or with a parser of
    its own.
    """
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=False)
    assert shared_render.out_of_scope(component)
    model = get_user_model()
    ann = await model.objects.acreate(username=f"sh-ann-{uuid4().hex[:6]}")
    bob = await model.objects.acreate(username=f"sh-bob-{uuid4().hex[:6]}")
    a, b = await Page.open(component, user=ann), await Page.open(component, user=bob)
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast([a, b], "m-1")

    assert ann.username in a.html() and bob.username not in a.html()
    assert bob.username in b.html() and ann.username not in b.html()
    assert RENDERS[component.__name__] == 2, "each connection renders its own"


class _Lookalike:
    """A partial of no class the check knows: its file's Origin, and a name of its own."""

    def __init__(self, file: str, name: str) -> None:
        from django.template import engines

        template = engines["django"].engine.get_template(file)  # type: ignore[attr-defined]
        self.origin = template.origin
        self.name = name
        self.nodelist = template.extra_data["template-partials"][name].nodelist


@pytest.mark.unit
def test_a_partial_is_told_by_its_name_whatever_its_class():
    """Two partials of one file share its Origin: each is read, and only the part of the file it renders."""
    reach = shared_render._Reach(ShIncPlain)
    reach.template(_Lookalike("sh/proxy_parts.html", "safe"), owned=True)
    assert reach.names == {}
    reach.template(_Lookalike("sh/proxy_parts.html", "viewer"), owned=True)
    assert reach.names == {"user": {"sh/proxy_parts.html#viewer"}}

    reach = shared_render._Reach(ShIncPlain)
    reach.template(_Lookalike("sh/page_parts.html", "row"), owned=True)
    assert reach.names == {}, "the page around the partial reads the user; the partial does not"
    reach.template(_Lookalike("sh/page_parts.html", "linked"), owned=True)
    assert reach.names == {"user": {"sh/page_parts.html#named"}}, "{% partial named %} is followed"


@pytest.mark.unit
def test_a_partial_that_two_libraries_define_is_read_as_its_whole_file():
    """Which of the two bodies the render takes is not known: the file is read, both bodies with it."""
    reach = shared_render._Reach(ShIncPlain)
    reach.template(_Lookalike("sh/alias_parts.html", "row"), owned=True)
    assert reach.names == {"user": {"sh/alias_parts.html#row"}}


@partials
@pytest.mark.unit
def test_a_partial_is_read_from_the_file_its_render_uses():
    """A cached loader keeps the file it read first; the check read the file as it is now (#208, round 3).

    Without the autoreloader -- ``runserver --noreload``, a deploy that changes the files before the
    restart -- the render drew the old partial, which read the user, and the check passed the new one.
    """

    class ShStale(_Shared):
        class Meta:
            template_name = "sh/stale.html"

    sources = {
        "sh/stale.html": '{% load wireview %}<p {% tag_header %}>{% include "sh/stale_parts.html#row" %}</p>',
        "sh/stale_parts.html": "{% partialdef row %}<b>{{ user.username }}</b>{% endpartialdef %}",
    }
    cached = [("django.template.loaders.cached.Loader", [("django.template.loaders.locmem.Loader", sources)])]
    with override_settings(
        TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates", "OPTIONS": {"loaders": cached}}]
    ):
        from django.template.loader import get_template

        get_template("sh/stale_parts.html#row")  # what the render draws from now on
        sources["sh/stale_parts.html"] = "{% partialdef row %}<b>safe</b>{% endpartialdef %}"
        reasons = shared_render.out_of_scope(ShStale)
    assert any("reads user (through sh/stale_parts.html#row)" in reason for reason in reasons), reasons


@pytest.mark.unit
def test_a_template_whose_source_is_not_its_tree_keeps_the_render_its_own(monkeypatch):
    """Parsed again, a source left out made no node: the tree the render draws was not read."""
    from django.template import engines, loader

    template = engines["django"].get_template("sh/inc_user.html")
    template.template.source = ""  # type: ignore[attr-defined]
    monkeypatch.setattr(loader, "get_template", lambda name, using=None: template)
    reasons = shared_render.out_of_scope(ShIncUser)
    assert any("could not be read through (LookupError" in reason for reason in reasons), reasons


@pytest.mark.unit
def test_the_system_check_names_where_those_shapes_read_the_viewer():
    found = {message.obj: message.msg for message in check_shared_render(None)}
    if django.VERSION >= (6, 0):
        assert "reads user (through sh/parts.html#viewer)" in found[ShPartials]
        assert "reads user (through sh/parts_whole.html)" in found[ShPartsWhole]
    assert "reads user" in found[ShBlocktrans]
    assert "reads user (through sh/user_part.html)" in found[ShWrap]
    assert "reads user" in found[ShWrapDirect]
    assert "reads user (through <unknown source>)" in found[ShAnonCards]
    assert "reads user (through sh/card.html)" in found[ShSubCard]
    assert "prints the whole context" in found[ShDebug] and "{% debug %}" in found[ShDebug]
    if django.VERSION < (6, 0):
        assert "reads user (through sh/proxy_parts.html#viewer)" in found[ShProxy]
    assert "reads user (through sh/hn.html)" in found[ShHistory]
    assert "reads user (through sh/user_part.html)" in found[ShSlot]
    for cls in (ShBranches, ShShow, ShVariable, ShCond, ShClass, ShLater, ShFresh):
        assert "its template reads user," in found[cls], cls
    assert "reads user (through sh/macro_parts.html#row)" in found[ShMacro]
    assert ShComment not in found, "a {% comment %} is not read"
    assert "holds an expression this check cannot read ({% cond {user: } %})" in found[ShCondBroken]
    assert "holds an expression this check cannot read ({{ a b }})" in found[ShLaterUnread]


@pytest.mark.unit
@pytest.mark.parametrize("component", [ShRequestCard, ShRequestFunc])
def test_another_tag_s_template_reads_the_viewer_s_name_although_the_component_has_that_field(component):
    """``request`` is a field, so the component's template reads the field; an inclusion tag's or a
    function's template is rendered with a context of its own, where ``request`` is never the field."""
    reasons = shared_render.out_of_scope(component)
    assert any("reads request (through sh/request_card.html)" in reason for reason in reasons), reasons


@pytest.mark.unit
def test_a_walk_that_fails_keeps_the_render_its_own(monkeypatch):
    """What could not be read may read the viewer: the class does not share on a guess."""

    def fails(*args: t.Any, **kwargs: t.Any) -> None:
        raise RuntimeError("a node that cannot be read")

    monkeypatch.setattr(shared_render._Reach, "node", fails)
    assert any("could not be read through (RuntimeError" in reason for reason in shared_render.out_of_scope(ShIncPlain))
    assert not shared_render.declared(ShIncPlain(id="board", user=AnonymousUser(), wire=WireviewMeta(params={})))


@pytest.mark.unit
def test_a_template_that_is_not_django_s_keeps_the_render_its_own(monkeypatch):
    from django.template import loader

    class Elsewhere:
        """A template of another backend: no node tree to read."""

        template = object()

    monkeypatch.setattr(loader, "get_template", lambda name, using=None: Elsewhere())
    assert shared_render.out_of_scope(ShIncPlain) == [
        "its template is not a Django template, which this check cannot read"
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "error",
    [
        TemplateDoesNotExist("sh/later.html"),
        TemplateSyntaxError("broken"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
    ],
)
def test_a_template_that_fails_to_load_keeps_the_render_its_own(monkeypatch, error):
    """Its render fails as well. Kept in scope, a template that loads later -- a database loader's,
    a fixed file without a reloader -- would share unread; any other error would break the check."""
    from django.template import loader

    def load(name: str, using: str | None = None) -> None:
        raise error

    monkeypatch.setattr(loader, "get_template", load)
    component = ShIncPlain(id="board", user=AnonymousUser(), wire=WireviewMeta(params={}))
    assert not shared_render.declared(component)
    found = {message.obj: message.msg for message in check_shared_render(None)}
    assert f"its template could not be loaded ({type(error).__name__}" in found[ShIncPlain]


@pytest.mark.asyncio
@pytest.mark.parametrize("component", [ShFuncCsrf, ShFuncBlockCsrf, ShFuncComponent])
async def test_a_function_component_s_template_is_read_as_well(monkeypatch, component):
    """A function's template gets the page's ``csrf_token`` and draws into the page's repository."""
    set_wireview(monkeypatch, VERIFY_SHARED_RENDER=False)
    assert not shared_render.declared(component(id="board", user=AnonymousUser(), wire=WireviewMeta(params={})))
    pages = [await Page.open(component) for _ in range(2)]
    RENDERS.clear()
    STORE["headline"] = "news"

    await broadcast(pages, "m-1")

    assert RENDERS[component.__name__] == 2


@pytest.mark.unit
def test_the_system_check_follows_includes_parents_and_function_components():
    found = {message.obj: message.msg for message in check_shared_render(None)}
    assert "reads user (through sh/user_part.html)" in found[ShIncUser]
    assert "reads user (through sh/user_base.html)" in found[ShExtUser]
    assert "reads user (through sh/user_part.html)" in found[ShDeep]
    assert "csrf_token (through sh/form.html)" in found[ShFuncCsrf]
    assert "csrf_token (through sh/form.html)" in found[ShFuncBlockCsrf]
    assert "draws another component" in found[ShFuncComponent] and "sh/holder.html" in found[ShFuncComponent]
    assert "reads request (through sh/partial.html)" in found[ShInclude]
    assert "reads user (through sh/card.html)" in found[ShCardTag]


@pytest.mark.unit
def test_a_template_the_check_cannot_name_keeps_the_render_its_own():
    """A name only the render knows -- a variable, a function not registered, a missing file -- fails closed."""
    found = {message.obj: message.msg for message in check_shared_render(None)}
    assert "{% include part %}" in found[ShIncVar]
    assert "{% extends base %}" in found[ShExtVar]
    assert "{% func kind %}" in found[ShFuncVar]
    assert '{% func "sh_nowhere" %}' in found[ShFuncUnknown]
    assert '{% include "sh/nowhere.html" %}' in found[ShIncMissing]
    for cls in (ShIncVar, ShExtVar, ShFuncVar, ShFuncUnknown, ShIncMissing):
        assert "which this check cannot follow" in found[cls]
        assert not shared_render.declared(cls(id="board", user=AnonymousUser(), wire=WireviewMeta(params={})))


@pytest.mark.unit
def test_what_the_other_templates_read_of_their_own_is_in_scope():
    """An include of itself ends, and a function's template that reads nothing of the viewer shares."""
    found = {message.obj for message in check_shared_render(None)}
    assert ShIncPlain not in found
    assert ShFuncPlain not in found


@pytest.mark.unit
@pytest.mark.parametrize("loader", ["locmem", "cached", "filesystem", "cached-filesystem"])
def test_the_scope_is_the_same_under_every_loader(loader, tmp_path):
    for name, source in TEMPLATES.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(source)
    locmem = ("django.template.loaders.locmem.Loader", TEMPLATES)
    filesystem = ("django.template.loaders.filesystem.Loader", [str(tmp_path)])
    loaders = {
        "locmem": [locmem],
        "cached": [("django.template.loaders.cached.Loader", [locmem])],
        "filesystem": [filesystem],
        "cached-filesystem": [("django.template.loaders.cached.Loader", [filesystem])],
    }[loader]
    classes = [ShIncUser, ShExtUser, ShDeep, ShFuncCsrf, ShFuncComponent, ShCardTag, ShIncVar, ShIncPlain, ShBoard]
    expected = {cls: shared_render.out_of_scope(cls) for cls in classes}
    assert all(expected[cls] for cls in classes[:-2]) and not expected[ShIncPlain] and not expected[ShBoard]
    options = {"loaders": loaders, "libraries": {"sh_tags": "testproj.inclusion_tags"}}
    with override_settings(
        TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates", "OPTIONS": options}]
    ):
        assert {cls: shared_render.out_of_scope(cls) for cls in classes} == expected


@pytest.mark.unit
def test_a_template_that_extends_its_namesake_is_followed_to_the_next_loader():
    """``{% extends "x" %}`` in x skips the loaders it was found in, as Django's render does."""

    class ShNamesake(_Shared):
        class Meta:
            template_name = "sh/namesake.html"

    first = {"sh/namesake.html": '{% extends "sh/namesake.html" %}{% block b %}{{ headline }}{% endblock %}'}
    second = {"sh/namesake.html": "{% load wireview %}<p {% tag_header %}>{% block b %}{% endblock %}{{ user }}</p>"}
    with override_settings(
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "OPTIONS": {
                    "loaders": [
                        ("django.template.loaders.locmem.Loader", first),
                        ("django.template.loaders.locmem.Loader", second),
                    ]
                },
            }
        ]
    ):
        assert any("reads user" in reason for reason in shared_render.out_of_scope(ShNamesake))


@pytest.mark.unit
def test_a_template_change_under_the_dev_server_reads_the_scope_again(tmp_path):
    """The scope was kept for the process: an include edited to read the user went on sharing."""
    from pathlib import Path

    from django.utils.autoreload import file_changed

    class ShEdited(_Shared):
        class Meta:
            template_name = "sh/inc_plain.html"

    def templates(part: str):
        sources = {**TEMPLATES, "sh/plain_part.html": part}
        return override_settings(
            TEMPLATES=[
                {
                    "BACKEND": "django.template.backends.django.DjangoTemplates",
                    "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", sources)]},
                }
            ]
        )

    component = ShEdited(id="board", user=AnonymousUser(), wire=WireviewMeta(params={}))
    with templates("<i>{{ headline }}</i>"):
        assert shared_render.declared(component)
    with templates("<i>{{ user.username }}</i>"):
        assert shared_render.declared(component), "kept until the reloader says a file changed"
        file_changed.send(sender=None, file_path=Path(tmp_path / "plain_part.html"))
        assert not shared_render.declared(component)
