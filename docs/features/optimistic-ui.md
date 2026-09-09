# Optimistic UI

서버 왕복이 끝나기 전에 사용자에게 반응을 돌려주는 장치들이다. 로딩 클래스, `wire-disabled-with`,
그리고 즉시 실행되는 JS 명령 셋으로 나뉜다.

## 로딩 클래스

이벤트가 발생하면 그것을 일으킨 엘리먼트에 로딩 클래스가 자동으로 붙는다.

```html
<button {% on "click" "save" %} class="btn">저장</button>
```

서버 왕복이 진행되는 동안 이 버튼이 갖는 클래스는 다음과 같다.

| 클래스 | 언제 |
|--------|------|
| `wireview-loading` | 항상 |
| `wireview-click-loading` | click 이벤트 |
| `wireview-submit-loading` | 폼 제출 |
| `wireview-change-loading` | change 이벤트 |

### 로딩 상태 꾸미기

```css
/* 로딩 중 흐리게 */
.wireview-loading {
  opacity: 0.7;
  cursor: wait;
}

/* 스피너 아이콘 */
.wireview-click-loading::after {
  content: "";
  display: inline-block;
  width: 1em;
  height: 1em;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: spin 0.75s linear infinite;
  margin-left: 0.5em;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
```

## wire-disabled-with

`wire-disabled-with`는 세 가지를 한다.

1. 클릭 즉시 엘리먼트를 비활성화한다
2. 버튼 텍스트를 로딩 문구로 바꾼다
3. 작업이 끝나면 원래 상태로 되돌린다

### 기본 사용

```html
<button
  {% on "click" "save" %}
  wire-disabled-with="저장 중..."
>
  저장
</button>
```

클릭하면 버튼이 `disabled`가 되고 텍스트가 "저장 중..."으로 바뀌었다가, 서버 응답이 오면 "저장"으로
돌아오며 다시 활성화된다.

### 폼 제출

```html
<form {% on "submit" "create_post" %}>
  <input type="text" name="title" placeholder="글 제목">
  <textarea name="content"></textarea>

  <button
    type="submit"
    wire-disabled-with="글 만드는 중..."
  >
    글 쓰기
  </button>
</form>
```

### 아이콘과 함께 (Tailwind/Heroicons)

```html
<button
  {% on "click" "delete_item" id=item.id %}
  wire-disabled-with="삭제 중..."
  class="flex items-center gap-2"
>
  <svg class="w-4 h-4"><!-- trash icon --></svg>
  삭제
</button>
```

**주의.** `wire-disabled-with`는 **텍스트 콘텐츠만** 교체한다. 로딩 중에도 아이콘을 유지해야 하면
이 속성 대신 CSS 기반 로딩 표시를 쓴다.

### 로딩 클래스와 함께

둘은 겹쳐 쓸 수 있다.

```html
<button
  {% on "click" "process" %}
  wire-disabled-with="처리 중..."
  class="btn"
>
  데이터 처리
</button>
```

```css
/* 버튼을 흐리게 하고 커서를 바꾼다 */
.btn.wireview-loading {
  opacity: 0.6;
  cursor: wait;
}
```

## 즉시 반응은 JS 명령으로

서버를 기다리지 않고 UI를 바로 바꾸려면 JS 명령을 쓴다.

```html
<button
  {% on "click" "toggle_menu" %}
  wire-disabled-with="여는 중..."
  onclick="{{ JS().toggle_class(target='#menu', names='hidden') }}"
>
  메뉴 토글
</button>
```

`JS()` 명령은 그 자리에서 실행되고, 서버 왕복에 대한 피드백은 `wire-disabled-with`가 맡는다.

## 권장 사항

1. **의미 있는 문구를 쓴다.** "로딩 중..."보다 "저장 중..."이 낫다
2. **짧게 쓴다.** 긴 문구는 레이아웃을 흔든다
3. **동작을 그대로 반영한다.** 버튼이 하는 일과 로딩 문구를 맞춘다
4. **시각적 신호를 더한다.** opacity·cursor를 바꾸는 CSS와 함께 쓴다

### 로딩 문구 예

| 버튼 | 로딩 문구 |
|------|-----------|
| 저장 | 저장 중... |
| 삭제 | 삭제 중... |
| 제출 | 제출 중... |
| 계정 만들기 | 계정 만드는 중... |
| 메시지 보내기 | 보내는 중... |
| 장바구니에 담기 | 담는 중... |

## Phoenix LiveView 대응

| Phoenix LiveView | django-wireview |
|-----------------|-----------------|
| `phx-disable-with` | `wire-disabled-with` |
| `phx-click-loading` | `wireview-click-loading` |
| `phx-submit-loading` | `wireview-submit-loading` |
| `phx-change-loading` | `wireview-change-loading` |

## 응답 시간 측정

내장 프로파일링으로 실제 왕복 시간을 잴 수 있다.

```javascript
// 브라우저 콘솔에서 켠다
wireview.debug.enableProfiling();

// 페이지를 조작한 뒤...

// 리포트를 본다
wireview.debug.profilingReport();

// 끈다
wireview.debug.disableProfiling();
```

리포트에 담기는 것:

- **patch 시간** — DOM morph를 적용하는 데 걸린 시간
- **왕복 시간** — 이벤트를 보낸 시점부터 응답을 받은 시점까지
- **통계** — 항목별 최소·최대·평균·중앙값

## 관련

- [JS 명령](../implementation/js-commands.md)
- [튜토리얼 02 — Counter 컴포넌트](../tutorials/02-counter-component.md)
