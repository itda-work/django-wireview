# 편집기 지원 (템플릿)

템플릿을 편집하는 동안 컴포넌트·핸들러·수정자·슬롯·태그·필터의 이름을 편집기가 알게 한다. 이름을 틀리면
페이지를 그려 볼 때가 아니라 치는 자리에서 안다.

두 갈래가 있다. 파이썬 쪽은 [타입 스텁](./type-stubs.md)이 맡는다 — 컴포넌트 클래스의 `.pyi`로 pyright·mypy와
IDE가 필드와 핸들러를 안다. 템플릿 쪽이 이 문서다. `manage.py wireview_lsp`가 프로젝트를 읽어 JSON 하나로 내고,
편집기가 그것을 읽는다. 이 저장소의 `editors/vscode/`에 VS Code 확장이 있고, 같은 진단을 CI에서 돌리는 것이
`manage.py wireview_check_templates`다.

## VS Code 확장

Marketplace에는 아직 올리지 않았다. 저장소에서 `.vsix`를 만들어 설치한다.

```bash
make ext-install    # editors/vscode 의 npm 의존성
make ext-package    # editors/vscode/dist/django-wireview-<버전>.vsix
code --install-extension editors/vscode/dist/django-wireview-0.1.0.vsix
```

할 수 있는 일, 설정, 알려진 한계는 확장의 [README](../../editors/vscode/README.md)에 있다. 요약하면:

- `**/templates/**/*.html`과, 메타데이터의 `template_dirs` 안에 있는 `.html`을 `django-html` 언어로 열고 구문
  강조·주석·자동 닫기·들여쓰기를 준다. `files.associations`가 `html`로 적은 파일과 사용자가 `html`로 되돌린
  문서는 그대로 둔다. 언어가 `html`이
  아니게 되어 잃는 HTML 기능(태그·속성 자동완성, 닫는 태그)은 HTML 언어 서비스로 되돌린다. 다른 Django 확장은
  필요 없다
- Django 태그·필터와 django-wireview의 컴포넌트·인자·이벤트·수정자·핸들러·슬롯·훅의 자동완성, 호버, 정의로 이동
- Django나 django-wireview가 렌더할 때 낼 오류의 진단. **확실하지 않으면 말하지 않는다** — 이 저장소의 모든
  템플릿이 진단 0건인 것을 `tests/test_vscode_extension.py`가 확인한다
- 파이썬 파일을 저장하면 메타데이터를 다시 만든다. 실패하면 마지막으로 성공한 것을 쓴다

신뢰하지 않은 워크스페이스(제한 모드)에서 확장은 프로세스를 하나도 띄우지 않고 메타데이터 파일도 읽지 않는다.
`wireview_lsp`를 돌리는 것은 프로젝트의 코드를 실행하는 일이고, 메타데이터에 적힌 경로는 정의로 이동이 여는
파일이기 때문이다. 구문 강조·스니펫·HTML 기능만 되고, 워크스페이스를 신뢰하는 순간 메타데이터를 만든다.

확장은 라이브러리의 공개 API가 아니고 버전을 따로 매긴다(`editors/vscode/package.json`). 라이브러리와 확장
사이의 약속은 아래 JSON 하나다.

## CI에서: `manage.py wireview_check_templates`

확장과 같은 진단을 편집기 없이 돌리고, 문제가 있으면 **실패로 끝난다**. CI와 코딩 에이전트가 "템플릿이 틀렸다"를
종료 코드로 받는 길이다.

```console
$ python manage.py wireview_check_templates
myapp/templates/myapp/board.html:12:31: error [unknown-handler] XBoard has no method 'incremnt'.
1 error in 24 templates
CommandError: templates have problems that fail the check
$ echo $?
1
```

```bash
python manage.py wireview_check_templates                      # 프로젝트의 템플릿 디렉터리 전부
python manage.py wireview_check_templates myapp/templates a.html  # 파일·디렉터리를 골라서
python manage.py wireview_check_templates --strict             # warning도 실패로
python manage.py wireview_check_templates --format json        # 진단의 JSON 그대로
python manage.py wireview_check_templates --node /opt/node/bin/node
```

**Node.js 22.18 이상이 필요하다.** 진단은 확장의 코드(`editors/vscode/src/core/`) 그대로이고 node가 TypeScript
소스를 그대로 실행한다 — npm 패키지는 필요 없다. 그 코드는 wheel에 함께 실린다(`wireview/template_diagnostics/`).
파이썬은 메타데이터(`wireview_lsp`)를 임시 파일로 만들어 넘길 뿐이다.

| 종료 코드 | 뜻 |
|----|----|
| `0` | error가 없다(`--strict`면 warning도 없다) |
| `1` | 문제가 있다. 무엇이 어디에 있는지 한 줄씩 찍는다 |
| `2` | 검사를 하지 못했다: node가 없거나 22.18보다 낮다, 경로가 없다, 템플릿이 하나도 없다, 진단이 죽었다 |

`2`를 `1`과 나눈 것은 CI가 "node가 낡았다"를 "템플릿이 틀렸다"로 읽지 않게 하려는 것이다. 템플릿을 하나도 찾지 못한
경로도 `2`다 — 잘못된 디렉터리를 가리킨 관문은 언제나 통과한다.

**error만 실패다.** error는 Django나 django-wireview가 렌더할 때 낼 오류(모르는 필터·핸들러·컴포넌트, 닫히지 않은
블록, load하지 않은 태그 …)이고, warning은 그렇지 않을 수 있는 것이다 — 어느 라이브러리도 등록하지 않은 태그
(`unknown-tag`), 컴포넌트에 없는 인자(`unknown-argument`), 빠진 필수 인자(`missing-argument`, 같은 id의 인스턴스가
이미 그 값을 들고 있으면 괜찮다), 핸들러에 없는 인자(`unknown-handler-argument`), 파일 시스템에 없는
템플릿(`template-not-found`, 다른 로더가 찾을 수 있다). `--strict`가 이것도 실패로 친다. information(훅 파일이
등록하지 않은 훅, `unknown-hook`)은 찍기만 하고 `--strict`에서도 실패로 치지 않는다.

경로를 주지 않으면 템플릿 엔진의 로더가 찾는 디렉터리(`template_dirs`) 가운데 **설치된 패키지(site-packages) 밖의
것**만 본다. Django admin이나 서드파티 앱의 템플릿은 그 패키지가 검사할 것이다.

Node가 없는 CI라면 `manage.py check`의 [System Checks](./checks.md)가 파이썬 쪽 함정만 본다. 템플릿은 보지 않는다.

## `manage.py wireview_lsp`

```bash
python manage.py wireview_lsp                          # stdout
python manage.py wireview_lsp --output metadata.json   # 파일
python manage.py wireview_lsp --pretty
```

Django를 띄워 등록된 컴포넌트, 함수 컴포넌트, 훅 파일, 템플릿 엔진을 읽는다. 템플릿은 컴파일하지 않는다 — 편집
중인 템플릿 하나의 구문 오류가 다른 모든 것을 가리지 않게 하려는 것이다.

### 버전

최상위의 `version`이 형식의 버전이다. **minor는 키를 더할 때, major는 있던 키의 뜻이나 모양을 바꿀 때 올린다.**
읽는 쪽은 major가 같고 minor가 자기가 아는 것 이상이면 읽는다. 확장은 1.1 이상의 1.x를 읽고, 2.0이면 확장을,
1.0이면 django-wireview를 올리라고 알린다. 규칙은 [호환성 정책](../COMPATIBILITY.md)에도 있다.

### 형식 1.1

1.0에 있던 키는 그대로다. **1.1**이라고 적은 것이 더해진 것이다.

최상위:

| 키 | 뜻 |
|----|----|
| `version` | 형식의 버전, `"1.1"` |
| `wireview_version` | **1.1** 이 JSON을 만든 django-wireview의 버전. 설치되지 않은 소스 트리면 `""` |
| `generated_at` | 만든 시각(UTC, ISO 8601) |
| `components` | 등록된 이름 → 컴포넌트(아래). 같은 이름이 둘이면 템플릿의 이름 찾기처럼 나중 것 |
| `function_components` | **1.1** `{% func %}`의 이름 → 함수 컴포넌트(아래) |
| `hooks` | **1.1** 훅 파일이 등록한 이름 → `static_path`(`<app_label>/hooks/x.js`), `file_path`, `line_number`(등록한 줄) |
| `modifiers` | `{% on %}`의 수정자 → `description`, `docstring`(1.0부터 있던 키, `description`과 같은 값), `has_argument`, **1.1** `argument`(`"number"`, `"text"`, `null`) |
| `template_dirs` | **1.1** 로더가 찾는 순서의 템플릿 디렉터리(`DIRS`와 앱의 `templates`). 절대 경로 |
| `template_builtins` | **1.1** 템플릿 엔진의 내장 태그·필터(아래의 라이브러리 모양) |
| `template_libraries` | **1.1** `{% load %}`의 이름 → 라이브러리: `module`, `file_path`, `tags`, `filters` |

컴포넌트:

| 키 | 뜻 |
|----|----|
| `name`, `fqn`, `app_key` | 템플릿이 찾는 세 이름: `Counter`, `myapp.live.Counter`, `myapp:Counter` |
| `module`, `file_path`, `line_number` | 클래스가 있는 곳 |
| `kind` | **1.1** `"component"` 또는 `"live_component"` |
| `docstring` | 클래스의 docstring |
| `template_name` | `Meta.template_name` |
| `template_path` | **1.1** 그 이름이 가리키는 디스크의 파일, 없으면 `null`. 템플릿 디렉터리를 순서대로 찾고, 심볼릭 링크를 푼 실제 경로를 적는다 |
| `fields` | 필드 → `type`, `annotation`(그 안 객체의 repr에서 메모리 주소를 지운다 — 실행마다 같은 출력이 나오게. 문자열은 그대로다), `default`, `required`, `description`, **1.1** `in_state`. `id`·`user`·`session`·`wire`는 빠진다. **1.1부터** `Meta.exclude_fields`의 필드도 실린다(`in_state: false`) — 템플릿이 넘기는 인자이기 때문이다 |
| `accepts_extra_kwargs` | **1.1** 사용자 클래스가 `new()`를(LiveComponent면 `update()`·`update_many()`도) 오버라이드했는가. 참이면 필드가 아닌 인자도 그 코드가 읽을 수 있다 |
| `properties` | **1.1** 사용자 클래스의 `property`·`cached_property` → `type`, `is_async`, `docstring`, `file_path`, `line_number` |
| `methods` | 메서드 → `is_handler`(클라이언트가 부를 수 있는가, `is_client_callable`과 같은 판정), `is_async`, `parameters`, `docstring`, `line_number`, **1.1** `file_path`(믹스인의 메서드는 믹스인의 파일) |
| `slots` | `Meta.slots` |
| `subscriptions`, `subscriptions_is_dynamic`, `temporary_assigns` | `Meta`의 그것. `get_subscriptions()`를 오버라이드하면 `subscriptions_is_dynamic` |

`parameters`는 이름 → `type`, `default`, `has_default`, `kind`(`POSITIONAL_OR_KEYWORD`, `KEYWORD_ONLY`,
`VAR_KEYWORD` …)이다. 함수 컴포넌트는 `name`, `fqn`, `module`, `file_path`, `line_number`, `docstring`,
`template_name`, `template_path`, `parameters`, `slots`를 갖는다.

라이브러리(`template_builtins`와 `template_libraries`의 값):

| 키 | 뜻 |
|----|----|
| `tags` | 태그 → `docstring`, `file_path`, `line_number`(태그를 등록한 함수. `simple_tag`는 감싼 사용자 함수), `end`, `intermediate` |
| `filters` | 필터 → `docstring`, `file_path`, `line_number`, `argument`(`"none"`, `"optional"`, `"required"`), `forbidden_in_filter_tag`(`{% filter %}`가 거절하는가. Django의 `do_filter`처럼 이름이 아니라 함수가 마지막으로 등록된 이름 `_filter_name`이 `escape`·`safe`인지로 정한다. 키가 없으면 편집기는 말하지 않는다) |

### 휴리스틱의 한계

Django는 블록 태그가 어디서 끝나는지 기록하지 않는다. 태그의 컴파일 함수가 `parser.parse(("else", "endif"))`처럼
멈출 이름을 넘길 뿐이다. 그래서 `end`(끝 태그)와 `intermediate`(사이의 태그)는 그 함수의 **소스에서 읽는다**:

- `parser.parse((...))`와 `parser.skip_past(...)`의 따옴표 리터럴. `end`로 시작하는 첫 이름이 끝 태그, 나머지가
  사이의 태그다
- 이름을 변수로 넘기면(`parser.parse(until)`) 소스의 `"end…"` 리터럴
- `simple_block_tag`는 그것이 기억하는 `end_name`
- Django의 `blocktranslate`(`blocktrans`)는 끝 태그 이름을 실행 중에 `end` + 태그를 부른 이름으로 만든다. 그 컴파일
  함수(`do_block_translate`)로 등록된 태그만 그렇게 읽는다 — 다른 라이브러리가 같은 이름으로 등록한 태그는 제
  소스대로 읽는다

읽지 못하면 `end`는 `null`이다. **`null`은 "블록이 아니다"의 증거가 아니다** — `{% load %}`도 `null`이고, 이름을
다른 함수에서 만드는 서드파티 블록 태그도 `null`이다. 확장은 `null`인 태그를 블록으로 다루지 않고, 모르는 `end…`
태그와 제자리를 벗어난 사이 태그에는 아무것도 말하지 않는다.

`template_dirs`는 `TEMPLATES`에 적힌 Django 엔진의 로더가 찾는 디렉터리다. Django 폼 렌더러의 템플릿
(`django/forms/...`)은 엔진이 아니라 렌더러가 따로 찾으므로 여기 없다 — Django 자신의 폼 위젯 템플릿을 열면
그 안의 `{% include %}`가 `template-not-found` 경고를 받을 수 있다.

필드의 `annotation`과 `default`는 실행마다 같도록 객체 repr의 메모리 주소(`<function f.<lambda> at 0x…>`의 ` at 0x…`)를
뺀다. 값 안의 문자열은 그대로 두지만, 객체 repr 안에 `<… at 0x…>` 모양의 문자열이 있으면 정규화될 수 있다
(`Label(text='<object at 0xCAFE>')`가 `Label(text='<object>')`로).

필터의 `argument`는 Django의 `FilterExpression.args_check`가 세는 방식으로 함수의 인자를 센다. `needs_autoescape`
필터가 받는 `autoescape`는 Django가 넘기는 것이라 세지 않는다.

## 다른 편집기

같은 JSON을 읽으면 된다. 확장이 하는 것을 따라 하려면:

1. 프로젝트의 파이썬으로 `manage.py wireview_lsp --output <파일>`을 돌리고, 파이썬 파일이 바뀌면 다시 돌린다.
   실패하면 지난 결과를 쓴다. 실행을 멈출 때는 그것이 띄운 프로세스도 멈춰야 한다 — `uv run` 같은 래퍼는 SIGTERM을
   자식에게 넘기지 않을 수 있다. 확장은 macOS·Linux에서 명령을 작은 `/bin/sh` 감독자 아래에서 돌린다. 감독자가 자기
   프로세스 그룹의 리더이고, 멈출 때 그 그룹에 SIGTERM을, 2초 뒤 SIGKILL을 보낸다. 감독자는 SIGTERM을 받으면 그
   SIGKILL까지 리더로 남으므로, 래퍼가 SIGTERM에 먼저 죽어도 그룹에 남은 프로세스까지 SIGKILL이 닿는다. 남아 있는
   데는 쉘 내장 명령만 쓰고 사용자의 PATH에서 프로그램을 찾지 않는다. 명령은 포그라운드로 돌리므로 SIGINT·SIGQUIT를
   받은 그대로 시작한다(백그라운드 작업은 둘을 무시한 채 시작한다). 신호는
   Node가 감독자의 종료를 아직 보고하지 않았을 때만 보낸다(번호가 다른 그룹의 것이 된 뒤에 보내지 않으려는 것이다).
   스스로 다른 그룹으로 옮겨 간 프로세스에는 닿지 않는다. Windows는 `taskkill /T /F`로 프로세스 트리를 끝내며,
   이미 끝난 프로세스가 띄운 것은 트리에서 찾지 못한다. 그 판단은 `editors/vscode/src/core/runner.ts`의
   `supervised`·`stopSteps`·`Stopper`에 있다
2. `version`의 major를 확인한다
3. 템플릿 파일의 실제 경로(심볼릭 링크를 푼 것)가 어떤 컴포넌트의 `template_path`와 같으면 그 컴포넌트가 템플릿의
   `this`다 — 핸들러와 변수를 거기서 찾는다
4. 태그가 보이는가는 `template_builtins`에 템플릿의 `{% load %}`를 **적힌 순서대로** 얹은 것이다. 나중 load가
   같은 이름을 덮어쓰고(`Parser.add_library`), load보다 앞에 쓴 태그·필터는 그 라이브러리를 아직 모른다

확장의 판단은 `editors/vscode/src/core/`의 순수 모듈에 있다(VS Code를 import하지 않는다). 다른 편집기의 플러그인이
그대로 가져다 쓸 수도 있다. `editors/vscode/scripts/diagnose.ts`가 VS Code 없이 그 모듈로 템플릿을 진단하는 예다
(`wireview_check_templates`가 부르는 것도 이것이다):

```bash
node editors/vscode/scripts/diagnose.ts [--strict] metadata.json myapp/templates
```

경로마다 문제 목록을 JSON으로 찍고, 종료 코드는 위 표와 같다(error면 `1`, `--strict`면 warning도, 사용법 오류는
`2`). node 자신이 죽어도 `1`로 끝나므로 JSON을 읽지 못하면 문제가 아니라 실패로 다룬다.
