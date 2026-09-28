"""Django model instances in component state: stored as their primary keys.

A component's state is signed into the page as JSON, and a model instance is not
JSON. A field typed as a model was converted to its pk and loaded back, but only
at the top: ``list[Book]``, ``Book`` inside a ``dict``, or the result of an
``AsyncResult`` failed to sign, so the component could not render (#113).

``dump`` walks a value and replaces every instance with its pk (and a QuerySet
with its ids). ``load`` walks the field's annotation to turn those pks back into
instances: one query per model field, and one per list, which keeps its order.
What is stored is the row's identity, so a rejoin reads the rows as they are
now; a row deleted meanwhile drops out of a list and leaves a single field None.
"""

from __future__ import annotations

import types
import typing as t
import uuid
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet

from django.apps import apps
from django.db import models

__all__ = ("dump", "has_models", "has_queryset_marker", "load", "mentions_models")

_UNIONS = (t.Union, types.UnionType)


def _is_model(annotation: t.Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, models.Model)


def _is_pk(value: t.Any) -> bool:
    """What a signed state holds for a row. Anything else -- an instance, the request's
    user object handed to the constructor -- is already what the field wants."""
    return isinstance(value, (int, str, uuid.UUID)) and not isinstance(value, bool)


def has_queryset_marker(value: t.Any) -> bool:
    return isinstance(value, dict) and {"app", "model", "ids"} <= value.keys()


def _async_result() -> type:
    from ..async_result import AsyncResult

    return AsyncResult


def has_models(value: t.Any) -> bool:
    """Whether ``value`` holds a model instance or a QuerySet anywhere."""
    if isinstance(value, (models.Model, models.QuerySet)):
        return True
    if isinstance(value, _async_result()):
        return has_models(value.result)
    if isinstance(value, Mapping):
        return any(has_models(item) for item in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(has_models(item) for item in value)
    return False


def dump(value: t.Any) -> t.Any:
    """``value`` with every model instance as its pk and every QuerySet as its ids."""
    if isinstance(value, models.Model):
        return value.pk
    if isinstance(value, models.QuerySet):
        return {
            "app": value.model._meta.app_label,
            "model": value.model._meta.model_name,
            "ids": list(value.values_list("pk", flat=True)),
        }
    if isinstance(value, _async_result()):
        return {"state": value.state.value, "result": dump(value.result), "error_message": value.error_message}
    if isinstance(value, Mapping):
        return {key: dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [dump(item) for item in value]
    return value


def mentions_models(annotation: t.Any) -> bool:
    """Whether ``annotation`` names a model anywhere inside it."""
    if _is_model(annotation):
        return True
    return any(mentions_models(arg) for arg in t.get_args(annotation) if arg is not Ellipsis)


def load(annotation: t.Any, value: t.Any) -> t.Any:
    """Turn the pks in ``value`` back into instances, as ``annotation`` says where they are."""
    if value is None:
        return None
    if has_queryset_marker(value):
        model = apps.get_model(value["app"], value["model"])
        return model.objects.filter(pk__in=value.get("ids", []))
    if _is_model(annotation):
        if not _is_pk(value) or annotation._meta.abstract:
            return value
        return annotation.objects.filter(pk=value).first()

    origin, args = t.get_origin(annotation), t.get_args(annotation)
    if origin is t.Annotated:
        return load(args[0], value)
    if origin in _UNIONS:
        bearing = [arg for arg in args if mentions_models(arg)]
        return load(bearing[0], value) if len(bearing) == 1 else value
    if origin is _async_result() and args and isinstance(value, Mapping):
        return {**value, "result": load(args[0], value.get("result"))}
    if isinstance(origin, type) and issubclass(origin, Mapping) and len(args) == 2 and isinstance(value, Mapping):
        return {key: load(args[1], item) for key, item in value.items()}
    if (
        isinstance(origin, type)
        and issubclass(origin, (Sequence, AbstractSet))
        and not issubclass(origin, (str, bytes))
        and args
        and isinstance(value, (list, tuple, set, frozenset))
    ):
        item_type = args[0]
        items = _load_rows(item_type, value) if _is_model(item_type) else [load(item_type, item) for item in value]
        return origin(items) if origin in (tuple, set, frozenset) else items
    return value


def _load_rows(model: type[models.Model], pks: t.Iterable[t.Any]) -> list[models.Model]:
    """The rows for ``pks`` in one query, in their order; a pk with no row is dropped."""
    pks = list(pks)
    wanted = [pk for pk in pks if _is_pk(pk)]
    if not wanted or model._meta.abstract:
        return pks
    # filter, not in_bulk: on Django 6.0 with SQLite, in_bulk asks an unopened
    # connection for its parameter limit and raises, and a join is often the first
    # query after channels closed the thread's connection
    rows = {str(row.pk): row for row in model.objects.filter(pk__in=wanted)}
    loaded = []
    for pk in pks:
        if pk is None:
            continue
        if not _is_pk(pk):
            loaded.append(pk)
        elif (row := rows.get(str(pk))) is not None:
            loaded.append(row)
    return loaded
