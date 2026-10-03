# 왜 wireview인가

서버 렌더링 컴포넌트에 흔히 나오는 반론(느리다, 굼뜨다, 개발이 어렵다, UI에 제약이 있다 등)에 wireview가 무엇으로 답하고 어디서는 답하지 못하는지 정리한 문서입니다. 맞는 지적은 맞다고 쓰고, 동작에 관한 문장은 브라우저 테스트(E2E)로 확인한 것만 씁니다.

어울리지 않는 경우의 목록은 [README](../README.md#이럴-땐-쓰지-마세요)에 있습니다. 이 문서는 그 목록 밖에서 고민하는 경우를 위한 것입니다.

## 느리다: 클릭마다 서버를 다녀온다

맞습니다. `{% on %}`으로 묶은 이벤트는 모두 WebSocket으로 서버에 가고, 서버가 다시 렌더한 diff가 와야 화면이 바뀝니다. 사용자가 느끼는 시간은 서버 처리 시간에 네트워크 왕복(RTT)을 더한 것입니다.

서버 쪽은 작습니다. 값 7개인 컴포넌트의 이벤트 하나가 핸들러와 diff를 합쳐 0.28ms, 항목 50개 목록이 1.1ms이고, 값 하나가 바뀐 응답은 239B입니다(Apple Silicon macOS, DB 조회 제외, [측정값](PERFORMANCE.md#측정값)). 그러니 체감을 정하는 것은 대개 RTT와 핸들러가 하는 DB 조회입니다.

왕복을 없앨 수는 없고, 기다리는 동안을 가리는 장치가 있습니다([Optimistic UI](features/optimistic-ui.md)).

- **로딩 클래스.** 이벤트를 일으킨 요소에 응답이 올 때까지 `wireview-loading`과 `wireview-click-loading` 같은 클래스가 붙습니다. CSS만으로 눌린 상태를 보여 줍니다.
- **`wire-disabled-with`.** 응답이 올 때까지 버튼을 막고 문구를 바꿉니다. 두 번 누르는 일을 막습니다.
- **`JS()` 명령.** 클래스 토글, 보이기·숨기기, 값 넣기 같은 일을 서버를 기다리지 않고 그 자리에서 하고, 끝의 `push`로 서버 핸들러를 부릅니다([JS 명령](../README.md#js-명령-빌더)).
- **debounce·throttle.** `{% on "input.debounce.300" ... %}`처럼 입력마다 보내지 않고 멈췄을 때 한 번 보냅니다.

RTT가 큰 망(먼 리전, 모바일 망)의 사용자가 주 대상이고 클릭마다 즉시 반응해야 한다면 SPA가 맞습니다.

## 굼뜨다: 입력하는 동안 화면이 흔들린다

렌더가 입력 중인 칸을 덮어쓰지 않습니다. 사용자가 고친 칸은 그 값을 지키고, 포커스가 있는 칸은 서버가 다른 값을 그려도 바꾸지 않습니다. 서버의 값이 사용자의 행동(제출, change, Enter)에 대한 답일 때만 그 값으로 바뀝니다. 브라우저 E2E(`tests/test_input_values_e2e.py`)가 지킵니다. 서버가 굳이 바꿔야 하면 `JS().set_value`를 씁니다.

드래그, 캔버스, 매 프레임 반응하는 애니메이션은 서버 왕복으로 할 일이 아닙니다. 그 부분은 [JavaScript 훅](features/hooks.md)으로 브라우저에서 처리하고, 결과만 `pushEvent`로 서버에 보냅니다. 렌더가 그 요소를 건드리지 않게 하려면 `wire-update="ignore"`를 답니다.

### 한글 입력

키 수식어(`keydown.enter` 등)는 IME가 조합 중인 키를 이벤트로 치지 않습니다. 한글을 조합하다 누른 Enter가 제출로 나가지 않습니다(`wireview/static/wireview/events.mjs`).

한글을 조합하는 도중에 렌더가 와도 조합은 깨지지 않습니다. 자기 입력의 debounce 응답이 사용자가 더 입력한 뒤에 도착해도, 다른 사용자의 브로드캐스트로 렌더가 와도 같습니다. 조합 중인 칸은 사용자가 고친 칸으로 보고 서버 값을 덮어쓰지 않기 때문입니다(`tests/test_ime_e2e.py`, Chromium에서 확인). 다만 `input` 바인딩은 자모마다 불리고 끝나지 않은 음절을 받으므로, 검색처럼 입력마다 서버에 보내는 칸에는 debounce를 겁니다.

## 개발이 어렵다

화면 하나를 만드는 데 필요한 것은 Django 템플릿 하나와 Python 클래스 하나입니다. 프런트엔드와 백엔드 사이의 API(엔드포인트, 직렬화, 클라이언트 상태 동기화)가 없어서, 둘이 어긋나는 종류의 버그가 생길 자리가 없습니다.

확인도 Python 안에서 끝납니다.

- **`mount()` 테스트.** 브라우저와 WebSocket 없이 컴포넌트를 마운트하고, 핸들러를 부르고, 렌더된 HTML을 봅니다([테스트](features/testing.md)).
- **`manage.py check`.** 조용히 실패하는 실수 17가지(async가 아닌 핸들러, 이름 충돌, 로드되지 않는 JS, 다중 프로세스의 InMemory 레이어 등)를 `wireview.W*` 경고로 알립니다([체크 목록](features/checks.md)).
- **타입 스텁과 편집기 지원.** 컴포넌트마다 `.pyi`를 만들고, VS Code 확장이 템플릿 태그를 진단합니다([편집기 지원](features/editor-support.md)).

어려운 점도 있습니다. 핸들러와 라이프사이클 메서드는 이벤트 루프 위에서 도는 async 함수라, 동기 ORM을 그대로 부르면 Django가 막습니다. `await Model.objects.aget(...)` 같은 async API를 쓰거나 `sync_to_async`로 감쌉니다. 처음에는 이 규칙이 가장 자주 걸립니다. 순서대로 따라 할 [튜토리얼](tutorials/README.md)이 15편 있습니다.

## UI에 제약이 있다

화면은 Django 템플릿이 그리는 HTML 그대로라, CSS 프레임워크(Tailwind 등)와 마크업에는 제약이 없습니다. diff가 바꾸는 것도 서버가 그린 HTML뿐입니다.

제약은 JavaScript 컴포넌트 생태계 쪽에 있습니다. React용으로 만든 UI 키트(shadcn/ui 등)나 데이터 그리드는 그대로 꽂을 수 없고, 바닐라 JS 라이브러리는 [훅](features/hooks.md)으로 감싸서 씁니다. 문서에 Chart.js와 CodeMirror 예가 있습니다. 그 수준의 UI 키트가 앱의 중심이거나, 협업 편집기처럼 클라이언트 상태가 본체인 앱이라면 SPA가 맞습니다.

## 연결이 끊기거나 배포하면

페이지는 스스로 다시 연결합니다. 간격은 백오프와 지터로 흩어지고 `RECONNECT_*` 설정으로 바꿉니다([설정](features/settings.md)). 다시 연결하면 페이지의 컴포넌트가 페이지에 실린 서명된 상태(`data-state`)로 다시 join합니다. 상태가 서버 메모리가 아니라 페이지에 있으므로 어느 인스턴스에 붙어도 되고, sticky session이 필요 없습니다([배포 가이드](DEPLOYMENT.md#수평-확장)). 서명된 상태는 렌더마다 diff와 함께 내려와 갱신됩니다. 서명 상태에 싣지 않는 것(`Meta.temporary_assigns`, `Meta.exclude_fields`, 늘 빠지는 `user`·`wire`·`session`)은 다시 join할 때 기본값이나 연결에서 다시 채워집니다.

다시 연결하면 페이지는 마지막으로 받은 렌더의 상태로 돌아갑니다. 처음 로드한 상태로 되돌아가지 않고 새로고침도 없습니다. 서버 프로세스를 재시작해도(배포), 다른 워커가 연결을 받아도 서명 키만 같으면 그대로 이어지고, 입력하고 아직 보내지 않은 값도 입력란에 남습니다. 서명 키가 다르거나 상태가 만료(기본 14일)됐으면 페이지가 새로고침되고 처음 로드한 상태로 시작합니다([재연결이 돌려주는 것](DEPLOYMENT.md#재연결이-돌려주는-것), `tests/test_reconnect_state_e2e.py`).

끊긴 동안에는 이벤트를 보내지 않고 버립니다. 컴포넌트에는 `wireview-disconnected` 클래스가 붙고, 훅은 `disconnected()`·`reconnected()`를 받으므로 연결 상태를 화면에 보여 줄 수 있습니다. 오프라인에서도 입력을 모아 두었다가 보내야 하는 앱에는 맞지 않습니다.

롤링 배포에서는 내린 인스턴스의 소켓이 한꺼번에 닫히고, 모든 페이지가 다른 인스턴스로 다시 join합니다. 그래서 배포 순간의 부하는 요청 수가 아니라 join 수이고, 용량은 join/s로 잽니다([롤링 배포와 재연결](DEPLOYMENT.md#롤링-배포와-재연결)).

### 뒤로가기

뒤로 가기·앞으로 가기 뒤의 화면은 항상 주소창의 URL과 맞습니다. 그 URL을 다시 가져와 그리므로, 쿼리에 담긴 상태는 돌아오고 이벤트로만 바꾼 상태는 그 URL을 새로 연 페이지의 값이 됩니다. `push_to`도 같은 방식이라 이동하면 이벤트로만 바꾼 상태는 남지 않습니다. `replace_to`는 상태를 유지한 채 주소만 바꾸고 history 항목을 늘리지 않습니다. live_session 경계를 넘는 이동은 전체 페이지 로드입니다([내비게이션](features/navigation.md), `tests/test_history_e2e.py`). 남기고 싶은 상태는 URL 쿼리에 둡니다.

## 연결마다 메모리를 쓴다

맞습니다. 연결마다 서버 프로세스가 WebSocket과 컴포넌트 인스턴스를 들고 있습니다. daphne에서 연결 2,000개, 항목 5개 컴포넌트일 때 연결당 46~55KB였고(프로세스 1개 InMemory, 프로세스 4개 channels-nats), 그중 약 85%는 wireview가 아니라 WebSocket 스택의 몫입니다. uvicorn은 기본으로 협상하는 permessage-deflate 때문에 연결당 약 211KB이고, 압축을 끄면 51KB입니다([배포 가이드](DEPLOYMENT.md), [transport-abstraction.md](design/transport-abstraction.md) §5).

확인한 것은 연결 2,000개까지입니다. 그보다 많은 연결은 재지 않았으니, 자기 컴포넌트로 `make bench`를 돌려 잽니다([측정값](PERFORMANCE.md#측정값)). 읽기만 하는 구독자가 아주 많은 페이지라면 연결마다 서버 상태를 둘 이유가 없으니 SSE와 CDN이 낫습니다.

## 생태계가 작다

맞습니다. React나 Vue만큼의 라이브러리, 예제, 질문과 답이 없습니다.

대신 wireview 아래에 있는 것은 Django와 Channels입니다. 폼, admin, 인증, 마이그레이션, 서드파티 Django 앱은 그대로 쓰고, 외부에 공개 API가 필요하면 DRF나 Django Ninja를 같은 프로젝트에 둡니다. 공개 API의 범위와 없애는 절차는 [호환성 정책](COMPATIBILITY.md)이 정합니다.

AI 도구를 위해서는 문서 목록인 [llms.txt](https://itda.work/wireview/llms.txt)와, 프로젝트에 설치하는 앱 개발자용 [스킬](features/agent-skill.md)이 있습니다. 이것들이 AI가 wireview를 더 잘 짜게 만든다는 실측은 아직 없고, AI가 wireview를 React만큼 잘 짠다는 근거도 없습니다. 확실한 것은 AI가 쓴 코드를 `mount()` 테스트와 `manage.py check`로 브라우저 없이 확인할 수 있다는 점입니다.

## 권한 검사는 누가 하나

상태와 권한 판단이 서버 한 곳에 있습니다.

- **상태는 위조할 수 없습니다.** 페이지에 실리는 `data-state`는 서명되어 있고, 컴포넌트 클래스·페이지 경계·인증 세대에 묶이며 만료가 있습니다. 클라이언트가 고친 상태로는 join하지 못합니다.
- **클라이언트가 부를 수 있는 것은 정해져 있습니다.** 사용자 코드가 정의한 `_` 없는 메서드만 이벤트 핸들러이고, 인자는 타입 표기대로 검증됩니다(`validate_call`). 프레임워크의 메서드는 오버라이드해도 클라이언트가 부르지 못합니다.
- **소켓은 같은 사이트에서만 열립니다.** 컨슈머가 소켓을 받기 전에 `Origin`을 `ALLOWED_HOSTS`와 대조합니다([WebSocket의 Origin](DEPLOYMENT.md#websocket의-origin)).
- **페이지 경계.** [live_session](features/live-session.md)의 `authorize` 술어가 뷰와 join에서 같은 함수로 돕니다. 로그아웃하면 그 경계 안의 열린 소켓이 닫힙니다. 다만 발행은 최선 노력이라 "모든 소켓이 즉시 닫힌다"는 보장은 아닙니다.

이벤트마다 다시 인가하지는 않습니다. 사용자와 세션은 소켓을 연결할 때 읽은 것이고, Django의 HTTP 미들웨어도 WebSocket 이벤트에는 돌지 않습니다. 로그아웃을 거치지 않는 권한 변경(`is_staff`를 떼는 것 등)은 다음 연결에서야 반영되므로, 이벤트마다 확인할 권한은 핸들러 안에서 확인합니다. 즉시 끊어야 하면 `invalidate_authentication`을 부릅니다([로그아웃과 기존 연결](features/live-session.md#로그아웃과-기존-연결)).

## 채널 레이어 메시지가 사라지면

Channels의 채널 레이어는 전달을 보장하지 않습니다(at-most-once). 그래서 브로드캐스트가 유실될 수 있고, 유실의 결과는 오류가 아니라 낡은 화면입니다. 브로드캐스트를 놓친 컴포넌트는 다시 렌더하지 않고, 다음 이벤트가 올 때까지 그 화면만 낡은 채 남습니다([transport-abstraction.md](design/transport-abstraction.md) §5-4).

실제로 확인했습니다. 연결 하나가 처리를 따라가지 못하면 채널 레이어는 `capacity`(기본 100)를 넘는 브로드캐스트를 버리고, 보낸 쪽에도 받는 쪽에도 오류가 나지 않으며 연결도 끊기지 않습니다. 다음 렌더 때 템플릿이 원천(DB 등)에서 다시 읽는 값은 맞아지지만, 받은 알림을 상태에 누적하는 컴포넌트는 잃은 것을 되찾지 못합니다. channels-nats는 유실을 WARNING으로 남기고, channels_redis는 같은 프로세스의 다른 연결이 읽는 중이면 가장 오래된 메시지를 로그 없이 버립니다(`tests/test_broadcast_loss_e2e.py`). 그래서 화면에 보여 줄 값은 알림을 누적하지 말고 원천에서 다시 읽고, capacity는 부하에 맞게 올립니다([배포 가이드](DEPLOYMENT.md)).
