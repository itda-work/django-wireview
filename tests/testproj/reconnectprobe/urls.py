from django.shortcuts import render
from django.urls import path

app_name = "reconnectprobe"

urlpatterns = [
    # The name comes from the view: the page passes ``currency``, which only the
    # versions of ReconnectBox a test deploys have (live.py), and the editor's
    # template check reads the first one's fields (#231)
    path("", lambda request: render(request, "reconnectprobe/page.html", {"box": "ReconnectBox"}), name="index"),
]
