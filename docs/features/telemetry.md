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

모든 시그널이 공통으로 싣는 것:

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

Prometheus로 내보낸다면 히스토그램 하나에 컴포넌트 이름을 라벨로 붙이는 편이 낫다.

```python
RENDER = Histogram("wireview_render_seconds", "render duration", ["component"])


@receiver(telemetry.component_rendered)
def observe(sender, component_name, duration_ms, **kwargs):
    RENDER.labels(component=component_name).observe(duration_ms / 1000)
```

## 작동 방식

계측 지점은 네 곳이다.

| 단계 | 위치 |
|------|------|
| 이벤트 | `ComponentRepository.dispatch_event` — 핸들러 호출을 감싼다 |
| 렌더 | `WireviewMeta.render_diff`의 렌더 구간(라이브)과 `WireviewMeta.render`(HTTP·컴포넌트 태그) |
| diff | `WireviewMeta.render_diff`의 diff 구간. 마커 기반이든 레거시 토큰 diff든 같은 시그널 |
| 브로드캐스트 | `WireviewMeta._send_broadcast`와 `wireview.utils`의 `send_to`/`asend_to` — 어떤 `Broker` 구현이든 계측된다 |

각 지점은 `telemetry.span(...)` 컨텍스트 매니저를 쓴다. 켜져 있으면 `Span`이 시계를 읽고
빠져나갈 때 시그널을 한 번 보낸다. 꺼져 있으면 공유 no-op 스팬 하나를 돌려주므로
시계도 읽지 않고 페이로드 크기도 계산하지 않는다. 남는 비용은 `with` 문과 no-op 메서드
호출 몇 개뿐이다.

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
