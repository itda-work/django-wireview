# shared_render - 브로드캐스트 렌더 공유

> 모두가 같은 화면을 보는 컴포넌트는, 같은 브로드캐스트를 받은 연결들이 렌더를 한 번만 하고 함께 쓴다.

---

## 개요

### 문제: 같은 화면을 연결마다 다시 그린다

브로드캐스트(`abroadcast()`, 모델 알림)는 토픽을 구독한 모든 연결에 닿는다. 연결마다 수신자(`notification()`,
`mutation()`)가 돌고, 컴포넌트를 다시 렌더하고, 그 연결의 화면과 비교해 diff를 보낸다. 공지판·시세판·현황판처럼
1,000명이 같은 컴포넌트를 같은 상태로 보고 있으면, 서버는 똑같은 템플릿을 1,000번 렌더한다. 브로드캐스트 하나가
연결 1,000개에 닿는 시간의 대부분이 이 렌더와 그 결과의 파싱이다([성능](../PERFORMANCE.md#브로드캐스트를-많은-연결이-받을-때)).

### 해결: 선언한 컴포넌트는 렌더를 함께 쓴다

```python
from wireview import Component


class Scoreboard(Component):
    class Meta:
        template_name = "games/scoreboard.html"
        subscriptions = {"scores"}
        shared_render = True  # 이 컴포넌트의 렌더는 보는 사람과 무관하다

    game_id: int

    @property
    def scores(self):
        return Score.objects.filter(game_id=self.game_id).order_by("-points")[:20]

    async def notification(self, channel: str, **kwargs):
        pass  # 다시 그리기만 하면 된다
```

```django
{% component "Scoreboard" id="scoreboard" game_id=game.pk %}
```

같은 프로세스에서 같은 브로드캐스트 메시지를 처리하는 연결들 가운데, 클래스·컴포넌트 id·필드가 같은 것끼리는
처음 온 연결 하나만 렌더한다. 나머지는 그 렌더를 받아 자기 화면과 비교만 한다. 브라우저가 받는 프레임은 공유하지
않을 때와 바이트까지 같다.

**선언은 약속이다.** "이 컴포넌트의 렌더는 필드와, 모두에게 같은 데이터만 읽는다. 보는 사람이 누구인지는 읽지
않는다." 약속이 틀리면 **처음 렌더한 사람의 화면이 다른 사람에게 간다.** 그래서 기본값은 꺼져 있고, 선언한
컴포넌트에만 일어나며, 틀린 선언을 [최대한 잡는다](#틀린-선언-잡기).

---

## 작동 방식

### 함께 쓰는 것과 연결마다 하는 것

| | 함께 쓴다 | 연결마다 |
|---|---|---|
| 수신자 (`notification()`, `mutation()`) | | ✓ 각 연결의 컴포넌트에서 돈다. 필드를 바꿔도 된다 |
| 템플릿 렌더 (property 읽기 포함) | ✓ 메시지와 키마다 한 번 | |
| 마커 파싱 (`Rendered`) | ✓ | |
| `data-state` (서명한 상태) | | ✓ 연결의 경계(`live_session`)와 인증 세대에 묶인 토큰이다 |
| diff | | ✓ 그 연결의 화면이 보여 주는 것과 비교한다 |
| `after_render` 훅, LiveComponent 수명주기, 구독 갱신 | | ✓ |
| 이벤트(클릭)에 대한 렌더 | | ✓ 공유는 브로드캐스트에서만 일어난다 |

`data-state`는 공유하지 않는다. 서명 봉투에는 클래스와 상태 말고도 페이지 경계 이름과 그 연결의 인증 세대 지문이
들어간다(`docs/design/live-session.md`). 그래서 공유 렌더는 그 자리를 비워 두고, 연결마다 자기 토큰을 끼운다.
토큰 재사용(같은 상태면 같은 토큰)도 연결마다 그대로다.

### 같은 렌더의 조건

아래가 모두 같아야 한 렌더를 함께 쓴다. 하나라도 다르면 따로 렌더한다.

- **브로드캐스트 메시지.** 발행할 때 메시지마다 id(`message_id`)가 붙는다. 같은 메시지를 처리하는 동안만 함께
  쓴다. 다음 브로드캐스트는 다시 렌더한다. 프로세스는 최근 메시지 64개를 10초까지 기억한다.
- **클래스와 컴포넌트 id.** id는 루트 요소(`{% tag_header %}`)에 찍힌다. **`id=`를 주지 않은 컴포넌트는 페이지마다
  id가 달라 공유되지 않는다.** 모두가 보는 컴포넌트에는 고정 id를 준다.
- **필드.** `user`·`wire`·`session`을 뺀 모든 필드의 JSON이다. `Meta.exclude_fields`로 서명에서 뺀 필드도 렌더는
  읽으므로 키에 넣는다. 수신자가 연결마다 필드를 다르게 바꾸면 그 값마다 따로 렌더한다.
- **활성 언어와 시간대.** `translation.get_language()`와 `timezone.get_current_timezone_name()`이다. 연결마다
  언어나 시간대를 켜는 앱은 그 값마다 따로 렌더한다.

키를 만드는 비용은 연결당 수 µs다(필드 JSON 한 번).

### 처음 렌더한 연결이 실패하면

처음 온 연결의 렌더가 예외를 던지면 그 연결만 [오류 처리](./errors.md)를 거친다. 기다리던 연결 가운데 하나가
다시 렌더하고 나머지는 그것을 받는다. join이 실패해 막힌 컴포넌트(`repo.refused`)는 공유에 끼지 않는다. 렌더하지
않는 것은 지금과 같다.

---

## 언제 쓰면 안 되는가

렌더가 **보는 사람에 따라 달라지는** 컴포넌트에는 선언하지 않는다. 흔한 경우는 이렇다.

| 경우 | 왜 틀리나 | 대신 |
|---|---|---|
| 권한별 화면: `{% if this.user.is_staff %}`, property에서 `self.user.has_perm()` | 처음 렌더한 사람의 권한으로 모두가 본다 | 선언하지 않는다. 또는 권한마다 다른 클래스·토픽으로 나눈다 |
| 사용자별 데이터: "내 알림 3개", "내가 좋아요 누름" | 남의 숫자가 보인다 | 그 부분을 선언하지 않은 다른 컴포넌트로 뺀다 |
| 사용자 프로필의 언어·시간대·통화로 직접 포맷 | 키는 Django의 활성 언어·시간대만 본다. 프로필 필드를 템플릿이 읽으면 키에 없다 | 언어·시간대는 `translation.activate()`·`timezone.activate()`로 켠다(그러면 키가 가른다). 아니면 선언하지 않는다 |
| 쿼리 파라미터를 렌더에서 직접 읽음 (`self.wire.params`) | 키에 없다 | `params_changed()`에서 필드에 옮긴다. 필드는 키에 들어간다 |
| 연결마다 다른 비공개 속성(`_rows`)을 수신자가 채움 | 비공개 속성은 키에 없다 | 공개 필드에 둔다 |
| "현재 사용자"를 thread-local·contextvar로 읽는 property (django-crum류) | 처음 렌더한 연결의 사용자로 계산된다 | 선언하지 않는다 |
| `{% now %}`·현재 시각 | 메시지 하나를 처리하는 수백 ms 안에서는 같은 값이 간다. 대개 괜찮다 | 초 단위가 중요하면 필드로 |
| 업로드(`allow_upload`), 슬롯, LiveComponent를 그림 | 연결의 저장소와 업로드 상태를 그린다 | 범위 밖이다(아래). 공유되지 않는다 |

공유해도 결과가 같다는 확신이 없으면 선언하지 않는다. 선언하지 않은 컴포넌트에는 아무것도 바뀌지 않는다.

### 범위

다음 컴포넌트는 선언해도 공유하지 않는다. 렌더는 연결마다 그대로이고, 첫 렌더에서 서버 로그에 경고가 한 줄 남고,
`manage.py check`의 [`wireview.W019`](./checks.md)가 미리 알린다.

- `LiveComponent`. 그리는 쪽이 렌더와 수명주기를 정한다.
- `Meta.temporary_assigns`가 있는 컴포넌트. 연결마다 자기 화면에 남은 값으로 맞춘다([temporary_assigns](./temporary-assigns.md)).
- `Meta.slots`가 있는 컴포넌트. 그리는 페이지가 내용을 채운다.
- `Meta.live_sessions`가 있는 컴포넌트. 경계는 누가 보는가에 관한 것이다.
- 템플릿이 다른 컴포넌트·슬롯·업로드를 그리는 컴포넌트(`{% component %}`, `{% live_component %}`, `{% render_slot %}`,
  `{% upload_input %}` …).
- 템플릿이 `user`·`session`·`this.user`·`this.session`·`request`·`perms`·`csrf_token`·`messages`를 읽는 컴포넌트.
  같은 이름의 필드나 property를 가진 컴포넌트는 그것을 읽는 것이므로 빼고 본다.

정적으로 보이지 않는 것도 있다. 함수 컴포넌트가 다른 컴포넌트를 그리거나, `{% tag_header %}`가 두 번 나오면
첫 렌더가 그것을 알아채고 그 클래스는 그 뒤로 공유하지 않는다.

---

## 틀린 선언 잡기

### 시스템 체크

`manage.py check`가 위 [범위](#범위)를 `wireview.W019`로 알린다. 템플릿은 그 파일만 보고, `{% include %}`한 파일과
property 안은 보지 않는다.

### 렌더 때의 검사 (`VERIFY_SHARED_RENDER`)

[설정](./settings.md#개발-도구) `VERIFY_SHARED_RENDER`가 켜져 있으면 선언한 컴포넌트를 두 가지로 검사한다. 기본값
`None`은 `DEBUG`를 따르고, `wireview.testing`의 `render_diff()`에서는 언제나 켜진다. `False`로 끈다.

1. **보는 사람의 이름을 읽으면 오류다.** 렌더하는 동안 컴포넌트의 `user`와 `session`, 컨텍스트의 `request`·`perms`·
   `csrf_token`·`messages` 자리에 감시 객체를 둔다. 속성 접근·문자열 변환·진릿값 평가·비교에서
   `SharedRenderError`(`ImproperlyConfigured`의 하위 클래스)를 던진다. property 안의 `self.user`와 `{% include %}`한
   템플릿의 `{{ request.path }}`도 잡는다. 렌더 밖(핸들러, `joined()`, `notification()`)에서는 그대로 읽힌다.
2. **받은 렌더를 다시 렌더해 비교한다.** 다른 연결의 렌더를 받은 연결이 자기도 렌더해서, 둘이 다르면
   `SharedRenderError`를 던진다. 감시 이름을 거치지 않고 연결마다 달라지는 것(비공개 속성, `self.wire.params`,
   thread-local의 현재 사용자)을, **두 연결이 실제로 다른 값을 가질 때** 잡는다. 개발 중에 브라우저 두 개를 다른
   사용자로 열어 브로드캐스트해 보면 드러난다.

오류는 그 컴포넌트의 [오류 처리](./errors.md)를 탄다. 서버 로그에 무엇이 달랐는지(처음 다른 곳 앞뒤 40자)가
남는다.

켜 두면 받은 연결도 렌더하므로 공유로 아끼는 시간이 없다. 운영에서는 끈다(`DEBUG = False`면 기본으로 꺼진다).

### 잡지 못하는 것

- 감시 이름을 거치지 않는 읽기를, 테스트나 개발에서 **연결 하나로만** 돌렸을 때. 비교할 다른 렌더가 없다.
- 개발 환경에서 우연히 모두 같은 값이었던 것(사용자가 한 명뿐, 언어가 하나뿐).

그래서 테스트에서 사용자 둘을 만들어 보는 것이 가장 확실하다.

```python
from wireview import mount


async def test_the_scoreboard_does_not_read_the_viewer():
    board = await mount(Scoreboard, id="scoreboard", game_id=1)
    await board.render_diff()  # self.user를 읽으면 SharedRenderError
```

---

## 이름

`shared_render`는 **결과**를 이름으로 삼는다. 이 렌더는 보는 사람들 사이에서 공유된다. 선언하는 사람이 받아들여야
하는 것이 바로 그것이다. `viewer_independent`처럼 약속을 이름으로 삼으면 최적화라는 사실이 가려져, 아무 데나
붙여도 될 것처럼 읽힌다. 렌더 없이 같은 패치를 보내는 브로드캐스트(#178)와도 낱말이 겹치지 않는다. 그쪽은
`Broadcast`이고 발행하는 쪽이 고르며, 이것은 받는 컴포넌트가 선언한다.

## 렌더 없는 브로드캐스트와의 관계

둘은 서로를 대체하지 않는다. `shared_render`는 필드와 property에 기대어 **컴포넌트를 다시 그려야 하는**
브로드캐스트를 싸게 한다. 연결마다 수신자가 돌고 diff를 만드는 구조는 그대로다. 스트림 항목 추가처럼 서버가
추적하지 않는 DOM만 바꾸는 경우는 렌더 없이 같은 프레임을 보내는 쪽이 더 싸다(설계: #178).

## 관련 문서

- [성능](../PERFORMANCE.md#브로드캐스트를-많은-연결이-받을-때): 측정값
- [System Checks](./checks.md): `wireview.W019`
- [설정](./settings.md#개발-도구): `VERIFY_SHARED_RENDER`
- [서버 오류 처리](./errors.md)
- [temporary_assigns](./temporary-assigns.md)
- [설계와 측정](../design/broadcast-fanout.md): §4-A가 설계, §7이 결과다
