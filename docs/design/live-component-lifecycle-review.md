# LiveComponent 수명주기 조사에 대한 리뷰 원문 (Codex gpt-6-astra, 2026-09-09)

> [live-component-lifecycle.md](./live-component-lifecycle.md) 초판을 검증한 결과다.
> 초판의 결론 하나(재연결 상태 손실)가 **틀렸고**, 두 개가 부정확했다. 이 리뷰로 문서를 다시 썼다.
> 여기서 나온 버그는 별도 이슈로 뗀다.

---

검토 기준: 2026-09-09 작업 트리 및 `7cb37dc` 반영본. 코드 수정 없음.
`CLAUDE.md`, `wireview-dev` 절차와 지정 문서를 읽었다. GitHub wip 조회는 네트워크 제한으로 실패했으며, 외부 이슈 작성·댓글은 하지 않았다.

핵심 결론: 등록·인라인 렌더 시점 조사는 맞지만, **재연결하면 자식 상태가 반드시 사라진다는 결론은 실제 클라이언트 경로와 다르다.** 부모 join 직후 자식도 개별 join하여 상태를 다시 덮어쓰며, 같은 인스턴스의 joined()가 두 번 실행된다. 또한 params_changed·브로드캐스트 경로에는 flush 자체가 없다.

## 1. 다섯 주장 판정

| 항목 | 판정 | 정정 요점 |
|---|---|---|
| 1-1 | 맞음 | 새 자식은 부모 템플릿 평가 중 등록·렌더되고 joined는 뒤다. 기존 인스턴스 재사용에는 이 표현을 그대로 적용하지 말 것. |
| 1-2 | 부정확 | join·user_event·hook_event에는 해당하지만 params_changed·브로드캐스트에는 flush가 없다. 서버 전송과 브라우저의 실제 paint도 구분해야 한다. |
| 1-3 | 부정확 | pending 진입 비대칭은 맞다. 다만 stream/push_js의 세션 큐 투입을 WebSocket 전송으로 해석하면 틀린다. changed_props는 실제 변경 여부를 검사하지 않는다. |
| 1-4 | 틀림 | build_live_component 단독 관찰은 맞지만 브라우저 재연결 전체에 대한 결론은 틀림. 자식 개별 join의 복원 경로를 누락했다. |
| 1-5 | 부정확 | 서버 렌더 기반 고아 판정이 없다는 핵심은 맞음. 정상 morph·stream·remove는 스캔한다. 연결 종료는 기존 저장소의 장기 고아 존속과 다르다. 실제 미통지 경로는 별도로 존재한다. |

### 1-1: HTTP 렌더까지 포함하면 더 이르다

`templatetags/wireview.py:787`의 build → `:794`의 _render, `repository.py:144` 이후의 생성 → _parent_id 설정 → 등록 → pending 추가 순서는 조사대로다.

HTTP의 `_build_and_render_component`도 is_live=False 저장소에서 같은 템플릿 태그를 실행한다(`templatetags/wireview.py:60`). 여기서는 자식 joined를 flush하지 않는다. 따라서 “joined 이전 HTML 노출” 문제는 WebSocket 첫 응답만이 아니라 **최초 HTTP HTML에도** 있다. joined만 인가 경계로 삼으면 부족하다.

### 1-2: 진입점별 실제 순서

| 진입점 | 순서 및 차이 |
|---|---|
| command_join | 부모 repo.join/joined → 부모 send_render → 자식 flush(joined/update) → 자식 send_render/flush_pending. URL params가 있으면 부모 params_changed → render → 자식 flush를 한 번 더 수행. 마지막에 부모 pending과 chores. |
| command_user_event | 이벤트 → 대상 render → 자식 flush → chores. |
| command_hook_event | hook handler → ref가 있으면 hook_reply → 대상 render → 자식 flush → chores. |
| command_params_changed / legacy query_string | 현재 components의 스냅샷을 돌며 params_changed → render. 그 뒤 chores만 수행. **자식 flush 없음.** |
| notification / model_mutation | _dispatch_notifications에서 구독 컴포넌트별 callback → render → chores. **자식 flush 없음.** |
| component_dispatch_event | callback → render → 자식 flush → chores. send_to_parent로 보낸 이벤트의 수신도 이 경로다. |
| component_send_render | render → 자식 flush. chores는 없음. |
| component_update_live_component | update → 자식 render. pending 자식 flush와 chores 없음. |
| upload register/cancel/complete | 해당 send_render 이후 자식 flush 없음. |

근거: `consumer.py:75,124,155,165,296,299,308,350,362,445,584`.

params_changed나 브로드캐스트로 처음 표시되는 자식은 HTML과 서버 등록은 생기지만 joined가 실행되지 않은 상태로 남을 수 있다. 기존 자식의 prop update 역시 큐에만 쌓이고, 나중에 관련 없는 user_event/join 등이 flush할 때 실행될 수 있다. after_mutation_chores는 구독과 query string만 처리하며 이 누락을 보완하지 않는다(`consumer.py:608`).

“화면이 두 번 그려진다”는 단정도 완화해야 한다. 서버의 부모/자식 렌더·전송 단계가 나뉘는 것은 맞지만, diff 생략·requestAnimationFrame 배치·후술할 자식 diff 유실에 따라 실제 paint 횟수는 달라진다. 민감한 HTML을 이미 보냈다는 문제는 paint 여부와 무관하게 성립한다.

### 1-3: pending 큐와 전송을 구분해야 한다

`repository.py:199`는 새 자식만 enter_pending_mode하고, `:208`의 기존 자식 update에는 없다. 이 비대칭 자체는 맞다.

그러나 stream/push_js는 일반적으로 다음 경로를 탄다.

`wire.send → _do_send → broker.send_to_session → channel-layer message_from_component → consumer.component_* → outbound`

`core/meta.py:446`, `core/transport.py:80`, `consumer.py:347` 참조. 현재 Channels는 dispatch를 await하여 순차 처리한다(설치된 channels/utils.py의 await_many_dispatch 확인). update가 자기 세션에 넣은 메시지는 현재 handler의 send_render가 끝난 뒤 수신 처리된다. 따라서 **이 비대칭만으로 현재 연결에서 stream/push_js가 자식 render 프레임보다 먼저 나간다는 주장은 성립하지 않는다.** 큐에 먼저 들어가는 것과 브라우저에 먼저 도착하는 것은 다르다.

반면 pending이 아닌 broadcast는 즉시 publish할 수 있으므로 다른 세션에서 먼저 관찰될 수 있다(`core/meta.py:149`). 임의 외부 I/O도 pending이 막아 주지 않는다. “모든 부수효과를 렌더 뒤로 미루는 장치”로 설명해서도 안 된다.

추가 정정:

- changed_props는 이름과 달리 값 비교를 하지 않는다. id를 제외한 **전달된 모든 모델 필드**다(`repository.py:137`).
- 따라서 부모가 count=0을 계속 전달하면 자식이 자체 이벤트로 7까지 올려도 다음 부모 렌더/flush에서 0으로 덮어쓴다. “초기 props”만은 아니다.
- 필드가 아닌 prop의 사전 필터는 템플릿 경로에만 있다. send_update 경로는 사용자 update에 assigns를 그대로 전달한다(`consumer.py:482`). 기본 update가 알 수 없는 필드를 무시하는 것과 사용자 override에 전달조차 하지 않는 것은 다른 계약이다.
- model_fields의 인스턴스 접근 문제는 타당하다. 이 검토에서는 경고 자체를 별도로 재현하지 않았다.

### 1-4: 실제 재연결은 부모 children 전송에서 끝나지 않는다

`7cb37dc`의 `child_state | state` 설명은 build 함수에 대해서는 정확하다. 그러나 브라우저 전체의 최종 복원 결과로 일반화하면 여전히 틀린다.

클라이언트는 `wireview-live`를 join에서 제외하지 않는다.

1. close 시 모든 [wireview-component]의 data-is-live를 false로 바꾸고 클라이언트 components를 비운다(`wireview.js:119`).
2. open 시 다시 components를 비우고 joinAllComponents를 실행한다(`:104`).
3. DOM 순서로 부모 join()이 실행된다. 이 함수는 서버 ACK를 기다리지 않고 **그 자리에서 부모 data-is-live=true**로 바꾸며 children과 함께 join을 전송한다(`:938`).
4. 같은 동기 루프에서 자식도 방문한다. 부모가 이미 true이므로 자식은 자신의 data-state로 **별도의 join을 전송한다**. LiveComponent 예외가 없다.
5. 서버는 부모 join을 처리하면서 자식을 초기 props로 만들고 joined를 호출한다.
6. 이어 자식의 command_join이 repo.join → 일반 build를 호출한다. 이미 등록된 인스턴스에 서명 상태의 필드를 덮어쓰고 joined를 다시 호출한다(`repository.py:82,215`).

#### 실행 확인

실제 wireview.js의 join() 본문을 추출해 최소 DOM 대역으로 실행한 결과:

```text
Parent join: parent-signed, children={c:[Child,count7-signed]}
Child join:  count7-signed, children={}
```

실제 ComponentRepository를 사용하여 이 메시지 순서를 재생했다. 자식은 count=0, note="default"가 기본값이며 저장 상태는 count=7, note="kept", 부모 prop은 count=0이다.

```text
after parent render/flush: count=0 note=default
joined calls: [(c,0,default)]

after child join: count=7 note=kept, _parent_id=p
joined calls: [(c,0,default), (c,7,kept)]
```

즉 **초기화된 상태로 한 번 joined/렌더된 뒤 서명 상태가 복원되고 joined가 다시 실행된다.** “복원이 없다”보다 “복원 경로가 이중이며 초기화 훅 전에 일관되게 복원되지 않는다”가 정확한 결함이다. 훅 자체가 값을 초기화하거나 후속 부모 update가 들어오면 최종 값은 또 달라진다.

#### 상황별 구분

| 상황 | 관찰 |
|---|---|
| 기존 브라우저 페이지의 실제 WebSocket 재연결 | 이전 DOM의 서명 상태가 남음. 부모와 자식 모두 join하므로 위 복원·중복 joined가 가능. 새 consumer이므로 **서버의 이전 부모 인스턴스가 살아서 재사용되는 것은 아님**. |
| 동일 연결에서 부모는 live이고 자식 DOM만 false인 상태의 재join | 자식의 직접 join이 실행됨. 서버에 자식이 있으면 상태 덮어쓰기·joined 재호출, 없으면 일반 build로 생성되어 _parent_id를 세우지 못할 수 있음. |
| 부모 이벤트가 새 LiveComponent를 라이브 HTML로 추가 | 서버가 data-is-live=true로 렌더함. 이후 DOM 스캔은 클라이언트 객체를 만들지만 별도 join은 안 보냄. 이 경로는 부모 flush만으로 초기화됨. |
| 페이지 새로 열기/전체 reload | 이전 페이지 DOM의 count=7을 보내는 것이 아님. 새 HTTP 응답의 초기 서명 상태를 부모·자식 join으로 전송함. 별도 영속 저장이 없다면 예전 값이 없는 것은 정상이다. 여기서도 중복 joined는 발생 가능. |
| 서버 parent만 join하는 테스트 | children을 무시하는 build_live_component 문제를 잘 드러내지만 현재 브라우저 재연결 전체의 재현은 아님. |

일반 중첩 컴포넌트도 별도 join으로 서명 상태를 다시 덮어쓸 수 있다. `7cb37dc`의 표에는 “부모 렌더 중 build 단계의 병합 결과”라는 범위 표시가 필요하다.

### 1-5: 정상 스캔 경로와 누락 경로

**정상적으로 스캔하는 경로**

- WebSocket open → joinAllComponents.
- boost newContent → joinAllComponents(`wireview.js:140`).
- render의 requestAnimationFrame 안에서 유효한 element/html을 morph한 뒤 sendNewContent(`:786`).
- append/prepend/insert/replace_with와 remove 명령(`:186`).
- stream reset/insert/동일 id replace/delete/limit trim 완료 후 sendNewContent(`:384–453`).
- boost의 정상 콘텐츠 교체(`wireview-boost.js:146`).

sendNewContent는 동기 EventTarget 이벤트다(`wireview-boost.js:96`). 따라서 “스트림 컨테이너 교체나 삭제 자체 때문에 leave가 안 온다”는 예시는 정상 내장 경로에 해당하지 않는다.

**실제로 통지가 없거나 지연되는 경로**

1. 사용자 hook/외부 JS가 element.remove()/innerHTML 등을 실행하고 newContent를 보내지 않는 경우. HookManager의 MutationObserver는 hook destroy를 처리하며 저장소용 leave 스캔은 하지 않는다(`wireview.js:1225`).
2. exec_js 자체는 스캔하지 않는다(`:299`). 다만 내장 JS 명령에 일반 remove 명령은 없고 hide는 DOM 제거가 아니다. 구체적 예는 JS.dispatch가 부른 사용자 listener에서 DOM을 제거하거나, remove_attr로 wireview-component 표지를 없애는 경우다. “JS 명령으로 제거”라는 포괄적 표현은 좁혀야 한다.
3. render가 아직 RAF 대기 중이거나, 대상 element/클라이언트 객체가 없거나, html이 없으면 그 render에서 스캔하지 않는다. 다음 정상 newContent까지 정리가 늦어질 수 있다.
4. 서버에는 생성됐으나 클라이언트 components에는 한 번도 등록되지 않은 id. 예를 들어 브라우저의 두 부모 패치가 한 프레임에 처리되어 첫 패치가 자식을 추가하고 곧 다음 패치가 제거하는 경우, 서버에 이미 큐잉된 자식 개별 join이 이후 다시 객체를 생성하면 클라이언트는 그 id를 더 이상 추적하지 않을 수 있다. 더 단순하게는 DOM에 없는 자식의 직접 join을 서버가 받아도 그렇다. 스캔은 **클라이언트에 등록된 id와 현재 DOM**만 비교하므로 서버의 미등록 고아를 발견할 수 없다.

첫째·둘째는 네트워크 패킷 유실보다 **통지를 생성하지 않는 경로**다. 다음 정상 스캔에서 정리될 수 있으나 그 전에 고아가 이벤트·구독 알림을 받을 수 있다. 악의적 클라이언트가 leave를 보내지 않는 경우도 서버 자체의 생존 판정이 없다는 한계를 드러낸다.

연결 종료 자체는 구분해야 한다. disconnect는 등록된 컴포넌트마다 leaving을 호출하고 구독을 제거한다(`consumer.py:47`). 저장소는 연결 단위이고 재연결 때 새로 생성된다. 따라서 “끊긴 leave 때문에 이전 객체가 새 연결에서도 영원히 남는다”는 설명은 맞지 않는다.

## 2. 다섯 항목 외 추가 결함·위험

### A. [높음] 초기화가 경로마다 누락되거나 중복된다

위 params_changed/notification의 flush 누락과 브라우저 재연결의 중복 joined가 우선 수정 대상이다. 초기화가 구독·DB 작업·외부 부수효과를 수행하면 단순 화면 깜박임에 그치지 않는다. 새 페이지 최초 접속에서도 HTTP 자식의 별도 join이 중복 실행을 만들 수 있다.

### B. [높음] 부모 render 직후의 새 자식 diff가 클라이언트에서 사라질 수 있다

`wireview.js:184`는 render 수신 순간 `this.components[id]?.applyDiff(diff)`를 호출한다. 부모의 applyDiff는 DOM patch와 새 자식 등록을 RAF까지 미룬다(`:786`).

기존 페이지에 새 자식을 추가할 때:

1. 부모 render 수신 → RAF 예약.
2. 부모 RAF 실행 전에 자식의 joined 후 render 수신.
3. 아직 components[childId]가 없으므로 자식 diff를 버림.
4. 부모 RAF가 joined 이전 자식 HTML을 붙이고 객체를 등록. data-is-live=true이므로 join도 보내지 않음.

서버가 두 번 렌더했다고 최종 joined 상태가 화면에 반드시 반영되는 것이 아니다. 전송 도착과 RAF 타이밍에 따른 코드상 race이며 **실제 브라우저 E2E로 재현한 결과는 아니다.** 후속 자식 diff가 부분 diff이면 클라이언트에 초기 static 기준이 없다는 문제도 이어질 수 있다. 새 자식 등록/첫 diff의 처리 순서를 프로토콜로 보장해야 한다.

### C. [높음] leave에서 leaving()을 호출하지 않는다

`command_leave`는 upload unregister와 repo.remove만 한다(`consumer.py:118`). `repo.remove`는 pop뿐이다. 정상 DOM 삭제로 이미 pop된 자식은 이후 disconnect의 leaving 대상에서도 제외된다. 자원 해제·비동기 작업 취소 등을 leaving에 둔 사용자는 정리 기회를 잃는다.

또한 leave는 일반 구독 재계산을 호출하지 않는다. 마지막 구독자를 삭제한 그룹도 다음 chores 또는 disconnect까지 남을 수 있다. 부모 제거를 서버에서 자식에게 cascade하는 기능도 없다.

### D. [중간] id 재사용 시 클래스·부모 검증이 없다

`build_live_component`의 기존 객체 경로는 isinstance(LiveComponent)만 확인한다(`repository.py:133`). 같은 id로 다른 자식 클래스를 선택하거나 boost 후 다른 부모 아래 배치하면 이전 클래스/이전 _parent_id를 재사용한다. 다른 부모로 send_to_parent가 가거나 send_update의 부모 검증에 걸릴 수 있다.

페이지 내 id 고유 규칙은 동시 충돌을 금지하지만 시간에 따른 교체·이동의 의미까지 정하지는 않는다. LiveComponent와 일반 Component 간 id 충돌에서는 덮어쓰기와 정리 누락도 고려해야 한다.

### E. [중간] update는 변경 감지가 아니며, 최초 초기화에도 일관되게 쓰이지 않는다

최초 생성은 모델 생성자 → joined이며 update를 호출하지 않는다. 기존 부모 렌더는 값이 같아도 모든 명시 prop을 다시 update한다. send_update와 템플릿의 필드 필터도 다르다. 따라서 update에서만 파생값을 계산하는 컴포넌트는 최초 화면과 이후 화면이 달라질 수 있다.

### F. [중간] flush는 큐가 안정될 때까지 도는 작업이 아니며 예외 복구도 없다

`repository.py:195–211`는 큐를 분리해 한 번 처리하고, `consumer.py:441`는 그 결과를 한 번 렌더한다. 그 렌더에서 생성된 새 자식/새 update는 다음 flush까지 남는다. 문서는 LiveComponent 안의 LiveComponent를 미지원이라고 하지만 템플릿 태그에 이를 거부하는 검사도 없다.

joined/update 중 예외가 나면 분리된 큐의 나머지가 처리되지 않고 pending mode가 남을 수 있다. 자식 flush는 command_join의 repo.join try/except 바깥이다. 실제 ASGI 실행에서 예외가 연결 종료로 이어지더라도, 이미 보낸 HTML과 부수효과를 되돌리는 초기화 계약은 없다.

### G. [중간] 구독 준비 전에 초기 broadcast를 flush한다

자식 wire.flush_pending과 부모 wire.flush_pending 뒤에야 after_mutation_chores가 실행된다(`consumer.py:104–116,441`). joined에서 새 채널을 subscribe하고 broadcast한 경우, 이 연결은 아직 그 그룹에 가입하지 않았으므로 자기 초기 알림을 놓칠 수 있다. 다른 기존 구독자는 받을 수 있다. pending queue의 “구독 준비 후 broadcast”라는 의도와 실제 호출 순서가 다르다.

## 3. 설계 질문 평가와 빠진 질문

1. **첫 렌더가 joined를 기다려야 하는가:** 유효하다. 다만 “정책 통과 전에 아무것도 내보내지 않는다”와 “모든 비동기 초기 데이터 로딩을 기다린다”는 별개다. HTTP 렌더, 업데이트로 바뀐 리소스의 재인가, 안전한 loading 화면 허용 여부까지 정해야 한다.
2. **재연결의 정답:** 유효하지만 전제를 교체해야 한다. 현재는 “보내고 무시한다”가 아니라 **children 경로에서는 무시하고 자식 join 경로에서는 적용한다.** 먼저 LiveComponent가 부모 소유 자식인가, 독립 join도 허용하는가를 정해야 한다. 필드별 복원값·부모 prop·joined의 우선순위와 복원 시점도 필요하다.
3. **고아 수명을 서버가 알아야 하는가:** 유효하다. 렌더에서 사라졌다고 즉시 삭제할지, 클라이언트 patch 완료 확인 후 삭제할지, 같은 id의 이동을 보존할지까지 질문해야 한다. Phoenix도 클라이언트가 제거를 관찰한 뒤 컴포넌트를 폐기하므로 클라이언트 통지 의존 자체가 곧 설계 오류는 아니다. [Phoenix LiveComponent 수명주기](https://phoenix-live-view.hexdocs.pm/Phoenix.LiveComponent.html#module-mount-and-update).
4. **pending 비대칭:** 유효하다. 현재 transport에서는 자기 세션 메시지 순서가 우연히 보완하지만 broadcast/실패 처리/향후 transport에도 유지할 계약인지 정해야 한다. 구독 활성화와 DOM patch 완료 시점도 포함해야 한다.
5. **필드 필터:** 유효하다. 기본 update의 unknown-field 무시는 코드와 tests/test_live_component.py:154에서 이미 명시된 동작이다. 그러나 사용자 update override까지 템플릿이 사전 필터해야 하는지는 다른 질문이며 send_update와 불일치한다.

추가로 명시할 질문:

- joined는 인스턴스 생성당 한 번인가, join 메시지마다 한 번인가? HTTP→WS와 reconnect를 어떤 lifecycle로 구분하는가?
- 모든 render 진입점에서 같은 초기화/prop 반영/구독/부수효과 순서를 누가 보장하는가?
- 부모와 자식의 첫 diff, 클라이언트 등록, 실제 DOM patch 완료를 어떻게 연결하는가?
- leave/reparent/id 재사용 때 leaving·구독·upload·async 작업을 누가 정리하는가?
- 최초 update, 동일값 update, 템플릿 미전달 필드의 의미는 무엇인가?
- 초기화 실패나 halt 시 등록·pending·render·부수효과 중 무엇을 롤백하는가?
- 정책을 부모에서 상속할지, 자식에서 별도로 검증할지? 자식의 직접 join에 부모 연결·정책 증거를 요구할지?
- “미지원 중첩”을 실제로 거절할지, 여러 단계 자식의 큐 처리를 보장할지?

`docs/design/live-component.md` §4.1은 이미 joined→render를 설계 목표로 적고 있다. 그러므로 질문 1은 완전히 새 결정이라기보다 **기존 설계를 구현할지, 다른 계약으로 개정할지**다. 반대로 그 문서에는 현재와 맞지 않는 API와 미구현 구조가 많아 전체를 확정된 구현 계약으로 볼 수 없다.

Phoenix를 따르더라도 “자식 상태는 모두 부모 assigns의 파생”으로 한정할 필요는 없다. Phoenix 문서는 부모 또는 자식 중 한쪽을 상태의 진실 소스로 선택하는 두 모델을 설명한다. [Phoenix 상태 관리](https://phoenix-live-view.hexdocs.pm/Phoenix.LiveComponent.html#module-managing-state). 재연결에서는 LiveView mount/handle_params가 다시 실행되므로 영속 저장·부모 데이터·복구 이벤트 등으로 필요한 상태를 재구성하는 문제로 봐야 한다. [Phoenix LiveView 수명주기](https://phoenix-live-view.hexdocs.pm/Phoenix.LiveView.html#module-life-cycle).

## 4. 질문 1: 동기 템플릿 안에서 가능한 선택지

동기 Django 템플릿 때문에 async 훅 실행이 “불가능”한 것은 아니다. 현재 `core/meta.py:270`는 템플릿을 database_sync_to_async로 실행한다. 제약은 전환 비용, DB thread affinity, 렌더 중 상태 변경, 실패 시 전송 경계다.

### 선택 A: 동기 태그에서 async_to_sync로 초기화 후 렌더

태그가 새 자식을 만들면 worker thread에서 async_to_sync로 초기화 훅을 완료한 뒤 HTML을 렌더한다. 호출 경로 전체가 적절한 sync/async adapter 안에 있어야 한다. 이벤트 루프 스레드에서 직접 async_to_sync를 호출하는 방식은 안 된다.

이번 환경에서 아래 중첩을 실행해 성공(`ok`)을 확인했다.

```text
async main
→ sync_to_async(template, thread_sensitive=True)
→ async_to_sync(hook)
→ sync_to_async(DB 대역, thread_sensitive=True)
```

이는 브리지의 기본 실행 가능성을 확인한 것이며 실제 ORM·다수 자식·취소·성능 검증을 대신하지 않는다. 저장소가 이미 async property의 중첩 전환을 줄이려고 한 이유(`core/meta.py:246`)도 존중해야 한다. HTTP 렌더에서도 같은 초기화 훅을 쓸지, 연결 전용 joined와 별도 훅으로 나눌지도 정해야 한다. [Django async adapter 문서](https://docs.djangoproject.com/en/5.2/topics/async/#async-adapter-functions).

### 선택 B: 자식 발견과 실제 렌더/전송을 분리

동기 태그에서는 실제 자식 HTML 대신 컴포넌트 명세/placeholder를 기록한다. 바깥 async 단계가 자식들을 생성·인가·초기화하고, 그 결과를 합성하여 최종 HTML/diff를 보낸다. 루프·조건부 템플릿에서만 알 수 있는 자식도 다룰 수 있다.

단순히 send_render 뒤 flush를 앞으로 옮기면 자식이 아직 존재하지 않는다. 따라서 **render 계산과 전송 API를 분리**하고 자식을 발견할 중간 표현 또는 준비 패스가 필요하다. 초기화 중 조건이 바뀌거나 손자식이 생길 때의 반복/중복 방지, diff 마커와 캐시, 임시 상태 초기화 시점까지 정의해야 한다.

“일단 평범하게 렌더하고 버린 다음 다시 렌더”는 작은 시제품으로는 가능하나, 템플릿 평가·update 큐·부수효과를 두 번 발생시키지 않도록 준비 패스의 의미를 제한해야 한다.

### 선택 C: 안전한 placeholder만 먼저 전송

등록·정책 검사 후 안전한 loading shell만 내보내고 async 초기화 완료 후 실제 내용을 보낸다. 첫 유의미한 콘텐츠가 joined/인가를 기다리는 계약을 만들 수 있다. 자식 컴포넌트의 일반 템플릿이나 민감한 data-state를 먼저 보내지 않아야 하며, CSS로 숨기는 것으로는 안 된다.

이 선택은 placeholder조차 “아무것도 내보내지 않음”에 위배되는지에 따라 정책 문구 조정이 필요하다. 자식 등록보다 diff가 앞서는 race도 함께 해결해야 한다.

### 선택 D: 정책/필수 데이터와 연결 후 작업을 분리

인가와 필수 초기 데이터는 부모의 async 준비 단계 또는 별도 자식 초기화 훅에서 끝낸다. joined는 구독·연결 후 작업을 맡긴다. 느린 선택적 데이터는 loading 상태로 처리한다. 구조가 고정된 앱에서는 부모에서 미리 로딩하는 것이 실용적이지만, 동적으로 발견되는 범용 자식은 B 같은 처리 단계가 여전히 필요하다.

권장 판단 순서는 **인가 완료 전 보호 HTML·상태를 전송하지 않는 경계**를 먼저 확정하고, 전체 초기 데이터까지 대기할지/안전한 shell을 허용할지 결정하는 것이다. joined 한 훅에 정책·DB 로딩·구독·DOM 부수효과를 모두 맡긴 채 호출 위치만 바꾸면 문제를 옮길 가능성이 크다.

### Phoenix는 무엇이 다른가

Phoenix의 공개 계약은 최초 `mount → update → render`, 이후 `update → render`다. 부모 LiveView 프로세스 안에서 자식 lifecycle을 처리하고, 일반 update 안의 DB 조회도 렌더 전에 완료한다. Python 동기 템플릿에서 async 훅으로 왕복하는 제약과 동일하지 않다. [Phoenix LiveComponent lifecycle](https://phoenix-live-view.hexdocs.pm/Phoenix.LiveComponent.html#module-life-cycle).

느린 비동기 작업은 assign_async/start_async와 loading/result 상태로 별도 처리할 수 있다. 이는 초기화 훅 실행 자체를 렌더 뒤로 미루는 것과 다르다. [Phoenix async operations](https://phoenix-live-view.hexdocs.pm/Phoenix.LiveView.html#module-async-operations).

이 검토는 공식 공개 계약을 확인했다. diff.ex 소스 URL은 조회에 실패했으므로 Phoenix 내부 렌더러의 세부 구현을 검증했다고 주장하지 않는다.

## 5. 사용자 문서와 설계 문서의 불일치

### docs/features/live-component.md

- **“myself가 없으면 부모로 전달”, “반드시 myself=True”는 현재 코드와 틀린다.** `wireview.send`는 _target이 없으면 가장 가까운 [wireview-component]의 id를 사용한다(`wireview.js:2696`). live_tag_header가 바로 그 속성을 생성하므로 일반 버튼에서는 자식 자신이 대상이다. myself=True는 명시적 타깃 지정이며, 문서처럼 생략을 부모 통신 수단으로 사용할 수 없다.
- **“기타 파라미터 = 초기 props”는 불충분하다.** 매 부모 렌더마다 update 대상으로 들어가며 같은 값도 다시 전달된다. count=10 예제를 “처음에만 10”으로 이해하면 자식 카운터가 부모 렌더에서 되돌아간다.
- **send_update의 실제 완료 시점이 빠져 있다.** await send_update는 자기 세션에 메시지를 보내는 완료이지 자식 update/렌더 완료가 아니다(`core/component.py:1282`). 직후 자식 상태를 읽으면 갱신 완료를 가정할 수 없다.
- **Component 독립 WebSocket / 중첩 불가 표는 틀린다.** 브라우저 ServerConnection은 페이지의 Component들도 한 연결로 관리한다. 일반 Component도 템플릿에서 중첩할 수 있고 children 복원 코드가 이를 전제로 한다.
- **wireview-component/wireview-live는 CSS class가 아니라 HTML boolean attribute다.** .wireview-live 선택자를 믿고 쓰면 맞지 않는다.
- **LiveComponent 내부 중첩 미지원은 지원 범위 선언으로는 가능하지만, 코드가 거절하는 제한은 아니다.** 태그는 부모가 Component인지 확인하는 정도다. 현 상태는 금지되지 않으면서 큐 처리가 불완전하다고 설명해야 한다.
- **모달의 render_slot 예제는 내용을 전달할 LiveComponent block/fill API가 없다.** live_component 태그는 slots를 전달하지 않고 _render를 호출한다. 모달 내부 slot을 채울 수 있다는 예시로 쓰려면 별도 지원 경로가 필요하다.
- 초기 HTTP/WS 렌더와 joined 순서, 재연결의 상태 덮어쓰기·중복 호출, DOM 삭제에서 leaving이 호출되지 않는 제한이 빠져 있다.

### docs/design/live-component.md

설계 초안임을 명확히 표시하고 구현된 동작과 분리해야 한다. 특히 복사해 사용할 API 예시는 수정이 필요하다.

- §4.1의 **joined → render**는 현재 구현과 반대다. HTTP에는 joined 호출 자체가 없다.
- §2.3의 `target="@myself"`는 실제 shorthand가 아니다. on 태그는 `myself=True`일 때만 _target을 넣고, target은 일반 이벤트 인자로 간다(`templatetags/wireview.py:338`). 같은 예제의 tag_header는 live 전용 data-parent/표지도 생성하지 않는다.
- §2.4/§4.3의 `send_update("Counter", id="counter-1", ...)` 또는 클래스 인자는 실제 API와 다르다. 첫 인자는 자식 id여야 한다. 문서대로면 Counter라는 id를 찾다가 실패할 수 있다.
- `self.live_components.values()`, repo.live_components, register_live_component/get_live_component/get_live_component_by_id 구조는 현재 구현이 아니다. 실제는 repo.components와 get_live_components(parent_id)다.
- `wire.send_to_component`, lc: target 분기, JS resolveTarget("@myself")는 현재 경로가 아니다. 실제 send_to_parent는 세션 메시지로 dispatch_event를 요청한다.
- “mount 이후 update”/mount 중심 수명주기도 구현된 호출로 오해하면 안 된다. 현재 생성 경로는 _build→new→생성자이며 LiveComponent mount를 부르지 않는다.
- §4.2의 자식 이벤트 뒤 자식만 렌더는 **부모로 이벤트를 보내지 않고 추가 자식 작업도 없는 경우**의 설명이다. send_to_parent를 수행하면 후속 부모 렌더가 별도로 발생한다.
- Component 전체 렌더 vs LiveComponent 부분 렌더 표도 부정확하다. 둘 다 공통 _render_diff 경로를 사용한다.
- “단일 레벨 중첩”, “개별 연결” 설명은 기능 문서와 같은 정정이 필요하다.

## 검증 범위

실제 Python 저장소 메서드로 부모 join→자식 생성/flush→자식 join을 실행했고, 실제 JS join 함수의 최소 DOM 대역 실행으로 두 join 메시지를 확인했다. async/sync/async/thread-sensitive 왕복도 실행했다. 소비자 각 진입점·전송 계층·DOM 스캔은 코드 추적으로 검증했다.

전체 테스트와 브라우저 E2E는 실행하지 않았다. 특히 RAF race는 코드로 가능한 순서를 제시한 것이며 브라우저 재현 완료라고 보고하지 않는다. 저장소 코드·문서는 변경하지 않고 이 결과 파일만 작성했다.
