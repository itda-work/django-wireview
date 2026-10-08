# Benchmark results

`make bench`가 커밋 SHA 이름으로 저장한 JSON입니다. 작업 트리에 미커밋 변경이 있으면 `-dirty`가 붙습니다.
같은 기계에서 잰 값끼리만 비교하세요. `meta.platform`과 `meta.date`를 확인합니다.

```bash
uv run python -m bench.compare bench/results/997ee59.json bench/results/<sha>.json
```

| 파일 | 의미 |
|------|------|
| `997ee59.json` | GAP-024 이전 main. 비교의 기준점 |
| `<sha>.json` | 그 커밋에서 잰 값 |
| `<sha>-servers-<라벨>[-<접미사>].json` | `bench/servers.py`의 ASGI 서버 비교. 서버마다 회차별 원자료와 측정 환경. 문서가 싣는 파일은 `bench/servers_chart.py`의 `MACHINES` |
| `<sha>-servers-shutdown-<라벨>.json` | `bench/servers_shutdown.py`. SIGTERM에 서버가 닫는 방식과 `leaving()`이 끝까지 도는지 |
