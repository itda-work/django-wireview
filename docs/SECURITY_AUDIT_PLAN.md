# django-wireview 보안 감사 계획

## 배경: React Server Components CVE-2025-55182

**핵심 취약점**: 클라이언트 → 서버 통신에서 **역직렬화(Deserialization) 검증 부족**으로 인한 원격 코드 실행(RCE)

```
공격자 → 악의적 HTTP/WebSocket 요청 → 서버 역직렬화 → 임의 코드 실행
```

---

## django-wireview 공격 표면(Attack Surface) 분석

### 1. WebSocket 메시지 (consumer.py)

클라이언트 → 서버 명령:

| 명령 | 입력 데이터 | 위험도 |
|------|-----------|--------|
| `join` | component state (JSON) | 🔴 높음 |
| `call` | event name, args (JSON) | 🔴 높음 |
| `upload_register` | entries (JSON) | 🟡 중간 |
| `upload_cancel` | ref (string) | 🟢 낮음 |
| `upload_complete` | ref (string) | 🟢 낮음 |
| `query_string` | qs (string) | 🟡 중간 |
| `leave` | id (string) | 🟢 낮음 |

### 2. HTTP 업로드 엔드포인트 (views.py)

| 입력 | 데이터 | 위험도 |
|------|--------|--------|
| Headers | X-Upload-Token, X-Entry-Ref 등 | 🟡 중간 |
| Body | 바이너리 파일 청크 | 🔴 높음 |

### 3. 템플릿 태그 (wireview.py)

| 태그 | 입력 | 위험도 |
|------|------|--------|
| `{% on %}` | 이벤트 문법 문자열 | 🟡 중간 |
| `{% component %}` | 컴포넌트 이름, 인자 | 🟡 중간 |

---

## 감사 체크리스트

### Phase 1: WebSocket 메시지 검증 (가장 위험)

#### 1.1 `command_join` - 컴포넌트 상태 복원
- [ ] 클라이언트가 보낸 state를 그대로 신뢰하는가?
- [ ] state에 악의적인 Python 코드/객체가 포함될 수 있는가?
- [ ] Pydantic 검증이 모든 필드에 적용되는가?
- [ ] `__class__`, `__reduce__` 등 매직 메서드 악용 가능성?

#### 1.2 `command_call` - 이벤트 핸들러 호출
- [ ] 호출 가능한 메서드가 화이트리스트로 제한되는가?
- [ ] private 메서드(`_xxx`)에 접근 가능한가?
- [ ] 인자(args)가 검증되는가?
- [ ] `getattr()` 사용 시 안전한가?

#### 1.3 `command_upload_register` - 업로드 등록
- [ ] entries 배열의 각 필드가 검증되는가?
- [ ] 파일명에 path traversal 공격 가능성?
- [ ] 파일 크기 검증이 서버에서도 이루어지는가?

### Phase 2: HTTP 업로드 검증

#### 2.1 토큰 검증
- [ ] 서명된 토큰이 올바르게 검증되는가?
- [ ] 토큰 재사용 방지?
- [ ] 토큰 만료 검증?

#### 2.2 파일 콘텐츠 검증
- [ ] Magic bytes 검증이 우회 가능한가?
- [ ] 파일 내용에 악성 코드 포함 시 영향?
- [ ] 임시 파일 경로 조작 가능성?

#### 2.3 청크 업로드
- [ ] 청크 인덱스 조작 시 영향?
- [ ] 비정상적인 청크 순서 처리?
- [ ] 메모리/디스크 고갈 공격?

### Phase 3: Pydantic 역직렬화

#### 3.1 Component 상태 복원
- [ ] `model_validate()` 사용 시 안전한가?
- [ ] 커스텀 validator가 안전한가?
- [ ] 중첩된 Pydantic 모델 처리?

#### 3.2 JSON 파싱
- [ ] `json.loads()` 사용 시 안전한가?
- [ ] 큰 JSON payload로 인한 DoS?
- [ ] 재귀 깊이 제한?

### Phase 4: 이벤트 트랜스파일러 (event_transpiler.py)

- [ ] 이벤트 문법 파싱 시 코드 인젝션 가능성?
- [ ] 수정자(modifier) 검증?
- [ ] 인자 파싱 시 eval/exec 사용 여부?

### Phase 5: JavaScript 직렬화/역직렬화

- [ ] Django 모델 → JSON 변환 시 민감 정보 노출?
- [ ] `_exclude_fields` 우회 가능성?

---

## 감사 수행 방법

### Step 1: 코드 분석
각 체크리스트 항목에 대해 관련 코드를 읽고 분석

### Step 2: 테스트 케이스 작성
발견된 취약점에 대한 PoC(Proof of Concept) 테스트

### Step 3: 수정 및 검증
취약점 수정 후 테스트로 검증

---

## 우선순위

1. **🔴 Critical**: `command_call` - 이벤트 핸들러 호출 (getattr 사용)
2. **🔴 Critical**: `command_join` - 상태 복원 (역직렬화)
3. **🟡 High**: HTTP 업로드 토큰 검증
4. **🟡 Medium**: 파일 업로드 콘텐츠 검증
5. **🟢 Low**: 이벤트 트랜스파일러

---

## 관련 파일

| 파일 | 역할 |
|------|------|
| `wireview/consumer.py` | WebSocket 메시지 처리 |
| `wireview/views.py` | HTTP 업로드 처리 |
| `wireview/core/component.py` | 컴포넌트 상태 관리 |
| `wireview/event_transpiler.py` | 이벤트 문법 파싱 |
| `wireview/serializer.py` | JSON 직렬화 |
| `wireview/features/uploads.py` | 업로드 데이터 구조 |
