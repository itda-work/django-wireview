# HTML Diff

> 렌더 결과를 static/dynamic 파트로 나눠, 이벤트마다 바뀐 dynamic 값만 보낸다.

## 개요

wireview는 Phoenix LiveView의 렌더 엔진을 본떠 템플릿 출력을 두 종류로 나눕니다.

- **static**: 템플릿 리터럴. 구조가 같은 한 바뀌지 않습니다.
- **dynamic**: `{{ 변수 }}` 출력과 컴포넌트의 서명 상태(`data-state`). 이벤트마다 바뀔 수 있습니다.

첫 렌더는 static과 dynamic을 모두 보내고, 이후 렌더는 바뀐 dynamic 값만 인덱스와 함께 보냅니다. 클라이언트는 static과 dynamic을 이어 붙여 HTML을 복원한 뒤 idiomorph로 DOM에 반영합니다.

## 작동 방식

1. `render_with_markers()`가 템플릿의 `VariableNode`를 `MarkedVariableNode`로 감쌉니다. 출력은 `<!--$n-->값<!--/$n-->` 형태의 마커로 둘러싸입니다 (`wireview/template_engine.py`).
2. `{% tag_header %}`는 라이브 렌더에서 서명 상태도 같은 마커로 감쌉니다 (`wireview/templatetags/wireview.py`의 `_signed_state`). HTTP 렌더에서는 마커 없이 그대로 넣습니다. 속성 안의 마커 텍스트는 최초 join을 깨뜨리기 때문입니다.
3. `Rendered.from_marked_html()`이 마커를 기준으로 static 목록과 dynamic 목록을 만들고, static 목록의 해시를 fingerprint로 삼습니다 (`wireview/core/rendered.py`).
4. `{% for %}`는 `ComprehensionNode`가 감싸 루프 전체를 `<!--$Cn-->`, 각 반복을 `<!--$In-->` 마커로 표시합니다. 파서는 이를 **comprehension** 하나로 만듭니다. 항목 템플릿의 static은 한 번만, 항목마다 dynamic 목록만 갖는 구조라 항목이 늘거나 바뀌어도 부모 fingerprint는 그대로입니다 (GAP-025).
5. `{% if %}`는 `ConditionalNode`가 감싸 `<!--$Bn-->` 마커로 표시합니다. 파서는 이를 자체 static과 dynamic을 가진 **블록**으로 만듭니다. 분기가 바뀌어도 부모 static은 그대로이고, 루프 항목 안의 조건문도 항목 템플릿을 흐트러뜨리지 않습니다. 이전에는 조건문 안의 변수에 마커가 붙지 않아 그 안의 어떤 변화도 전체 렌더였습니다.
6. `WireviewMeta.render_diff()`가 직전 `Rendered`와 비교합니다.
   - fingerprint가 다르면 전체 렌더 `{"s": [...], "d": [...], "f": "..."}`. `d`의 원소는 문자열, comprehension `{"s": [...], "d": [[...], ...]}`, 블록 `{"r": [...], "d": [...]}` 중 하나입니다.
   - 같으면 바뀐 인덱스만. 값은 문자열, comprehension 전체(항목 템플릿이 바뀌었거나 처음 생겼을 때), 항목 갱신 `{"u": {"<항목 인덱스>": [...]}, "n": 항목 수}`, 블록 전체(분기가 바뀌었을 때), 블록 부분 갱신 `{"p": {"<인덱스>": 값}}`입니다.
   - 바뀐 값이 없으면 `None`이고 아무것도 보내지 않습니다.

클라이언트는 `wireview/static/wireview/rendered.mjs`의 순수 함수로 diff를 적용하고 HTML을 복원합니다. `node --test tests/js/`로 검증합니다.

서명 상태는 `wireview.core.state`가 만듭니다. `Signer.sign_object`에 zlib 압축을 더한 base64 문자열이라 속성값으로 들어가도 `&quot;`로 부풀지 않고, 상태가 같으면 결과도 같아서 diff가 건너뛸 수 있습니다. join 시에는 구형식인 `Signer().sign(json)`도 받아들이므로 배포 전에 렌더된 페이지도 재연결됩니다.

## 실측

`make bench-compare BASE=997ee59`의 출력입니다 (2026-09-08, macOS, Python 3.12, Django 6.0). 997ee59는 GAP-024 이전의 main이고, 항목 50개 리스트 컴포넌트는 항목마다 `{% if %}`가 있습니다. 같은 명령으로 누구나 다시 잴 수 있습니다 ([bench/README.md](../../bench/README.md)).

| 지표 | 997ee59 | 현재 | 변화 |
|------|--------:|-----:|-----:|
| 리스트, 항목 하나 값 변경 (B) | 8,015 | 595 | −93% |
| 리스트, 항목 하나 추가 (B) | 8,161 | 604 | −93% |
| 리스트, 항목 안 조건 토글 (B) | 8,164 | 607 | −93% |
| 리스트, 최상위 조건 켜기 (B) | 8,215 | 639 | −92% |
| 리스트, 첫 렌더 (B) | 8,015 | 2,218 | −72% |
| 플랫, 값 하나 변경 (B) | 676 | 194 | −71% |
| WebSocket render 페이로드, 항목 50개 (B) | 8,012 | 624 | −92% |
| 이벤트당 CPU, 리스트 (ms) | 0.406 | 0.543 | +34% |
| 그중 템플릿 렌더 (ms) | 0.339 | 0.371 | +9% |
| 컴포넌트 메모리, 항목 50개 (KB) | 31.1 | 25.6 | −18% |
| 연결당 서버 RSS, 항목 50개 (KB) | 78.7 | 72.4 | −8% |
| 이벤트 처리량, 항목 50개 (/s, 프로세스당) | 1,844 | 1,504 | −18% |

읽는 법: 이전에는 서명 상태가 static 파트에 들어 있어 부분 diff가 한 번도 발동하지 않았고, 어떤 이벤트든 HTML 전체를 보냈습니다. 지금 남은 600 B의 대부분은 재연결용 서명 상태이고 diff 자체는 70 B 안팎입니다. 대가는 이벤트당 CPU 약 0.13 ms입니다. 마커가 늘어 템플릿 렌더가 조금 느려졌고, 마커 파싱이 0.12 ms를 씁니다. 회귀 테스트는 `tests/test_diff_stability.py`와 `tests/test_comprehension.py`입니다.

## 주의사항

- **루프는 항목 단위로 diff됩니다.** 항목 추가·삭제·변경은 해당 항목의 dynamic만 보냅니다. 단, 항목 앞에 끼워 넣으면 뒤 항목의 인덱스가 밀려 모두 다시 보냅니다. 키 기반 diff는 없습니다. 항목마다 구조가 달라지는 구성(`{% include %}`로 다른 템플릿을 고르는 경우 등)은 루프 전체가 문자열 하나로 취급됩니다. 대량 목록은 [Streams](../tutorials/06-streams-api.md)를 쓰세요.
- **`{% include %}`된 템플릿 안의 변수는 마커가 없습니다.** 그 내용이 바뀌면 부모의 static이 달라져 전체 렌더가 됩니다.
- **`USE_HMIN`은 마커를 지웁니다.** django-hmin이 HTML 주석을 제거하므로 부분 diff가 꺼지고 공백 토큰 기반 레거시 diff로 퇴화합니다. 켤 때는 대역폭 손익을 실측하세요.
- **서명 상태는 상태가 바뀔 때마다 다시 전송됩니다.** 재연결 시 클라이언트가 이 값을 돌려보내 컴포넌트를 복원하기 때문입니다. 렌더에 필요 없는 큰 필드는 `_exclude_fields`로 빼거나 `_temporary_assigns`로 렌더 후 비우세요.
- **HTTP 렌더에는 마커가 없습니다.** `is_live`가 아닌 렌더는 `strip_markers()`를 거칩니다. 예전에는 `value="<!--$0-->…"`처럼 속성 안에 마커 텍스트가 남아 WebSocket 연결 전까지 입력값과 링크가 깨졌습니다.

## 관련 기능

- [temporary_assigns](./temporary-assigns.md)
- [Streams 튜토리얼](../tutorials/06-streams-api.md)
- [성능 가이드](../PERFORMANCE.md)
