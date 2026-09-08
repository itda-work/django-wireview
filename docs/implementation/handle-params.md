# GAP-004: handle_params 구현 지침

> URL 파라미터 변경 시 자동으로 호출되는 콜백 메서드 구현

---

## 1. 개요

### 목표

Phoenix LiveView의 `handle_params/3`와 유사하게, URL 파라미터가 변경되면 컴포넌트의 `params_changed()` 메서드를 자동으로 호출합니다.

### 사용 시나리오

- **페이지네이션**: `/products?page=2` → 2페이지 데이터 로드
- **필터링**: `/products?category=shoes` → 필터 적용
- **정렬**: `/products?sort=price` → 정렬 변경
- **북마크 가능한 상태**: URL이 앱 상태를 반영

### 예상 API

```python
class ProductList(Component):
    page: int = 1
    sort: str = "created_at"
    products: list[Product] = []

    async def params_changed(self, params: dict[str, str], uri: str):
        """URL 파라미터 변경 시 자동 호출"""
        self.page = int(params.get("page", "1"))
        self.sort = params.get("sort", "created_at")
        self.products = await self.fetch_products()

    async def next_page(self):
        # URL 변경 → params_changed 자동 호출
        await self.push_to(f"?page={self.page + 1}")
```

---

## 2. 현재 코드 분석

### 2.1 URL 관련 현재 흐름

```
┌─────────────────────────────────────────────────────────────────┐
│ 현재 흐름 (params_changed 없음)                                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [Client - wireview.js]                                         │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ connection.open()                                        │   │
│  │   └── sendQueryString() // 초기 query string 전송        │   │
│  │                                                          │   │
│  │ navEvent.on("newLocation")                               │   │
│  │   └── sendQueryString() // URL 변경 시 query string 전송 │   │
│  │                                                          │   │
│  │ url_change 명령어 수신                                    │   │
│  │   ├── redirect → HistoryCache.load(url)                  │   │
│  │   ├── replace → HistoryCache.replace(url)                │   │
│  │   └── push → HistoryCache.push(url)                      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  [Server - consumer.py]                                         │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ command_query_string(qs)                                 │   │
│  │   └── repo.set_query_string(qs) // params 업데이트만    │   │
│  │                                                          │   │
│  │ ※ 컴포넌트에 알림 없음!                                   │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 관련 파일

| 파일 | 역할 | 수정 필요 |
|------|------|:--------:|
| `wireview/static/wireview/wireview.js` | 클라이언트 메인 | ✅ |
| `wireview/static/wireview/wireview-boost.js` | 히스토리/네비게이션 | ⚠️ 확인 필요 |
| `wireview/consumer.py` | WebSocket 핸들러 | ✅ |
| `wireview/core/component.py` | 컴포넌트 베이스 | ✅ |
| `wireview/core/meta.py` | push_to, replace_to 등 | ⚠️ 확인 필요 |
| `wireview/repository.py` | params 관리 | ⚠️ 확인 필요 |

### 2.3 현재 params 관련 코드

**consumer.py:106-108**
```python
async def command_query_string(self, qs: str):
    self.query_string = qs
    self.repo.set_query_string(qs)  # ← 여기서 끝. 컴포넌트 알림 없음
```

**repository.py:40-48**
```python
def set_query_string(self, qs: str):
    params = self.extract_params(qs)
    for key in list(self.params.keys()):
        if key not in params:
            self.params.pop(key)
    self.params.update(params)
```

---

## 3. 구현 계획

### 3.1 목표 흐름

```
┌─────────────────────────────────────────────────────────────────┐
│ 목표 흐름 (params_changed 포함)                                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [트리거 1: push_to/replace_to 호출]                             │
│  Component.push_to("?page=2")                                   │
│    ↓                                                            │
│  Client: URL 변경 + 서버에 params_changed 전송                   │
│    ↓                                                            │
│  Server: 해당 컴포넌트의 params_changed() 호출                   │
│    ↓                                                            │
│  렌더링                                                          │
│                                                                 │
│  [트리거 2: 브라우저 뒤로가기/앞으로가기]                          │
│  popstate 이벤트                                                 │
│    ↓                                                            │
│  Client: 서버에 params_changed 전송                              │
│    ↓                                                            │
│  Server: 모든 live 컴포넌트의 params_changed() 호출              │
│    ↓                                                            │
│  렌더링                                                          │
│                                                                 │
│  [트리거 3: 초기 join 시 (선택적)]                                │
│  Component join                                                  │
│    ↓                                                            │
│  joined() 후 params_changed() 자동 호출 (URL params 있을 때)     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 구현 단계

```
Phase 1: 기본 구조 (Day 1-2)
├── Component에 params_changed() 메서드 추가
├── Consumer에 command_params_changed 핸들러 추가
└── 클라이언트에 sendParamsChanged() 함수 추가

Phase 2: 통합 (Day 2-3)
├── push_to() 호출 시 params_changed 자동 전송
├── popstate 이벤트 시 params_changed 전송
└── query_string 명령어와 통합 또는 대체

Phase 3: 테스트 및 문서화 (Day 3-4)
├── 단위 테스트
├── E2E 테스트 (페이지네이션 시나리오)
└── 문서 작성
```

---

## 4. 상세 구현 명세

### 4.1 Component 클래스 수정

**파일**: `wireview/core/component.py`

```python
class Component(BaseModel):
    # ... 기존 코드 ...

    async def params_changed(self, params: dict[str, str], uri: str) -> None:
        """URL 파라미터 변경 시 호출되는 콜백.

        push_to(), replace_to() 호출 시 또는 브라우저 뒤로가기/앞으로가기 시
        자동으로 호출됩니다.

        Args:
            params: URL query parameters (예: {"page": "2", "sort": "name"})
            uri: 전체 URI (예: "/products?page=2&sort=name")

        Example:
            async def params_changed(self, params, uri):
                self.page = int(params.get("page", "1"))
                await self.load_data()
        """
        pass  # 서브클래스에서 오버라이드
```

### 4.2 Consumer 수정

**파일**: `wireview/consumer.py`

```python
async def command_params_changed(self, params: dict[str, str], uri: str):
    """Handle URL parameter changes from client.

    Called when:
    - push_to() or replace_to() is called
    - Browser back/forward navigation
    """
    log.debug(f"<<< PARAMS-CHANGED {uri} {params}")

    # Update repository params
    self.repo.params.clear()
    self.repo.params.update(params)

    # Call params_changed on all live components
    for component in list(self.repo.components.values()):
        if hasattr(component, 'params_changed'):
            await component.params_changed(params, uri)
            await self.send_render(component)

    await self.after_mutation_chores()
```

### 4.3 클라이언트 수정

**파일**: `wireview/static/wireview/wireview.js`

```javascript
class ServerConnection {
  // ... 기존 코드 ...

  /**
   * Sends URL parameter change notification to server.
   * @param {string} uri - Full URI including query string
   * @param {Object<string, string>} params - Parsed query parameters
   */
  sendParamsChanged(uri, params) {
    debugLog("send", "params_changed", { uri, params });
    this._send("params_changed", { uri, params });
  }
}

// URL 파라미터 파싱 헬퍼
function parseQueryString(search) {
  const params = {};
  const searchParams = new URLSearchParams(search);
  for (const [key, value] of searchParams) {
    params[key] = value;
  }
  return params;
}
```

### 4.4 wireview-boost.js 수정

**파일**: `wireview/static/wireview/wireview-boost.js`

```javascript
// popstate 이벤트 핸들러 수정
window.addEventListener("popstate", (event) => {
  navEvent.sendNewLocation();

  // 새로 추가: params_changed 알림
  const params = parseQueryString(document.location.search);
  connection.sendParamsChanged(document.location.href, params);

  if (event.state?.content !== undefined) {
    replaceBodyContent(event.state.content, event.state.scrollY);
  }
  HistoryCache.replaceContentFromUrl(document.location.href);
});

// push() 수정
static async push(path) {
  // ... 기존 코드 ...
  history.pushState({}, document.title, path);

  // 새로 추가: params_changed 알림
  const params = parseQueryString(new URL(path, document.location.origin).search);
  connection.sendParamsChanged(path, params);

  this.replaceContentFromUrl(path);
}
```

### 4.5 서버 → 클라이언트 URL 변경 시

**파일**: `wireview/core/meta.py`

`push_to()`, `replace_to()` 호출 시 클라이언트에서 자동으로 `params_changed`를 보내도록 할지, 아니면 서버에서 직접 처리할지 결정 필요.

**옵션 A**: 클라이언트가 URL 변경 후 params_changed 전송 (현재 설계)
- 장점: 일관된 흐름, 클라이언트가 URL 상태의 진실의 원천
- 단점: 라운드트립 1회 추가

**옵션 B**: 서버에서 push_to() 시 직접 params_changed 호출
- 장점: 라운드트립 없음
- 단점: 브라우저 URL과 서버 상태 동기화 복잡

**권장**: 옵션 A (클라이언트 중심)

---

## 5. 메시지 형식

### 5.1 Client → Server

```json
{
  "command": "params_changed",
  "payload": {
    "uri": "/products?page=2&sort=name",
    "params": {
      "page": "2",
      "sort": "name"
    }
  }
}
```

### 5.2 기존 query_string과의 관계

**옵션 1**: `query_string` 명령어 유지, `params_changed` 추가
- `query_string`: repository.params 업데이트만 (현재 동작)
- `params_changed`: 컴포넌트 콜백 호출

**옵션 2**: `params_changed`로 통합, `query_string` 폐기
- `params_changed`가 모든 역할 담당

**권장**: 옵션 2 (단순화)
- 기존 `command_query_string`을 `command_params_changed`로 대체
- 또는 `command_query_string`이 `command_params_changed` 호출

---

## 6. 테스트 케이스

### 6.1 단위 테스트

```python
# tests/test_params_changed.py

@pytest.mark.asyncio
async def test_params_changed_called_on_push_to():
    """push_to() 호출 시 params_changed가 호출되어야 함"""
    pass

@pytest.mark.asyncio
async def test_params_changed_receives_correct_params():
    """params_changed에 올바른 파라미터가 전달되어야 함"""
    pass

@pytest.mark.asyncio
async def test_params_changed_not_called_when_not_defined():
    """params_changed 미정의 시 에러 없이 무시"""
    pass

@pytest.mark.asyncio
async def test_params_changed_triggers_rerender():
    """params_changed 후 렌더링이 발생해야 함"""
    pass
```

### 6.2 E2E 테스트

```python
# examples/todo/tests.py (또는 새 앱)

@pytest.mark.e2e
async def test_pagination_url_sync():
    """페이지네이션 버튼 클릭 시 URL과 상태가 동기화"""
    pass

@pytest.mark.e2e
async def test_browser_back_triggers_params_changed():
    """브라우저 뒤로가기 시 이전 상태로 복원"""
    pass
```

---

## 7. 엣지 케이스

### 7.1 초기 로드 시 params

**문제**: 페이지 새로고침 시 URL에 params가 있지만 joined()에서 처리해야 할까, params_changed()에서 처리해야 할까?

**해결**:
- `joined()` 호출 시 초기 params가 있으면 자동으로 `params_changed()` 호출
- 또는 개발자가 `joined()`에서 `self.wire.params`를 직접 사용

### 7.2 여러 컴포넌트

**문제**: 한 페이지에 여러 컴포넌트가 있을 때 모두 `params_changed`가 호출되어야 하나?

**해결**:
- 모든 live 컴포넌트에 호출 (기본 동작)
- 또는 `_watch_params = True` 같은 플래그로 제어

### 7.3 params 타입

**문제**: URL params는 모두 문자열이지만, 개발자는 int, bool 등을 원할 수 있음

**해결**:
- 기본적으로 `dict[str, str]` 전달
- 컴포넌트에서 타입 변환 책임

---

## 8. 참고 자료

- [Phoenix LiveView handle_params](https://hexdocs.pm/phoenix_live_view/Phoenix.LiveView.html#c:handle_params/3)
- [Phoenix Live Navigation](https://hexdocs.pm/phoenix_live_view/live-navigation.html)
- 관련 이슈: #51
- Epic: #47

---

## 9. 체크리스트

### 구현

- [ ] Component.params_changed() 메서드 추가
- [ ] Consumer.command_params_changed() 핸들러 추가
- [ ] 클라이언트 sendParamsChanged() 함수 추가
- [ ] popstate 이벤트에서 params_changed 전송
- [ ] HistoryCache.push()에서 params_changed 전송
- [ ] url_change 명령어 수신 후 params_changed 전송
- [ ] 기존 query_string 명령어와 통합/대체

### 테스트

- [ ] 단위 테스트: params_changed 호출 확인
- [ ] 단위 테스트: 파라미터 전달 확인
- [ ] 단위 테스트: 미정의 시 에러 없음
- [ ] E2E 테스트: 페이지네이션 시나리오
- [ ] E2E 테스트: 브라우저 뒤로가기

### 문서

- [ ] API 레퍼런스 문서
- [ ] 사용 예제 (페이지네이션, 필터링)
- [ ] CLAUDE.md 업데이트
- [ ] FEATURE-GAP.md 업데이트
