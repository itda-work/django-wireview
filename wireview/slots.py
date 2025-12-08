"""Slot system for component content composition.

Phoenix LiveView 스타일의 슬롯 시스템으로, 컴포넌트에 콘텐츠를 유연하게 주입할 수
있습니다. 재사용 가능한 레이아웃 컴포넌트를 만들 때 유용합니다.

Quick Start
===========

1. 컴포넌트 클래스 정의 (components.py):

    from wireview.component import Component

    class Card(Component):
        _template_name = "components/card.html"
        _slots = {
            "header": {"required": False, "doc": "카드 헤더 영역"},
            "footer": {"required": False, "doc": "카드 푸터 영역"},
        }
        title: str = ""

2. 컴포넌트 템플릿 정의 (card.html):

    {% load wireview %}
    <div {% tag_header %} class="card">
        {% if slots.header %}
            <header class="card-header">
                {% render_slot "header" %}
            </header>
        {% elif title %}
            <header class="card-header">
                <h3>{{ title }}</h3>
            </header>
        {% endif %}

        <div class="card-body">
            {% render_slot %}  {# default slot #}
        </div>

        {% if slots.footer %}
            <footer class="card-footer">
                {% render_slot "footer" %}
            </footer>
        {% endif %}
    </div>

3. 컴포넌트 사용 (page.html):

    {% load wireview %}
    {% component_block "Card" %}
        {% fill header %}
            <h1>{{ page_title }}</h1>
        {% endfill %}

        <p>카드 본문 내용입니다.</p>

        {% fill footer %}
            <button>저장</button>
        {% endfill %}
    {% endcomponent %}


Template Tags Reference
=======================

{% component_block "ComponentName" attr=value %}...{% endcomponent %}
    슬롯을 지원하는 블록 형태의 컴포넌트 태그.

{% fill slotname %}...{% endfill %}
    부모 컴포넌트의 슬롯에 콘텐츠를 전달.

{% fill slotname let:var1 let:var2 %}...{% endfill %}
    컴포넌트에서 전달한 변수를 바인딩하여 사용.

{% render_slot %}
    기본(default) 슬롯 렌더링.

{% render_slot "name" %}
    명명된 슬롯 렌더링.

{% render_slot "name" var1=value1 var2=value2 %}
    변수 바인딩과 함께 슬롯 렌더링 (let: 문법과 함께 사용).


let: Variable Binding
=====================

컴포넌트에서 슬롯 렌더링 시 변수를 전달할 수 있습니다:

컴포넌트 템플릿 (list.html):

    {% for item in items %}
        <li>{% render_slot "item" item=item index=forloop.counter %}</li>
    {% endfor %}

사용 측:

    {% component_block "List" items=products %}
        {% fill item let:item let:index %}
            <span>{{ index }}. {{ item.name }} - {{ item.price }}원</span>
        {% endfill %}
    {% endcomponent %}


Required Slots
==============

_slots 딕셔너리에 required: True를 설정하면 해당 슬롯이 필수가 됩니다:

    class Modal(Component):
        _slots = {
            "title": {"required": True, "doc": "모달 제목 - 필수"},
            "body": {"required": False},
        }

필수 슬롯이 누락되면 TemplateSyntaxError가 발생합니다.


Context Access
==============

- 슬롯 콘텐츠는 부모 템플릿의 컨텍스트에 접근할 수 있습니다.
- let: 바인딩이 있는 슬롯은 컴포넌트에서 전달한 변수에 접근할 수 있습니다.
- 슬롯에서 컴포넌트의 속성(예: this.title)에 직접 접근할 수는 없습니다.


Slot Container API
==================

컴포넌트 템플릿에서 slots 객체 사용:

    {% if slots %}                    {# 슬롯이 하나라도 있는지 확인 #}
    {% if slots.header %}             {# 특정 슬롯이 있는지 확인 #}
    {% render_slot "header" %}        {# 슬롯 렌더링 #}
    {% render_slot %}                 {# 기본 슬롯 렌더링 #}


Backwards Compatibility
=======================

기존 {% component 'Name' attr=value %} 심플 태그는 그대로 동작합니다.
슬롯이 필요 없는 컴포넌트는 기존 방식을 사용하세요.
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass, field

from django.template.base import NodeList
from django.template.context import Context
from django.utils.safestring import SafeString, mark_safe

__all__ = ("Slot", "SlotContainer")


@dataclass
class Slot:
    """
    Represents a named slot with its content.

    Attributes:
        name: Slot identifier (e.g., "header", "footer")
        nodelist: Django template NodeList containing slot content
        let_vars: Variable names to bind from render_slot's extra_context
    """

    name: str
    nodelist: NodeList
    let_vars: list[str] = field(default_factory=list)

    def render(
        self,
        context: Context,
        extra_context: dict[str, t.Any] | None = None,
    ) -> str:
        """
        Render the slot content with the given context.

        Args:
            context: Parent template context
            extra_context: Additional variables from render_slot (for let: binding)

        Returns:
            Rendered HTML string
        """
        # Build context with let: bindings
        bound_vars: dict[str, t.Any] = {}
        if extra_context and self.let_vars:
            for var_name in self.let_vars:
                if var_name in extra_context:
                    bound_vars[var_name] = extra_context[var_name]

        # Push a new context layer with bound variables
        with context.push(**bound_vars):
            return self.nodelist.render(context)


class _SlotAccessor:
    """
    Helper class to enable {% if slots.header %} truthiness checks.

    Also supports iteration over multiple slots with the same name.
    """

    __slots__ = ("_slots",)

    def __init__(self, slots: list[Slot]):
        self._slots = slots

    def __bool__(self) -> bool:
        return len(self._slots) > 0

    def __iter__(self) -> t.Iterator[Slot]:
        return iter(self._slots)

    def __len__(self) -> int:
        return len(self._slots)

    def __repr__(self) -> str:
        return f"_SlotAccessor({self._slots!r})"


@dataclass
class SlotContainer:
    """
    Container for all slots passed to a component.

    Provides convenient access patterns:
    - slots.header -> _SlotAccessor for truthiness and iteration
    - slots["header"] -> list[Slot]
    - "header" in slots -> bool
    - {% if slots.header %} -> bool
    - {% if slots %} -> bool (has any slots or default content)
    """

    _slots: dict[str, list[Slot]] = field(default_factory=dict)
    _default: NodeList | None = None

    def add(self, slot: Slot) -> None:
        """Add a slot to the container."""
        if slot.name not in self._slots:
            self._slots[slot.name] = []
        self._slots[slot.name].append(slot)

    def set_default(self, nodelist: NodeList) -> None:
        """Set the default (unnamed) slot content."""
        self._default = nodelist

    def get(self, name: str) -> list[Slot]:
        """Get slots by name."""
        return self._slots.get(name, [])

    def get_default(self) -> NodeList | None:
        """Get the default slot content."""
        return self._default

    def has(self, name: str) -> bool:
        """Check if a named slot exists and has content."""
        return name in self._slots and len(self._slots[name]) > 0

    def has_default(self) -> bool:
        """Check if default slot has content."""
        return self._default is not None and len(self._default) > 0

    def render_slot(
        self,
        context: Context,
        name: str = "",
        extra_context: dict[str, t.Any] | None = None,
    ) -> SafeString:
        """
        Render a slot by name.

        Args:
            context: Template context
            name: Slot name (empty string for default slot)
            extra_context: Additional variables to pass to slot

        Returns:
            Rendered HTML as SafeString
        """
        if not name:
            # Render default slot
            if self._default is not None:
                with context.push(**(extra_context or {})):
                    return mark_safe(self._default.render(context))
            return mark_safe("")

        # Render named slot(s)
        slot_list = self._slots.get(name, [])
        if not slot_list:
            return mark_safe("")

        # Render all slots with this name (usually just one)
        rendered_parts = []
        for slot in slot_list:
            rendered = slot.render(context, extra_context)
            rendered_parts.append(rendered)

        return mark_safe("".join(rendered_parts))

    def __getattr__(self, name: str) -> _SlotAccessor:
        """Enable slots.header syntax with truthiness check."""
        if name.startswith("_"):
            raise AttributeError(name)
        return _SlotAccessor(self._slots.get(name, []))

    def __contains__(self, name: str) -> bool:
        """Enable 'header' in slots."""
        return self.has(name)

    def __bool__(self) -> bool:
        """Enable {% if slots %}."""
        return bool(self._slots) or self.has_default()

    def __repr__(self) -> str:
        slot_names = list(self._slots.keys())
        has_default = self._default is not None
        return f"SlotContainer(slots={slot_names}, has_default={has_default})"
