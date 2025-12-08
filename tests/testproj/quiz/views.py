from django.shortcuts import get_object_or_404, render

from .models import Quiz


def index(request):
    """List all active quizzes."""
    quizzes = Quiz.objects.filter(is_active=True)
    return render(request, "quiz/index.html", {"quizzes": quizzes})


def quiz_detail(request, quiz_id):
    """Show quiz for taking."""
    quiz = get_object_or_404(Quiz, id=quiz_id)
    return render(request, "quiz/detail.html", {"quiz": quiz})
