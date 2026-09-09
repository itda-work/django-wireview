from django.shortcuts import get_object_or_404, render

from .models import Quiz


def index(request):
    """List all active quizzes."""
    quizzes = Quiz.objects.filter(is_active=True)
    return render(request, "quiz/index.html", {"quizzes": quizzes})


def quiz_detail(request, quiz_id):
    """Show quiz for taking."""
    quiz = get_object_or_404(Quiz, id=quiz_id)
    # The component reads the key through self.session; only a view can create
    # the session, since the WebSocket has no response to carry Set-Cookie.
    if not request.session.session_key:
        request.session.create()
    return render(
        request,
        "quiz/detail.html",
        {"quiz": quiz},
    )
