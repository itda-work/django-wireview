"""
Quiz App Components

This module demonstrates wireview's advanced state patterns:
- Model subscriptions (_subscriptions)
- mutation() hook for real-time updates
- destroy() to remove components
- force_render() vs skip_render()
- State machine pattern (questions -> results)
"""

from enum import StrEnum

from wireview.component import Component
from wireview.schemas import ModelAction

from .models import Choice, Question, Quiz, Submission


class QuizState(StrEnum):
    """Quiz state machine states."""

    INTRO = "intro"  # Show quiz title and start button
    PLAYING = "playing"  # Answering questions
    RESULTS = "results"  # Show final results


class XQuiz(Component):
    """
    Quiz container managing the quiz flow.

    Demonstrates:
    - State machine pattern for multi-step workflow
    - Model subscriptions for leaderboard updates
    - force_render() when state changes
    """

    _template_name = "quiz/quiz.html"
    _subscriptions = {"quiz.submission"}  # Subscribe to submission updates

    quiz: Quiz
    state: QuizState = QuizState.INTRO
    current_question_index: int = 0
    score: int = 0
    answers: dict[int, int] = {}  # question_id -> choice_id
    username: str = ""
    session_key: str = ""

    @property
    def questions(self):
        """Get all questions for this quiz."""
        return list(self.quiz.questions.all())

    @property
    def current_question(self):
        """Get the current question."""
        questions = self.questions
        if 0 <= self.current_question_index < len(questions):
            return questions[self.current_question_index]
        return None

    @property
    def progress_percentage(self):
        """Calculate progress through quiz."""
        total = len(self.questions)
        if total == 0:
            return 0
        return round((self.current_question_index / total) * 100)

    @property
    def current_answer_id(self):
        """Get the answer ID for the current question (if answered)."""
        question = self.current_question
        if question:
            return self.answers.get(question.id)
        return None

    @property
    def leaderboard(self):
        """Get top 10 submissions."""
        return list(self.quiz.submissions.all()[:10])

    async def joined(self):
        """Initialize session key on mount.

        The page passes it in (see views.py): a component's state is the seam,
        wireview does not carry the Django session into the socket.
        """
        self.session_key = self.session_key or "anonymous"

    async def mutation(self, channel: str, action: ModelAction, instance: Submission):
        """
        Update leaderboard when new submissions arrive.

        This allows multiple users to see real-time updates.
        """
        if instance.quiz_id == self.quiz.id:
            self.force_render()

    async def set_username(self, name: str):
        """Set username before starting."""
        self.username = name.strip()[:50]  # Limit length

    async def start_quiz(self):
        """Begin the quiz."""
        self.state = QuizState.PLAYING
        self.current_question_index = 0
        self.score = 0
        self.answers = {}

    async def answer(self, choice_id: int):
        """
        Submit an answer for the current question.

        Demonstrates:
        - State transitions
        - skip_render() for optimization
        """
        question = self.current_question
        if not question:
            self.skip_render()
            return

        # Record the answer
        self.answers[question.id] = choice_id

        # Check if correct
        try:
            choice = await Choice.objects.aget(id=choice_id)
            if choice.is_correct:
                self.score += 1
        except Choice.DoesNotExist:
            pass

    async def next_question(self):
        """Move to the next question or finish."""
        self.current_question_index += 1

        if self.current_question_index >= len(self.questions):
            # Quiz complete - save submission and show results
            await self._save_submission()
            self.state = QuizState.RESULTS

    async def _save_submission(self):
        """Save the quiz submission to database."""
        await Submission.objects.acreate(
            quiz=self.quiz,
            session_key=self.session_key,
            score=self.score,
            total_questions=len(self.questions),
            username=self.username or "Anonymous",
        )

    async def restart(self):
        """Restart the quiz."""
        self.state = QuizState.INTRO
        self.current_question_index = 0
        self.score = 0
        self.answers = {}


class XQuestion(Component):
    """
    Individual question display.

    Demonstrates:
    - {% cond %} for disabled states after answering
    - Dynamic property subscriptions
    - Conditional rendering based on answer state
    """

    _template_name = "quiz/question.html"

    question: Question
    selected_choice_id: int | None = None
    show_result: bool = False

    @property
    def choices(self):
        """Get all choices for this question."""
        return list(self.question.choices.all())

    async def select(self, choice_id: int):
        """Select an answer."""
        if self.selected_choice_id is not None:
            # Already answered
            self.skip_render()
            return

        self.selected_choice_id = choice_id
        self.show_result = True


class XLeaderboard(Component):
    """
    Real-time leaderboard component.

    Demonstrates:
    - Model subscriptions for live updates
    - mutation() hook
    - destroy() when quiz ends
    """

    _template_name = "quiz/leaderboard.html"
    _subscriptions = {"quiz.submission"}

    quiz: Quiz

    @property
    def submissions(self):
        """Get top submissions."""
        return list(self.quiz.submissions.all()[:10])

    async def mutation(self, channel: str, action: ModelAction, instance: Submission):
        """Update when new submissions arrive."""
        if instance.quiz_id == self.quiz.id:
            self.force_render()
