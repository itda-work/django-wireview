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

## 편집기로 보내기

개발 서버는 같은 귀속을 파일에도 쓴다. 편집기가 그것을 읽어 템플릿 줄과 property의 `def` 줄 끝에 쿼리 수를 단다.
VS Code 확장이 그 표시를 맡는다([편집기에서 보기](#편집기에서-보기)). 설계와 그 근거는
[설계 메모](../design/render-queries-editor.md)(#188)에 있다. 이 절이 파일 형식의 정본이다.

### 켜기와 끄기

아래가 모두 참일 때만 쓴다. 일이 시작할 때와 끝날 때 두 번 묻는다. 그 사이에 하나라도 꺼지면 쓰지 않는다.

1. 수집이 설정으로 켜져 있다(`DEBUG_RENDER_QUERIES`, `None` = `DEBUG`). `queries()` 블록이 모으는 것은 쓰지 않는다.
2. Django의 `DEBUG`가 참이다. `DEBUG_RENDER_QUERIES=True`를 적어도 `DEBUG=False`면 쓰지 않는다.
3. 억제되지 않았다. 환경 변수 `WIREVIEW_RENDER_QUERIES_DIR=off`가 있으면 쓰지 않는다. 그 프로세스가 띄운 자식 프로세스도
   환경을 물려받으므로 함께 꺼진다. `wireview.testing`을 import한 프로세스도 쓰지 않는다.
4. 위치가 정해진다. 설정 `DEBUG_RENDER_QUERIES_DIR`이 `False`가 아니고, 경로이거나, `None`이면서 `BASE_DIR`이 있다.

```python
WIREVIEW = {
    "DEBUG_RENDER_QUERIES_DIR": None,  # None = BASE_DIR/.wireview/render-queries, 경로, 또는 False(끔)
}
```

테스트가 별도 프로세스로 띄우는 개발 서버(E2E의 `runserver`)는 `wireview.testing`을 import하지 않는다. 그런 서버를
띄우는 테스트 실행은 환경 변수로 끈다. 끄지 않아도 파일은 아래 디렉터리 안에만 생긴다.

**지원하는 서버는 동시에 쓰는 프로세스가 하나인 개발 서버다**(`runserver`, `uvicorn` 단일 프로세스, daphne). 워커를 여럿
띄운다면 워커마다 `DEBUG_RENDER_QUERIES_DIR`을 나누거나 끈다. 같은 디렉터리에 여러 프로세스가 쓰면 고친 뒤의 0건이 옛 숫자를
지운다는 보장이 없다.

### 파일

- 디렉터리는 기본으로 `BASE_DIR/.wireview/render-queries/`다. 처음 만들 때 권한을 `0700`으로 두고, `*` 한 줄짜리
  `.gitignore`를 넣는다. 그래서 프로젝트가 `.wireview/`를 무시하지 않아도 커밋되지 않는다.
- 프로세스마다 `<UTC 시작 시각>-<pid>.<n>.jsonl`에 쓴다. 시작 시각은 `YYYYmmddTHHMMSS`이고 `n`은 1부터다. 파일 권한은
  `0600`이다.
- 파일은 덧붙이기만 한다. 1 MiB를 넘으려 하면 `n+1`로 넘어가고 `n-1`을 지운다. 그래서 프로세스마다 지금 것과 바로 앞
  것만 남는다. 쓰던 파일이 지워지면 다음 번호로 새로 연다.
- 새 파일을 열 때 다른 프로세스의 파일을 정리한다. 10분 넘게 바뀌지 않은 것만 대상이고, 24시간 넘은 것은 지우며, 그런
  파일이 32개를 넘으면 오래된 것부터 지운다. 디렉터리의 크기는 보장하지 않는 목표다.
- 한 줄은 잠금 아래 끝까지 쓴다. 다 쓰지 못하면 그 줄을 되돌리고, 그 프로세스는 더 쓰지 않으며(그때 잠금을 기다리던 다른
  스레드도. 줄을 준비하다 실패해도 같다), `wireview.queries`에 WARNING을 한 번 남긴다. 렌더는 그대로 진행된다. 템플릿의 지문을 만들지 못하면 그 행만
  `source` 없이 쓴다. SQL은 그대로 실행된다.

### 무엇을 쓰나

가장 바깥 일 하나가 한 줄이다. 그 안에서 끝난 **렌더**마다 스냅샷이 들고, 쿼리가 없던 렌더도 들어간다. 읽는 쪽은 컴포넌트
클래스마다 가장 최근의 스냅샷 하나를 현재 상태로 둔다. 그래서 고쳐서 쿼리가 없어진 컴포넌트는 다음 렌더가 그 숫자를
지운다. 다른 컴포넌트 안에서 그려졌든 혼자 그려졌든 같다.

- 렌더가 없는 일(핸들러, 작업, `mount`, `joined()`, 서명, `Broadcast` 항목)은 쓰지 않는다. 렌더가 든 일 안의 렌더 밖
  행은 `by` 없이 싣는다.
- 한 클래스의 관측이 그 클래스에 대해 마지막으로 쓴 줄이 말한 것과 같으면(위치·SQL·횟수·지문·완전한지까지) 60초 안에는 다시
  쓰지 않는다. 바이트 상한으로 잘려 쓰였던 클래스는 새 줄에 실제로 담길 것으로 비교하므로, 온전히 다시 관측되면 다시 쓴다. 그 프로세스에서 처음 보는 클래스는 0건이어도
  쓴다.

### 형식 1.0

한 줄이 JSON 객체 하나다(UTF-8, 64 KiB 이하).

```json
{"version": "1.0", "at": "2026-10-08T13:10:05.573Z", "process": "20261008T131003-80673", "segment": 1,
 "base": "/home/me/proj", "kind": "render", "detail": "event bump", "count": 7,
 "renders": [{"kind": "render", "component": "shelf.live.Shelf", "name": "Shelf", "id": "shelf", "why": "event bump"},
             {"kind": "render", "component": "shelf.live.Badge", "name": "Badge", "id": "b1", "why": "nested"}],
 "rows": [
  {"by": 0, "count": 6, "repeated": true, "sql": "SELECT … WHERE \"quiz_question\".\"id\" = %s LIMIT 21",
   "template": {"file": "/home/me/proj/shelf/templates/shelf/child.html", "rel": "shelf/templates/shelf/child.html",
                "name": "shelf/child.html", "source": "3e3d7c29…", "line": 2, "node": "{{ }}", "text": "c.question.text"}},
  {"by": 0, "count": 1, "sql": "SELECT COUNT(*) AS \"__count\" FROM \"quiz_choice\"",
   "property": {"name": "total", "owner": "shelf.live.Shelf", "async": false, "file": "/home/me/proj/shelf/live.py",
                "rel": "shelf/live.py", "line": 43, "stat": ["1791465001123456789", "4211"]}}]}
```

| 키 | 뜻 |
|----|----|
| `version` | 형식의 버전 `"major.minor"` |
| `at` | 일이 끝난 시각, UTC ISO 8601(밀리초) |
| `process`, `segment` | 쓴 프로세스와 세그먼트 번호. 파일 이름과 같다 |
| `base` | 서버가 본 `BASE_DIR`의 실제 경로. 없으면 `null` |
| `kind`, `detail` | 가장 바깥 일의 종류(`render`, `handler` …)와 이름. 정보일 뿐이다 |
| `count` | 그 일이 실행한 SQL 전체 수. 상한 때문에 뺀 행도 센다: `count = rows[].count의 합 + more.statements` |
| `renders` | 그 안에서 끝난 렌더. 바깥 일이 렌더면 그것이 0번이다. 각각 `kind`, `component`(`module.Qualname`), `name`, `id`, `why`(`join`, `event <핸들러>`, `http`, `nested` …). 100개까지 |
| `renders_more` | 100개를 넘어 뺀 렌더의 수 |
| `rows[].by` | 그 행을 낸 렌더의 `renders` 위치. 없으면 렌더 밖의 행이다 |
| `rows[].count`, `repeated` | 같은 자리·같은 SQL의 횟수. 한 줄 안에서 세 번 이상이면 `repeated: true` |
| `rows[].sql` | 실행한 문장. 파라미터는 없다(`%s`). 1,000자에서 자르고 `truncated: true` |
| `rows[].template` | `file`(실제 경로. 파일 시스템의 템플릿일 때만), `rel`(`base` 아래면), `name`, `source`, `line`, `node`(`{{ }}` 또는 태그 이름), `text`(`token.contents`, 200자까지), 근사면 `approximate: true` |
| `rows[].template.source` | 그 줄을 실행한 컴파일본의 원본 지문: `sha256`(줄바꿈을 `\n`으로 바꾼 UTF-8). Django의 filesystem·app_directories 로더(cached로 감싼 것 포함)의 템플릿이고 실행 중인 `Template`을 찾았을 때만 있다 |
| `rows[].property` | `name`, `owner`(정의한 클래스), `async`, 그리고 위치가 확정되면 `file`·`rel`·`line`(`def` 줄)·`stat`(`[st_mtime_ns, st_size]`, **10진 문자열**). 그 파일이 라이브러리를 import한 뒤 바뀌었으면 위치를 싣지 않는다 |
| `rows[].in` | 그 행이 렌더 안의 다른 일(async property, 서명)에서 돌았으면 그 `kind`·`detail` |
| `more` | 64 KiB에 맞추려고 뺀 행: `{"groups": …, "statements": …}` |
| `partial` | 스냅샷이 완전하지 않은 클래스(`component` 값)의 목록. 그 클래스의 렌더나 행이 상한 때문에 빠졌다. 읽는 쪽은 그 클래스의 스냅샷을 "0건"으로 읽으면 안 된다 |

**줄이 맞다는 근거.** `source`가 디스크 파일의 지문과 같으면, 서버가 실행한 원본이 지금 그 파일이고 `line`은 그 파일의
줄이다. 개발 서버가 캐시된 옛 컴파일본을 실행했으면 지문이 다르다. property의 위치는 그만큼 강하지 않다. 보증하는 것은
"모듈을 로드한 뒤 그 파일이 바뀌지 않았고(`stat`이 같다), 그 줄에 그 `def`가 있다"까지다.

**버전.** 키를 더하면 minor, 있던 키의 뜻이나 모양을 바꾸면 major를 올린다. 파일 이름과 "덧붙이기만 하고 번호를 올린다"는
규칙도 이 버전에 든다. 읽는 쪽은 줄마다 판정하고, 자기가 아는 major이고 minor가 그 이상인 줄만 읽으며, 모르는 키는 무시한다.

**한계.**

- 렌더가 끝난 뒤 따로 그려지는 노드리스트에는 `source`가 없다. `live_component_block`의 `let:` 슬롯을 라이브로 그릴 때가
  그렇다. HTTP 렌더에서는 있다.
- 위 로더가 만든 `Origin`을 사용자 코드가 다른 `Template`에 넘기면 지문이 그 노드의 것이 아닐 수 있다.
- `RawSQL`·`extra()`가 문장에 넣은 리터럴은 파일에 남는다.

### 편집기에서 보기

[VS Code 확장](./editor-support.md)이 이 파일을 읽어 그 줄 끝에 inlay hint로 수를 단다. 템플릿 줄에는 `6 queries`,
한 자리에서 같은 문장이 세 번 이상이면 `⚠ 6× same query`, property의 `def` 줄에는 `1 query per render`다. 한 줄에
여러 컴포넌트 클래스의 렌더가 있으면 합과 클래스 수(`7 queries · 2 components`)를 단다. tooltip에는 문장과 횟수,
컴포넌트와 id, 몇 분 전의 렌더인지가 나온다. Problems에는 넣지 않는다. N+1은 렌더 오류가 아니고, 관측은 시간이
지나면 낡는다.

확장은 아래가 모두 참일 때만 숫자를 보인다. 엉뚱한 줄에 붙은 숫자는 없는 것보다 나쁘다.

- 문서가 저장된 상태다. 고치는 동안에는 그 파일의 숫자를 감추고, 저장한 뒤 다시 보인다.
- 템플릿 행: `source`가 디스크 파일의 지문과 같고, 그 줄에 기록된 `{{ … }}`나 `{% … %}`가 있다. 개발 서버가 옛
  컴파일본을 실행했으면 다음 렌더가 새 지문을 쓸 때까지 그 파일에는 아무것도 보이지 않는다. 자동 리로더가 있는
  개발 서버에서는 템플릿 리로드가 곧바로 다시 그린다.
- property 행: 디스크 파일의 `[mtime_ns, size]`가 `stat`과 문자열로 정확히 같고, 그 줄에 그 이름의 `def`가 있다.
- 그 클래스의 마지막 줄이 `wireview.renderQueries.maxAge`분(기본 30) 안이다. 서버는 같은 결과도 1분마다 다시 쓰므로
  계속 그려지는 컴포넌트는 만료되지 않는다.
- 두 프로세스가 같은 디렉터리에 동시에 쓰고 있지 않다. 확장은 프로세스마다 읽은 기록의 `at` 구간을 보고, 두 구간이
  엄격히 겹치면 그 폴더에서 아무것도 보이지 않고 출력 채널에 이유를 한 번 적는다. 감지는 최선 노력이다. 순차
  재시작은 겹침으로 보지 않지만, 서버가 같은 결과를 생략한 탓에 실제로 겹친 두 writer를 놓칠 수는 있다.

**읽는 법.** 확장이 시작할 때 이미 있던 파일은 프로세스마다 가장 높은 세그먼트의 끝 512 KiB만 읽는다. 그 뒤에 생긴
세그먼트는 처음부터 읽고, 다음 번호가 보이면 앞 번호를 끝까지 읽고 넘어간다. 번호가 건너뛰었거나 읽던 세그먼트가
끝까지 읽기 전에 사라졌으면 그 프로세스가 정한 숫자를 모두 버린다. 그 사이에 0건 스냅샷이 있었을 수 있기 때문이다.
프로세스의 파일이 모두 사라졌다가 그 프로세스가 다음 번호로 다시 쓰기 시작해도 같다. 렌더가 모두 상한 밖으로 밀려
`partial`에만 이름이 있는 클래스는 그 시각의 빈 부분 스냅샷이 되어 옛 숫자를 감춘다.
줄바꿈으로 끝나지 않은 끝 조각은 다음 읽기까지 기다린다. 모르는 major의 줄은 버리고 출력 채널에 한 번 적는다.

**설정.** `wireview.renderQueries.enable`, `directory`(이 문서의 `DEBUG_RENDER_QUERIES_DIR`을 바꿨을 때, 폴더 기준),
`maxAge`, `mapRelative`. 표는 [편집기 지원](./editor-support.md)에 있다. `Wireview: Clear Render Queries`는 확장이
읽은 숫자를 잊는다. 파일은 서버의 것이라 지우지 않는다. 신뢰하지 않은 워크스페이스에서는 파일을 읽지 않는다.

**컨테이너의 서버.** 기록의 `file`은 서버가 본 경로다. 편집기가 같은 환경(Remote)에서 돌면 그대로 맞는다. 편집기가
바깥에 있으면 `mapRelative`를 켠다. 확장은 `rel`을 `manage.py`의 디렉터리 아래에서도 찾는다. 이때 지문이 보증하는
것은 "그 자리의 파일이 서버가 실행한 원본과 내용이 같다"까지다. 같은 내용의 다른 파일이 그 자리에 있으면 그 파일에
숫자가 붙는다.

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
