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
4. `WireviewMeta.render_diff()`가 직전 `Rendered`와 비교합니다.
   - fingerprint가 다르면 전체 렌더 `{"s": [...], "d": [...], "f": "..."}`
   - 같으면 바뀐 인덱스만 `{"3": "새 값", "7": "새 값"}`
   - 바뀐 값이 없으면 `None`이고 아무것도 보내지 않습니다.

서명 상태는 `wireview.core.state`가 만듭니다. `Signer.sign_object`에 zlib 압축을 더한 base64 문자열이라 속성값으로 들어가도 `&quot;`로 부풀지 않고, 상태가 같으면 결과도 같아서 diff가 건너뛸 수 있습니다. join 시에는 구형식인 `Signer().sign(json)`도 받아들이므로 배포 전에 렌더된 페이지도 재연결됩니다.

## 실측

항목 50개 루프가 있는 8.8 KB 템플릿, 값 두 개가 바뀌는 이벤트 기준입니다 (2026-09-08).

| 경로 | GAP-024 이전 | 이후 |
|------|-------------|------|
| WebSocket render 페이로드, 항목 5개 | 697 B | 208 B |
| WebSocket render 페이로드, 항목 50개 | 8,003 B | 336 B |
| 첫 렌더 전체 페이로드, 항목 50개 | 7,983 B | 4,317 B |
| 항목 하나 추가 (구조 변경) | 8,130 B | 전체 렌더 |

이전에는 서명 상태가 static 파트에 들어 있어 fingerprint가 매 렌더 달라졌고, 부분 diff가 한 번도 발동하지 않았습니다. 회귀 테스트는 `tests/test_diff_stability.py`입니다.

## 주의사항

- **구조가 바뀌면 전체 렌더입니다.** `{% for %}` 항목이 늘거나 줄면 static 목록이 달라집니다. 항목 단위 diff는 GAP-025(Keyed comprehensions)로 남아 있습니다. 대량 목록은 [Streams](../tutorials/06-streams-api.md)를 쓰세요.
- **`USE_HMIN`은 마커를 지웁니다.** django-hmin이 HTML 주석을 제거하므로 부분 diff가 꺼지고 공백 토큰 기반 레거시 diff로 퇴화합니다. 켤 때는 대역폭 손익을 실측하세요.
- **서명 상태는 상태가 바뀔 때마다 다시 전송됩니다.** 재연결 시 클라이언트가 이 값을 돌려보내 컴포넌트를 복원하기 때문입니다. 렌더에 필요 없는 큰 필드는 `_exclude_fields`로 빼거나 `_temporary_assigns`로 렌더 후 비우세요.
- **빈 출력은 dynamic이 아닙니다.** 값이 빈 문자열이면 마커가 생략되어 인덱스가 밀립니다. 이 경우 static 목록이 달라져 전체 렌더가 됩니다.

## 관련 기능

- [temporary_assigns](./temporary-assigns.md)
- [Streams 튜토리얼](../tutorials/06-streams-api.md)
- [성능 가이드](../PERFORMANCE.md)
