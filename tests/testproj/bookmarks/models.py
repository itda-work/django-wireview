from django.db import models


class Bookmark(models.Model):
    title = models.CharField(max_length=200)
    url = models.URLField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
