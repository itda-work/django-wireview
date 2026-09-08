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
