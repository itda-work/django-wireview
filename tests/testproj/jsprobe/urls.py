from django.shortcuts import redirect, render
from django.urls import path

app_name = "jsprobe"


def say(request):
    """A form's POST, answered with a redirect (post/redirect/get) -- or, with no
    word, with the page itself and an error, as a form with errors is."""
    word = request.POST.get("word", "")
    if not word:
        return render(request, "jsprobe/said.html", {"word": "a word is required"})
    return redirect(f"/jsprobe/said/?word={word}")


def said(request):
    return render(request, "jsprobe/said.html", {"word": request.GET.get("word", "")})


urlpatterns = [
    path("", lambda request: render(request, "jsprobe/page.html"), name="index"),
    path("landed/", lambda request: render(request, "jsprobe/landed.html"), name="landed"),
    # A boosted link to here ends on landed/ (#104)
    path("bounce/", lambda request: redirect("/jsprobe/landed/"), name="bounce"),
    # Boosted forms and wireview.visit() (#103)
    path("say/", say, name="say"),
    path("said/", said, name="said"),
    path("slow-join/", lambda request: render(request, "jsprobe/slow_join_page.html"), name="slow_join"),
]
