# 13. Quiz 앱 - 퀴즈와 상태 머신

> 동작하는 전체 코드: [examples/quiz/](../../examples/quiz/) — CI가 매번 돌리는 예제다.

이 튜토리얼에서는 퀴즈 앱을 만들며 상태 머신 패턴과 실시간 리더보드를 학습합니다.

## 학습 목표

- 상태 머신 패턴 (intro → playing → results)
- `Meta.subscriptions` 모델 구독
- `mutation()` 훅 활용
- `force_render()` vs `skip_render()`
- 실시간 리더보드

## 완성 미리보기

다단계 퀴즈 앱:
- 시작 화면 → 질문 → 결과
- 실시간 리더보드
- 다른 사용자 점수 실시간 반영

## 1. 모델 정의

`quiz/models.py`:

```python
from django.db import models


class Quiz(models.Model):
    """퀴즈"""
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)


class Question(models.Model):
    """퀴즈 질문"""
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    text = models.TextField()
    order = models.PositiveSmallIntegerField(default=0)
    explanation = models.TextField(blank=True)

    class Meta:
        ordering = ["order", "id"]


class Choice(models.Model):
    """선택지"""
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField(max_length=200)
    is_correct = models.BooleanField(default=False)


class Submission(models.Model):
    """퀴즈 제출"""
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="submissions")
    session_key = models.CharField(max_length=40)
    score = models.PositiveSmallIntegerField(default=0)
    total_questions = models.PositiveSmallIntegerField(default=0)
    completed_at = models.DateTimeField(auto_now_add=True)
    username = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["-score", "completed_at"]

    @property
    def percentage(self):
        if self.total_questions == 0:
            return 0
        return round((self.score / self.total_questions) * 100)
```

## 2. AUTO_BROADCAST 설정

`Submission` 모델 구독을 위해:

```python
from wireview import AutoBroadcast

WIREVIEW = {
    "AUTO_BROADCAST": AutoBroadcast(
        model=True,      # "quiz.submission" 채널 활성화
        model_pk=True,
        senders={("quiz", "Submission")},  # 알릴 모델. 비우면 아무것도 알리지 않는다
    ),
}
```

## 3. 상태 머신 패턴

`quiz/live.py`:

```python
from enum import StrEnum

from wireview import Component, ModelAction

from .models import Choice, Question, Quiz, Submission


class QuizState(StrEnum):
    """퀴즈 상태"""
    INTRO = "intro"       # 시작 화면
    PLAYING = "playing"   # 진행 중
    RESULTS = "results"   # 결과 화면


class XQuiz(Component):
    """퀴즈 컴포넌트"""

    class Meta:
        template_name = "quiz/quiz.html"
        subscriptions = {"quiz.submission"}  # 리더보드 업데이트용

    quiz: Quiz
    state: QuizState = QuizState.INTRO
    current_question_index: int = 0
    score: int = 0
    answers: dict[int, int] = {}  # question_id -> choice_id
    username: str = ""

    @property
    def questions(self):
        """템플릿이 읽는다. 핸들러는 아래 async 헬퍼를 쓴다"""
        return list(self.quiz.questions.all())

    async def _acurrent_question(self) -> Question | None:
        if self.current_question_index < 0:
            return None
        return await self.quiz.questions.all()[self.current_question_index : self.current_question_index + 1].afirst()

    async def _aquestion_count(self) -> int:
        return await self.quiz.questions.acount()

    @property
    def current_question(self):
        questions = self.questions
        if 0 <= self.current_question_index < len(questions):
            return questions[self.current_question_index]
        return None

    @property
    def current_answer_id(self):
        """현재 질문의 답변 ID"""
        question = self.current_question
        if question:
            return self.answers.get(question.id)
        return None

    async def mutation(self, channel, action, instance: Submission):
        """새 제출 시 리더보드 업데이트"""
        if instance.quiz_id == self.quiz.id:
            self.force_render()

    async def set_username(self, name: str):
        """이름 입력. 인자 이름은 input의 name="name"과 같아야 값이 들어온다"""
        self.username = name.strip()[:50]

    async def start_quiz(self):
        """퀴즈 시작 - INTRO → PLAYING"""
        self.state = QuizState.PLAYING
        self.current_question_index = 0
        self.score = 0
        self.answers = {}

    async def answer(self, choice_id: int):
        """답변 제출"""
        question = await self._acurrent_question()
        if not question or question.id in self.answers:
            # 질문이 없거나 이미 답했다. 같은 정답을 다시 눌러 점수를 올릴 수 없다
            self.skip_render()
            return

        # choice_id는 클라이언트가 보낸 값이다. 이 질문의 선택지인지 확인한다
        choice = await Choice.objects.filter(id=choice_id, question_id=question.id).afirst()
        if choice is None:
            self.skip_render()
            return

        self.answers[question.id] = choice_id
        if choice.is_correct:
            self.score += 1

    async def next_question(self):
        """다음 질문 또는 결과"""
        self.current_question_index += 1

        if self.current_question_index >= await self._aquestion_count():
            await self._save_submission()
            self.state = QuizState.RESULTS  # PLAYING → RESULTS

    async def _save_submission(self):
        """결과 저장"""
        await Submission.objects.acreate(
            quiz=self.quiz,
            session_key=self.session.session_key or "anonymous",
            score=self.score,
            total_questions=await self._aquestion_count(),
            username=self.username or "Anonymous",
        )

    async def restart(self):
        """재시작 - RESULTS → INTRO"""
        self.state = QuizState.INTRO
```

> **핸들러는 property 대신 async 헬퍼를 쓴다.** `questions`·`current_question`은 동기 ORM이다. 템플릿이
> 읽을 때는 렌더가 워커 스레드에서 돌아 괜찮지만, `answer()` 같은 핸들러는 이벤트 루프 위에서 돌므로
> 같은 property를 읽으면 프로덕션에서 `SynchronousOnlyOperation`이 난다. 그래서 핸들러는
> `afirst()`·`acount()`로 묻는 `_acurrent_question()`·`_aquestion_count()`를 쓴다.

> **세션 키는 뷰에서 만든다.** 컴포넌트의 `self.session`은 Django 세션의 읽기 전용 뷰다.
> WebSocket에는 `Set-Cookie`를 실을 응답이 없어 세션을 만들거나 쓰는 것은 뷰의 몫이다.
>
> ```python
> def quiz_detail(request, quiz_id):
>     if not request.session.session_key:
>         request.session.create()
>     ...
> ```
>
> 자세한 내용은 [세션 읽기](../features/session.md).

## 4. 상태별 템플릿

`quiz/templates/quiz/quiz.html`:

```html
{% load wireview %}

<div {% tag_header %} class="quiz-card">
  {% if state == 'intro' %}
    <!-- 시작 화면 -->
    <div class="intro">
      <h2>{{ this.quiz.title }}</h2>
      <p>{{ this.questions|length }} questions</p>

      <input
        type="text"
        name="name"
        placeholder="Enter your name"
        value="{{ username }}"
        {% on 'input' 'set_username' %}
      />
      <button {% on 'click' 'start_quiz' %}>Start Quiz</button>
    </div>

  {% elif state == 'playing' %}
    <!-- 질문 화면 -->
    {% with question=this.current_question answer_id=this.current_answer_id %}
      <div class="question-number">
        Question {{ current_question_index|add:1 }} of {{ this.questions|length }}
      </div>
      <div class="question-text">{{ question.text }}</div>

      <div class="choices">
        {% for choice in question.choices.all %}
          <button
            {% class {
              'choice-btn': True,
              'selected': answer_id == choice.id,
              'correct': answer_id and choice.is_correct,
              'incorrect': answer_id == choice.id and not choice.is_correct
            } %}
            {% cond {'disabled': answer_id} %}
            {% on 'click' 'answer' choice_id=choice.id %}
          >
            {{ choice.text }}
          </button>
        {% endfor %}
      </div>

      {% if answer_id %}
        <button {% on 'click' 'next_question' %}>
          {% if current_question_index|add:1 >= this.questions|length %}
            See Results
          {% else %}
            Next Question
          {% endif %}
        </button>
      {% endif %}
    {% endwith %}

  {% elif state == 'results' %}
    <!-- 결과 화면 -->
    <div class="results">
      <h2>Quiz Complete!</h2>
      <div class="score">{{ score }}/{{ this.questions|length }}</div>
      <button {% on 'click' 'restart' %}>Try Again</button>
    </div>

    {% component 'XLeaderboard' id="leaderboard" quiz=quiz %}
  {% endif %}
</div>
```

## 5. 리더보드 컴포넌트

```python
class XLeaderboard(Component):
    """실시간 리더보드"""

    class Meta:
        template_name = "quiz/leaderboard.html"
        subscriptions = {"quiz.submission"}

    quiz: Quiz

    @property
    def submissions(self):
        return list(self.quiz.submissions.all()[:10])

    async def mutation(self, channel, action, instance):
        if instance.quiz_id == self.quiz.id:
            self.force_render()
```

`quiz/templates/quiz/leaderboard.html`:

```html
{% load wireview %}

<div {% tag_header %} class="leaderboard">
  <h3>Leaderboard</h3>
  {% for submission in this.submissions %}
    <div class="leaderboard-item">
      #{{ forloop.counter }} {{ submission.username }}
      {{ submission.score }}/{{ submission.total_questions }}
    </div>
  {% empty %}
    <p>No submissions yet. Be the first!</p>
  {% endfor %}
</div>
```

`Submission.Meta.ordering`(`-score`, `completed_at`) 덕분에 `self.quiz.submissions.all()[:10]`이 점수 높은 순,
동점이면 먼저 끝낸 순이다.

## 6. 핵심 개념

### 상태 머신

```
INTRO → start_quiz() → PLAYING → next_question() → RESULTS
                                                      ↓
                          ← restart() ← INTRO ←──────┘
```

상태에 따라 다른 UI를 표시합니다.

### force_render() vs skip_render()

```python
async def mutation(self, channel, action, instance):
    # 다른 사용자 제출 시 리더보드 업데이트 필요
    self.force_render()

async def answer(self, choice_id):
    question = await self._acurrent_question()
    if not question or question.id in self.answers:
        # 불필요한 렌더링 방지
        self.skip_render()
        return
```

## 연습 문제

1. **타이머**: 질문당 시간 제한 추가
2. **힌트**: 힌트 버튼 (사용 시 점수 감소)
3. **카테고리**: 카테고리별 퀴즈 필터

## 다음 단계

- [04. Chat 앱](./04-chat-app.md) - Streams API, Presence API

---

[← 이전: 12. Live Search](12-live-search.md) | [목차](README.md) | [다음: 04. Chat 앱 →](04-chat-app.md)
