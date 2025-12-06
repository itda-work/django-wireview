# JS 명령어 시스템 구현 가이드

> Phoenix LiveView.JS 스타일 클라이언트 명령어 시스템

---

## 1. 개요

### 1.1 목표

서버 왕복 없이 클라이언트에서 즉시 실행되는 DOM 조작 명령어 시스템

### 1.2 Phoenix LiveView.JS 참조

```elixir
# Phoenix LiveView 예시
<button phx-click={JS.toggle(to: "#modal") |> JS.push("save")}>
  저장
</button>
```

### 1.3 django-reactor 목표

```html
<!-- 목표 문법 -->
<button {% on "click" JS().toggle("#modal").push("save") %}>
  저장
</button>
```

---

## 2. Python 측 구현

### 2.1 JS 클래스

```python
# reactor/features/js.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Self
import json


@dataclass
class JS:
    """
    Phoenix LiveView.JS 스타일 클라이언트 명령어 빌더

    Usage:
        JS().show("#modal").push("save")
        JS().toggle("#dropdown").add_class("#btn", "active")
    """

    commands: list[dict[str, Any]] = field(default_factory=list)

    # ─────────────────────────────────────────────────────────────
    # Visibility
    # ─────────────────────────────────────────────────────────────

    def show(
        self,
        selector: str,
        transition: str | None = None,
        time: int = 200,
        display: str | None = None,
    ) -> Self:
        """요소 표시"""
        cmd = {"op": "show", "to": selector, "time": time}
        if transition:
            cmd["transition"] = transition
        if display:
            cmd["display"] = display
        self.commands.append(cmd)
        return self

    def hide(
        self,
        selector: str,
        transition: str | None = None,
        time: int = 200,
    ) -> Self:
        """요소 숨기기"""
        cmd = {"op": "hide", "to": selector, "time": time}
        if transition:
            cmd["transition"] = transition
        self.commands.append(cmd)
        return self

    def toggle(
        self,
        selector: str,
        transition_in: str | None = None,
        transition_out: str | None = None,
        time: int = 200,
        display: str | None = None,
    ) -> Self:
        """요소 표시/숨기기 토글"""
        cmd = {"op": "toggle", "to": selector, "time": time}
        if transition_in:
            cmd["in"] = transition_in
        if transition_out:
            cmd["out"] = transition_out
        if display:
            cmd["display"] = display
        self.commands.append(cmd)
        return self

    # ─────────────────────────────────────────────────────────────
    # CSS Classes
    # ─────────────────────────────────────────────────────────────

    def add_class(
        self,
        selector: str,
        classes: str,
        transition: str | None = None,
        time: int = 200,
    ) -> Self:
        """CSS 클래스 추가"""
        cmd = {
            "op": "add_class",
            "to": selector,
            "classes": classes.split() if isinstance(classes, str) else classes,
            "time": time,
        }
        if transition:
            cmd["transition"] = transition
        self.commands.append(cmd)
        return self

    def remove_class(
        self,
        selector: str,
        classes: str,
        transition: str | None = None,
        time: int = 200,
    ) -> Self:
        """CSS 클래스 제거"""
        cmd = {
            "op": "remove_class",
            "to": selector,
            "classes": classes.split() if isinstance(classes, str) else classes,
            "time": time,
        }
        if transition:
            cmd["transition"] = transition
        self.commands.append(cmd)
        return self

    def toggle_class(
        self,
        selector: str,
        classes: str,
        transition: str | None = None,
        time: int = 200,
    ) -> Self:
        """CSS 클래스 토글"""
        cmd = {
            "op": "toggle_class",
            "to": selector,
            "classes": classes.split() if isinstance(classes, str) else classes,
            "time": time,
        }
        if transition:
            cmd["transition"] = transition
        self.commands.append(cmd)
        return self

    # ─────────────────────────────────────────────────────────────
    # Attributes
    # ─────────────────────────────────────────────────────────────

    def set_attribute(
        self,
        selector: str,
        attr: str,
        value: str,
    ) -> Self:
        """속성 설정"""
        self.commands.append({
            "op": "set_attr",
            "to": selector,
            "attr": attr,
            "value": value,
        })
        return self

    def remove_attribute(
        self,
        selector: str,
        attr: str,
    ) -> Self:
        """속성 제거"""
        self.commands.append({
            "op": "remove_attr",
            "to": selector,
            "attr": attr,
        })
        return self

    # ─────────────────────────────────────────────────────────────
    # Focus
    # ─────────────────────────────────────────────────────────────

    def focus(self, selector: str) -> Self:
        """요소에 포커스"""
        self.commands.append({"op": "focus", "to": selector})
        return self

    def focus_first(self, selector: str) -> Self:
        """첫 번째 입력 요소에 포커스"""
        self.commands.append({"op": "focus_first", "to": selector})
        return self

    # ─────────────────────────────────────────────────────────────
    # Server Events
    # ─────────────────────────────────────────────────────────────

    def push(
        self,
        event: str,
        value: dict[str, Any] | None = None,
        target: str | None = None,
        loading: str | None = None,
    ) -> Self:
        """서버로 이벤트 전송"""
        cmd = {"op": "push", "event": event}
        if value:
            cmd["value"] = value
        if target:
            cmd["target"] = target
        if loading:
            cmd["loading"] = loading
        self.commands.append(cmd)
        return self

    # ─────────────────────────────────────────────────────────────
    # DOM Events
    # ─────────────────────────────────────────────────────────────

    def dispatch(
        self,
        event: str,
        to: str | None = None,
        bubbles: bool = True,
        **detail: Any,
    ) -> Self:
        """커스텀 DOM 이벤트 발생"""
        cmd = {
            "op": "dispatch",
            "event": event,
            "bubbles": bubbles,
        }
        if to:
            cmd["to"] = to
        if detail:
            cmd["detail"] = detail
        self.commands.append(cmd)
        return self

    # ─────────────────────────────────────────────────────────────
    # Navigation
    # ─────────────────────────────────────────────────────────────

    def navigate(self, url: str, replace: bool = False) -> Self:
        """페이지 이동"""
        self.commands.append({
            "op": "navigate",
            "url": url,
            "replace": replace,
        })
        return self

    def patch(self, url: str, replace: bool = False) -> Self:
        """URL 변경 (LiveView 내 이동)"""
        self.commands.append({
            "op": "patch",
            "url": url,
            "replace": replace,
        })
        return self

    # ─────────────────────────────────────────────────────────────
    # Transitions
    # ─────────────────────────────────────────────────────────────

    def transition(
        self,
        selector: str,
        transition: str,
        time: int = 200,
    ) -> Self:
        """CSS 트랜지션 실행"""
        self.commands.append({
            "op": "transition",
            "to": selector,
            "transition": transition,
            "time": time,
        })
        return self

    # ─────────────────────────────────────────────────────────────
    # Serialization
    # ─────────────────────────────────────────────────────────────

    def to_json(self) -> str:
        """JSON 문자열로 변환"""
        return json.dumps(self.commands)

    def __str__(self) -> str:
        return self.to_json()

    def __repr__(self) -> str:
        return f"JS({self.commands!r})"

    def __bool__(self) -> bool:
        return bool(self.commands)
```

### 2.2 템플릿 태그 통합

```python
# reactor/templatetags/reactor.py에 추가

from django import template
from django.utils.safestring import mark_safe
from ..features.js import JS

register = template.Library()

@register.simple_tag
def on(event: str, handler, **kwargs):
    """
    이벤트 바인딩 템플릿 태그

    Usage:
        {% on "click" "increment" %}
        {% on "click" JS().toggle("#modal").push("save") %}
    """
    # JS 객체인 경우
    if isinstance(handler, JS):
        js_commands = handler.to_json()
        return mark_safe(
            f'data-reactor-event="{event}" '
            f'data-reactor-js=\'{js_commands}\''
        )

    # 문자열 핸들러인 경우 (기존 방식)
    args_str = " ".join(f'data-{k}="{v}"' for k, v in kwargs.items())
    return mark_safe(
        f'data-reactor-event="{event}" '
        f'data-reactor-handler="{handler}" '
        f'{args_str}'
    )
```

---

## 3. TypeScript 측 구현

### 3.1 타입 정의

```typescript
// src/types.ts

export type JSCommand =
  | ShowCommand
  | HideCommand
  | ToggleCommand
  | AddClassCommand
  | RemoveClassCommand
  | ToggleClassCommand
  | SetAttrCommand
  | RemoveAttrCommand
  | FocusCommand
  | FocusFirstCommand
  | PushCommand
  | DispatchCommand
  | NavigateCommand
  | PatchCommand
  | TransitionCommand;

interface ShowCommand {
  op: "show";
  to: string;
  time?: number;
  transition?: string;
  display?: string;
}

interface HideCommand {
  op: "hide";
  to: string;
  time?: number;
  transition?: string;
}

interface ToggleCommand {
  op: "toggle";
  to: string;
  time?: number;
  in?: string;
  out?: string;
  display?: string;
}

interface AddClassCommand {
  op: "add_class";
  to: string;
  classes: string[];
  time?: number;
  transition?: string;
}

interface RemoveClassCommand {
  op: "remove_class";
  to: string;
  classes: string[];
  time?: number;
  transition?: string;
}

interface ToggleClassCommand {
  op: "toggle_class";
  to: string;
  classes: string[];
  time?: number;
  transition?: string;
}

interface SetAttrCommand {
  op: "set_attr";
  to: string;
  attr: string;
  value: string;
}

interface RemoveAttrCommand {
  op: "remove_attr";
  to: string;
  attr: string;
}

interface FocusCommand {
  op: "focus";
  to: string;
}

interface FocusFirstCommand {
  op: "focus_first";
  to: string;
}

interface PushCommand {
  op: "push";
  event: string;
  value?: Record<string, unknown>;
  target?: string;
  loading?: string;
}

interface DispatchCommand {
  op: "dispatch";
  event: string;
  to?: string;
  bubbles?: boolean;
  detail?: Record<string, unknown>;
}

interface NavigateCommand {
  op: "navigate";
  url: string;
  replace?: boolean;
}

interface PatchCommand {
  op: "patch";
  url: string;
  replace?: boolean;
}

interface TransitionCommand {
  op: "transition";
  to: string;
  transition: string;
  time?: number;
}
```

### 3.2 명령어 실행기

```typescript
// src/commands.ts

import type { JSCommand } from "./types";

export class JSCommandExecutor {
  private componentEl: HTMLElement;

  constructor(componentEl: HTMLElement) {
    this.componentEl = componentEl;
  }

  async execute(commands: JSCommand[]): Promise<void> {
    for (const cmd of commands) {
      await this.executeCommand(cmd);
    }
  }

  private async executeCommand(cmd: JSCommand): Promise<void> {
    switch (cmd.op) {
      case "show":
        await this.show(cmd);
        break;
      case "hide":
        await this.hide(cmd);
        break;
      case "toggle":
        await this.toggle(cmd);
        break;
      case "add_class":
        await this.addClass(cmd);
        break;
      case "remove_class":
        await this.removeClass(cmd);
        break;
      case "toggle_class":
        await this.toggleClass(cmd);
        break;
      case "set_attr":
        this.setAttr(cmd);
        break;
      case "remove_attr":
        this.removeAttr(cmd);
        break;
      case "focus":
        this.focus(cmd);
        break;
      case "focus_first":
        this.focusFirst(cmd);
        break;
      case "push":
        this.push(cmd);
        break;
      case "dispatch":
        this.dispatch(cmd);
        break;
      case "navigate":
        this.navigate(cmd);
        break;
      case "patch":
        this.patch(cmd);
        break;
      case "transition":
        await this.transition(cmd);
        break;
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Visibility
  // ─────────────────────────────────────────────────────────────

  private async show(cmd: ShowCommand): Promise<void> {
    const elements = this.selectAll(cmd.to);

    for (const el of elements) {
      if (cmd.transition) {
        await this.applyTransition(el, cmd.transition, cmd.time ?? 200);
      }
      el.style.display = cmd.display ?? "";
      el.hidden = false;
    }
  }

  private async hide(cmd: HideCommand): Promise<void> {
    const elements = this.selectAll(cmd.to);

    for (const el of elements) {
      if (cmd.transition) {
        await this.applyTransition(el, cmd.transition, cmd.time ?? 200);
      }
      el.hidden = true;
    }
  }

  private async toggle(cmd: ToggleCommand): Promise<void> {
    const elements = this.selectAll(cmd.to);

    for (const el of elements) {
      const isHidden = el.hidden || getComputedStyle(el).display === "none";

      if (isHidden) {
        if (cmd.in) {
          await this.applyTransition(el, cmd.in, cmd.time ?? 200);
        }
        el.style.display = cmd.display ?? "";
        el.hidden = false;
      } else {
        if (cmd.out) {
          await this.applyTransition(el, cmd.out, cmd.time ?? 200);
        }
        el.hidden = true;
      }
    }
  }

  // ─────────────────────────────────────────────────────────────
  // CSS Classes
  // ─────────────────────────────────────────────────────────────

  private async addClass(cmd: AddClassCommand): Promise<void> {
    const elements = this.selectAll(cmd.to);

    for (const el of elements) {
      if (cmd.transition) {
        await this.applyTransition(el, cmd.transition, cmd.time ?? 200);
      }
      el.classList.add(...cmd.classes);
    }
  }

  private async removeClass(cmd: RemoveClassCommand): Promise<void> {
    const elements = this.selectAll(cmd.to);

    for (const el of elements) {
      if (cmd.transition) {
        await this.applyTransition(el, cmd.transition, cmd.time ?? 200);
      }
      el.classList.remove(...cmd.classes);
    }
  }

  private async toggleClass(cmd: ToggleClassCommand): Promise<void> {
    const elements = this.selectAll(cmd.to);

    for (const el of elements) {
      if (cmd.transition) {
        await this.applyTransition(el, cmd.transition, cmd.time ?? 200);
      }
      for (const cls of cmd.classes) {
        el.classList.toggle(cls);
      }
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Attributes
  // ─────────────────────────────────────────────────────────────

  private setAttr(cmd: SetAttrCommand): void {
    const elements = this.selectAll(cmd.to);
    for (const el of elements) {
      el.setAttribute(cmd.attr, cmd.value);
    }
  }

  private removeAttr(cmd: RemoveAttrCommand): void {
    const elements = this.selectAll(cmd.to);
    for (const el of elements) {
      el.removeAttribute(cmd.attr);
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Focus
  // ─────────────────────────────────────────────────────────────

  private focus(cmd: FocusCommand): void {
    const el = this.select(cmd.to);
    if (el instanceof HTMLElement) {
      requestAnimationFrame(() => el.focus());
    }
  }

  private focusFirst(cmd: FocusFirstCommand): void {
    const container = this.select(cmd.to);
    if (!container) return;

    const focusable = container.querySelector<HTMLElement>(
      'input:not([type="hidden"]), textarea, select, button, [tabindex]:not([tabindex="-1"])'
    );
    if (focusable) {
      requestAnimationFrame(() => focusable.focus());
    }
  }

  // ─────────────────────────────────────────────────────────────
  // Server Events
  // ─────────────────────────────────────────────────────────────

  private push(cmd: PushCommand): void {
    // 서버로 이벤트 전송
    // connection.sendUserEvent() 호출
    const event = new CustomEvent("reactor:push", {
      bubbles: true,
      detail: {
        event: cmd.event,
        value: cmd.value ?? {},
        target: cmd.target,
        loading: cmd.loading,
      },
    });
    this.componentEl.dispatchEvent(event);
  }

  // ─────────────────────────────────────────────────────────────
  // DOM Events
  // ─────────────────────────────────────────────────────────────

  private dispatch(cmd: DispatchCommand): void {
    const target = cmd.to ? this.select(cmd.to) : this.componentEl;
    if (!target) return;

    const event = new CustomEvent(cmd.event, {
      bubbles: cmd.bubbles ?? true,
      detail: cmd.detail ?? {},
    });
    target.dispatchEvent(event);
  }

  // ─────────────────────────────────────────────────────────────
  // Navigation
  // ─────────────────────────────────────────────────────────────

  private navigate(cmd: NavigateCommand): void {
    if (cmd.replace) {
      window.location.replace(cmd.url);
    } else {
      window.location.href = cmd.url;
    }
  }

  private patch(cmd: PatchCommand): void {
    if (cmd.replace) {
      history.replaceState({}, "", cmd.url);
    } else {
      history.pushState({}, "", cmd.url);
    }
    // LiveView 업데이트 트리거
    window.dispatchEvent(new PopStateEvent("popstate"));
  }

  // ─────────────────────────────────────────────────────────────
  // Transitions
  // ─────────────────────────────────────────────────────────────

  private async transition(cmd: TransitionCommand): Promise<void> {
    const elements = this.selectAll(cmd.to);
    await Promise.all(
      elements.map((el) =>
        this.applyTransition(el, cmd.transition, cmd.time ?? 200)
      )
    );
  }

  private applyTransition(
    el: HTMLElement,
    transition: string,
    time: number
  ): Promise<void> {
    return new Promise((resolve) => {
      // 트랜지션 클래스 파싱 (예: "fade-in fade-out")
      const classes = transition.split(" ");

      // 트랜지션 클래스 추가
      el.classList.add(...classes);

      // 트랜지션 완료 후 클래스 제거
      setTimeout(() => {
        el.classList.remove(...classes);
        resolve();
      }, time);
    });
  }

  // ─────────────────────────────────────────────────────────────
  // Helpers
  // ─────────────────────────────────────────────────────────────

  private select(selector: string): Element | null {
    // 컴포넌트 내부에서 먼저 검색, 없으면 전역 검색
    return (
      this.componentEl.querySelector(selector) ??
      document.querySelector(selector)
    );
  }

  private selectAll(selector: string): HTMLElement[] {
    const results = this.componentEl.querySelectorAll<HTMLElement>(selector);
    if (results.length > 0) {
      return Array.from(results);
    }
    return Array.from(document.querySelectorAll<HTMLElement>(selector));
  }
}
```

### 3.3 이벤트 핸들러 통합

```typescript
// src/component.ts에 추가

import { JSCommandExecutor } from "./commands";
import type { JSCommand } from "./types";

class ReactorComponent {
  // ...

  private setupEventListeners(): void {
    const elements = this.element.querySelectorAll("[data-reactor-event]");

    for (const el of elements) {
      const eventType = el.getAttribute("data-reactor-event");
      const jsCommands = el.getAttribute("data-reactor-js");
      const handler = el.getAttribute("data-reactor-handler");

      if (!eventType) continue;

      el.addEventListener(eventType, async (e) => {
        // JS 명령어가 있으면 먼저 실행 (Optimistic UI)
        if (jsCommands) {
          const commands: JSCommand[] = JSON.parse(jsCommands);
          const executor = new JSCommandExecutor(this.element);

          // 서버 이벤트(push)와 나머지 분리
          const clientCommands = commands.filter((c) => c.op !== "push");
          const serverCommands = commands.filter((c) => c.op === "push");

          // 클라이언트 명령어 즉시 실행
          await executor.execute(clientCommands);

          // 서버 이벤트 전송
          for (const cmd of serverCommands) {
            if (cmd.op === "push") {
              this.dispatch(cmd.event, cmd.value ?? {});
            }
          }
        }

        // 기존 핸들러 방식
        if (handler) {
          this.dispatch(handler, {});
        }
      });
    }
  }
}
```

---

## 4. 사용 예시

### 4.1 모달 토글

```html
<!-- 서버 왕복 없이 즉시 모달 열기 -->
<button {% on "click" JS().show("#modal", transition="fade-in") %}>
  모달 열기
</button>

<button {% on "click" JS().hide("#modal", transition="fade-out") %}>
  닫기
</button>

<div id="modal" hidden>
  모달 내용
</div>
```

### 4.2 폼 제출 + 로딩 상태

```html
<form {% on "submit" JS().add_class("#submit-btn", "loading").push("save") %}>
  <input name="title" />
  <button id="submit-btn" type="submit">
    저장
  </button>
</form>
```

### 4.3 드롭다운 메뉴

```html
<button {% on "click" JS().toggle("#dropdown").toggle_class("#btn", "active") %}
        id="btn">
  메뉴
</button>

<div id="dropdown" hidden>
  <a href="#">항목 1</a>
  <a href="#">항목 2</a>
</div>
```

### 4.4 탭 전환

```html
<div class="tabs">
  <button {% on "click" JS()
    .remove_class(".tab-btn", "active")
    .add_class("#tab1-btn", "active")
    .hide(".tab-content")
    .show("#tab1")
  %} id="tab1-btn" class="tab-btn active">
    탭 1
  </button>

  <button {% on "click" JS()
    .remove_class(".tab-btn", "active")
    .add_class("#tab2-btn", "active")
    .hide(".tab-content")
    .show("#tab2")
  %} id="tab2-btn" class="tab-btn">
    탭 2
  </button>
</div>

<div id="tab1" class="tab-content">탭 1 내용</div>
<div id="tab2" class="tab-content" hidden>탭 2 내용</div>
```

---

## 5. 테스트

### 5.1 Python 단위 테스트

```python
# tests/test_js.py

from reactor.features.js import JS

def test_js_show():
    js = JS().show("#modal")
    assert js.commands == [{"op": "show", "to": "#modal", "time": 200}]

def test_js_chaining():
    js = JS().toggle("#modal").add_class("#btn", "active").push("save")
    assert len(js.commands) == 3
    assert js.commands[0]["op"] == "toggle"
    assert js.commands[1]["op"] == "add_class"
    assert js.commands[2]["op"] == "push"

def test_js_to_json():
    js = JS().show("#modal")
    json_str = js.to_json()
    assert '"op": "show"' in json_str
    assert '"to": "#modal"' in json_str
```

### 5.2 TypeScript 단위 테스트

```typescript
// tests/commands.test.ts

import { JSCommandExecutor } from "../src/commands";

describe("JSCommandExecutor", () => {
  let container: HTMLElement;
  let executor: JSCommandExecutor;

  beforeEach(() => {
    container = document.createElement("div");
    container.innerHTML = `
      <div id="target" hidden>Target</div>
      <button id="btn">Button</button>
    `;
    document.body.appendChild(container);
    executor = new JSCommandExecutor(container);
  });

  afterEach(() => {
    document.body.removeChild(container);
  });

  test("show command", async () => {
    await executor.execute([{ op: "show", to: "#target" }]);
    expect(document.getElementById("target")?.hidden).toBe(false);
  });

  test("add_class command", async () => {
    await executor.execute([
      { op: "add_class", to: "#btn", classes: ["active", "primary"] },
    ]);
    const btn = document.getElementById("btn");
    expect(btn?.classList.contains("active")).toBe(true);
    expect(btn?.classList.contains("primary")).toBe(true);
  });
});
```

---

*이 문서는 JS 명령어 시스템의 구현 가이드입니다.*
