# 편집기 지원 (템플릿)

템플릿을 편집하는 동안 컴포넌트·핸들러·수정자·슬롯·태그·필터의 이름을 편집기가 알게 한다. 이름을 틀리면
페이지를 그려 볼 때가 아니라 치는 자리에서 안다.

두 갈래가 있다. 파이썬 쪽은 [타입 스텁](./type-stubs.md)이 맡는다 — 컴포넌트 클래스의 `.pyi`로 pyright·mypy와
IDE가 필드와 핸들러를 안다. 템플릿 쪽이 이 문서다. `manage.py wireview_lsp`가 프로젝트를 읽어 JSON 하나로 내고,
편집기가 그것을 읽는다. 이 저장소의 `editors/vscode/`에 VS Code 확장이 있다.

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
| `filters` | 필터 → `docstring`, `file_path`, `line_number`, `argument`(`"none"`, `"optional"`, `"required"`) |

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

필터의 `argument`는 Django의 `FilterExpression.args_check`가 세는 방식으로 함수의 인자를 센다. `needs_autoescape`
필터가 받는 `autoescape`는 Django가 넘기는 것이라 세지 않는다.

## 다른 편집기

같은 JSON을 읽으면 된다. 확장이 하는 것을 따라 하려면:

1. 프로젝트의 파이썬으로 `manage.py wireview_lsp --output <파일>`을 돌리고, 파이썬 파일이 바뀌면 다시 돌린다.
   실패하면 지난 결과를 쓴다
2. `version`의 major를 확인한다
3. 템플릿 파일의 실제 경로(심볼릭 링크를 푼 것)가 어떤 컴포넌트의 `template_path`와 같으면 그 컴포넌트가 템플릿의
   `this`다 — 핸들러와 변수를 거기서 찾는다
4. 태그가 보이는가는 `template_builtins`에 템플릿의 `{% load %}`를 **적힌 순서대로** 얹은 것이다. 나중 load가
   같은 이름을 덮어쓰고(`Parser.add_library`), load보다 앞에 쓴 태그·필터는 그 라이브러리를 아직 모른다

확장의 판단은 `editors/vscode/src/core/`의 순수 모듈에 있다(VS Code를 import하지 않는다). 다른 편집기의 플러그인이
그대로 가져다 쓸 수도 있다. `editors/vscode/scripts/diagnose.ts`가 VS Code 없이 그 모듈로 템플릿을 진단하는 예다:

```bash
node editors/vscode/scripts/diagnose.ts metadata.json myapp/templates
```
