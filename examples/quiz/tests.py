"""What this example is for: a state machine held in component state, scored
server-side, ending in a row other components subscribe to."""

import pytest
from wireview import mount

from .live import QuizState, XQuiz
from .models import Choice, Question, Quiz, Submission


async def _quiz_with_one_question():
    quiz = await Quiz.objects.acreate(title="파이썬 기초")
    question = await Question.objects.acreate(quiz=quiz, text="GIL은?")
    right = await Choice.objects.acreate(question=question, text="맞음", is_correct=True)
    wrong = await Choice.objects.acreate(question=question, text="틀림", is_correct=False)
    return quiz, right, wrong


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_the_quiz_starts_in_the_intro_state():
    quiz, _, _ = await _quiz_with_one_question()
    view = await mount(XQuiz, quiz=quiz)

    assert view.component.state == QuizState.INTRO

    await view.call("start_quiz")
    assert view.component.state == QuizState.PLAYING
    assert view.component.score == 0


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_a_correct_answer_scores_and_a_wrong_one_does_not():
    quiz, right, wrong = await _quiz_with_one_question()

    view = await mount(XQuiz, quiz=quiz)
    await view.call("start_quiz")
    await view.call("answer", choice_id=right.pk)
    assert view.component.score == 1

    other = await mount(XQuiz, quiz=quiz)
    await other.call("start_quiz")
    await other.call("answer", choice_id=wrong.pk)
    assert other.component.score == 0


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_finishing_the_last_question_records_a_submission():
    quiz, right, _ = await _quiz_with_one_question()
    view = await mount(XQuiz, quiz=quiz, session_key="s1", username="철수")

    await view.call("start_quiz")
    await view.call("answer", choice_id=right.pk)
    await view.call("next_question")

    assert view.component.state == QuizState.RESULTS
    submission = await Submission.objects.filter(quiz=quiz, session_key="s1").afirst()
    assert submission is not None
    assert submission.score == 1
    assert submission.username == "철수"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.django_db
async def test_restart_returns_to_the_intro():
    quiz, right, _ = await _quiz_with_one_question()
    view = await mount(XQuiz, quiz=quiz)

    await view.call("start_quiz")
    await view.call("answer", choice_id=right.pk)
    await view.call("restart")

    assert view.component.state == QuizState.INTRO
    assert view.component.score == 0
    assert view.component.answers == {}
