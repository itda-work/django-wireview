"""Django model signals -> ``model_mutation`` messages on the channel layer.

Only the models named in ``AUTO_BROADCAST.senders`` are broadcast. An empty
``senders`` connects nothing, whatever flags are on (``wireview.W015`` reports
it). ``connect()`` runs once from ``WireviewConfig.ready()``; tests call it again
with another ``AutoBroadcast`` to change what is connected.

A set of senders sends every field of the instance. A mapping names, per model,
the fields that go (#144): the three signals encode through ``_encode``, and the
receiving side loads only those, leaving the rest deferred (``serializer.decode``).
"""

import logging
import typing as t

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.signals import m2m_changed, post_save, pre_delete
from django.dispatch import Signal

from . import serializer
from .schemas import AutoBroadcast, ModelAction
from .utils import send_to

__all__ = []

log = logging.getLogger("wireview")

#: The configuration ``connect()`` last installed. The receivers read their flags from it.
_config: AutoBroadcast = AutoBroadcast()
#: The models named in ``_config.senders``.
_senders: frozenset[type[models.Model]] = frozenset()
#: The field names each sender's payload carries, or None for every field.
_fields: dict[type[models.Model], tuple[str, ...] | None] = {}
#: ``(signal, sender, dispatch_uid)`` for every receiver ``connect()`` attached.
_connections: list[tuple[Signal, type[models.Model], str]] = []
#: Set on an object while its last save was raw. ``loaddata`` sets a row's m2m on the
#: object it has just saved raw, and ``m2m_changed`` carries no ``raw`` of its own.
_SAVED_RAW = "_wireview_saved_raw"


def resolve_senders(config: AutoBroadcast) -> dict[type[models.Model], tuple[str, ...] | None]:
    """Each model ``config.senders`` names, in a stable order, and the fields its payload carries.

    None stands for every field. The names come back as the serializer selects them: a foreign key by its field
    name, whether it was written as ``product`` or ``product_id``, and once if it was written both ways.
    """
    resolved: dict[type[models.Model], tuple[str, ...] | None] = {}
    keys: dict[type[models.Model], tuple[str, str]] = {}
    for app_label, model_name in sorted(config.senders):
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError as e:
            raise ImproperlyConfigured(
                f"WIREVIEW['AUTO_BROADCAST'].senders names ({app_label!r}, {model_name!r}), "
                f"which is not an installed model: {e}"
            ) from e
        fields = config._fields_for((app_label, model_name))
        sent = None if fields is None else tuple(dict.fromkeys(_sent_field_name(model, name) for name in fields))
        if model in resolved and _field_set(resolved[model]) != _field_set(sent):
            # get_model ignores the case of a model name, so two keys can be one model. Letting either list win
            # could send every field where the other asked for a few.
            raise ImproperlyConfigured(
                f"WIREVIEW['AUTO_BROADCAST'].senders names {model._meta.label_lower} twice, as {keys[model]!r} "
                f"and {(app_label, model_name)!r}, with different fields. Name it once."
            )
        resolved[model], keys[model] = sent, (app_label, model_name)
    return resolved


def _field_set(fields: tuple[str, ...] | None) -> frozenset[str] | None:
    return None if fields is None else frozenset(fields)


def sendable_fields(model: type[models.Model]) -> list[models.Field]:
    """The fields Django's serializer puts in ``model``'s payload besides the pk.

    The model's own concrete fields and forward many-to-many fields, not the pk
    (it is always sent beside them), reverse relations, the fields of a
    multi-table parent or a field with ``serialize=False``.
    """
    opts = (model._meta.concrete_model or model)._meta
    return [f for f in (*opts.local_fields, *opts.local_many_to_many) if getattr(f, "serialize", False)]


def _sent_field_name(model: type[models.Model], name: str) -> str:
    """The serializer's name for the field ``name`` of ``model``; refuses one the payload cannot carry."""
    for field in sendable_fields(model):
        if name in (field.name, field.attname):
            return field.name
    label = model._meta.label_lower
    if name in (model._meta.pk.name, model._meta.pk.attname):
        reason = "the primary key, which every payload carries; leave it out"
    else:
        reason = (
            "not a field the payload can carry (a reverse relation, an inherited field, "
            "a field with serialize=False or no field at all)"
        )
    raise ImproperlyConfigured(f"WIREVIEW['AUTO_BROADCAST'].senders lists {name!r} for {label}, which is {reason}.")


def _encode(sender: type[models.Model], instance: models.Model) -> str:
    """``instance`` as the payload of a ``model_mutation``, with the fields ``senders`` gives ``sender``."""
    return serializer.encode(instance, fields=_fields.get(sender))


def _m2m_throughs(senders: t.Iterable[type[models.Model]]) -> list[type[models.Model]]:
    """The through models of every many-to-many relation that touches a sender, either side."""
    throughs: list[type[models.Model]] = []
    for model in senders:
        for field in model._meta.get_fields():
            if not field.many_to_many:
                continue
            through = getattr(field, "through", None) or getattr(field.remote_field, "through", None)
            if isinstance(through, type) and through not in throughs:
                throughs.append(through)
    return throughs


def connect(config: AutoBroadcast | None = None) -> None:
    """Connect the receivers ``config`` asks for, replacing any connected before.

    ``config`` defaults to ``WIREVIEW['AUTO_BROADCAST']``, read now.
    """
    global _config, _senders, _fields
    from . import settings

    disconnect()
    config = settings.AUTO_BROADCAST if config is None else config
    fields = resolve_senders(config)
    senders = list(fields)
    _config = config
    _senders = frozenset(senders)
    _fields = fields
    MODEL_RELATED_FIELDS.clear()

    receivers: list[tuple[Signal, t.Callable[..., t.Any], list[type[models.Model]]]] = []
    if config.model or config.model_pk or config.related or config.m2m:
        # With m2m alone it announces nothing, but marks a raw save for broadcast_m2m_changed.
        receivers.append((post_save, broadcast_post_save, senders))
    if config.model or config.model_pk or config.related:
        receivers.append((pre_delete, broadcast_pre_delete, senders))
    if config.m2m:
        receivers.append((m2m_changed, broadcast_m2m_changed, _m2m_throughs(senders)))

    for signal, receiver, models_ in receivers:
        for sender in models_:
            uid = f"wireview.auto_broadcast.{receiver.__name__}.{sender._meta.label_lower}"
            signal.connect(receiver, sender=sender, dispatch_uid=uid)
            _connections.append((signal, sender, uid))


def disconnect() -> None:
    """Disconnect every receiver ``connect()`` attached."""
    global _config, _senders, _fields
    while _connections:
        signal, sender, uid = _connections.pop()
        signal.disconnect(sender=sender, dispatch_uid=uid)
    _config = AutoBroadcast()
    _senders = frozenset()
    _fields = {}


def broadcast_post_save(sender, instance, created=False, raw=False, **kwargs):
    # A raw save is a fixture load (``loaddata``): the row is stored as given, the
    # database may not be consistent yet, and Django tells receivers not to query
    # other rows. Nothing a reader did changed it either. The object remembers it, so
    # the m2m loaddata sets on it next is not announced.
    if raw:
        instance.__dict__[_SAVED_RAW] = True
        return
    instance.__dict__.pop(_SAVED_RAW, None)
    if not (_config.model or _config.model_pk or _config.related):
        return
    name = sender._meta.label_lower
    encoded_instance = _encode(sender, instance)
    action: ModelAction = ModelAction.CREATED if created else ModelAction.UPDATED
    if _config.model:
        notify_mutation([name], action, encoded_instance)

    if instance.pk is not None:
        if _config.model_pk:
            notify_mutation(
                [f"{name}.{instance.pk}"],
                action,
                encoded_instance,
            )
        if _config.related:
            broadcast_related(
                sender,
                action,
                instance,
                encoded_instance,
            )


def broadcast_pre_delete(sender, instance, **kwargs):
    name = sender._meta.label_lower
    encoded_instance = _encode(sender, instance)
    if _config.model:
        notify_mutation([name], ModelAction.DELETED, encoded_instance)

    if instance.pk is not None:
        if _config.model_pk:
            notify_mutation(
                [f"{name}.{instance.pk}"],
                ModelAction.DELETED,
                encoded_instance,
            )
        if _config.related:
            broadcast_related(
                sender,
                ModelAction.DELETED,
                instance,
                encoded_instance,
            )


def broadcast_related(sender, action: ModelAction, instance, encoded_instance):
    for field in get_related_fields(sender):
        if field["is_m2m"]:
            fk_ids = getattr(instance, field["name"]).values_list("id", flat=True)
        else:
            fk_ids = filter(None, [getattr(instance, field["name"])])

        if fk_ids:
            group_names = [f"{field['related_model_name']}.{fk_id}.{field['related_name']}" for fk_id in fk_ids]
            notify_mutation(group_names, action, encoded_instance)


MODEL_RELATED_FIELDS = {}


def get_related_fields(model):
    related_fields = MODEL_RELATED_FIELDS.get(model)
    if related_fields is None:
        fields = []
        for field in model._meta.get_fields():
            if isinstance(field, (models.ForeignKey, models.ManyToManyField)):
                related_name = field.related_query_name()
                if related_name != "+":
                    is_m2m = isinstance(field, models.ManyToManyField)
                    if not is_m2m or _config.m2m and is_m2m:
                        related_model = field.related_model
                        # Handle self-referential relationships
                        if related_model == "self" or not hasattr(related_model, "_meta"):
                            related_model = model
                        fields.append(
                            {
                                "is_m2m": is_m2m,
                                "name": field.attname,
                                "related_name": related_name,
                                "related_model_name": related_model._meta.label_lower,
                            }
                        )
        related_fields = MODEL_RELATED_FIELDS[model] = tuple(fields)
    return related_fields


def broadcast_m2m_changed(sender, instance, action, model, pk_set, **kwargs):
    # The message carries ``instance``, so the side whose manager made the change
    # has to be a sender too. Name both models to hear a change from either side.
    if type(instance) not in _senders or instance.__dict__.get(_SAVED_RAW):
        return
    if action.startswith("post_") and instance.pk:
        encoded_instance = _encode(type(instance), instance)
        m2m_action: ModelAction
        if action.endswith("_add"):
            m2m_action = ModelAction.ADDED
        elif action.endswith("_remove"):
            m2m_action = ModelAction.REMOVED
        elif action.endswith("_clear"):
            m2m_action = ModelAction.CLEARED
        else:
            raise ValueError(f"Unknown action `{action}`")

        model_name = model._meta.label_lower
        attr_name = get_name_of(sender, model)
        updates = [f"{model_name}.{pk}.{attr_name}" for pk in pk_set or []]
        notify_mutation(updates, m2m_action, encoded_instance)

        # Both sides in one form, whichever side's manager made the change. The
        # instance's side carried a trailing ``.<pk>`` per related row, so a
        # subscriber of ``auth.user.1.groups`` missed ``user.groups.add(g)`` and saw
        # only ``group.user_set.add(user)`` -- and a clear, with no pks, reached
        # no channel at all (#119).
        instance_model = type(instance)
        instance_model_name = instance_model._meta.label_lower
        instance_attr_name = get_name_of(sender, instance_model)
        notify_mutation(
            [f"{instance_model_name}.{instance.pk}.{instance_attr_name}"],
            m2m_action,
            encoded_instance,
        )


def get_name_of(through, model):
    for model_field in model._meta.get_fields():
        found = getattr(model_field, "through", None) or getattr(
            getattr(model, model_field.name, None), "through", None
        )
        if through is found:
            return model_field.name


def notify_mutation(names: t.Iterable[str], action: ModelAction, instance: str):
    for name in (n.replace("_", "-") for n in names):
        log.debug(f"<-> {action} {name}")
        send_to(name, "model_mutation", action=action, instance=instance)
