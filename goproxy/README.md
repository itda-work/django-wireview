# goproxy

> 실험 (#56). Go가 브라우저 WebSocket을 종단하고, Python은 같은 `WireviewConsumer`를 ASGI 앱으로 그대로 돌린다.

## 구조

```
Browser ──WebSocket──▶ goproxy (Go) ──Unix socket, JSON lines──▶ manage.py wireview_gohost (Python)
                        연결당 goroutine                          연결당 ASGI 세션 = 기존 WireviewConsumer
```

- Go 쪽은 연결에 id를 붙여 프레임을 한 소켓으로 다중화한다. 프로토콜은 `{"c": id, "t": "open"|"frame"|"close", ...}` 한 줄에 하나.
- Python 쪽(`wireview/contrib/gohost.py`)은 `open`마다 ASGI websocket scope를 만들고 프로젝트의 `ASGI_APPLICATION`을 호출한다. daphne가 하던 일 중 소켓 처리만 Go로 갔고 컨슈머, 채널 레이어, 인증 미들웨어는 변경이 없다.
- Python 프로세스 하나에 Go 프로세스 하나가 붙는다. 다중 워커 라우팅, HTTP 프록시, TLS는 범위 밖이다.

## 실행

```bash
go build -o goproxy/goproxy ./goproxy
python manage.py wireview_gohost --socket /tmp/wireview-gohost.sock
./goproxy/goproxy -listen 127.0.0.1:8100 -backend /tmp/wireview-gohost.sock
```

클라이언트는 `ws://127.0.0.1:8100/__wireview__`에 붙는다. HTTP 페이지는 여전히 Django(runserver 등)가 내주므로, 실제 배포에서는 리버스 프록시가 `/__wireview__`만 goproxy로 보내야 한다.

## 벤치마크

```bash
make bench                        # daphne와 goproxy를 나란히 (Go 툴체인이 있으면)
make bench ARGS="--front go"      # goproxy만
```

`bench/ws.py`가 두 프로세스를 띄우고 연결당 RSS를 프로세스별로 나눠 기록한다. 결과와 판단은 `docs/design/transport-abstraction.md`에 있다.
