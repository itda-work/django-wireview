# 렌더 부분별 SQL 계측 (#182)

> **상태: 결정됨, 1·2단계 구현됨.** 메인테이너가 §9의 권고를 모두 받아들였다(이슈 #182의 결정 댓글). 단계 3(JSONL·VS Code)은
> [#188](https://github.com/itda-work/django-wireview/issues/188), property 읽기를 좁히는 검토는
> [#187](https://github.com/itda-work/django-wireview/issues/187)로 갔다. 구현은 `wireview/debug/render_queries.py`, 사용 문서는
> [render-queries](../features/render-queries.md), 구현 후 비용은 §4-4다. 아래는 3판 그대로다.
> 근거는 main `ff18ca7`의 코드와 이 메모를 쓰며 돌린 스파이크 세 벌(§2)이다. 스파이크 코드는 저장소에 없다.
> 2판은 1판에 대한 리뷰(REQUEST_CHANGES)를 반영했다. 1판의 줄 귀속 규칙(가장 가까운 `Template` 프레임의 파일)은
> 템플릿 상속에서 틀렸고, shared render 수신자를 "0건"으로 본 것과 래퍼 설치 방식, 비용 수치도 틀렸다.
> 3판은 2판에 대한 리뷰를 반영했다. 2판의 묶음 서명 경계는 컴포넌트 이름만 바꿀 뿐 요청한 쪽의 수집기를 되살리지
> 않았다(§1-4). 그리고 `render_annotated`를 덮어쓴 사용자 노드에서는, "위치 모름"이라는 약속과 달리 바깥 파일을
> 확정했다(§1-2). 고친 자리마다 무엇이 틀렸는지 적었다.

[#182](https://github.com/itda-work/django-wireview/issues/182)가 묻는 것은 "어떤 출력이 어떤 쿼리를 불렀는가"를
렌더 부분마다 보여 주는 개발용 훅이다. 서버 렌더에서 N+1을 가장 싸게 찾는 길이 그것이고, 이슈의 스파이크는
join 렌더에서 그것이 된다는 것을 보였다. 이 메모는 그 귀속을 공식 기능으로 만들려면 **wireview가 어떤 경계를
내야 하는지**, 그리고 **어디까지 약속하는지**를 정한다.

결론부터:

- **경계는 내부에 둔다.** 컴포넌트 렌더·async property·핸들러·작업·상태 서명 같은 굵은 경계만 wireview가
  `ContextVar`로 건다. 템플릿의 파일:줄은 **쿼리가 실행되는 순간 스택에서, 가장 안쪽 노드 자신의 `origin`과
  `token`으로** 읽는다. sync property의 이름도 스택(컨텍스트를 모으는 프레임의 지역 변수)에서 읽는다.
- **telemetry 시그널에는 넣지 않는다.** 공개되는 것은 결과뿐이다: 로그 한 덩어리와 `wireview.testing`의 단언(§5).
- 켜는 것은 설정 키 하나, 기본은 `DEBUG`다. 쿼리 래퍼는 연결의 `execute_wrappers` **맨 아래에** 한 번 둔다(§6).
- 비용(참고): 2판 스파이크에서는 쿼리가 없는 렌더와 끈 상태가 측정 잡음 안이었고, 템플릿에서 쿼리 7건을 내는 렌더는
  켜면 약 10~15% 느려졌다(§4). 3판의 위치 규칙과 수집기 사슬의 비용은 재지 못했다. 구현 PR이 같은 벤치로 다시 잰다.

## 1. 렌더 부분 경계는 지금 어디에 있나

### 1-1. 한 렌더가 지나는 단계

라이브 렌더(`WireviewMeta.render_diff`, [`wireview/core/meta.py`](../../wireview/core/meta.py))는 다음 순서로 일한다.

| 단계 | 어디서 | 어느 스레드 | 그 단계의 쿼리 |
|------|--------|-------------|----------------|
| ① 컨텍스트 수집 | `_collect_context` — `_context_names`가 고른 이름(공개 속성. `FunctionType`·`classmethod`·`staticmethod`인 메서드는 뺀다)을 `getattr`로 읽는다. temporary assign이 있는 클래스는 `dir()` 전체를 `_read`로 읽는다 | `db()`(channels의 `database_sync_to_async`, thread-sensitive) 워커 | sync property의 쿼리. **템플릿이 그 이름을 쓰는지와 무관하게** 읽힌다. `cached_property`는 인스턴스에 처음 한 번만 계산된다 |
| ② async property | `_await_properties` — ①이 남긴 코루틴을 루프에서 기다린다 | 루프. 그 안의 async ORM은 다시 thread-sensitive 워커로 간다 | async property의 쿼리 |
| ③ 템플릿 | `_render_with_context` → `render_with_markers` | ①과 같은 워커 | lazy QuerySet의 평가(`{% for %}`, `{{ qs.count }}`), `{{ b.author.name }}` 같은 관계 접근(N+1), `{{ this.x }}`로 다시 읽은 property, `{% tag_header %}`의 상태 서명(computed field), 포함·중첩된 모든 것 |

async property가 없으면 `_collect_and_render`가 ①과 ③을 **워커 왕복 한 번에** 끝낸다. 있으면 ①, 루프에서 ②,
그다음 워커에서 ③으로 세 구간이 된다. 어느 쪽이든 ①이 ③보다 먼저 끝나므로, property가 부른 쿼리는 템플릿의
어느 줄에서 쓰였는지와 무관하게 이미 끝나 있다. 그래서 `{% if quiz_count %}`의 쿼리는 그 줄이 아니라
`property:quiz_count`로 귀속된다(§2-1). 템플릿 줄로 귀속되는 것은 ③에서 실제로 실행된 쿼리뿐이다.

HTTP 렌더와 템플릿 안의 중첩 렌더(`WireviewMeta.render`)는 `_get_context`로 ①을 하고 같은 스레드에서 ③을 한다.
async property는 `async_to_sync`로 하나씩 기다린다.

### 1-2. 템플릿 안의 부분과 파일:줄을 읽는 규칙

[`wireview/template_engine.py`](../../wireview/template_engine.py)의 `TemplateMarker.prepare_template`이 컴파일된
템플릿의 노드를 감싼다.

| 노드 | 감싸는 것 | 자기 위치 |
|------|-----------|-----------|
| `MarkedVariableNode` | `{{ … }}` 하나 | `token`은 복사하지만 **`origin`은 없다**. 원본은 `original_node` |
| `ComprehensionNode` | `{% for %}` 전체 | `token`만 복사, 원본은 `for_node` |
| `ConditionalNode`·`IncludedNode`(`_BlockNode`) | `{% if %}`, `{% include %}` | `token`만 복사, 원본은 `inner`. `_PartNode.key`는 (소스 해시, 부분 번호)라 줄이 아니다 |
| `ComprehensionItemNode` | 반복 한 번 | wireview가 만든 노드라 `token`도 `origin`도 없다 |
| 그 밖의 모든 Django 노드 | — | Django 파서가 `extend_nodelist`에서 `node.origin`을 붙이고 렉서가 `token.lineno`를 준다. 사용자 태그가 컴파일 때 돌려준 노드도 같다 |

마커는 컴포넌트 템플릿에만 붙는다. 함수 컴포넌트의 템플릿(`FunctionComponent.render`가 `template.render()`로 그린다)과
`{% include %}`된 파일의 안쪽에는 마커가 없다. 그래서 wireview 노드만 보는 계측은 그 안쪽의 쿼리를 바깥 부분으로
보낸다. **Django의 일반적인 렌더 경로에서 모든 템플릿의 노드를 지나는 길은 `NodeList.render`가 부르는
`render_annotated`다.** 사용자 노드가 그것을 덮어쓰거나 노드가 `render()`를 직접 부르는 경우는 아래 규칙이 다룬다.

**파일:줄은 그 노드 자신의 `origin.name`과 `token.lineno`다.** 쿼리 순간 스택을 안쪽부터 보며 **노드 프레임**을 찾는다.
노드 프레임은 이름이 `render_annotated`나 `render`인 메서드의 프레임 가운데 `self`가 템플릿 `Node`인 것이다. Django의
원본이든 사용자가 덮어쓴 것이든 이름만 맞으면 된다. 그중 처음으로 `origin`과 `token`을 함께 가진 노드를 고른다.
wireview의 래퍼는 `original_node`·`for_node`·`inner`를 따라 풀어 Django가 파싱한 노드에 닿는다.

2판은 Django의 `Node.render_annotated` 코드 객체만 알아보았다. 그래서 그것을 덮어쓰고 부모 메서드를 부르지 않는 사용자
노드는 보이지 않았다. 그 노드를 `{% include %}`한 바깥 파일의 `IncludeNode`가 정답처럼 보고되었다(리뷰의 재현:
`inner.html:3`의 쿼리가 `outer.html:1`로 기록됨). 3판의 규칙으로는 같은 재현에서 `inner.html:3`이 나온다(§2-4).

**약속하는 범위**는 다음과 같다.

| 노드 | 보고 |
|------|------|
| 진입 메서드의 이름이 `render_annotated`나 `render`인 노드(Django의 모든 노드, `render`만 쓰는 사용자 태그, 둘 중 하나를 덮어쓴 사용자 노드) | **그 노드의 파일:줄. 정확하다** |
| 렌더 중에 만들어져 파서가 `origin`을 주지 않은 노드(태그가 `render()` 안에서 만들어 부르는 노드) | 그 노드는 건너뛰고, 그것을 부른 바깥 노드의 파일:줄을 **"근사"** 표시와 함께 보고한다. 건너뛴 노드가 있었다는 것은 스택에서 알 수 있으므로 표시할 수 있다 |
| 진입 메서드를 다른 이름의 함수에 묶은 노드(`render_annotated = _impl`) | 그 프레임은 노드 프레임으로 보이지 않는다. **가장 가까운 관측 가능한 노드**(예: 그것을 include한 바깥 파일의 노드)를 보고하고, 근사 표시도 할 수 없다. 이 경우 그 위치가 SQL을 실행한 노드 자신의 파일이라는 보장은 없다 |
| 노드 프레임이 하나도 없음 | 파일을 정하지 않는다("템플릿 밖", 또는 스코프만) |

마지막에서 둘째 행이 이 방법의 한계다. 그것을 없애려면 모든 프레임의 `self`를 꺼내 `Node`인지 봐야 한다. 그러면
쿼리마다 비용이 스택 깊이만큼 커진다. 그런 노드는 드물다고 보고, 이 한계를 문서에 적는 쪽을 권한다.

1판은 파일을 **가장 가까운 `Template._render` 프레임**에서 읽었다. 그 방식은 틀렸다. 상속에서 자식의 블록 노드는
부모 템플릿의 `_render` 안에서 실행되므로 `child.html:3`의 쿼리가 `base.html:3`으로 기록된다. cached.Loader로 두 번
렌더해도 같은 결과였다(리뷰의 재현). 그 방식에는 다른 결함도 하나 있다. pytest-django가 켜는
`setup_test_environment()`가 `Template._render`를 `instrumented_test_render`로 바꿔 끼운다. 그래서 테스트 안에서는
코드 객체 비교가 그 프레임을 아예 찾지 못했다(§2-2의 "1판 규칙" 행). 노드 자신의 `origin`은 두 문제가 모두 없다.

### 1-3. 중첩과 다른 렌더 경로

"스코프"는 쿼리가 실행되는 동안 열려 있던 굵은 경계이고, "파일:줄"은 §1-2의 규칙이 고른 자리다. 둘은 다를 수 있다.

| 경로 | 렌더하는 곳 | 스코프 | 파일:줄 |
|------|-------------|--------|---------|
| join 렌더 | `WireviewSession.command_join` → `send_render` → `_render_tree` → `render_diff` | 그 컴포넌트의 렌더 | 그 컴포넌트의 템플릿(상속이면 블록을 쓴 파일) |
| 이벤트 렌더 | `command_user_event` → `repository.dispatch_event`(핸들러) → `send_render` | 핸들러와 렌더가 **따로** | 같음 |
| 중첩 `{% component %}` | 부모의 ③ 안에서 `_build_and_render_component` → `WireviewMeta.render`(동기) | 자식의 렌더 | 자식 템플릿. 자식의 ① property는 이름으로 |
| `LiveComponent` | 부모의 렌더 **뒤에** `_render_tree`가 자식마다 `render_diff`. HTTP 렌더에서는 부모의 ③ 안에서 인라인 | 자식의 렌더. 자식의 `joined()`·`update_many()`는 렌더 밖 | 자식 템플릿 |
| 함수 컴포넌트 | 그린 컴포넌트의 ③ 안. 마커 없는 템플릿 | 그린 컴포넌트의 렌더 | 함수의 템플릿 |
| 슬롯, `let:` 없음 | `_extract_slots`(`templatetags/wireview.py`)가 **채우는 쪽의 패스에서 미리 렌더**하고 결과를 `TextNode`로 넘긴다 | 채우는 쪽의 렌더 | 채우는 쪽 템플릿의 `{% fill %}` 안 |
| 슬롯, `let:` 있음 | 노드리스트를 보관했다가(`slots.py`의 `Slot`) **슬롯 주인의 렌더 안에서** `{% render_slot %}`이 그린다 | **슬롯 주인**의 렌더 | **채우는 쪽** 템플릿의 `{% fill %}` 안. 행에 "슬롯 `row`(채운 곳: …)"을 곁들인다 |
| temporary assign이 있는 렌더 | 같은 경로에 `RenderReads`·`TrackingContext`가 더해질 뿐([`render_reads.py`](../../wireview/core/render_reads.py)) | 같음 | 같음 |
| shared render | §1-4 | | |
| 스트림 항목 | `Component._render_stream_item`(`render_gate.rendering()` 안) | 그 컴포넌트의 항목 렌더 | 항목 템플릿 |
| `Broadcast` 항목 | [`core/patches.py`](../../wireview/core/patches.py)가 발행 때 한 번 렌더 | 발행. 연결과 무관하다 | 항목 템플릿 |
| 비동기 작업 | `start_async`·`assign_async`의 태스크. `render_gate.run`이 렌더 사이로 미룬다 | 작업. 경계가 없으면 §2-1처럼 **그것을 만든 핸들러로 잘못** 간다 | 사용자 코드 |

### 1-4. shared render: 공유·검증·서명은 셋 다 SQL을 낸다

[`shared_render.py`](../../wireview/core/shared_render.py)에서 같은 브로드캐스트(`message_id`)를 처리하는 연결 중
처음 온 연결(leader)이 렌더하고, 나머지(taker)는 그 `Rendered`를 받는다. 1판은 taker의 쿼리를 0건으로 보았다.
그것은 한 경우에만 맞다.

| taker가 하는 일 | 언제 | 그 SQL |
|-----------------|------|--------|
| 받은 렌더를 그대로 쓴다 | `verifying()`이 거짓이고 상태 칸이 없을 때(`state_path is None`) | 없음 |
| 자기 상태 토큰을 서명한다 | `verifying()`이 거짓이고 상태 칸이 있을 때. `_Signer`가 그 사이에 모인 taker들을 `db(_sign_each)` **한 번에** 서명한다 | computed field·QuerySet 필드의 직렬화. **이 왕복은 처음 서명을 청한 taker의 태스크(그 컨텍스트)에서 돈다** — 손쓰지 않으면 다른 연결의 서명 SQL이 그 taker에게 붙는다(아래) |
| 자기도 렌더해 비교한다(검증) | `verifying()`이 참일 때. `VERIFY_SHARED_RENDER`가 `None`이면 `DEBUG`를 따른다 | leader의 렌더와 같은 SQL 전부 |

`verifying()`은 `None`일 때 `DEBUG` **또는** `shared_render.testing()` 블록 안(`MountedComponent.render_diff()`가
씌운다)에서 참이다. `VERIFY_SHARED_RENDER=False`를 명시하면 테스트에서도 끈다. Django 테스트 러너(pytest-django
포함)는 테스트 동안 `settings.DEBUG`를 `False`로 바꾸므로, 세션을 직접 모는 테스트에서는 `None`이 검증을 켜지
않는다(§2-3).

계측은 이 셋을 구별해 **실행된 SQL을 모두 센다.** leader의 렌더는 "렌더", taker의 검증 렌더는 "검증 렌더(공유받음)",
서명은 "상태 서명"으로 적는다.

**묶음 서명은 항목마다 요청한 쪽의 계측 상태로 돌린다.** `_Signer.sign()`은 `(component, future)`만 쌓아 두고, 그것을
처음 요청한 쪽이 만든 `_drain` 태스크가 묶음 전체를 실행한다. 그 태스크의 `ContextVar`는 첫 요청자의 것이다.
그러므로 2판처럼 `_sign_each` 안에서 컴포넌트마다 "서명" 스코프를 열기만 해서는 부족하다. 이름은 맞게 붙지만,
그 스코프의 바깥 사슬(부모 렌더, `queries()` 블록, 연결)은 여전히 첫 요청자의 것이다. 리뷰의 재현에서는 B가 요청한
서명의 SQL이 A의 수집기에 들어갔다. 3판의 설계는 다음과 같다.

1. `sign()`이 요청 시점의 **계측 상태**를 pending 항목에 함께 담는다. 그 상태는 가장 안쪽 스코프 하나다. 스코프는
   `outer`로 부모 렌더와 그것을 둘러싼 `queries()` 블록, 연결을 사슬로 갖고 있으므로 그것 하나면 된다. 사용자의
   `Context` 전체를 옮기지 않는다. 계측에 필요한 `ContextVar` 하나뿐이다.
2. `_sign_each`는 항목마다 그 상태를 되살린다(`ContextVar.set`). 그 안에서 "서명" 스코프를 열고 서명한 뒤, `finally`에서
   되돌린다.
3. 계측이 꺼져 있으면 1의 값은 `None`이고 2는 아무 일도 하지 않는다.

같은 문제는 앞으로 "요청을 모아 다른 태스크가 한 번에 처리하는" 길을 새로 만들 때마다 생긴다. 그래서 구현은
`capture()`·`restored()` 한 쌍을 계측 모듈에 두고, 그런 길이 그것을 쓰게 한다.

### 1-5. 스레드와 격리

라이브 세션의 렌더와 async ORM은 기본적으로 thread-sensitive 워커에서 돈다. 1판은 이것을 "모든 렌더는 워커 하나에서
직렬로 돈다"고 적었는데, 일반화한 그 문장은 틀렸다. Django의 ASGIHandler는 요청마다 `ThreadSensitiveContext`를 쓰고,
WSGI는 요청 스레드에서 렌더한다. 프로세스 전역 `TemplateMarker`가 있다는 것도 직렬화의 증거가 아니다. 이 설계는
직렬화에 기대지 않는다. 쿼리가 섞이지 않는 것은 `ContextVar`가 태스크마다 따로이고, asgiref가 `sync_to_async`·
`async_to_sync`를 건널 때 컨텍스트를 복사해 넘기기 때문이다. §2-1의 두 세션 실험은 그 실험 안에서의 연결 격리만 보인다.
요청이 여럿 겹치는 HTTP에서의 격리는 구현의 계약 테스트가 따로 본다(§8).

## 2. 스파이크

이슈의 스파이크(main `5a4f037`)는 wireview 노드 클래스를 monkeypatch했다. 이번에는 바깥에서 감싸는 스파이크를 세 벌
돌렸다. 쿼리는 연결마다 `connection.execute_wrappers`에 붙인 래퍼로 받았다. 연결은 스레드마다 따로라서
`connection.execute_wrapper()` 컨텍스트 매니저(현재 스레드의 연결에만 붙는다)로는 워커 스레드의 쿼리를 놓친다.
시나리오는 `examples.quiz`의 모델(Quiz → Question → Choice, 질문 3개 × 선택지 2개)과 `WireviewSession` +
`RecordingOutbound`다(`tests/test_session_extraction.py`와 같은 방식).

### 2-1. 1벌: 렌더 경로 (join·이벤트·HTTP·동시 세션·작업)

같은 쿼리를 두 방식으로 기록했다. ctx는 노드·템플릿·property마다 `ContextVar`를 거는 방식이고, stack은 쿼리 순간의
스택을 보는 방식이다. 쿼리 수는 join 25건, 이벤트 `bump` 24건(핸들러 1 + 렌더 23), 이벤트 `load` 24건, HTTP 렌더
24건이었다. 이 벌의 **파일** 판정은 1판 규칙(가장 가까운 `Template`)이라 상속이 없는 이 템플릿에서만 맞았다. 두
방식이 일치했다는 것은 정확성의 근거가 아니다. 파일:줄은 §2-2가 다시 본다. 아래 표는 스코프와 property 귀속의
결과로만 읽는다.

| 쿼리 | 귀속 | 새는 것 |
|------|------|---------|
| sync property `total`·`quiz_count`·`method_count` | 렌더 스코프 / `property:<이름>` (join·이벤트·HTTP 모두) | `{% if quiz_count %}`는 그 줄이 아니라 property로 간다(§1-1) |
| async property `first_title` | 라이브: `_await_properties`에 경계를 둔 ctx만 귀속. stack은 못 본다(쿼리가 다른 스레드의 `sync_to_async`에서 돈다) | HTTP 렌더: `_get_context`의 `async_to_sync`에 경계가 없어서 스코프까지만 귀속 |
| `{% for c in choices %}`, `{{ c.question.text }}` ×6 | 그 줄. N+1 6건이 한 줄로 모인다 | — |
| 중첩 `SpNested.n` | 자식의 렌더 스코프 | — |
| 함수 컴포넌트 안 ×6, `{% include %}` 안 | 그린 컴포넌트의 스코프, 그 템플릿의 줄 | — |
| `{{ this.method_count }}` | 템플릿 줄 + 같은 property가 ①에서 한 번 더 | **두 번 쿼리한다** |
| `LiveComponent` `SpLive.q` | 라이브: 부모 뒤에 따로 도는 자식 스코프. HTTP: 부모 템플릿 안 | 정상 |
| `joined()`의 쿼리 | join 스코프, 부분 없음 | join의 다른 쿼리와 구별할 경계가 없다 |
| 핸들러 `bump`의 쿼리 | 핸들러 스코프 | 렌더와 분리된다 |
| `assign_async` 작업의 쿼리 | **핸들러 `load`** 스코프 | `asyncio.create_task`가 만든 순간의 컨텍스트를 복사한다. 작업 경계가 필요하다 |
| 두 세션 동시 join(`asyncio.gather`) | 연결 태그별 25 / 25건 | 섞이지 않음 |

### 2-2. 2벌: 권고 알고리즘으로 파일:줄과 스코프를 단언

권고 알고리즘을 바깥에서 그대로 흉내 냈다. 노드 자신의 `origin`+`token`(래퍼를 풂), ① 프레임의 `attr_name`,
async property·핸들러·join·작업·서명 경계, 맨 아래 래퍼가 그 내용이다. 그리고 기대 파일:줄과 스코프를 **단언**했다.
로더는 `cached.Loader(locmem)`이고, 같은 컴파일본을 다시 쓰는지 보려고 두 번 렌더했다.

```django
{# t2/base.html #}                          {# t2/child.html — T2Page의 템플릿 #}
1 {% load wireview %}<div {% tag_header %}>   1 {% extends "t2/base.html" %}
2 {% block body %}                            2 {% load wireview %}
3 {{ questions.count }}                       3 {% block body %}
4 {% endblock %}                              4 {{ block.super }}
5 {{ choices.exists }}                        5 {{ choices.count }}
6 </div>                                      6 {% include "t2/inc.html" %}
                                              7 {% func "t2card" items=choices %}
{# t2/owner.html — T2Owner #}                 8 {% component_block "T2Owner" id="own" %}{% fill row let:r %}
1 {% load wireview %}<s {% tag_header %}>     9 {{ r.question.text }}
2 {% render_slot "head" %}                   10 {% endfill %}{% fill head %}
3 {% for r in rows %}{% render_slot "row" r=r %}{% endfor %}
                                             11 {{ questions.exists }}
                                             12 {% endfill %}{% endcomponent %}
                                             13 {% endblock %}
```

| 쿼리 | 기대(단언) = 결과 | 1판 규칙이었다면 |
|------|-------------------|------------------|
| `block.super`로 그린 부모 블록 | `t2/base.html:3`, 스코프 T2Page | 같음 |
| 자식 블록의 `{{ choices.count }}` | `t2/child.html:5`, T2Page | `t2/base.html` (틀림) |
| 부모의 블록 밖 | `t2/base.html:5`, T2Page | 같음 |
| `{% include %}` 안 | `t2/inc.html:2`, T2Page | 같음 |
| 함수 컴포넌트 안 | `t2/card.html:2`, T2Page | 같음 |
| `let:` 없는 슬롯 | `t2/child.html:11`, **T2Page**(채우는 쪽의 패스) | `t2/base.html` (틀림) |
| `let:` 있는 슬롯 ×2 | `t2/child.html:9`, **T2Owner**(주인의 렌더) | `t2/owner.html` (틀림) |
| 슬롯 주인의 `{% for %}` | `t2/owner.html:3`, T2Owner | 같음 |

- HTTP 렌더 두 번(cached.Loader가 같은 컴파일본을 줌)과 라이브 join 한 번이 모두 이 표와 정확히 같았다.
- 1판 규칙을 pytest 안에서 그대로 돌리면 9건 모두 파일이 `None`이었다. `setup_test_environment()`가 `Template._render`를
  바꿔 끼웠기 때문이다(§1-2). 위 표의 "1판 규칙" 칸은 리뷰의 재현(같은 상속, 테스트 환경 밖)과 상속 규칙에서 따른 것이다.

### 2-3. 2벌: shared render 수신자

`Meta.shared_render`인 보드 하나를 연결 셋이 join한 뒤 같은 `message_id`의 알림을 동시에 받게 했다. 보드의 렌더는
SQL 4건이다. computed field `signed_rows`가 ①에서 1건과 `{% tag_header %}` 서명에서 1건, property `total` 1건,
템플릿의 `{{ choices.count }}` 1건이다. 건수는 연결 태그(그 연결의 태스크에서 세팅한 `ContextVar`)별로 셌다.

| 설정 | leader | taker 1 | taker 2 | 합계 |
|------|--------|---------|---------|------|
| `DEBUG=True`, `VERIFY_SHARED_RENDER=None`(검증 켜짐) | 렌더 4 | 검증 렌더 4 | 검증 렌더 4 | 12 |
| `DEBUG=True`, `VERIFY_SHARED_RENDER=False` | 렌더 4 | 서명 1 | 서명 1 | 6 |
| `DEBUG=False`(Django 테스트 러너의 기본), `None` | 렌더 4 | 서명 1 | 서명 1 | 6 |

두 번째·세 번째 줄의 서명 2건은 **둘 다 taker 1의 태스크에서** 돌았다(연결 태그가 둘 다 taker 1이었다). 그러므로
연결 태그만으로는 taker 2의 서명이 taker 1에게 붙는다. `_sign_each` 안에서 컴포넌트마다 스코프를 열면 스코프의
이름은 맞게 나뉘었다. 그러나 이름이 맞다고 수집기가 맞는 것은 아니다. 그것은 §2-4가 본다.

### 2-4. 3벌: 덮어쓴 노드와 묶음 서명의 주인

2벌 리뷰의 재현 두 개를 3판 규칙으로 다시 돌리고 단언했다. 설정은 `cached.Loader(locmem)`, SQLite 메모리 DB이고
템플릿은 두 번 렌더했다. 모든 행이 두 번 다 같았다.

`outer.html`은 `{% include "inner.html" %}` 한 줄이고, `inner.html`의 3~6줄에 사용자 태그 넷을 두었다.

| 사용자 노드(`inner.html`) | 3판 결과 = 단언 | 2판 규칙 |
|--------------------------|-----------------|----------|
| 3줄: `render_annotated`를 덮어써 `render()`를 직접 부름(리뷰의 재현) | `inner.html:3`, 정확 | `outer.html:1 IncludeNode` (틀림) |
| 4줄: 덮어쓴 `render_annotated`가 다른 이름의 메서드로 SQL | `inner.html:4`, 정확 | `outer.html:1 IncludeNode` (틀림) |
| 5줄: `render()` 안에서 만든 `origin` 없는 노드가 SQL | `inner.html:5`, **근사** 표시 | `inner.html:5`, 근사 표시 없음 |
| 6줄: `render_annotated = _impl` (다른 이름의 함수) | `outer.html:1 IncludeNode`, 근사 표시 없음 — §1-2가 적은 한계 그대로 | 같음 |

묶음 서명은 실제 `_Signer`로 요청 둘을 한 묶음으로 몰아넣고 단언했다. 서명 함수는 SQL 1건을 내는 함수로 바꿔 끼웠다.
요청 A와 B는 각자 자기 `queries()` 블록과 렌더 스코프 안에서 서명을 청한다.

| 경우 | 묶음 | A의 블록 | B의 블록 | 바깥 블록 | 서명 스코프의 부모 |
|------|------|----------|----------|-----------|--------------------|
| A·B가 서로 다른 블록 | 2건 한 묶음 | 1 | 1 | — | 각자의 렌더 |
| B의 블록이 묶음 실행 전에 닫힘 | 2건 한 묶음 | 1 | 0 (버림) | — | 각자의 렌더 |
| A가 바깥 블록 O 안의 블록 | 2건 한 묶음 | 1 | 1 | O: 1 | 각자의 렌더 |
| 대조군: 되살리지 않음(2판) | 2건 한 묶음 | **2** | **0** | — | — |

블록을 겹쳤을 때 바깥 블록도 세는 것은 래퍼가 하는 일이다. 래퍼는 가장 안쪽 스코프에 행을 넣고, 그 바깥 사슬에서 열린
`queries()` 블록마다 같은 행을 센다.

## 3. 훅 형태

### 3-1. 선택지

| | (a) telemetry 시그널 | (b) 내부 경계 + 결과만 공개 | (c) 스택만, 경계 없음 |
|---|---|---|---|
| 무엇 | `render_part` 같은 시그널을 [`telemetry`](../features/telemetry.md)에 더해 부분마다 발신 | 굵은 경계를 내부 `ContextVar`로, 파일:줄과 sync property는 쿼리 순간 스택에서 | debug-toolbar처럼 쿼리 순간 스택만 |
| 공개 API | 시그널 이름·인자가 [COMPATIBILITY](../COMPATIBILITY.md)의 약속이 된다. "부분"의 정의(노드? 줄?)를 1.x 동안 못 바꾼다 | 내부. 공개는 `wireview.testing`의 단언 두 개뿐(로그 형식은 약속하지 않는다) | 내부 |
| 끈 상태의 비용 | 노드마다 플래그 검사 | 렌더당 플래그 검사 몇 번 | 0 |
| 켠 상태의 비용 | 노드마다 시그널 발신(재지 않음) | 쿼리마다 스택 탐색(§4) | 같음 |
| async property, 작업, 서명 묶음 | 시그널 하나로는 스레드·태스크를 건넌 쿼리를 묶을 수 없다 | 경계가 묶는다 | **못 묶는다**(§2-1, §2-3) |
| 연결별 격리 | 받는 쪽이 해야 한다 | `ContextVar` | 스코프가 없다 |

(a)를 버리는 이유는 셋이다.

- telemetry는 운영 지표의 통로다. 꺼져 있을 때의 비용을 "플래그 하나"로 약속했는데(#57, #124), 노드 단위 발신은
  그 약속과 맞지 않는다.
- 귀속 자체가 Django 내부(`render_annotated`)와 스택에 기댄다. 그것을 공개 시그널로 내면, 약속할 수 없는 것을
  약속하게 된다.
- 소비자가 원하는 것은 이벤트 흐름이 아니라 "이 렌더에서 이 줄이 몇 번"이라는 **집계**다. 시그널로 내보내면
  모든 소비자가 같은 집계를 다시 짠다.

(c)는 스파이크에서 바로 새는 곳이 보였다. async property, 비동기 작업, 서명 묶음이다.

### 3-2. 권고: (b). 메인테이너의 초기안과 같고, 줄은 노드 경계가 아니라 스택에서 읽는다

| 경계 | 어디에 | 무엇을 싣나 |
|------|--------|-------------|
| 렌더 | `WireviewMeta.render_diff`, `WireviewMeta.render`(중첩·HTTP), `_render_stream_item`, `Broadcast` 항목 렌더 | 컴포넌트 클래스·id, 연결, 렌더 종류(join·event·http·nested·stream·broadcast) |
| shared render | `_render_for`의 taker 갈래 | "검증 렌더(공유받음)" 표시 |
| 상태 서명 | `_Signer.sign()`이 요청 시점의 계측 상태를 pending 항목에 담고, `_sign_each`가 항목마다 되살린 뒤 연다(§1-4) | 그 컴포넌트와, 요청한 쪽의 렌더·블록·연결 |
| async property | `_await_properties`의 이름마다, `_get_context`의 `_run_coro` | property 이름 |
| 핸들러 | `ComponentRepository.dispatch_event`, `handle_hook_event`, `params_changed`, `update_many`, `mutation`·`notification` 수신 | 이름 |
| 마운트 | `_mount`(on_mount 훅), `joined()` | `mount`·`joined` |
| 작업 | `start_async`·`assign_async`가 태스크를 만들 때 코루틴을 감싼다 | 작업 이름 |

경계가 아닌 것은 둘이다.

- **템플릿 파일:줄**: 쿼리 래퍼가 스택에서 찾는다(§1-2의 규칙).
- **sync property 이름**: ①의 프레임(`_collect_context`·`_get_context`)의 지역 변수 `attr_name`에서 읽는다.
  이름마다 경계를 걸면 쿼리가 없는 렌더도 property 수만큼 값을 치른다. 스택에서 읽으면 쿼리가 있을 때만 치른다.

`render_reads`와의 관계: 모양은 같다. 렌더 하나 동안 켜지는 수집기이고, 안쪽 컴포넌트가 바깥 것을 잠시 가린다. 그래도
합치지 않는다. `render_reads`는 diff의 정확성을 위해 temporary assign이 있을 때만 **스레드 로컬**로 돈다(그 추적은
③이 한 스레드에서 끝나므로 그것으로 충분하다). 이 계측은 ②·작업·서명처럼 스레드와 태스크를 건너야 하므로
`ContextVar`여야 하고, 꺼져 있으면 아무것도 만들지 않는다.

`debug/sync_detector.py`와의 관계: 둘 다 개발용 계측이라 같은 패키지(`wireview/debug/`)에 둔다. 공유할 상태는 없다.
sync_detector는 전역 카운터와 스레드 로컬로 깊이를 세는데, 그 방식으로는 동시 연결의 것이 섞인다. 이 계측이
`ContextVar`를 고르는 이유이기도 하다.

## 4. 비용 실측

조건: Apple M5 Pro, Python 3.12.14, Django 6.0, testproj 설정, SQLite(파일). 바깥 감싸기로 쟀으므로 구현의 값과는
다를 수 있다.

### 4-1. 2판 스파이크의 권고 알고리즘, 라이브 렌더 (참고 비용. 3판은 구현에서 재측정)

`mount()`의 `render_diff()`(db 워커 왕복 포함)를 워밍업 30회 뒤 40회씩 15라운드 돌렸다. 값은 렌더 하나의 **중앙값**이다. "on − base"는 같은 회차끼리의 차이다.
`base`는 패치 없이(따로 띄운 프로세스), `off`는 경계를 모두 설치하고 플래그를 끈 상태, `on`은 켠 상태다. 세 모드를
번갈아 세 번 돌렸고, 칸은 세 번의 범위다. 템플릿마다 100행 `{% for %}`가 있다.

| 렌더 | base | off | on | on − base |
|------|------|-----|----|-----------|
| 쿼리 없음 | 283~288 µs | 289~298 µs | 290~300 µs | +7~16 µs (사분위 폭 안) |
| 템플릿 SQL 7건(`{% for c in choices %}` + N+1 ×6) | 1,044~1,087 µs | 1,057~1,092 µs | 1,184~1,213 µs | **+105~153 µs (+10~15%)** |
| sync property SQL 1건 | 522~535 µs | 518~531 µs | 534~556 µs | −1~+34 µs |
| async property SQL 1건 | 678~694 µs | 682~694 µs | 687~711 µs | −7~+24 µs |

- 같은 측정을 한 번 더 했을 때(base와 on만)는 사분위 폭이 ±10%까지 벌어졌다. 템플릿 SQL 7건 행은 다시
  +73~210 µs였다.
- 그러므로 렌더 중 템플릿 SQL 하나에 붙는 비용은 대략 **15~22 µs**다(첫 측정의 회차별 차이 ÷ 7). 실제 렌더의 스택은 asgiref·db 워커·노드 중첩
  때문에 깊다. 그래서 1판이 40프레임 합성 스택에서 잰 3~4 µs보다 크다.
- 쿼리가 없는 렌더, sync·async property 렌더, 끈 상태는 이 방법의 잡음 안이다. 끈 상태는 base와 구별되지 않았다.
- 중앙값을 쓴 것은 백그라운드 부하에서 생기는 꼬리를 빼기 위해서다. 1판은 최솟값을 썼고, 그것은 가장 좋은 경우의
  값이라 바꿨다.

### 4-2. 기본 단위(참고)

1판의 수치는 기본 단위의 비용이다. 권고안의 비용이라고 1판에 적은 것은 잘못이었다.

| 기본 단위 | 측정 | 비용 |
|-----------|------|------|
| 노드마다 `ContextVar`를 거는 방식(노드 경계 대안) | HTTP 렌더 200행, `render_annotated` 1,003회, 21회 최솟값 | 켜면 +3~8%(노드당 약 0.15 µs), 끄면 잡음 안 |
| `render_annotated` 코드 객체 하나만 비교하는 간이 스택 순회 | 40프레임 합성 스택의 SQLite 쿼리 | +3~4 µs |
| `ContextVar` 하나를 읽고 지나가는 비활성 래퍼(카운터를 올리지 않음, §6-1) | SQLite `exists()` 500회 × 15라운드 중앙값, 세 번 | +0.7~1.0 µs (쿼리 42~43 µs의 약 2%) |

### 4-3. 미결

정확한 비용은 구현 PR이 같은 벤치(쿼리 없음·템플릿 SQL·property·async property × 끔·켬)로 다시 재서 문서에 남긴다.
스택 탐색에는 줄일 여지가 있다. 노드 위치를 찾은 뒤에는 바깥 프레임에서 지역 변수를 꺼내지 않고, 코드 객체 비교만
남길 수 있다. 다만 이번 측정에서 그 최적화의 효과는 잡음보다 작았다. 켠 상태의 비용이 견딜 만한지(템플릿 SQL이
많은 렌더에서 +10~15%)는 §9의 결정 1에 넣었다.

§4-1의 수치는 2판의 위치 규칙(Django의 `render_annotated` 코드 객체만 비교)으로 잰 것이다. 3판의 규칙은 이름이
`render`·`render_annotated`인 프레임마다 `self`를 꺼내 본다(§1-2). 노드 위치를 찾으면 멈추므로 대개 가장 안쪽의 몇
프레임에서 끝나지만, 쿼리당 비용은 조금 늘 수 있다. 3판 규칙으로 같은 벤치를 다시 돌렸지만, 그때 기계의 부하로
기준(base)조차 1,193~1,772 µs로 흔들려 결과로 쓰지 않았다. 구현 PR의 재측정이 3판 규칙의 수치를 남긴다.

### 4-4. 구현 후 재측정 (3판 규칙, #182 1·2단계)

벤치는 [`bench/render_queries.py`](../../bench/render_queries.py)이고, 원본 결과는
[`bench/results/fdf39c7-render-queries.json`](../../bench/results/fdf39c7-render-queries.json)이다. 조건은 §4-1과 같다.
`mount()`의 `render_diff()`를 워밍업 30회 뒤 40회씩 15라운드 돌렸고, 템플릿마다 100행 `{% for %}`가 있다. 켠 상태에서는
`wireview.queries` 로거를 `ERROR`로 두었다. 그래서 수집과 반복 판정의 비용만 재고, 로그 출력의 비용은 재지 않았다.

측정하는 동안 이 기계에는 다른 작업(Go 테스트, Parallels VM)이 돌아 load average가 7~9였다. 그래서 §4-1처럼 프로세스를
번갈아 띄우는 방식(base·off·on)은 회차마다 수백 µs씩 흔들려 차이를 읽을 수 없었다. 그 결과도 JSON에 남겼다. 아래 표는
**한 프로세스 안에서 끔과 켬을 라운드마다 번갈아** 잰 것이다(`--paired`). 오가는 부하가 양쪽에 함께 걸리므로, 라운드별
차이의 중앙값은 흔들림이 작다. 칸은 프로세스 5개씩 두 번, 모두 10회의 범위다.

| 렌더 | 끔 | 켬 | 켬 − 끔 (라운드 차이의 중앙값) |
|------|----|----|--------------------------------|
| 쿼리 없음 | 298~353 µs | 301~359 µs | **0~7 µs** |
| 템플릿 SQL 7건(`{% for c in choices %}` + N+1 ×6) | 1,099~1,393 µs | 1,166~1,491 µs | **+52~142 µs** (SQL 하나에 약 7~20 µs) |
| sync property SQL 1건 | 509~845 µs | 526~816 µs | −16~+31 µs |
| async property SQL 1건 | 719~1,048 µs | 717~1,027 µs | −33~+75 µs |

- 템플릿 SQL 하나의 비용은 2판 스파이크의 15~22 µs(§4-1)와 같은 범위다. 3판 규칙은 이름이 `render`·`render_annotated`인
  프레임마다 `self`를 꺼내 보지만, 가장 안쪽 노드에서 멈추므로 대개 프레임 몇 개에서 끝난다.
- "끔"은 래퍼가 설치된 상태다(벤치가 `wireview.testing`을 import한다). 그래서 이 표의 끔은 비활성 래퍼의 비용을 포함한다.
  끔과 base(기능 전 트리)의 차이는 부하 속에서 ±200 µs로 흔들려 측정되지 않았다. §4-2의 쿼리당 +0.7~1.0 µs는
  `ContextVar` 하나만 읽는 스파이크 래퍼의 값이라 지금 구현의 값이 아니다. 지금의 비활성 래퍼는 `ContextVar`가 비어
  있으면 열린 블록 수와 설정도 본다(미귀속 카운터). 경계도 래퍼와 무관하게 설정을 조회한다. 둘 다 따로 재지 않았다.
- 같은 벤치를 조용한 기계에서 `--compare <base 트리>`로 다시 돌리면 base·끔·켬의 절대값을 얻는다.

## 5. 표시 (소비자)

### 5-1. 1차 범위: 로그

로거는 `wireview.queries`다. 스코프 하나(렌더·핸들러·작업·서명)가 끝날 때 쿼리가 있었으면 한 덩어리를 `DEBUG`로
남긴다. 같은 파일:줄의 같은 SQL이 문턱(기본 3) 이상 반복되었으면 `WARNING`으로 남긴다. 형식은 약속하지 않는다.

```text
wireview.queries WARNING render SpShelf#shelf (event bump): 23 queries, 2 repeated
  property total                          1  SELECT COUNT(*) AS "__count" FROM "quiz_choice"
  property first_title (async)            1  SELECT … FROM "quiz_quiz" … LIMIT 1
  templates/shelf/child.html:4  for       1  SELECT … FROM "quiz_choice" …
  templates/shelf/child.html:5  {{ }}     6× SELECT … FROM "quiz_question" WHERE "id" = %s   ← repeated
  templates/shelf/card.html:3   {{ }}     6× SELECT … FROM "quiz_quiz" WHERE "id" = %s       ← repeated (func sp_card)
  render SpNested#nest @ child.html:8     1  SELECT COUNT(*) …
wireview.queries DEBUG handler SpShelf#shelf.bump: 1 query
wireview.queries DEBUG shared render T2Board#board: verify render (taken from another connection): 4 queries
```

스코프 없이 실행된 SQL(§7)은 이 덩어리에 넣지 않는다.

### 5-2. 1차 범위: `wireview.testing`

`MountedComponent`는 문서화된 멤버가 공개다([COMPATIBILITY](../COMPATIBILITY.md)). 메서드 하나를 더한다.

```python
view = await mount(Shelf)
async with view.queries() as q:          # 이 블록의 call()·render_diff()·render()가 부른 쿼리
    await view.call("bump")
    await view.render_diff()
assert q.count <= 5
q.assert_no_repeats()                    # 같은 파일:줄·같은 SQL이 두 번 이상이면 실패. 메시지는 5-1의 표
```

**공개는 `view.queries()`, 그것이 주는 `q.count`, `q.assert_no_repeats(threshold=2)` 셋이다.** 행 목록과 실패 메시지의
모양은 공개가 아니다(1판의 5-2와 §9가 서로 다르게 말하던 것을 이렇게 하나로 정했다). 이 블록은 설정과 무관하게
수집한다. 꺼진 프로세스에서 어떻게 켜는지는 §6-2다.

블록의 계약:

- 블록 안에서 만들어진 컨텍스트(그 안에서 부른 핸들러·렌더, 그 안에서 만든 작업 태스크)가 블록이 끝나기 전에 실행한
  SQL을 센다. 블록 전에 만든 태스크는 수집기를 모르므로 세지 않는다.
- 블록이 끝난 뒤 그 작업이 실행한 SQL은 버린다(수집기에 닫힘 표시. §6-2 재현).
- 블록을 겹치면 안쪽에서 센 것을 바깥도 센다.
- 다른 태스크가 모아서 처리하는 일(묶음 서명)의 SQL은 그것을 요청한 쪽의 블록에 들어간다(§1-4, §2-4). 요청한 쪽의
  블록이 이미 닫혔으면 버린다.
- 동시 세션은 각자의 컨텍스트라 섞이지 않는다.

### 5-3. 후속: 패널·편집기

VS Code 확장([editor-support](../features/editor-support.md))이 인라인 힌트를 그리는 데는 파일 하나면 된다. 개발 서버가
스코프마다 `.wireview/render-queries.jsonl`(이미 gitignore된 디렉터리)에 한 줄씩 덧붙인다.

```json
{"version": 1, "at": "2026-10-08T06:16:51Z", "scope": "render", "render": "event", "component": "SpShelf", "id": "shelf",
 "rows": [{"template": "/abs/path/templates/shelf/child.html", "line": 5, "node": "variable", "count": 6,
           "sql": "SELECT … WHERE \"id\" = %s"}]}
```

`template`은 노드의 `origin.name`(파일 시스템 로더면 절대 경로)이다. 확장은 열린 파일의 경로로 행을 골라 줄 끝에
"6 queries"를 단다. 형식은 메타데이터 JSON처럼 자기 `version`으로 따로 매긴다. 패널(브라우저 오버레이 등)은 같은 행을
소켓으로 보내는 일이라 프로토콜을 건드린다. 이 메모의 범위 밖이다.

## 6. 쿼리 래퍼의 설치와 수명

### 6-1. 맨 아래에, 한 번

Django의 `BaseDatabaseWrapper.execute_wrapper()`는 끝날 때 자기 래퍼를 찾아 지우지 않고 **목록 끝을 `pop()`**한다.
1판의 스파이크처럼 `connection_created`에서 `append`하면 깨지는 경우가 있다. 다른 계측이
`with connection.execute_wrapper(other)` 안에 있을 때 그 연결의 첫 커서가 열리면, 블록 안에서 `[other, ours]`였다가
블록이 끝나면 `ours`가 빠지고 `other`가 남는다(리뷰의 재현, 아래 표 첫 줄).

권고는 **목록의 맨 아래(`insert(0, …)`)에, 이미 있으면 다시 넣지 않는 것**이다. 다른 계측의 임시 래퍼는 언제나 그
위에 쌓였다가 자기 것만 `pop`된다. 스크래치 재현(SQLite 별칭 둘, Django 6.0):

| 경우 | 결과 |
|------|------|
| `append`(1판), 다른 `execute_wrapper` 안에서 첫 연결 | 안 `[other, ours]` → 밖 `[other]` **실패** |
| 맨 아래, 같은 경우 | 안 `[ours, other]` → 밖 `[ours]` |
| 맨 아래, 닫았다 다시 연결(`connection_created`가 다시 옴) | `[ours]` 하나 |
| 맨 아래, 다른 블록 안에서 재연결 후 예외 | `[ours]` |
| 맨 아래, 블록을 겹친 안에서 재연결 | 안 `[ours, other, other]` → 밖 `[ours]` |

래퍼가 하는 일은 계측의 상태에 따라 둘로 나뉜다.

- **활성**(설정이 켜졌거나 `queries()` 블록이 열려 있음): 열린 스코프가 있으면 행을 넣는다. 열린 스코프가 없는 SQL은
  프로세스 수준의 "미귀속" 카운터를 올린다(§7). 닫힌 스코프에는 쓰지 않는다.
- **비활성**(래퍼는 설치되어 있지만 계측이 꺼짐): `ContextVar` 하나를 읽고 그대로 지나간다. 카운터도 올리지 않는다.
  §4-2의 +0.7~1.0 µs는 이 비활성 래퍼의 비용이다. 미귀속 카운터는 그 안에 들어 있지 않다.

### 6-2. 누가 켜나: 설정, 시작, 그리고 `queries()`

연결은 스레드(asgiref `Local`)마다 따로이고, 다른 스레드의 연결을 열거하는 공개 API는 없다. `ContextVar`가 실행
스레드로 건너가는 것과, 그 스레드의 연결에 래퍼가 있는 것은 별개의 일이다. 설치 주체를 이렇게 정한다.

| 언제 | 누가 | 무엇을 |
|------|------|--------|
| 시작 | `apps.ready()` | 설정(`DEBUG_RENDER_QUERIES`, `None` = `DEBUG`)이 참이면 `connection_created`를 연결하고 이 스레드의 열린 연결(모든 별칭)에 설치한다. 그 뒤 어느 스레드에서든 새로 열리는 연결은 시그널이 받는다 |
| 테스트 | `wireview.testing`을 import할 때 | 설정과 무관하게 같은 일을 한다. 테스트 모듈은 보통 첫 DB 연결보다 먼저 import된다 |
| `queries()` 진입 | 그 블록 | 시그널이 아직 없으면 연결하고, 닿을 수 있는 스레드의 열린 연결에 설치한다. 그 스레드는 호출 스레드와 `sync_to_async(thread_sensitive=True)`의 워커다(모든 별칭) |
| 해제 | 하지 않는다 | 한 번 설치한 래퍼는 남는다. 수집기가 없으면 `ContextVar` 하나를 읽고 지나간다(+0.7~1.0 µs/쿼리, §4-2). 다른 스레드의 연결 목록을 바깥에서 고치는 일을 만들지 않는다 |

재현(스크래치, 위와 같은 조건):

- 끈 상태에서 async ORM으로 thread-sensitive 워커의 `default`·`other` 연결을 먼저 열었다. 그다음 늦게 켰다(시그널 +
  호출 스레드 + `sync_to_async(install)`).
- 그러자 두 별칭의 쿼리가 모두 잡혔다.
- 블록 안에서 만든 태스크가 블록 뒤에 실행한 쿼리는 버려졌다.
- **`thread_sensitive=False`로 미리 연결을 연 풀 스레드의 쿼리는 0/8건 잡혔다.** 닿을 수 없는 스레드의 한 예다.

이 마지막 구멍을 어떻게 다룰지가 §9의 결정 2다.

- 권고는 위 표 그대로다. 빈틈을 일반 규칙으로 쓰면 이렇다. **계측이 켜지기 전에 연결을 열었고, `queries()`
  진입 때 닿지 못하는 기존 스레드 전부**가 빈틈이다. 닿는 스레드는 호출 스레드와 지금 컨텍스트의 thread-sensitive
  워커뿐이다. 그래서 다음이 모두 빈틈에 든다.
  - `thread_sensitive=False` 풀 스레드
  - 다른 `ThreadSensitiveContext`(예: 그 전에 처리된 ASGI 요청)의 워커
  - 사용자가 만든 스레드

  wireview의 경로와 Django의 async ORM은 호출 컨텍스트의 thread-sensitive 워커를 쓴다. 그러므로 `mount()`로 띄운
  컴포넌트의 경로는 빈틈에 들지 않는다. 이 규칙을 `queries()` 문서에 한계로 적는다.
- 빈틈을 없애려면 래퍼를 **언제나**(운영 포함) 비활성으로 설치하면 된다. 비용은 쿼리당 약 1 µs다.
- 1판의 "꺼져 있으면 어느 스레드에도 래퍼가 없다"는 이렇게 고친다. **설정이 꺼져 있고 `wireview.testing`을
  import하지 않은 프로세스에는 래퍼가 없다.**

## 7. 범위 밖과 위험

| 무엇 | 어떻게 보이나 |
|------|---------------|
| 핸들러·`joined()`·`mount`·`params_changed`·`update_many` | 자기 스코프의 덩어리로 따로 보인다. 렌더 덩어리에 섞지 않는다. 템플릿 줄은 없다 |
| `start_async`·`assign_async` 작업 | 작업 스코프. 경계를 빠뜨리면 §2-1처럼 **만든 핸들러로 간다.** 구현에서 가장 놓치기 쉬운 곳이라 테스트로 고정한다 |
| 상태 서명 묶음 | 요청 시점의 계측 상태를 항목에 담아 항목마다 되살린다(§1-4). 이름만 붙이면 §2-4의 대조군처럼 **다른 연결의 블록으로 간다** |
| async ORM이 다른 스레드에서 돈 쿼리 | `ContextVar`가 `sync_to_async`로 넘어가므로 스코프는 맞다. 스택에는 템플릿도 property도 없으므로 위치는 경계(async property 이름)로만 |
| 스코프가 없는 SQL | **프로세스 수준**의 "미귀속" 카운터(스레드별)만 올린다. 다른 HTTP 요청, 관리 명령, 사용자 스레드, 컨텍스트를 넘기지 않은 백그라운드 코드의 SQL이 모두 여기 온다. 그래서 어느 컴포넌트에도 붙이지 않고, 어느 컴포넌트의 "누락"으로도 세지 않는다. 컨텍스트 없는 SQL에서 원래의 소유자를 되찾을 길은 없다 |
| `Broadcast` 항목 | 연결이 아니라 발행의 덩어리 |
| 운영 | 켜지 않는다. 기본값이 DEBUG이고, 켜도 수집은 스코프 하나 동안의 목록뿐이라 메모리가 쌓이지 않는다. 운영 관측성은 [#124](https://github.com/itda-work/django-wireview/issues/124)의 telemetry 몫이다 |
| Django 내부 의존 | 노드 프레임의 판별 — 코드 이름이 `render_annotated`·`render`이고 그 프레임의 `self` 지역 변수가 `Node`인 것(§1-2). 노드의 `origin`·`token.lineno`, wireview 래퍼의 `original_node`·`for_node`·`inner`. 컨텍스트 수집 프레임(`_collect_context`·`_get_context`)의 `attr_name`. `execute_wrappers`의 목록 의미와 `execute_wrapper()`의 `pop()`. 아래 가드가 지킨다 |
| 다른 계측과의 중첩 | debug-toolbar 등의 임시 래퍼와는 §6-1의 설치로 공존한다. `CaptureQueriesContext`도 그대로 동작한다 |
| 쓰지 않는 property | 아래 |

**Django 내부 가드.**

- 계약 테스트는 프레임이 있는지만 보지 않는다. §2-2의 템플릿(상속·`block.super`·cached.Loader 두 번·`let:` 있는/없는
  슬롯·include·함수 컴포넌트)에서 **실제 기대 파일:줄과 스코프**를 단언한다.
- 그리고 §6-1의 래퍼 수명 표를 단언한다. 이 테스트는 `make test`에 있으므로 CI 매트릭스(Django 5.2·6.0·6.1 ×
  Python 3.12·3.13·3.14)와 `test-lowest`·`test-latest`의 모든 칸에서 돈다. `pyproject.toml`에는 Django 상한이 없으니,
  새 Django가 이 가정을 깨면 `test-latest`가 먼저 알린다.
- 사용자 노드에 대한 약속은 §1-2의 표 그대로이고, §2-4의 네 행을 테스트로 고정한다.
  - `render_annotated`를 덮어쓴 노드는 정확하다.
  - `origin` 없는 동적 노드는 바깥 노드의 위치를 근사 표시와 함께 보고한다.
  - 다른 이름의 함수에 묶인 진입은 가장 가까운 관측 가능한 노드로 가며, 그 노드의 파일이라는 보장은 없다.
  - 그 밖의 경우에는 파일을 정하지 않는다.
  - 지원 밖 Django 버전이 노드 프레임의 이름이나 `self` 지역 변수를 바꾸면, 위치는 "템플릿 밖"이나 스코프만으로
    떨어진다. 위 계약 테스트가 그것을 먼저 알린다.

**쓰지 않는 property.** ①은 템플릿이 참조하지 않는 property도 읽으므로, 그런 property의 SQL도 렌더마다 돈다. 1판은
`template_engine.referenced_names()`로 "쓰지 않는 property"를 판정하자고 했는데, 그 판정은 오탐한다.

- `referenced_names()`는 정적 이름 집합이고 줄 정보가 없다.
- 동적 `{% include %}`은 `DRAWS_UNKNOWN`이 된다.
- 사용자 `takes_context` 태그가 파이썬에서 읽는 이름은 보이지 않는다.
- 함수 컴포넌트의 별도 템플릿까지 따라가지 않는다.
- `{{ other }}`의 property가 안에서 `self.total`을 쓰면, 템플릿에는 `total`이 없어도 `total`은 쓰인다.

그래서 1차 범위는 **"컨텍스트 수집에서 실행된 SQL"을 property 이름과 함께 보여 주는 것까지**다. 그것은 확정 정보다.
"쓰지 않는다"는 판정은 하지 않는다. 진단을 더한다면 문구는 "정적 템플릿에서 직접 참조를 찾지 못함"으로 한정한다.
`DRAWS_UNKNOWN`·사용자 태그·간접 의존이 있으면 진단하지 않는다. 쓰인 줄까지 보여 주려면 지금의 API 밖의 분석이
필요하다. 이 선택은 §9의 결정 5다.

## 8. 구현 계획

| 단계 | 내용 | 바꿀 파일 | 크기 |
|------|------|-----------|------|
| 1 | 수집기·경계·래퍼·로그 | 새 `wireview/debug/render_queries.py`(스코프, 래퍼 설치, 스택 위치, 집계·로그), `core/meta.py`(렌더·async property 경계), `core/component.py`(작업·스트림 항목·`_mount`), `repository.py`(핸들러), `session.py`(`joined()`·수신자), `core/shared_render.py`(검증 렌더 표시, `_sign_each` 경계), `core/patches.py`(발행), `settings.py`(`DEBUG_RENDER_QUERIES`, `None` = DEBUG; `REJOIN_ON_TEMPLATE_CHANGE`와 같은 꼴), `apps.py`(켜져 있으면 설치) | 코드 350~450줄 |
| 2 | 테스트 단언 | `testing.py`(`MountedComponent.queries()`, import 때 설치), `docs/features/testing.md` | 100~150줄 |
| 3 | 후속 이슈: JSONL 싱크와 VS Code 인라인 힌트 | `editors/vscode/` | 별도 |

테스트:

- **위치 계약 표**: §2-2의 표를 그대로 단언한다. 상속·`block.super`·cached.Loader 반복·`let:` 있는/없는 슬롯·include·
  함수 컴포넌트에서 파일:줄과 스코프를 본다. §2-4의 사용자 노드 네 행(덮어쓴 노드 둘, `origin` 없는 동적 노드,
  다른 이름의 진입)도 넣는다.
- **경로 계약 표**: 경로(join·이벤트·HTTP·중첩·LiveComponent·스트림 항목·`Broadcast` 항목·작업·핸들러) × 쿼리 위치
  (sync property·async property·템플릿 줄)를 `parametrize`로 돈다. `tests/test_live_session_contract.py`처럼 경로를 새로
  만들면 행을 더한다.
- **shared render**: §2-3의 세 설정에서 leader·taker별 건수와 종류(렌더·검증 렌더·서명)를 본다. 서명은 자기 연결의
  것으로 간다.
- **묶음 서명의 주인**: §2-4의 표를 본다. 요청 A·B가 서로 다른 `queries()` 블록에서 한 묶음으로 서명되면 각 1건이다.
  한쪽 블록이 먼저 닫히면 그쪽 것은 버린다. 블록을 겹치면 바깥도 센다. 서명 스코프의 부모는 요청한 쪽의 렌더다.
  되살리기를 지우면 실패한다.
- **격리**: 두 세션 동시 join(라이브), 두 HTTP 요청 동시(ASGI)에서 각자 자기 건수인지 본다.
- **작업 스코프**: `assign_async` 작업의 쿼리가 핸들러가 아니라 작업으로 가는지 본다. 경계를 지우면 실패한다.
- **래퍼 수명**: §6-1의 표 다섯 줄과 §6-2의 늦은 활성화(모든 별칭, thread-sensitive 워커, 블록 뒤 작업 버림)를 본다.
- **`queries()`**: 꺼진 프로세스(`DEBUG=False`)에서 async SQL을 먼저 실행한 뒤 블록이 잡는지, 블록 겹침, 동시 세션을 본다.
- **끈 상태**: 설정이 꺼져 있으면 경계가 스코프를 만들지 않는지 구조로 검사한다.
- **비용**: §4-1의 벤치를 `bench/`에 두고 PR에 수치를 남긴다.
- E2E는 필요 없다. 브라우저가 보는 것이 없다.

문서:

- `docs/features/render-queries.md`(새 페이지, `docs/site.toml`에 분류)
- `docs/features/settings.md`, `docs/features/testing.md`
- `docs/PERFORMANCE.md`에 N+1 찾는 법 한 절
- `CHANGELOG.md`
- `CLAUDE.md` 저장소 지도의 `debug/` 줄

1·2단계를 PR 하나로 하면 코드·테스트·문서를 합쳐 1,100~1,500줄로 본다.

## 9. 메인테이너가 정할 것

1. **훅 형태와 켠 상태의 비용.** 세 가지가 있다.
   - (b) 내부 경계 + 스택 위치(권고). 2판 스파이크에서 관측한 참고 비용은 템플릿 SQL 하나에 약 15~22 µs이고, 쿼리가
     없는 렌더와 끈 상태는 측정 잡음 안이었다. 3판 규칙의 비용은 구현 PR에서 재측정한다.
   - 노드마다 경계. §4-2의 마이크로벤치(SQL 없는 200행 HTTP 렌더)에서 켜면 +3~8%였다. 비율은 렌더의 노드 수에 비례하고
     SQL이 지배하는 렌더에서는 작아진다. 쿼리당 비용은 작다.
   - (a) telemetry.
2. **래퍼를 언제 설치하나.** 두 가지가 있다.
   - 설정이 켜졌을 때·`wireview.testing` import 때·`queries()` 진입 때, 한 번 설치하면 남긴다(권고). 늦게 켜면
     "켜기 전에 연결을 열었고 진입 때 닿지 못하는 기존 스레드"(§6-2)의 SQL은 안 보인다는 한계를 문서에 적는다.
   - 운영까지 언제나 비활성으로 설치한다. 빈틈이 없고, 쿼리당 약 1 µs다.
3. **설정 키.** `DEBUG_RENDER_QUERIES`(`None` = DEBUG, 권고), DEBUG에 직접 묶기, 기본 꺼짐 중에서 고른다.
4. **반복 경고 문턱.** 3(권고) 또는 2.
5. **쓰지 않는 property.**
   - 수집 단계의 SQL을 property 이름으로 보여 주는 것까지만 한다(권고).
   - "정적 참조 없음" 진단을 더하되, 불확실한 경우는 진단하지 않는다.
   - 렌더의 property 읽기 자체를 좁히는 별도 이슈를 연다.
   - 셋 중 어느 쪽이든, 사용자 문서(component-api)에 "렌더는 컨텍스트 대상 공개 속성을 모두 읽는다"를 적을지도 함께 정한다.
6. **단계 3(JSONL·VS Code)을 이 이슈에 둘지, 새 이슈로 뗄지.**
