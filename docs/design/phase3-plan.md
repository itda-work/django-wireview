# Phase 3: Navigation & Form Enhancement 계획

> 2025-12-09 작성

---

## 요약

| 항목 | 내용 |
|------|------|
| **Epic** | #47 |
| **마일스톤** | v6.0.0-beta.1 |
| **예상 기간** | 1-2주 |
| **선행 조건** | Phase 2 완료 ✅ |

---

## 현황 분석

### 이미 완료된 기능

| GAP | 기능 | 상태 |
|-----|------|------|
| GAP-004 | handle_params (params_changed) | ✅ 완료 |

### 남은 작업

| GAP | 기능 | 난이도 | 예상 기간 | 우선순위 |
|-----|------|:------:|:--------:|:--------:|
| GAP-010 | Page Title | 하 | 2-3시간 | P0 |
| GAP-011 | Flash Messages | 하 | 4-6시간 | P0 |
| GAP-008 | Form Auto-Recovery | 중 | 1-2일 | P1 |

---

## GAP-010: Page Title

### 개요

페이지 타이틀을 동적으로 변경하는 기능입니다.

### Phoenix LiveView 참조

```elixir
# 서버
socket = assign(socket, :page_title, "Product List")

# 레이아웃
<title><%= assigns[:page_title] || "My App" %></title>
```

### 제안 API

```python
class ProductList(Component):
    async def joined(self):
        await self.push_title(f"Products - Page {self.page}")

    async def next_page(self):
        self.page += 1
        await self.push_title(f"Products - Page {self.page}")
```

### 구현 계획

1. **Component 메서드 추가** (`core/component.py`)
   ```python
   async def push_title(self, title: str) -> None:
       """Update the page title dynamically."""
       await self.wire.send("title", title=title)
   ```

2. **JavaScript 핸들러 추가** (`wireview.js`)
   ```javascript
   title(payload) {
       document.title = payload.title;
   }
   ```

3. **테스트**
   - 단위 테스트: push_title이 wire.send 호출
   - E2E 테스트: document.title 변경 확인

### 예상 작업량

- 구현: 1시간
- 테스트: 1시간
- 문서: 30분

---

## GAP-011: Flash Messages

### 개요

일회성 알림 메시지를 표시하는 기능입니다.

### Phoenix LiveView 참조

```elixir
# 서버
socket = put_flash(socket, :info, "Saved successfully!")
socket = put_flash(socket, :error, "Failed to save")
socket = clear_flash(socket)

# 템플릿
<.flash_group flash={@flash} />
```

### 제안 API

```python
class ProductForm(Component):
    async def save(self):
        try:
            await Product.objects.acreate(...)
            await self.put_flash("success", "Product saved!")
        except Exception as e:
            await self.put_flash("error", str(e))
```

```html
<!-- 레이아웃 또는 컴포넌트 템플릿 -->
<div id="flash-container" wire-flash></div>

<!-- 또는 수동 렌더링 -->
{% for flash in this.flashes %}
    <div class="alert alert-{{ flash.type }}">
        {{ flash.message }}
        <button {% on "click" "clear_flash" flash_id=flash.id %}>×</button>
    </div>
{% endfor %}
```

### 구현 계획

1. **Component 메서드 추가** (`core/component.py`)
   ```python
   async def put_flash(self, type: str, message: str) -> None:
       """Add a flash message."""
       await self.wire.send("flash", type=type, message=message)

   async def clear_flash(self, flash_id: str | None = None) -> None:
       """Clear flash message(s)."""
       await self.wire.send("clear_flash", flash_id=flash_id)
   ```

2. **JavaScript 핸들러 추가** (`wireview.js`)
   ```javascript
   flash(payload) {
       const container = document.querySelector('[wire-flash]');
       if (!container) return;

       const id = `flash-${Date.now()}`;
       const el = document.createElement('div');
       el.id = id;
       el.className = `wireview-flash wireview-flash-${payload.type}`;
       el.innerHTML = `
           <span>${payload.message}</span>
           <button onclick="this.parentElement.remove()">×</button>
       `;
       container.appendChild(el);

       // Auto-dismiss after 5s
       setTimeout(() => el.remove(), 5000);
   }

   clear_flash(payload) {
       if (payload.flash_id) {
           document.getElementById(payload.flash_id)?.remove();
       } else {
           document.querySelectorAll('.wireview-flash').forEach(el => el.remove());
       }
   }
   ```

3. **CSS 스타일 (선택적)**
   ```css
   .wireview-flash { /* 기본 스타일 */ }
   .wireview-flash-success { background: #d4edda; }
   .wireview-flash-error { background: #f8d7da; }
   .wireview-flash-info { background: #d1ecf1; }
   .wireview-flash-warning { background: #fff3cd; }
   ```

### 예상 작업량

- 구현: 2시간
- 테스트: 2시간
- 문서: 1시간

---

## GAP-008: Form Auto-Recovery

### 개요

WebSocket 재연결 시 폼 상태를 자동으로 복구하는 기능입니다.

### Phoenix LiveView 참조

```elixir
# 자동으로 처리됨
# phx-auto-recover="restore_form"으로 커스텀 복구 가능
```

```html
<form phx-change="validate" phx-submit="save" phx-auto-recover="restore">
  ...
</form>
```

### 제안 API

```html
<!-- 자동 복구 활성화 -->
<form {% on "submit" "save" %} wire-auto-recover>
    <input name="title" value="{{ this.title }}">
    <textarea name="content">{{ this.content }}</textarea>
</form>

<!-- 커스텀 복구 핸들러 -->
<form {% on "submit" "save" %} wire-auto-recover="restore_draft">
    ...
</form>
```

```python
class ArticleEditor(Component):
    async def restore_draft(self, form_data: dict):
        """Custom recovery handler."""
        self.title = form_data.get("title", "")
        self.content = form_data.get("content", "")
```

### 구현 계획

1. **JavaScript: 폼 상태 저장** (`wireview.js`)
   ```javascript
   // 연결 끊김 감지 시 폼 상태 저장
   saveFormState() {
       document.querySelectorAll('[wire-auto-recover]').forEach(form => {
           const formData = new FormData(form);
           const key = `wireview-form-${form.closest('[wireview-component]').id}`;
           sessionStorage.setItem(key, JSON.stringify(Object.fromEntries(formData)));
       });
   }

   // 재연결 시 복구
   restoreFormState() {
       document.querySelectorAll('[wire-auto-recover]').forEach(form => {
           const key = `wireview-form-${form.closest('[wireview-component]').id}`;
           const saved = sessionStorage.getItem(key);
           if (saved) {
               const data = JSON.parse(saved);
               Object.entries(data).forEach(([name, value]) => {
                   const input = form.querySelector(`[name="${name}"]`);
                   if (input) input.value = value;
               });

               // 커스텀 핸들러 호출
               const handler = form.getAttribute('wire-auto-recover');
               if (handler && handler !== 'true') {
                   wireview.send(form, handler, data);
               }

               sessionStorage.removeItem(key);
           }
       });
   }
   ```

2. **연결 이벤트에 훅 추가**
   ```javascript
   // WebSocket 연결 끊김 시
   connection.onclose = () => {
       this.saveFormState();
   };

   // 재연결 완료 시
   connection.onopen = () => {
       this.restoreFormState();
   };
   ```

3. **테스트**
   - E2E: 연결 끊김 → 재연결 → 폼 상태 유지 확인

### 예상 작업량

- 구현: 4시간
- 테스트: 3시간
- 문서: 1시간

---

## 구현 순서

### 권장 순서

```
1. GAP-010: Page Title (가장 간단, 2-3시간)
   ↓
2. GAP-011: Flash Messages (독립적, 4-6시간)
   ↓
3. GAP-008: Form Auto-Recovery (가장 복잡, 1일)
```

### 이유

1. **Page Title**: 가장 간단하고 독립적. 빠르게 완료 가능.
2. **Flash Messages**: Page Title과 유사한 패턴. 학습 효과.
3. **Form Auto-Recovery**: 가장 복잡하지만 다른 기능에 의존하지 않음.

---

## 테스트 전략

| 기능 | 단위 테스트 | E2E 테스트 |
|------|:----------:|:----------:|
| Page Title | ✓ | ✓ |
| Flash Messages | ✓ | ✓ |
| Form Auto-Recovery | - | ✓ (필수) |

### E2E 테스트 계획

```python
@pytest.mark.e2e
class TestPhase3:
    def test_push_title_updates_document_title(self, page):
        """push_title이 document.title을 업데이트하는지"""
        pass

    def test_flash_message_appears_and_auto_dismisses(self, page):
        """flash 메시지가 표시되고 자동으로 사라지는지"""
        pass

    def test_form_recovery_on_reconnect(self, page):
        """재연결 시 폼 상태가 복구되는지"""
        pass
```

---

## 문서화 계획

| 문서 | 내용 |
|------|------|
| docs/features/page-title.md | Page Title 사용법 |
| docs/features/flash-messages.md | Flash Messages 사용법 |
| docs/features/form-recovery.md | Form Auto-Recovery 사용법 |
| CLAUDE.md | API 레퍼런스 업데이트 |
| docs/FEATURE-GAP.md | 완료 상태 업데이트 |

---

## 성공 기준

- [ ] push_title로 페이지 타이틀 동적 변경
- [ ] put_flash로 알림 메시지 표시
- [ ] 5초 후 자동 dismiss
- [ ] wire-auto-recover로 폼 상태 복구
- [ ] 모든 E2E 테스트 통과
- [ ] 문서화 완료

---

## 다음 단계

Phase 3 완료 후:
1. 이슈 #47 닫기
2. Phase 4 (Performance & Developer Experience) 검토
3. v0.6.0-beta 릴리스 준비
