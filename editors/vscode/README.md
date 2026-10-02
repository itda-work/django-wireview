# Django Wireview

Django 템플릿을 편집하는 데 필요한 것을 이 확장 하나로 준다. 다른 Django 확장은 깔지 않아도 된다.
[django-wireview](https://github.com/itda-work/django-wireview) 프로젝트라면 컴포넌트·핸들러·슬롯까지
알아듣는다 — 이름을 틀리면 페이지를 그려 보기 전에 편집기가 먼저 말한다.

## 기능

**템플릿 언어.** `**/templates/**/*.html` 파일을 `django-html` 언어로 연다. 이름이 `templates`가 아닌 디렉터리라도
프로젝트의 템플릿 엔진이 찾는 곳(`TEMPLATES`의 `DIRS`, 환경 변수로 정한 것 포함)이면, 메타데이터를 읽은 뒤 그
안의 `.html`도 `django-html`로 바꾼다(`wireview.associateTemplateDirs`). 구문 강조는 텍스트, 속성 값,
`<script>` 안의 `{% %}`·`{{ }}`·`{# #}`를 모두 칠한다. `{% comment %}`와 `{% verbatim %}` 안은 태그로 보지 않는다.
`Ctrl+/`가 `{# #}`로 주석을 단다. `{%`를 치면 `%}`가, `{#`를 치면 `#}`가 붙고, 블록 태그 다음 줄은 들여 쓰고
`{% endif %}`·`{% else %}`는 다시 내어 쓴다.

**HTML은 그대로.** 언어가 `html`에서 바뀌어도 태그·속성 자동완성, 호버, 닫는 태그 자동 삽입, 짝 태그 이름 동시
수정, 접기가 그대로 된다(`vscode-html-languageservice`). Emmet도 켜 둔다. `wire-hook` 같은 `wire-*` 속성도
속성 목록에 나온다.

**Django 태그와 필터.** 목록을 확장이 갖고 있지 않고 프로젝트의 템플릿 엔진에서 읽는다. 그래서 설치된 Django
버전의 내장 태그와, `{% load %}`할 수 있는 모든 라이브러리(서드파티 포함)가 그대로 나온다.

- 자동완성: 태그 이름(블록 태그는 끝 태그까지 한 번에, 감싸는 블록이 기다리는 `endif`·`else`가 맨 위), `{% load %}`의
  라이브러리, `|` 뒤의 필터, `{% extends %}`·`{% include %}`의 템플릿 경로, 변수(필드, `{% for %}`·`{% with %}`의 이름)
- 호버와 정의로 이동: 태그·필터는 그것을 등록한 파이썬 함수로, 라이브러리는 그 모듈로, 템플릿 경로는 그 파일로
- 접기: 블록 태그마다(`{% if %}`부터 `{% else %}`까지, 거기서 `{% endif %}`까지)

**django-wireview.**

- 자동완성: `{% component %}`·`{% live_component %}`·`{% func %}`(와 `_block`)의 이름과 인자, `{% on %}`의 이벤트·수정자·
  핸들러·핸들러 인자, `{% fill %}`의 슬롯과 `let:`, `{% render_slot %}`, `wire-hook`, `wire-viewport-*`
- 호버와 정의로 이동: 컴포넌트 → 클래스, 인자 → 필드 선언, 핸들러 → 메서드, 훅 → JS 파일의 등록 줄,
  변수 → 필드나 프로퍼티. 파이썬 파일의 `template_name = "todo/list.html"` 같은 문자열은 템플릿으로 가는 링크가 된다
- 스니펫: 템플릿(`wv-template`, `wv-component-block`, `wv-form`, `wv-upload` …)과 파이썬(`wv-component`,
  `wv-handler`, `wv-function-component`, `wv-test` …)

**진단.** Django나 django-wireview가 템플릿을 읽거나 그릴 때 낼 오류만 미리 말한다. 확실하지 않으면 말하지
않는다 — 변수로 넘긴 이름은 보지 않고, 메타데이터가 없으면 아무것도 말하지 않는다.

| 코드 | 무엇 |
|------|------|
| `unknown-component`, `not-a-live-component`, `live-component-needs-id`, `unknown-function-component` | 없는 컴포넌트, `live_component`에 일반 컴포넌트, `id` 없는 LiveComponent |
| `unknown-argument`, `missing-argument` | 필드가 아닌 인자, 넘기지 않은 필수 필드(경고) |
| `unknown-handler`, `not-a-handler`, `unknown-handler-argument` | 템플릿을 그리는 컴포넌트에 없는 핸들러, 클라이언트가 부를 수 없는 메서드, 핸들러가 받지 않는 인자 |
| `invalid-event`, `unknown-modifier`, `modifier-argument` | `{% on %}`의 이벤트 이름과 수정자 |
| `missing-slot` | 채우지 않은 필수 슬롯 |
| `unknown-hook` | 훅 파일이 등록하지 않은 `wire-hook` 이름(정보) |
| `tag-not-loaded`, `unknown-tag`, `unknown-filter`, `filter-not-loaded`, `filter-argument`, `filter-not-permitted`, `unknown-library` | `{% load %}`하지 않은(또는 그 `{% load %}`보다 앞에 쓴) 태그·필터, 없는 태그·필터·라이브러리, 필터 인자 개수, `{% filter %}`가 거절하는 `escape`·`safe`(그 이름으로 마지막에 등록된 함수) |
| `unclosed-block`, `unmatched-end` | 닫지 않은 블록, 여는 태그 없는 끝 태그 |
| `template-not-found` | 템플릿 디렉터리에 없는 `{% extends %}`·`{% include %}` 경로(경고) |

## 요구 사항

- VS Code 1.100 이상
- 프로젝트의 django-wireview가 메타데이터 형식 1.1을 낸다: `python manage.py wireview_lsp`의 출력에
  `"version": "1.1"`이 있으면 된다. 낮으면 상태 표시줄과 출력 채널이 django-wireview를 올리라고 알린다.
  django-wireview가 없는 Django 프로젝트에서도 구문 강조·HTML 기능·스니펫은 쓸 수 있다

## 동작

확장은 워크스페이스 폴더마다 `manage.py`를 찾아 `python manage.py wireview_lsp --output <파일>`을 돌리고, 그
JSON(프로젝트의 컴포넌트, 템플릿 디렉터리, 태그·필터)을 읽는다. 출력 파일은 프로젝트가 아니라 확장의 저장소에
쓴다. 파이썬 파일을 저장하면 다시 돌린다. 실패하면(Django가 뜨지 않음, 편집 중인 파일의 구문 오류) 마지막으로
성공한 결과를 그대로 쓰고, 상태 표시줄에 경고를 띄운다. 상태 표시줄을 누르면 출력 채널이 열린다.

명령 팔레트: `Wireview: Refresh Project Metadata`, `Wireview: Go to Component…`, `Wireview: Show Output`.

**제한 모드(Restricted Mode).** 신뢰하지 않은 워크스페이스에서는 아무 프로세스도 띄우지 않고 메타데이터 파일도
읽지 않는다 — `manage.py`를 돌리는 것은 그 프로젝트의 코드를 실행하는 것이고, 메타데이터의 경로는 정의로 이동이
가는 곳이기 때문이다. 지난 세션이 남긴 결과도 쓰지 않는다. 그동안 구문 강조·스니펫·HTML 기능은 그대로 되고,
상태 표시줄에 `Restricted Mode`가 뜬다. 워크스페이스를 신뢰하면 그때 메타데이터를 만든다. 실행에 쓰이는 설정
(`pythonPath`, `managePy`, `metadataCommand`, `metadataPath`)은 신뢰하기 전에는 워크스페이스의 값을 따르지 않는다.

## 설정

| 설정 | 기본값 | 뜻 |
|------|--------|-----|
| `wireview.pythonPath` | `""` | `manage.py`를 돌릴 파이썬. 비우면 Python 확장이 고른 인터프리터, 그다음 `.venv`, 그다음 `python3` |
| `wireview.managePy` | `""` | `manage.py`의 경로(폴더 기준). 비우면 가장 얕은 것 |
| `wireview.metadataCommand` | `[]` | 인터프리터만으로 부족할 때 명령 전체. 예: `["uv", "run", "python", "manage.py", "wireview_lsp"]`. `--output <파일>`은 확장이 붙인다 |
| `wireview.metadataPath` | `""` | 명령을 돌리지 않고 이 파일을 읽고 감시한다. 다른 무엇이 `manage.py wireview_lsp --output`으로 쓴다 |
| `wireview.refreshOnSave` | `true` | 파이썬 파일을 저장하면 다시 돌린다 |
| `wireview.associateTemplateDirs` | `true` | 템플릿 엔진이 찾는 디렉터리 안의 `.html`을 `django-html`로 연다 |
| `wireview.diagnostics.enable` | `true` | 진단 |
| `wireview.html.enable` | `true` | `django-html`의 HTML 자동완성·호버·닫는 태그 |

## 알려진 한계

- **`templates` 디렉터리 아래의 `.html`과 템플릿 엔진이 찾는 디렉터리 안의 `.html`은 `django-html`이 된다.**
  Django 템플릿이 아닌 HTML이 거기 있으면 설정의 `files.associations`로 `html`이라고 적는다:
  `{"**/templates/static-site/**/*.html": "html"}`. 확장은 `html`로 적힌 파일은 바꾸지 않고, 한 번 바꾼 문서를
  언어 선택기로 `html`로 되돌리면 그 뒤로는 건드리지 않는다. 엔진이 찾지 않는 곳에서 읽는 템플릿(따로 `Engine`을
  만들어 읽는 메일 템플릿 등)은 `{"**/emails/**/*.html": "django-html"}`처럼 더한다. 디렉터리를 보고 바꾸는 것은
  메타데이터를 읽은 뒤라, 제한 모드에서는 `**/templates/**`만 적용된다. `html` 언어로 연 파일도 템플릿 디렉터리
  안에 있으면 진단은 받는다.
- **Django가 컨테이너 안에서만 돈다면** VS Code를 Remote(Dev Containers, SSH, WSL)로 그 안에서 연다. 메타데이터의
  경로는 Django가 보는 경로라, 바깥에서 연 편집기의 경로와 맞지 않는다.
- 블록 태그의 끝 태그는 태그 함수의 소스에서 읽는다. 읽지 못한 서드파티 블록 태그는 블록으로 다루지 않는다 —
  그 끝 태그와 중간 태그에 대해서는 아무것도 말하지 않으므로 거짓 경고는 없지만, 접기와 끝 태그 자동완성은 없다.
- 핸들러 진단은 그 템플릿을 `template_name`으로 쓰는 컴포넌트가 있을 때만 한다. `{% include %}`되는 조각과
  스트림 항목 템플릿은 누가 그리는지 알 수 없어서 보지 않는다(자동완성은 모든 컴포넌트의 핸들러를 낸다).
- `wire-*` 속성은 브라우저가 HTML을 읽는 규칙대로 시작 태그에서만 읽는다(주석·`<script>`·끝 태그 안은 속성이 아니고,
  같은 이름이 두 번이면 첫 것만). 어디서 끝나는지 확실하지 않은 곳 — SVG·MathML 안의 `<![CDATA[`, `<!--`와
  `<script`를 품은 `<script>`, SVG의 `<foreignObject>` 안의 HTML — 을 만나면 그 뒤의 속성은 보지 않는다.
  `&#97;` 같은 문자 참조가 든 값도 보지 않는다.
- 메타데이터 형식은 [docs/features/editor-support.md](../../docs/features/editor-support.md)에 있다. 다른 편집기도
  같은 JSON을 읽을 수 있다.
