"""The row changes ``AUTO_BROADCAST`` does not announce, because Django sends it no signal (#265).

The receivers hang on ``post_save``, ``pre_delete`` and ``m2m_changed``. A change
that skips them reaches no component: the page keeps showing the row as it was,
with no error anywhere. ``docs/features/settings.md`` ("알림이 가지 않는 변경")
lists these cases; each line there stands on a test here. ``wireview.W025``
reports the one a model declares (a ``DB_*`` ``on_delete``), the others are calls
the application makes.
"""

import pytest
from django.db import connection, models
from testproj.cascadeprobe.models import Post

from wireview import AutoBroadcast, ModelAction, auto_broadcast

pytestmark = [pytest.mark.unit, pytest.mark.django_db]

needs_db_on_delete = pytest.mark.skipif(
    not hasattr(models, "DB_CASCADE"), reason="DB_CASCADE and friends arrived in Django 6.1"
)

PROBE_MODELS = ("Post", "Comment", "Note", "Pin")


@pytest.fixture
def published(monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "wireview.auto_broadcast.send_to",
        lambda channel, type, action, instance: sent.append((channel, action)),
    )
    return sent


@pytest.fixture
def broadcasting():
    """Every probe model that exists on this Django is a sender, on its model channel."""
    from django.apps import apps

    names = [name for name in PROBE_MODELS if _has_model(apps, name)]
    auto_broadcast.connect(AutoBroadcast(model=True, senders={("cascadeprobe", name) for name in names}))
    yield
    auto_broadcast.connect()


def _has_model(apps, name: str) -> bool:
    try:
        apps.get_model("cascadeprobe", name)
    except LookupError:
        return False
    return True


def test_the_probe_announces_what_the_signals_see(published, broadcasting):
    """The baseline: a save and a delete of the parent itself are announced."""
    post = Post.objects.create(title="a")
    post.delete()

    assert published == [("cascadeprobe.post", ModelAction.CREATED), ("cascadeprobe.post", ModelAction.DELETED)]


@needs_db_on_delete
@pytest.mark.parametrize(
    ("model_name", "after"),
    [("Comment", "gone"), ("Note", None), ("Pin", None)],
    ids=["db-cascade", "db-set-null", "db-set-default"],
)
def test_a_row_the_database_changes_on_delete_is_not_announced(published, broadcasting, model_name, after):
    from testproj.cascadeprobe import models as probe

    model = getattr(probe, model_name)
    post = Post.objects.create(title="a")
    child = model.objects.create(post=post, text="x")
    published.clear()

    post.delete()

    # The database did change the row ...
    if after == "gone":
        assert not model.objects.filter(pk=child.pk).exists()
    else:
        assert model.objects.get(pk=child.pk).post_id is after
    # ... and only the parent's deletion was announced.
    assert published == [("cascadeprobe.post", ModelAction.DELETED)]


@needs_db_on_delete
def test_a_python_cascade_is_announced_for_comparison(published, monkeypatch):
    """The same delete through Django's collector: the child's ``pre_delete`` fires."""
    from django.db.models import deletion
    from testproj.cascadeprobe.models import Comment

    field = Comment._meta.get_field("post")
    monkeypatch.setattr(field.remote_field, "on_delete", deletion.CASCADE)
    auto_broadcast.connect(AutoBroadcast(model=True, senders={("cascadeprobe", "Comment")}))
    try:
        post = Post.objects.create(title="a")
        Comment.objects.create(post=post, text="x")
        published.clear()

        post.delete()
    finally:
        auto_broadcast.connect()

    assert published == [("cascadeprobe.comment", ModelAction.DELETED)]


def test_a_queryset_update_is_not_announced(published, broadcasting):
    post = Post.objects.create(title="a")
    published.clear()

    Post.objects.filter(pk=post.pk).update(title="b")

    assert published == []


def test_bulk_create_and_bulk_update_are_not_announced(published, broadcasting):
    posts = Post.objects.bulk_create([Post(title="a"), Post(title="b")])
    for post in posts:
        post.title += "!"
    Post.objects.bulk_update(posts, ["title"])

    assert Post.objects.filter(title__endswith="!").count() == 2
    assert published == []


def test_raw_sql_is_not_announced(published, broadcasting):
    post = Post.objects.create(title="a")
    published.clear()

    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {Post._meta.db_table} SET title = %s WHERE id = %s", ["b", post.pk])
        cursor.execute(f"DELETE FROM {Post._meta.db_table} WHERE id = %s", [post.pk])

    assert not Post.objects.filter(pk=post.pk).exists()
    assert published == []


def test_a_queryset_delete_is_announced(published, broadcasting):
    """Unlike update(), QuerySet.delete() collects the rows and sends ``pre_delete`` for each."""
    Post.objects.bulk_create([Post(title="a"), Post(title="b")])

    Post.objects.all().delete()

    assert published == [("cascadeprobe.post", ModelAction.DELETED)] * 2
