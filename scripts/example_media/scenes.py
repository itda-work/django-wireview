"""Every example's scene: what a person does on its page, step by step (#213).

One scene per example directory (tests/test_example_media.py checks there is one for
each). A scene shows the example's one concept -- the line in examples/README.md --
with the fewest steps that make it visible. Where the concept is that one window's
action reaches another, the scene has two actors and the clip shows both windows.

Seeds create what a page needs before anyone opens it, with fixed names and values,
so two runs record the same screens. They run with the database empty, on a thread
with no event loop; the models are imported inside them, after Django is set up.
"""

from __future__ import annotations

from datetime import timedelta

from .steps import Actor, Scene, click, hover, live, pause, press, scroll, see, select, type_, visit

ALICE = Actor("alice", "alice", "#2563eb")
BOB = Actor("bob", "bob", "#db2777")
#: Two windows of the same anonymous visitor, for the examples whose sharing needs no sign-in.
LEFT = Actor("left", "창 1", "#2563eb")
RIGHT = Actor("right", "창 2", "#db2777")

#: One window side by side with another: narrow enough that two fit a README's width.
HALF = (640, 600)


def _earlier(minutes: int):
    """An hour ago plus ``minutes``: what a seeded row says it was made at, before anything the clip sends."""
    from django.utils import timezone

    return timezone.now() - timedelta(hours=1) + timedelta(minutes=minutes)


def _users(*names: str) -> list:
    from django.contrib.auth import get_user_model

    return [get_user_model().objects.create(username=name) for name in names]


def _signed_in(prefix: str, who: str) -> tuple:
    """Sign in through the example's own demo button, as its E2E tests do."""
    return (
        visit(f"{prefix}/", who=who),
        click(f'role=button[name="Sign in as {who}"]', who=who),
        see('[data-testid="signed-in-as"]', who, who=who),
    )


# -- todo ---------------------------------------------------------------------------------


def _todo_seed() -> dict[str, str]:
    from examples.todo.models import Item

    for text in ("우유 사기", "wireview 예제 읽기"):
        Item.objects.create(text=text)
    return {}


TODO_LIST = '[data-name=XTodoList][data-is-live="true"]'

TODO = Scene(
    example="todo",
    caption="한 창에서 할 일을 더하고 끝내면 다른 창의 목록도 그대로 바뀐다",
    actors=(LEFT, RIGHT),
    seed=_todo_seed,
    viewport=(640, 560),
    prepare=(
        visit("/todo", who="left"),
        live(TODO_LIST, who="left"),
        visit("/todo", who="right"),
        live(TODO_LIST, who="right"),
    ),
    play=(
        pause(600),
        type_("input.new-todo", "빨래 걷기", who="left"),
        press("input.new-todo", "Enter", who="left"),
        see("ul.todo-list", "빨래 걷기", who="right"),
        pause(400),
        click("ul.todo-list li >> nth=0 >> input.toggle", who="right"),
        see("ul.todo-list li.completed", "우유 사기", who="left"),
        pause(400),
        type_("input.new-todo", "장보기 목록 정리", who="right"),
        press("input.new-todo", "Enter", who="right"),
        see("ul.todo-list", "장보기 목록 정리", who="left"),
    ),
)


# -- poll ---------------------------------------------------------------------------------


def _poll_seed() -> dict[str, str]:
    from examples.poll.models import Option, Poll

    poll = Poll.objects.create(question="오늘 점심은 뭘 먹을까요?")
    for order, (text, votes) in enumerate((("김치찌개", 4), ("파스타", 3), ("샐러드", 1), ("국밥", 2))):
        Option.objects.create(poll=poll, text=text, votes=votes, order=order)
    return {"poll": f"/poll/{poll.pk}/"}


POLL_LIVE = '[data-name=XPoll][data-is-live="true"]'

POLL = Scene(
    example="poll",
    caption="한 창이 투표하면 두 창의 막대와 표 수가 함께 움직인다",
    actors=(LEFT, RIGHT),
    seed=_poll_seed,
    viewport=HALF,
    prepare=(
        visit("{poll}", who="left"),
        live(POLL_LIVE, who="left"),
        visit("{poll}", who="right"),
        live(POLL_LIVE, who="right"),
    ),
    play=(
        pause(600),
        click('button.option-button:has-text("파스타")', who="left"),
        see(".total-votes", "11", who="right"),
        pause(700),
        click('button.option-button:has-text("파스타")', who="right"),
        see(".total-votes", "12", who="left"),
    ),
)


# -- rating -------------------------------------------------------------------------------


def _rating_seed() -> dict[str, str]:
    from examples.rating.models import Product, Rating

    product = Product.objects.create(
        name="무선 기계식 키보드", description="저소음 갈축, 블루투스 3대 연결, USB-C 충전."
    )
    for i, score in enumerate((5, 5, 5, 4, 4, 4, 4, 3, 2)):
        Rating.objects.create(product=product, score=score, session_key=f"seed-{i}")
    return {"product": f"/rating/{product.pk}/"}


STAR = ".star-rating .star >> nth={}"

RATING = Scene(
    example="rating",
    caption="별 위를 지나가는 미리보기는 잠깐 쓰는 상태, 누른 별점은 저장되어 통계가 바뀐다",
    seed=_rating_seed,
    viewport=(960, 760),
    prepare=(visit("{product}"), live('[data-name=XStarRating][data-is-live="true"]')),
    play=(
        pause(500),
        hover(STAR.format(0)),
        hover(STAR.format(2)),
        hover(STAR.format(4)),
        hover("h1"),
        pause(300),
        click(STAR.format(3)),
        see(".rating-text", "Your rating: 4"),
        see(".rating-count", "10 ratings"),
        pause(400),
        press(".star-rating", "ArrowRight"),
        see(".rating-text", "Your rating: 5"),
    ),
)


# -- search -------------------------------------------------------------------------------

BOOKS = (
    ("Two Scoops of Django", "Daniel Feldroy", 2020, "Django"),
    ("Django for Beginners", "William S. Vincent", 2022, "Django"),
    ("Django for APIs", "William S. Vincent", 2022, "Django"),
    ("High Performance Django", "Peter Baumgartner", 2014, "Django"),
    ("Fluent Python", "Luciano Ramalho", 2022, "Python"),
    ("Effective Python", "Brett Slatkin", 2019, "Python"),
    ("Architecture Patterns with Python", "Harry Percival", 2020, "Python"),
    ("Test-Driven Development with Python", "Harry Percival", 2017, "Python"),
    ("클린 코드", "로버트 C. 마틴", 2013, "설계"),
    ("리팩터링 2판", "마틴 파울러", 2020, "설계"),
    ("실용주의 프로그래머", "데이비드 토머스", 2022, "설계"),
)


def _search_seed() -> dict[str, str]:
    from examples.search.models import Book

    for title, author, year, category in BOOKS:
        Book.objects.create(title=title, author=author, published_year=year, category=category)
    return {}


SEARCH = Scene(
    example="search",
    caption="입력이 멈추면 검색하고, 방향키로 고른 결과를 Enter로 연다",
    seed=_search_seed,
    viewport=(960, 640),
    prepare=(visit("/search/"), live('[data-name=XLiveSearch][data-is-live="true"]')),
    play=(
        pause(500),
        type_("input.search-input", "django"),
        see(".dropdown", "Two Scoops of Django"),
        pause(500),
        press("input.search-input", "ArrowDown"),
        press("input.search-input", "ArrowDown"),
        pause(300),
        press("input.search-input", "Enter"),
        see(".selected-book", "Author"),
    ),
)


# -- quiz ---------------------------------------------------------------------------------

QUESTIONS = (
    (
        "컴포넌트의 이벤트 핸들러는 어떤 함수여야 하나요?",
        (("async 함수", True), ("동기 함수", False), ("클래스 메서드", False)),
        "핸들러와 라이프사이클 메서드는 모두 async입니다.",
    ),
    (
        "다른 브라우저의 화면을 함께 바꾸려면 무엇을 씁니까?",
        (("브로드캐스트", True), ("세션", False), ("쿠키", False)),
        "같은 채널을 듣는 모든 연결에 알립니다.",
    ),
    (
        "_로 시작하는 메서드는 클라이언트가 부를 수 있나요?",
        (("아니요", True), ("예", False)),
        "내부 헬퍼는 _ 접두사로 숨깁니다.",
    ),
)


def _quiz_seed() -> dict[str, str]:
    from examples.quiz.models import Choice, Question, Quiz, Submission

    quiz = Quiz.objects.create(title="wireview 기초 퀴즈", description="세 문제로 보는 컴포넌트의 기본")
    for order, (text, choices, explanation) in enumerate(QUESTIONS):
        question = Question.objects.create(quiz=quiz, text=text, order=order, explanation=explanation)
        for i, (label, correct) in enumerate(choices):
            Choice.objects.create(question=question, text=label, is_correct=correct, order=i)
    for name, score in (("민수", 3), ("지은", 2)):
        Submission.objects.create(quiz=quiz, session_key=f"seed-{name}", score=score, total_questions=3, username=name)
    return {"quiz": f"/quiz/{quiz.pk}/"}


def _choose(label: str) -> tuple:
    return (
        click(f'button.choice-btn:has-text("{label}")'),
        pause(500),
        click('.quiz-card button.btn:has-text("Next Question"), .quiz-card button.btn:has-text("See Results")'),
    )


QUIZ = Scene(
    example="quiz",
    caption="소개 → 문제 → 결과로 넘어가는 상태 머신. 끝나면 순위표에 내 점수가 오른다",
    seed=_quiz_seed,
    viewport=(960, 680),
    prepare=(visit("{quiz}"), live('[data-name=XQuiz][data-is-live="true"]')),
    play=(
        pause(400),
        type_("input.name-input", "하늘"),
        click('role=button[name="Start Quiz"]'),
        see(".question-number", "Question 1"),
        *_choose("async 함수"),
        see(".question-number", "Question 2"),
        *_choose("세션"),
        see(".question-number", "Question 3"),
        *_choose("아니요"),
        see(".score-display", "2/3"),
        see(".leaderboard", "하늘"),
    ),
)


# -- dashboard ----------------------------------------------------------------------------


def _dashboard_seed() -> dict[str, str]:
    from examples.dashboard.models import Activity, Stat

    for name, label, value, change, color in (
        ("revenue", "Revenue", 48250, 12.5, "green"),
        ("users", "Active users", 1842, 4.2, "blue"),
        ("orders", "Orders", 326, -3.1, "yellow"),
        ("conversion", "Conversion", 3.4, 0.6, "red"),
    ):
        Stat.objects.create(name=name, label=label, value=value, change_percent=change, color=color)
    feed = (
        ("purchase", "지은", "Pro 플랜을 구매했습니다"),
        ("comment", "민수", "릴리스 노트에 댓글을 남겼습니다"),
        ("upload", "하늘", "보고서 3건을 올렸습니다"),
        ("login", "서준", "새 기기에서 로그인했습니다"),
        ("update", "지은", "결제 정보를 바꿨습니다"),
        ("purchase", "도윤", "팀 플랜을 구매했습니다"),
        ("comment", "하늘", "이슈 #42에 답했습니다"),
        ("login", "민수", "로그인했습니다"),
        ("upload", "서준", "사진 12장을 올렸습니다"),
        ("update", "도윤", "프로필을 고쳤습니다"),
        ("purchase", "민수", "추가 좌석 2개를 샀습니다"),
        ("comment", "지은", "문서에 의견을 남겼습니다"),
        ("login", "하늘", "로그인했습니다"),
        ("upload", "도윤", "백업을 올렸습니다"),
    )
    for minutes, (kind, user, text) in enumerate(feed):
        activity = Activity.objects.create(type=kind, user_name=user, description=text)
        Activity.objects.filter(pk=activity.pk).update(created_at=_earlier(50 - minutes * 3))
    return {}


DASHBOARD = Scene(
    example="dashboard",
    caption=(
        "카드마다 느린 조회를 미뤄 두고 먼저 그린다. "
        "다시 불러오면 그 카드만 로딩 상태로 돌아가고, 활동 탭은 더 불러온다"
    ),
    seed=_dashboard_seed,
    viewport=(960, 720),
    prepare=(),
    play=(
        visit("/dashboard/"),
        live('[data-name=XDashboard][data-is-live="true"]'),
        see("#stat-revenue", "48250"),
        pause(800),
        click('#stat-users button[title="Refresh"]'),
        see("#stat-users", "1842"),
        pause(500),
        click('role=button[name="Activity"]'),
        see(".activity-feed li"),
        pause(400),
        click('role=button[name="Load More"]'),
        pause(600),
        click('role=button[name="Overview"]'),
        see("#stat-orders", "326"),
    ),
)


# -- notifications ------------------------------------------------------------------------


def _notifications_seed() -> dict[str, str]:
    _users("alice", "bob")
    return {}


NOTIFICATIONS_LIVE = '[data-name=XNotificationList][data-is-live="true"]'

NOTIFICATIONS = Scene(
    example="notifications",
    caption="alice가 bob에게 보낸 알림은 bob의 목록과 종에만 온다. 토스트는 저장되지 않고 지금 열린 화면에만 뜬다",
    actors=(ALICE, BOB),
    seed=_notifications_seed,
    viewport=(640, 720),
    prepare=(
        *_signed_in("/notifications", "alice"),
        live(NOTIFICATIONS_LIVE, who="alice"),
        *_signed_in("/notifications", "bob"),
        live(NOTIFICATIONS_LIVE, who="bob"),
        live('[data-name=XNotificationCreator][data-is-live="true"]', who="alice"),
    ),
    play=(
        pause(500),
        select("select[name=recipient]", "bob", who="alice"),
        type_("input[name=title]", "리뷰 부탁해요", who="alice"),
        click('role=button[name="Warning"]', who="alice"),
        click('[data-testid="send-notification"]', who="alice"),
        see(".bell-badge", "1", who="bob"),
        pause(400),
        scroll("window", 600, who="bob"),
        see(".notification-item", "리뷰 부탁해요", who="bob"),
        pause(500),
        type_("input[name=title]", "지금 회의 들어와요", who="alice"),
        click('[data-testid="send-toast"]', who="alice"),
        see("[wire-flash] .wireview-flash-message", "지금 회의 들어와요", who="bob"),
    ),
)


# -- livecomp -----------------------------------------------------------------------------

LIVECOMP = Scene(
    example="livecomp",
    caption="카운터 셋은 각자 상태를 가진 LiveComponent. 하나를 누르면 부모의 합계가, 부모의 버튼은 셋을 함께 바꾼다",
    viewport=(960, 560),
    prepare=(visit("/livecomp/"), live('[data-name=Dashboard][data-is-live="true"]')),
    play=(
        pause(500),
        click('[data-testid="increment-counter-1"]'),
        click('[data-testid="increment-counter-1"]'),
        click('[data-testid="decrement-counter-2"]'),
        see('[data-testid="total"]', "16"),
        pause(400),
        click('[data-testid="sync-all"]'),
        see('[data-testid="total"]', "15"),
        pause(400),
        click('[data-testid="increment-counter-3"]'),
        see('[data-testid="total"]', "16"),
    ),
)


# -- slots --------------------------------------------------------------------------------

SLOTS = Scene(
    example="slots",
    caption="틀은 컴포넌트가, 내용은 호출자가. 페이지가 넘기는 값이 바뀌면 채운 자리도 다시 그려진다",
    viewport=(960, 680),
    prepare=(visit("/slots/"), live('[data-name=SlotsGallery][data-is-live="true"]')),
    play=(
        pause(500),
        click('[data-testid="add-book"]'),
        see(".card-header", "3권"),
        click('[data-testid="add-book"]'),
        see(".card-header", "4권"),
        pause(300),
        click('[data-testid="cycle-alert"]'),
        click('[data-testid="cycle-alert"]'),
        pause(300),
        click('[data-testid="toggle-modal"]'),
        see(".modal.is-open", "필수 슬롯"),
    ),
)


# -- hooks --------------------------------------------------------------------------------

HOOKS = Scene(
    example="hooks",
    caption="훅은 브라우저만 할 수 있는 일(지금 시각 기준의 '몇 초 전')을 맡고, 서버와 이벤트를 주고받는다",
    viewport=(960, 680),
    prepare=(visit("/hooks/"), live('[data-name=XLifecycle][data-is-live="true"]')),
    play=(
        pause(1200),
        click('[data-testid="touch"]'),
        pause(600),
        click('[data-testid="highlight"]'),
        see('[data-testid="pushed"]', ""),
        pause(400),
        click('[data-testid="noter"]'),
        click('[data-testid="noter"]'),
        see('[data-testid="notes"]', "2"),
        pause(400),
        click('[data-testid="hide"]'),
        see('[data-testid="gone"]'),
        pause(400),
        click('[data-testid="reveal"]'),
        see('[data-testid="timeago"]'),
    ),
)


# -- chat ---------------------------------------------------------------------------------


def _chat_seed() -> dict[str, str]:
    from examples.chat.models import Message, Room

    room = Room.objects.create(name="general")
    for minutes, (who, text) in enumerate((("alice", "좋은 아침이에요"), ("bob", "안녕하세요! 오늘 배포 있죠?"))):
        message = Message.objects.create(room=room, username=who, content=text)
        Message.objects.filter(pk=message.pk).update(created_at=_earlier(minutes))
    return {"room": f"/chat/room/{room.pk}/"}


def _chat_room(prefix: str, url: str, who: str, root: str) -> tuple:
    return (visit(url, who=who), live(f'[data-name={root}][data-is-live="true"]', who=who))


CHAT = Scene(
    example="chat",
    caption="보낸 메시지는 Streams로 모든 창의 목록에 붙고, 누가 접속해 있는지·입력 중인지는 Presence가 알린다",
    actors=(ALICE, BOB),
    seed=_chat_seed,
    viewport=HALF,
    prepare=(
        *_chat_room("/chat", "{room}?username=alice", "alice", "XChatRoom"),
        *_chat_room("/chat", "{room}?username=bob", "bob", "XChatRoom"),
    ),
    play=(
        pause(600),
        type_("input[name=content]", "네, 3시에 합니다", who="alice"),
        press("input[name=content]", "Enter", who="alice"),
        see("[wire-stream=messages]", "3시에 합니다", who="bob"),
        pause(400),
        type_("input[name=content]", "좋아요, 저도 볼게요 👀", who="bob"),
        press("input[name=content]", "Enter", who="bob"),
        see("[wire-stream=messages]", "저도 볼게요", who="alice"),
    ),
)


# -- chat_lv2 / chat_lv3 / chat_lv4 ---------------------------------------------------------


def _members_room(app: str, name: str, users: list, history: tuple = ()) -> str:
    """A room ``users`` are all members of, with ``history`` -- (author index, text) -- a minute apart."""
    import importlib

    models = importlib.import_module(f"examples.{app}.models")
    room = models.Room.objects.create(name=name)
    for user in users:
        models.Membership.objects.create(room=room, user=user)
    for minutes, (author, text) in enumerate(history):
        message = models.Message.objects.create(room=room, author=users[author], content=text)
        models.Message.objects.filter(pk=message.pk).update(created_at=_earlier(minutes))
    return str(room.pk)


def _lv2_seed() -> dict[str, str]:
    users = _users("alice", "bob", "carol")
    room = _members_room("chat_lv2", "design", users[:2], ((0, "시안 올렸어요"), (1, "확인할게요")))
    return {"room": f"/chat-lv2/room/{room}/"}


def _lv_room(prefix: str, who: str, roots: tuple[str, ...]) -> tuple:
    return (
        *_signed_in(prefix, who),
        visit("{room}", who=who),
        *(live(f'[data-name={root}][data-is-live="true"]', who=who) for root in roots),
    )


LV2_ROOTS = ("Lv2ChatRoom", "Lv2MessageList", "Lv2OnlineUsers")

CHAT_LV2 = Scene(
    example="chat_lv2",
    caption="로그인한 멤버만 방에 들어온다. 보낸 사람의 이름은 입력이 아니라 서버가 아는 사용자다",
    actors=(ALICE, BOB),
    seed=_lv2_seed,
    viewport=HALF,
    prepare=(*_lv_room("/chat-lv2", "alice", LV2_ROOTS), *_lv_room("/chat-lv2", "bob", LV2_ROOTS)),
    play=(
        pause(600),
        type_("input[name=content]", "두 번째 시안도 올렸어요", who="alice"),
        press("input[name=content]", "Enter", who="alice"),
        see("[wire-stream=messages]", "두 번째 시안", who="bob"),
        pause(400),
        type_("input[name=content]", "이게 더 좋네요", who="bob"),
        press("input[name=content]", "Enter", who="bob"),
        see("[wire-stream=messages]", "더 좋네요", who="alice"),
    ),
)


def _lv3_seed() -> dict[str, str]:
    users = _users("alice", "bob")
    lines = [
        "어제 회의록 공유합니다",
        "고마워요",
        "배포는 목요일로 할까요?",
        "좋아요",
        "QA 체크리스트 만들었어요",
        "확인했어요",
    ]
    # More than a page (30), so scrolling up has something to load
    history = tuple((i % 2, f"{lines[i % len(lines)]} ({i + 1})") for i in range(36))
    room = _members_room("chat_lv3", "release", users, history)
    return {"room": f"/chat-lv3/room/{room}/"}


LV3_ROOTS = ("Lv3ChatRoom", "Lv3MessageList", "Lv3OnlineUsers")
NEWEST = "[wire-stream=messages] li.message >> nth=0"

CHAT_LV3 = Scene(
    example="chat_lv3",
    caption=(
        "스트림은 붙이기만 하지 않는다: alice가 고친 메시지와 지운 메시지가 bob의 화면에서도 바뀌고, "
        "위로 스크롤하면 과거를 불러온다"
    ),
    actors=(ALICE, BOB),
    seed=_lv3_seed,
    viewport=HALF,
    prepare=(*_lv_room("/chat-lv3", "alice", LV3_ROOTS), *_lv_room("/chat-lv3", "bob", LV3_ROOTS)),
    play=(
        pause(600),
        type_("input[name=content]", "배포 시간은 3시로 할게요", who="alice"),
        press("input[name=content]", "Enter", who="alice"),
        see(NEWEST, "3시로", who="bob"),
        pause(300),
        click(f'{NEWEST} >> role=button[name="Edit"]', who="alice"),
        type_('input[aria-label="Edit your message"]', "배포 시간은 4시로 미룰게요", who="alice"),
        press('input[aria-label="Edit your message"]', "Enter", who="alice"),
        see(NEWEST, "4시로", who="bob"),
        see(f"{NEWEST} >> .edited", who="bob"),
        pause(500),
        click('[wire-stream=messages] li.message >> nth=2 >> role=button[name="Delete"]', who="alice"),
        pause(800),
        scroll(".message-list", -2400, who="bob"),
        see(".history-start", "release", who="bob"),
    ),
)


def _lv4_seed() -> dict[str, str]:
    alice, bob = _users("alice", "bob")
    rooms = {name: _members_room("chat_lv4", name, [alice, bob]) for name in ("tea", "coffee")}
    _members_room("chat_lv4", "lunch", [alice])
    return {"tea": f"/chat-lv4/room/{rooms['tea']}/"}


CHAT_LV4 = Scene(
    example="chat_lv4",
    caption="bob이 보지 않는 방에 alice가 말하면 bob의 방 목록에만 안 읽은 수가 오르고, 방을 열면 사라진다",
    actors=(ALICE, BOB),
    seed=_lv4_seed,
    viewport=HALF,
    prepare=(
        *_signed_in("/chat-lv4", "alice"),
        visit("{tea}", who="alice"),
        live('[data-name=Lv4MessageList][data-is-live="true"]', who="alice"),
        *_signed_in("/chat-lv4", "bob"),
        live('[data-name=Lv4RoomList][data-is-live="true"]', who="bob"),
    ),
    play=(
        pause(600),
        type_("input[name=content]", "차 한잔 하실 분?", who="alice"),
        press("input[name=content]", "Enter", who="alice"),
        see('[data-room="tea"] .unread', "1", who="bob"),
        pause(300),
        type_("input[name=content]", "3층 라운지예요", who="alice"),
        press("input[name=content]", "Enter", who="alice"),
        see('[data-room="tea"] .unread', "2", who="bob"),
        pause(700),
        click('[data-room="tea"] a', who="bob"),
        live('[data-name=Lv4MessageList][data-is-live="true"]', who="bob"),
        see("[wire-stream=messages]", "3층 라운지", who="bob"),
    ),
)


#: In the order of examples/README.md.
SCENES: tuple[Scene, ...] = (
    TODO,
    POLL,
    RATING,
    SEARCH,
    QUIZ,
    CHAT,
    CHAT_LV2,
    CHAT_LV3,
    CHAT_LV4,
    DASHBOARD,
    NOTIFICATIONS,
    LIVECOMP,
    SLOTS,
    HOOKS,
)


def by_name() -> dict[str, Scene]:
    return {scene.example: scene for scene in SCENES}
