# 템플릿

모든 템플릿 맨 위에 `{% load wireview %}`.

## 태그

| 태그 | 용도 |
|---|---|
| `{% wireview_header %}` | 베이스 템플릿 `<head>`에 한 번. JS를 로드한다 |
| `{% tag_header %}` | **컴포넌트 템플릿의 루트 엘리먼트에 필수.** id·상태·이벤트가 붙는 자리 |
| `{% component 'Name' id="x" foo=bar %}` | 컴포넌트를 심는다 |
| `{% component_block 'Card' %}…{% endcomponent %}` + `{% fill name %}`, `{% render_slot %}` | 슬롯 |
| `{% live_component "Counter" id=... %}` + `{% live_tag_header %}` | LiveComponent (부모 소유 중첩 컴포넌트) |
| `{% live_component_block "Modal" id=... %}…{% endlive_component %}` + `{% fill name %}` | LiveComponent에 슬롯 전달 |
| `{% func "button" text="OK" %}`, `{% func_block "card" %}…{% endfunc %}` | 상태 없는 함수 컴포넌트 |
| `{% on 'click' 'handler' arg=1 %}` | 이벤트 바인딩 |
| `{% cond {"checked": is_done} %}` | 조건부 불리언 속성 |
| `{% class {"selected": showing == 'all'} %}` | 조건부 클래스 |
| `{% upload_input "images" %}`, `{% upload_drop_zone %}`, `{% upload_button %}`, `{% upload_preview entry %}` | 파일 업로드 |

필터: `|str`, `|concat:item.id`.

컴포넌트 안에서는 필드를 그대로 쓴다(`{{ amount }}`). 컴포넌트 인스턴스 자체는 `this`다
(`{% if not this.items %}`) — `@property`처럼 필드가 아닌 것은 `this`로 접근한다.

## 이벤트 바인딩

```html
<button {% on 'click' 'inc' %}>+</button>
<a {% on 'click.prevent' 'show' showing='all' %}>All</a>
<input {% on 'keypress.enter' 'add' %} name="new_item" />
<input {% on 'input.debounce.300' 'search' %} name="query" />
```

- 첫 인자는 `이벤트.수정자.수정자`, 둘째는 핸들러 이름, 나머지 kwargs는 핸들러 인자로 간다.
- 폼 요소의 `name` 속성은 같은 이름의 핸들러 인자로 전달된다.
- 수정자: `prevent`, `stop`, `debounce.<ms>`, `throttle.<ms>`, `key.<name>`, `key_code.<n>`,
  단축키 `enter`, `tab`, `delete`, `backspace`, `escape`, `space`, `arrowup`, `arrowdown`, `arrowleft`, `arrowright`.
- 중첩 컴포넌트에서 자기 자신을 대상으로 하려면 `myself=True`.

## 슬롯

```html
{% component_block "Card" id="c1" %}
  {% fill header %}<h1>제목</h1>{% endfill %}
  <p>본문</p>
  {% fill footer %}<button>저장</button>{% endfill %}
{% endcomponent %}
```

컴포넌트 쪽에서 `_slots = {"header": {"required": False}}`로 선언하고, 템플릿에서
`{% if slots.header %}{% render_slot "header" %}{% endif %}`, 이름 없는 본문은 `{% render_slot %}`.

## 클라이언트 DOM 속성

서버 렌더 HTML에 직접 쓰는 속성들.

| 속성 | 용도 |
|---|---|
| `wire-hook` | JavaScript Hook 연결 (Chart.js, 지도 등) |
| `wire-stream` | Streams가 아이템을 넣을 컨테이너 |
| `wire-viewport-top` / `wire-viewport-bottom` | 무한 스크롤 |
| `wire-disabled-with` | 요청 중 버튼 비활성화 + 대체 문구 |
| `wire-feedback-for` / `wire-no-feedback` | 폼 검증 오류 표시 시점 제어 |
| `wire-auto-recover` | 재연결 시 입력값 복원 |
| `wire-flash` | 플래시 메시지 표시 자리 |
| `wire-upload-drop` / `wire-preview` | 업로드 드롭존·미리보기 |

로딩 중에는 `wireview-click-loading` 계열 클래스가 붙는다. CSS로 스피너를 붙이면 된다.

## 부분 diff를 죽이지 않으려면

- 서버는 템플릿의 **동적 파트만** 골라 보낸다. `{% tag_header %}`의 서명 상태도 동적 파트다.
- `WIREVIEW["USE_HMIN"]`을 켜면 HTML 주석 기반 diff 마커가 지워져 부분 diff가 토큰 diff로 퇴화한다
  (`manage.py check`의 `wireview.W005`). 켤 거면 대역폭 손익을 실측한다.
- 상세: https://github.com/itda-work/django-wireview/blob/main/docs/features/html-diff.md

## 관련 정본

- 슬롯: https://github.com/itda-work/django-wireview/blob/main/docs/features/slots.md
- 함수 컴포넌트: https://github.com/itda-work/django-wireview/blob/main/docs/features/function-components.md
- JavaScript Hooks: https://github.com/itda-work/django-wireview/blob/main/docs/features/hooks.md
- 폼 피드백: https://github.com/itda-work/django-wireview/blob/main/docs/features/form-feedback.md
- 낙관적 UI: https://github.com/itda-work/django-wireview/blob/main/docs/features/optimistic-ui.md
