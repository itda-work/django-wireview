"""Rows the database changes when their parent is deleted, without Django's signals (#265).

Django 6.1's ``DB_CASCADE``, ``DB_SET_NULL`` and ``DB_SET_DEFAULT`` leave the
child rows to the database's ``ON DELETE`` clause: no collector visits them,
so neither ``pre_delete`` nor ``post_save`` reaches ``AUTO_BROADCAST``. On
older Django the options do not exist and this app defines only the parent.
No migrations: the test database creates the tables, as it does for
``inheritprobe``.
"""

from django.db import models


class Post(models.Model):
    title = models.CharField(max_length=50)


if hasattr(models, "DB_CASCADE"):

    class Comment(models.Model):
        post = models.ForeignKey(Post, on_delete=models.DB_CASCADE)
        text = models.CharField(max_length=50)

    class Note(models.Model):
        post = models.ForeignKey(Post, null=True, on_delete=models.DB_SET_NULL)
        text = models.CharField(max_length=50)

    class Pin(models.Model):
        post = models.ForeignKey(Post, null=True, db_default=None, on_delete=models.DB_SET_DEFAULT)
        text = models.CharField(max_length=50)
