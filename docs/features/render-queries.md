# 렌더의 SQL 찾기

개발 중에 렌더·핸들러·작업이 실행한 SQL을 **그것을 부른 템플릿 줄이나 property**와 함께 보여 준다. 서버
렌더에서 N+1을 가장 싸게 찾는 길이다. `{{ c.question.text }}`가 행마다 쿼리를 하나씩 낸다면, 그 줄이 몇 번
쿼리했는지가 로그 한 덩어리에 나온다. 설계와 그 근거는 [설계 메모](../design/render-part-queries.md)(#182)에 있다.

## 개요

Django Debug Toolbar는 HTTP 요청 하나의 쿼리를 보여 준다. 라이브 컴포넌트의 렌더는 요청이 아니라 소켓
메시지 안에서 돈다. 그리고 쿼리 목록만으로는 그 쿼리가 템플릿의 어느 줄에서 나왔는지 알 수 없다. 이 기능은
그 둘을 메운다.

- **어느 일의 쿼리인가.** 렌더, 핸들러, `joined()`, 마운트, `start_async`·`assign_async` 작업, 상태 서명을
  따로 센다. 동시에 도는 연결과 요청의 쿼리는 섞이지 않는다.
- **어디서 나왔나.** 템플릿의 파일:줄(상속한 템플릿이면 그 블록을 쓴 파일, include·함수 컴포넌트·슬롯 안이면
  그 파일), 또는 컨텍스트를 모으며 읽은 property의 이름이다.

## 켜기

설정 키 하나다. 기본값 `None`은 `DEBUG`를 따른다.

```python
WIREVIEW = {
    "DEBUG_RENDER_QUERIES": None,  # None = DEBUG. True·False로 고정
}
```

결과는 로거 `wireview.queries`로 나온다. 쿼리가 없는 일은 아무것도 남기지 않는다. 로그를 보려면 그 로거의
레벨을 `DEBUG`로 둔다.

```python
LOGGING = {
    "version": 1,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"wireview.queries": {"handlers": ["console"], "level": "DEBUG"}},
}
```

운영에서는 켜지 않는다. 켜도 쌓이는 것은 일 하나 동안의 목록뿐이지만, 쿼리마다 스택을 훑는 비용이 든다(아래 "비용").
운영 관측은 [telemetry](./telemetry.md)의 몫이다.

## 로그

일 하나(렌더·핸들러·작업 등)가 끝날 때 쿼리가 있었으면 한 덩어리를 남긴다. 다른 일 **안에서** 돈 일 —
템플릿이 그린 중첩 컴포넌트의 렌더, async property, 렌더가 청한 상태 서명, 받은 렌더의 검증 렌더 — 은 따로 남지
않고, 바깥 일의 덩어리에 대괄호로 표시된 행으로 들어간다. 그래서 덩어리는 가장 바깥 일마다 하나다. 같은 자리에서
같은 SQL이 **세 번 이상** 돌았으면 `WARNING`, 아니면 `DEBUG`다.

"같은 자리"는 템플릿 줄이면 그 파일:줄이다 — 어느 인스턴스가 그렸든 같은 줄이라, 행마다 중첩 컴포넌트가 같은
줄에서 쿼리하는 N+1도 잡힌다. property는 그것을 정의한 클래스와 이름이다(두 클래스의 `total`은 다른 자리). 템플릿
밖의 쿼리는 일의 종류·컴포넌트 클래스·이름(핸들러 이름, 작업 이름)이다.

```text
WARNING wireview.queries render Shelf#shelf (event bump): 12 queries, 1 repeated
  property total                                      1  SELECT COUNT(*) AS "__count" FROM "quiz_choice"
  property first_title                                1  SELECT … FROM "quiz_quiz" … LIMIT 1
  shelf/child.html:5  {{ }}                           1  SELECT COUNT(*) AS "__count" FROM "quiz_choice"
  shelf/owner.html:3  for  [render Owner#own (nested)]  1  SELECT … FROM "quiz_choice" …
  shelf/child.html:9  {{ }}  [render Owner#own (nested)]  3×  SELECT … FROM "quiz_question" WHERE … <- repeated
DEBUG wireview.queries handler Shelf#shelf.bump: 1 query
```

- 첫 줄은 일의 이름이다. 렌더는 왜 돌았는지(`join`, `event <핸들러>`, `hook <이벤트>`, `notification <채널>`,
  `http`, `stream item`)를 괄호에 적는다.
- 행의 위치는 셋 중 하나다. 템플릿 `파일:줄`과 노드 종류(`{{ }}`, `for`, `if`, `include` …), `property <이름>`,
  또는 템플릿 밖(`(outside a template)` — 핸들러나 `joined()`의 쿼리).
- 대괄호는 그 쿼리가 바깥 일 안의 다른 일에서 돌았다는 표시다. 위 예에서 `Owner`는 `Shelf`의 템플릿이 그린 중첩
  컴포넌트이고, `let:` 슬롯의 쿼리는 슬롯을 **채운** 파일의 줄로, 슬롯을 그린 `Owner`의 렌더 안에서 센다.
- 로그의 모양은 약속하지 않는다. 테스트는 아래 `queries()`로 단언한다.

## 테스트에서 단언하기

`mount()`가 돌려주는 `MountedComponent`의 `queries()`가 블록 안의 SQL을 모은다. 설정과 무관하게 돈다.

```python
from wireview import mount


async def test_the_shelf_has_no_n_plus_one():
    view = await mount(Shelf)
    async with view.queries() as q:
        await view.call("bump")
        await view.render_diff()
    assert q.count <= 5
    q.assert_no_repeats()  # 같은 자리·같은 SQL이 두 번 이상이면 실패. 메시지가 위 로그의 표다
```

공개는 `view.queries()`, `q.count`, `q.assert_no_repeats(threshold=2)` 셋이다. 행 목록과 실패 메시지의 모양은
공개가 아니다([호환성 정책](../COMPATIBILITY.md)).

- 블록 안에서 부른 핸들러·렌더, 그리고 블록 안에서 만든 작업 태스크가 **블록이 끝나기 전에** 실행한 SQL을 센다.
  블록이 끝난 뒤에 끝난 작업의 SQL은 버린다.
- 블록을 겹치면 안쪽에서 센 것을 바깥도 센다.
- 동시에 도는 다른 연결·테스트의 SQL은 들어오지 않는다.
- `view.render()`는 동기다. async property가 있는 컴포넌트는 `await sync_to_async(view.render)()`로 그린다.

## 어디까지 알 수 있나

**property는 템플릿 줄이 아니라 이름으로 나온다.** 렌더는 템플릿보다 먼저 컴포넌트의 공개 속성을 모두 읽어
컨텍스트에 넣는다. 그래서 property가 부른 쿼리는 템플릿에서 그 이름을 쓰는 줄이 아니라 `property <이름>`으로
나오고, **템플릿이 쓰지 않는 property도 렌더마다 읽힌다**([Component API](./component-api.md#렌더가-읽는-것)).
`{{ this.total }}`처럼 템플릿이 property를 다시 읽으면 그 줄에서 한 번 더 쿼리한다.

**템플릿 줄은 노드 자신의 위치다.** 쿼리가 실행되는 순간 스택에서 가장 안쪽 템플릿 노드(이름이 `render`나
`render_annotated`인 메서드의 `self`)를 찾아 그 노드의 `origin`과 `token.lineno`를 읽는다. 사용자 태그에 대한
약속은 이렇다.

| 노드 | 위치 |
|------|------|
| Django의 모든 노드, `render()`만 쓰는 사용자 태그, `render`나 `render_annotated`를 덮어쓴 노드 | 그 노드의 파일:줄. 정확하다 |
| 태그가 `render()` 안에서 만들어 부른 노드(파서가 위치를 주지 않음) | 그것을 부른 바깥 노드의 파일:줄, **`(approx.)`** 표시 |
| 진입 메서드를 다른 이름의 함수에 묶은 노드(`render_annotated = _impl`) | 가장 가까운 보이는 노드(예: 그것을 include한 파일의 줄). 그 노드의 파일이라는 보장이 없고 표시도 없다 |
| 템플릿 노드가 스택에 없음 | `(outside a template)` |

**보이지 않는 SQL.** 기록은 각 DB 연결의 `execute_wrappers` 맨 아래에 놓인 래퍼가 한다. 래퍼는 설정이 켜진 채
시작했을 때, `wireview.testing`을 import할 때, `queries()` 블록에 들어갈 때 놓이고, 한 번 놓이면 남는다. 그러므로
**계측을 켜기 전에 연결을 열었고, 블록에 들어갈 때 닿지 못하는 스레드**의 SQL은 보이지 않는다. 블록이 닿는
스레드는 부른 스레드와 async ORM이 쓰는 thread-sensitive 워커뿐이다. `sync_to_async(thread_sensitive=False)`의
풀 스레드, 앞서 처리된 다른 ASGI 요청의 워커, 직접 만든 스레드가 그 빈틈이다. `mount()`로 띄운 컴포넌트의
경로는 빈틈에 들지 않는다.

설정이 꺼져 있고 `wireview.testing`을 import하지 않은 프로세스에는 래퍼가 없다. 놓인 래퍼는 아무것도 모으지
않을 때 SQL마다 `ContextVar`를 읽고, 거기 아무것도 없으면 열린 블록이 있는지와 설정을 한 번 더 본다(모으는 중이면
미귀속으로 센다). 경계(렌더·핸들러 등)도 래퍼와 무관하게 설정을 본다. 다른 도구의 래퍼(`connection.execute_wrapper()`,
Debug Toolbar)와 `CaptureQueriesContext`는 그대로 함께 돈다.

설정이 꺼져 있으면 모으는 것은 **아직 열려 있는** `queries()` 블록뿐이다. 블록 안에서 만든 작업이 블록이 끝난 뒤에도
돌면, 그 뒤의 SQL은 어디에도 쌓이지 않고 로그도 남지 않는다. 바깥 블록이 아직 열려 있으면 그쪽이 계속 모은다.

## 비용

켠 상태에서 템플릿이 실행한 쿼리 하나에 스택을 훑는 비용이 붙는다. 구현 후 같은 프로세스에서 끔과 켬을 번갈아
잰 값은 템플릿 SQL 하나에 약 7~20 µs, 쿼리가 없는 렌더는 0~7 µs였다([설계 메모 §4-4](../design/render-part-queries.md#4-4-구현-후-재측정-3판-규칙-182-12단계)).
끈 상태가 기능 전과 비교해 얼마인지는 재지 못했다(측정하던 기계의 부하로 차이가 잡음에 묻혔다). 끈 상태에도 경계마다
설정 조회가, 래퍼가 놓였으면 SQL마다 위의 확인이 남으므로 0은 아니다.

## 관련 기능

- [테스트 헬퍼](./testing.md)
- [성능](../PERFORMANCE.md#n1-찾기)
- [Telemetry](./telemetry.md): 운영 지표
- [설정](./settings.md)
