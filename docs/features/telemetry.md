# Telemetry

운영 중인 앱이 wireview 내부 비용을 자기 모니터링에 연결하기 위한 옵트인 계측 훅이다.
`bench/`가 재현 가능한 실험실 수치를 준다면, telemetry는 실제 트래픽에서의 수치를 준다.

## 개요

이벤트 하나가 들어와 화면이 갱신되기까지 wireview는 네 단계를 지난다. 핸들러 실행,
템플릿 렌더, diff 계산, 그리고 (컴포넌트가 브로드캐스트한다면) 팬아웃이다. 이벤트당
비용의 60% 이상이 템플릿 렌더라는 것은 이미 측정돼 있지만
([docs/design/transport-abstraction.md](../design/transport-abstraction.md)), 그 비율은
템플릿과 데이터에 따라 달라진다. telemetry는 네 단계 각각의 **소요 시간**과 **페이로드
크기**를 Django 시그널로 내보내, 어떤 컴포넌트가 느린지 어떤 렌더가 큰지를 앱이 직접
관측하게 한다.

여기에 더해 운영자가 먼저 묻는 것 넷을 이벤트 시그널로 낸다(#124). 소켓이 몇 개 열려 있는가
(`connection_opened`·`connection_closed`), join이 왜 거절되는가(`join_rejected`), 채널 레이어가
메시지를 버리고 있는가(`publish_failed`). 전에는 로그 문자열로만 있었거나, 채널이 가득 찬 경우에는
핸들러를 죽이는 예외였다.

wireview는 아무것도 기록하지 않는다. 시그널을 보낼 뿐이고, 무엇을 어디에 쌓을지는
전적으로 앱의 몫이다.

## 켜기

기본값은 꺼짐이다.

```python
# settings.py
WIREVIEW = {
    "TELEMETRY": True,
}
```

런타임에 바꾸려면 `wireview.telemetry.enable()` / `disable()`을 쓴다. 테스트가 이 방식을
쓴다.

```python
from wireview import telemetry

telemetry.enable()
assert telemetry.is_enabled()
telemetry.disable()
```

## 시그널

| 시그널 | 언제 | sender |
|--------|------|--------|
| `event_handled` | 클라이언트 이벤트 핸들러가 끝났을 때 | 컴포넌트 클래스 |
| `component_rendered` | 템플릿 렌더가 끝났을 때 | 컴포넌트 클래스 |
| `diff_computed` | 렌더 결과에서 diff를 계산했을 때 | 컴포넌트 클래스 |
| `broadcast_published` | 토픽으로 팬아웃 메시지를 발행했을 때 | 브로커 클래스 |
| `connection_opened` | 수락된 소켓에서 세션이 시작됐을 때 | 세션 클래스(`WireviewConsumer`) |
| `connection_closed` | 그 세션이 끝났을 때(소켓이 닫혔을 때) | 세션 클래스 |
| `join_rejected` | 소켓이나 join이 거절됐을 때 | 세션 클래스 |
| `publish_failed` | 채널 레이어가 발행이나 세션 전송을 거부했을 때 | 브로커 클래스(`ChannelsBroker`) |

### 구간 시그널

위의 네 개(`event_handled`~`broadcast_published`)는 구간을 잰다. 공통으로 싣는 것:

| 키워드 | 뜻 |
|--------|-----|
| `duration_ms` | 측정 구간의 소요 시간(밀리초, `time.perf_counter` 기준) |
| `payload_size` | 바이트 수. 잴 수 없으면 `None` |
| `error` | 측정 구간을 빠져나간 예외. 정상 종료면 `None` |

시그널별로 더 싣는 것:

| 시그널 | 추가 키워드 |
|--------|-------------|
| `event_handled` | `component_id`, `component_name`, `event` (핸들러 이름). `payload_size`는 핸들러 인자 크기 |
| `component_rendered` | `component_id`, `component_name`, `live` (WebSocket 렌더면 `True`, HTTP 최초 렌더면 `False`). `payload_size`는 렌더된 HTML 크기 |
| `diff_computed` | `component_id`, `component_name`, `changed` (보낼 diff가 있으면 `True`). `payload_size`는 diff 페이로드 크기이고 `changed`가 `False`면 `None` |
| `broadcast_published` | `topic`. `payload_size`는 발행 메시지 크기 |

### 이벤트 시그널

아래 네 개는 일어난 일을 알린다. `duration_ms`·`payload_size`·`error` 공통 키가 없고 자기 키만 싣는다.

| 시그널 | 키워드 |
|--------|--------|
| `connection_opened` | `connection_id` |
| `connection_closed` | `connection_id`, `code`(닫힘 코드, 모르면 `None`), `duration_ms`(세션이 산 시간, 세션이 열린 뒤에 telemetry를 켰으면 `None`), `components`(닫힐 때 살아 있던 컴포넌트 수) |
| `join_rejected` | `reason`(아래 표), `component_name`(`origin`이면 `None`), `detail`(로그와 같은 설명 문장) |
| `publish_failed` | `kind`(`"publish"` 또는 `"send_to_session"`), `target`(토픽 또는 세션의 채널 이름), `error`(예외), `dropped`(아래) |

`connection_opened`는 Origin 검사를 통과하고 수락된 소켓에만 난다. 거절된 소켓은 `join_rejected(reason="origin")`
하나만 남기고 `connection_closed`도 내지 않으므로, 둘을 빼면 열린 소켓 수가 된다.

`join_rejected`의 `reason`은 **닫힌 집합**이다(`telemetry.JOIN_REJECTED_REASONS`). 지표 라벨로 써도 카디널리티가
늘지 않는다. 새 사유를 내는 코드를 넣으면 이 집합에도 넣어야 `tests/test_telemetry.py`가 통과한다.

| `reason` | 뜻 | 클라이언트가 받는 것 |
|----------|----|------------------------|
| `origin` | 소켓의 `Origin`이 `ALLOWED_HOSTS`에 없다. 수락 전에 거절(#96) | 핸드셰이크 403 |
| `expired` | 서명 상태가 `STATE_MAX_AGE`보다 오래됐다 | `reload` |
| `invalid` | 서명이 맞지 않거나 다른 클래스용으로 서명됐다. 키가 어긋난 배포가 여기로 온다 | `reload` |
| `live_session` | 페이지 경계가 거절했다(다른 경계, 로그아웃, 인가 술어) | `reload` |
| `halted` | `on_mount` 훅이 마운트를 멈췄다 | 요소 제거, 훅이 보낸 리다이렉트 |
| `error` | 마운트가 예외를 던졌다. 로그에 트레이스백이 있다 | `error` |

`expired`는 오래 열어 둔 탭이면 평범하다. `invalid`가 늘면 서명 키가 프로세스마다 다르거나 배포 사이에 바뀐
것이다. 부모의 join에 딸려 온 자식 상태를 버리는 경우(서명 불일치·경계)는 거절이 아니다 — 자식은 부모 템플릿의
props로 다시 만들어지고 경고 로그만 남는다.

`publish_failed`의 `dropped`는 wireview가 그 메시지를 어떻게 했는지다.

- `True` — 채널이 가득 찼다(`ChannelFull`). 받는 쪽이 따라오지 못한다는 뜻이고, 그 메시지 하나를 버리고 부른
  쪽은 계속한다. `wireview` 로거에 WARNING도 남는다. 레이어의 `group_send`가 가득 찬 멤버에게 하는 것과 같다.
- `False` — 그 밖의 오류(브로커 연결 끊김 등). 시그널을 낸 뒤 예외를 다시 던진다. 발행한 핸들러는 전처럼
  실패하고, 세션은 그 컴포넌트만 격리해 다시 join시킨다([errors](./errors.md)).

**보이는 것은 레이어가 던지는 것뿐이다.** `group_send`는 가득 찬 멤버에 대해 아무것도 던지지 않는다 —
channels_redis는 Redis에서 버리면 INFO로 로그하지만 프로세스 내부 버퍼에서는 말없이 버리고, in-memory 레이어는
말없이 버리고, channels-nats는 받는 쪽에서 WARNING 로그와 함께 버린다(pub/sub이라 보내는 쪽은 알 수 없다).
`ChannelFull`을 던지는 것은 채널 하나로 보내는 `send`, 즉 `send_to_session`이다. 그러니 브로드캐스트 유실은 이
시그널이 아니라 레이어의 로그(`channels_nats`, `channels_redis.core` 로거)에서 세고, 그 수도 하한이다
([배포](../DEPLOYMENT.md)의 모니터링 절).

## 사용법

수신자는 앱 준비 시점에 연결한다.

```python
# myapp/apps.py
from django.apps import AppConfig


class MyAppConfig(AppConfig):
    name = "myapp"

    def ready(self):
        from . import telemetry_receivers  # noqa: F401
```

```python
# myapp/telemetry_receivers.py
import logging

from django.dispatch import receiver

from wireview import telemetry

log = logging.getLogger("myapp.telemetry")
SLOW_MS = 50


@receiver(telemetry.component_rendered)
def log_slow_renders(sender, component_name, duration_ms, payload_size, **kwargs):
    if duration_ms > SLOW_MS:
        log.warning("slow render %s %.1fms %sB", component_name, duration_ms, payload_size)


@receiver(telemetry.event_handled)
def count_events(sender, component_name, event, duration_ms, error, **kwargs):
    statsd.timing(f"wireview.event.{component_name}.{event}", duration_ms)
    if error is not None:
        statsd.incr(f"wireview.event_error.{component_name}.{event}")
```

특정 컴포넌트만 보려면 `sender`로 거른다.

```python
@receiver(telemetry.diff_computed, sender=Dashboard)
def watch_dashboard_payloads(sender, payload_size, changed, **kwargs):
    if changed:
        statsd.histogram("wireview.dashboard.diff_bytes", payload_size)
```

Prometheus로 내보낸다면 히스토그램 하나에 컴포넌트 이름을 라벨로 붙이는 편이 낫다. 운영 지표까지
연결한 전체 예시는 [배포 문서의 모니터링](../DEPLOYMENT.md#telemetry-시그널을-지표로)에 있다.

```python
RENDER = Histogram("wireview_render_seconds", "render duration", ["component"])


@receiver(telemetry.component_rendered)
def observe(sender, component_name, duration_ms, **kwargs):
    RENDER.labels(component=component_name).observe(duration_ms / 1000)
```

## 작동 방식

계측 지점:

| 단계 | 위치 |
|------|------|
| 이벤트 | `ComponentRepository.dispatch_event` — 핸들러 호출을 감싼다 |
| 렌더 | `WireviewMeta.render_diff`의 렌더 구간(라이브)과 `WireviewMeta.render`(HTTP·컴포넌트 태그) |
| diff | `WireviewMeta.render_diff`의 diff 구간 |
| 브로드캐스트 | `WireviewMeta._send_broadcast`와 `wireview.utils`의 `send_to`/`asend_to` — 어떤 `Broker` 구현이든 계측된다 |
| 연결 | `WireviewSession.start`·`stop`. 닫힘 코드는 컨슈머의 `disconnect`가 넘긴다 |
| 거절 | `WireviewSession.command_join`·`_join_failed`, Origin은 `WireviewConsumer.websocket_connect` |
| 레이어 거부 | `ChannelsBroker.publish`·`send_to_session`. 직접 만든 `Broker`는 스스로 내야 한다 |

각 지점은 `telemetry.span(...)` 컨텍스트 매니저를 쓴다. 켜져 있으면 `Span`이 시계를 읽고
빠져나갈 때 시그널을 한 번 보낸다. 꺼져 있으면 공유 no-op 스팬 하나를 돌려주므로
시계도 읽지 않고 페이로드 크기도 계산하지 않는다. 남는 비용은 `with` 문과 no-op 메서드
호출 몇 개뿐이다. 이벤트 시그널은 `telemetry.emit`을 거치고, 꺼져 있으면 플래그 확인 하나로 끝난다 —
연결 수명을 재는 시계도 켜져 있을 때만 읽는다. 로그는 telemetry와 무관하게 늘 남는다.

예외가 나도 시그널은 나간다. `error`에 예외가 담기고, 예외 자체는 그대로 전파된다.

### 오버헤드 실측

`make bench-compare BASE=50fe19d ARGS="--skip-ws"` (macOS, Apple Silicon). 왼쪽이
계측 도입 전, 오른쪽이 도입 후(telemetry 꺼짐, 기본값).

```
metric                                        50fe19d      50fe19d     change
payload_bytes.list.first_render                 2,260        2,261        +0%
payload_bytes.list.change_one_item                602          602        +0%
timing.list.event_ms                            0.536        0.535        -0%
timing.list.template_render_ms                  0.370        0.367        -1%
timing.flat.event_ms                            0.151        0.149        -1%
memory.list.component_kb                       25.674       25.674        -0%
```

꺼져 있을 때의 차이는 회차 간 잡음(±1%) 안이다. 페이로드의 ±1~3 바이트도 서명 상태의
압축 길이가 회차마다 흔들리는 것이지 계측 때문이 아니다.

켰을 때의 비용은 이벤트당 약 0.007ms 고정이다. 같은 하드웨어에서 라운드 7회 중앙값:

```
metric                               off        on    change
list.event_ms                      0.312     0.319      +2.5%
list.template_render_ms            0.216     0.214      -0.9%
flat.event_ms                      0.153     0.160      +4.8%
```

`flat`은 스칼라 7개짜리 가장 싼 컴포넌트라 고정 비용이 비율로 크게 보인다. 렌더가
무거워질수록 비율은 작아진다. 이 수치는 수신자가 아무것도 하지 않을 때의 것이고,
실제 비용은 연결한 수신자가 무엇을 하느냐가 지배한다.

## 주의사항

- **수신자는 렌더·이벤트 경로 안에서 동기로 실행된다.** Django 시그널이 그렇다. 수신자에서
  네트워크 I/O를 하면 그 시간이 그대로 사용자 지연이 된다. 카운터를 올리거나 큐에 넣는
  정도로 끝내고, 전송은 별도 워커에 맡긴다.
- **`payload_size`는 계측 시점의 크기다.** 실제 WebSocket 프레임은 명령 봉투(`{"command":
  "render", "payload": ...}`)가 더해지므로 조금 더 크다. 압축이 걸려 있으면 더 작다.
- **`component_rendered`는 컴포넌트마다 난다.** 중첩 `LiveComponent`가 많은 페이지의 최초
  HTTP 렌더에서는 컴포넌트 수만큼 시그널이 난다.
- **`diff_computed`의 `changed=False`는 정상이다.** 상태가 안 바뀌면 보낼 diff가 없다.
  이 비율이 높다면 불필요한 이벤트나 `skip_render`로 줄일 여지가 있다는 신호다.
- **커스텀 `Broker`도 계측된다.** 계측이 `Broker.publish` 호출부에 있기 때문이다. 반대로
  브로커를 거치지 않고 채널 레이어를 직접 만지는 코드는 잡히지 않는다 — 그런 코드는
  애초에 `tests/test_transport.py`의 가드가 막는다.

## 관련 기능

- [HTML Diff](./html-diff.md) — `diff_computed`가 재는 대상
- [temporary_assigns](./temporary-assigns.md) — 렌더 메모리 줄이기
- [bench/README.md](../../bench/README.md) — 실험실 수치
- [docs/PERFORMANCE.md](../PERFORMANCE.md) — 성능 개요
