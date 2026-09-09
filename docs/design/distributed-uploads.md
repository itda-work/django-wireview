# 다중 프로세스 청크 업로드 설계 (#83)

> 상태: 초안. 2026-09-09. #77(소유권·수명 갈래)이 닫힌 뒤 남은 **분산 접근** 갈래.
> 배경: `docs/design/session-extraction.md` 2절, `docs/DEPLOYMENT.md` "Chunked uploads and multiple processes"

## 1. 무엇이 깨지나

청크는 WebSocket이 아니라 HTTP `/__wireview_upload__/<connection_id>/<component_id>/<upload_name>/`
로 온다. `wireview/views.py`의 `_upload_registries`는 **프로세스 안의 dict**라, 그 요청이
WebSocket을 쥔 워커와 다른 워커에 닿으면 유효한 토큰이어도 `404 Component not found`다.
#77 리뷰에서 독립 인터프리터 둘로 재현했다.

## 2. 결합은 세 겹이다

한 겹씩 봐야 답이 갈린다.

| 겹 | 지금 | 프로세스를 넘으면 |
|---|---|---|
| **인덱스** | `_upload_registries[(conn, comp)] → UploadRegistry` | 다른 워커에는 없다. 404 |
| **엔트리 가변 상태** | `entry.status`, `bytes_received`, `progress`, `temp_path`를 HTTP 핸들러가 그 자리에서 바꾼다. 소유 워커가 같은 객체를 본다 | HTTP 워커가 바꾼 것이 소유 워커에 안 보인다. `this.uploads`와 `consume_uploads()`가 거짓이 된다 |
| **바이트** | `tempfile.mkstemp()` → 시스템 temp에 `open(path, "ab")`로 이어붙인다 | 소유 워커가 그 파일을 못 읽는다. `ConsumedUpload.save_to()`가 실패한다 |

세 번째 겹이 결정적이다. 인덱스만 공유해도(Redis에 넣어도) 파일이 다른 호스트의 로컬 디스크에
있으면 아무것도 해결되지 않는다.

## 3. 이미 분산되어 있는 것

두 가지는 고칠 필요가 없다. 설계가 이것을 이용해야 한다.

- **토큰 검증은 이미 무상태다.** `TimestampSigner(salt="wireview.upload")`는 서명 키로
  서명한다 — 지금은 Django의 `SECRET_KEY`이고, 이 작업에서 전용 설정으로 뺀다(5-6).
  어느 쪽이든 **워커들이 공유하는 값**이라는 성질이 근거다. 레지스트리 인스턴스에 붙어 있을
  뿐 검증에 레지스트리 상태를 쓰지 않으므로, 어느 워커든
  `connection_id:component_id:config:ref`를 복원할 수 있다. 지금 코드가 **토큰 검증보다 먼저**
  인덱스를 조회해 404를 내는 것이 문제의 전부다.
- **통지 경로는 이미 브로커다.** 진행률·오류는 `wireview_upload_<connection_id>` 그룹으로
  publish되고 소유 워커가 그 그룹에 가입해 있다(#77). AC2는 지금도 만족한다.

또 하나, 클라이언트는 청크를 **순차로** 보낸다(`wireview.js:1990`, `for` 루프 안의 `await fetch`).
동시 청크도, 재시도도, 이어받기도 없다. 순서는 보장되고 중복은 없다.

## 4. 선택지

| 갈래 | 무엇 | 판단 |
|---|---|---|
| D. 스티키 라우팅 | `connection_id` 세그먼트로 소유 워커에 고정 | 코드 0. 이미 `DEPLOYMENT.md`에 적혀 있다. 워커 재시작·재배포에 약하고, LB 설정을 라이브러리가 강제할 수 없다. **해결이 아니라 회피다** |
| C. 브로커 RPC | 청크를 채널 레이어로 소유 워커에 전달 | 공유 저장소가 필요 없고 다중 호스트도 된다. 대신 **파일 전량이 브로커를 지난다**(100MB = 64KB 메시지 1600개). `group_send`에는 ack가 없어 큐가 차면 조용히 버려지고, 그 결과는 손상된 파일이다. HTTP 200이 거짓말이 된다 |
| B. 공유 레지스트리(Redis) | 레지스트리 항목을 공유 저장소에 | 인덱스 겹만 푼다. 가변 상태의 원자성을 새로 설계해야 하고, 바이트 겹은 그대로 남는다. **A의 부분집합인데 더 비싸다** |
| **A. 무상태 HTTP + 공유 바이트 저장소** | HTTP는 토큰만으로 판단하고 결정적 경로에 쓴다. 가변 상태는 브로커 메시지로 소유 워커가 갱신한다 | 아래 5절 |

## 5. 권고: A

### 5-1. HTTP 핸들러를 무상태로

`UploadView`는 `_upload_registries`를 조회하지 않는다. 토큰이 유일한 권한 증거다.

- 서명 검증 → `(connection_id, component_id, config, ref)`와 URL 세그먼트 대조.
- 바이트 한도는 토큰에서 읽는다. **토큰 v2**: `wv2:{conn}:{comp}:{config}:{ref}:{max_bytes}`.
  `max_bytes`는 발급 시점의 `entry.client_size`다. 완료 판정도 이 값으로 한다.
  구형 4-part 토큰은 롤링 배포 동안 받아 주되 한도를 `UPLOAD_MAX_FILE_SIZE`로 본다.
  (지금 HTTP는 크기를 **전혀** 검증하지 않고 `entry.client_size` 도달로만 완료를 판정한다.
  무상태화는 이 구멍을 함께 막는다.)
- 결과: 인덱스가 비어 있는 워커에서도 청크가 처리된다. `_upload_registries` 전역 dict가
  통째로 사라지고, `session-extraction.md`가 #60의 난제로 지목한 프로세스 전역 상태도 함께
  없어진다.

### 5-2. 바이트는 결정적 경로에

경로를 (연결, 컴포넌트, 업로드, ref)에서 **계산**한다. 어느 워커든 같은 경로를 얻는다.

```
<UPLOAD_TEMP_DIR or tempfile.gettempdir()>/wireview-uploads/<conn>/<h>.part
h = sha256(f"{component_id}\0{config}\0{ref}").hexdigest()[:32]
```

컴포넌트 id·업로드 이름·ref는 템플릿과 클라이언트에서 오므로 경로에 그대로 넣지 않고 해시한다
(경로 탈출 차단). `conn`은 서버가 만든 `token_urlsafe(16)`이고 URLconf에서 문자 집합을 강제한다.

이 결정은 `UPLOAD_TEMP_DIR`을 실제로 읽게 만든다 — **#84가 여기에 흡수된다.** 다만 #84는 그
자체로 버그이므로 먼저 독립으로 닫고, 이 작업이 그 위에 쌓는다.

### 5-3. 가변 상태는 소유 워커가 브로커로 받는다

HTTP 워커는 자기 요청 안에서만 상태를 만들고, 소유 워커가 진실을 들고 있는다.

| 시점 | HTTP 워커 | 소유 워커 |
|---|---|---|
| 청크마다 | 경로에 append, `upload.progress` publish | 지금처럼 클라이언트로 전달 + **엔트리의 `bytes_received`·`progress`·`status`를 갱신** |
| 마지막 청크 | magic bytes 검증, `upload.completed`(경로 포함) publish | 엔트리를 `COMPLETED`로, `temp_path`를 세팅 |
| 검증 실패 | `upload.error` publish | 지금처럼 전달 + 엔트리를 `ERROR`로 |

지금은 같은 프로세스라 이 갱신이 저절로 맞았다. 무상태화하면 **명시적으로** 해야 하고, 그래야
`this.uploads`와 `consume_uploads()`가 다중 프로세스에서도 진실이 된다.

클라이언트의 `upload_complete` WS 명령과 브로커의 `upload.completed`는 순서가 보장되지 않는다.
소유 워커는 순서를 신뢰하지 않는다 — `upload_complete`가 먼저 오면 경로를 stat해 크기가
`client_size`와 맞을 때만 승격한다.

### 5-4. 취소·정리 (AC3)

- **취소·leave**: 소유 워커가 `<h>.cancelled` tombstone을 만들고 `.part`를 지운다.
  HTTP 워커는 쓰기 전후로 tombstone을 stat하고, 있으면 자기가 쓴 것을 지우고 `410`.
  (#77이 넣은 "쓰기 뒤 재확인"의 프로세스 간 판본이다.)
- **정상 disconnect**: 연결 디렉터리 전체를 지운다. `unregister_connection_uploads`의 자리를
  경로 기반 정리가 대신한다.
- **프로세스 사망·소유자 부재**: 무상태 HTTP는 이미 떠난 컴포넌트로 오는 청크를 토큰 만료
  (`UPLOAD_TOKEN_MAX_AGE`, 기본 1시간)까지 받아 준다. mtime 기준 sweep이 필요하다 —
  `manage.py wireview_upload_gc`와, 쓰기 경로에서의 기회적 정리.

### 5-5. 남는 제약: 저장소를 정직하게 적는다

A는 "브로커만 있으면 어디서든 된다"가 **아니다**. 청크 저장소가 워커들 사이에 공유되어야 한다.

| 배포 | 되나 |
|---|---|
| 한 호스트에 워커 N개 (uvicorn N개 + Caddy — Windows 권장 구성 그대로) | **된다.** 같은 temp 디렉터리면 끝. 추가 인프라 0 |
| 여러 호스트 | 공유 볼륨(NFS/EFS)을 `UPLOAD_TEMP_DIR`로 지정하거나, external 업로드(presigned S3/GCS) |
| 공유 볼륨 없는 여러 호스트 | 오브젝트 스토리지 백엔드가 필요하다. **이번 범위 밖** |

오브젝트 스토리지를 지금 넣지 않는 이유: Django Storage API에는 append가 없다. 청크마다 객체를
만들고 완료 시 조립해야 하는데(100MB·64KB면 객체 1600개), 그건 같은 인터페이스의 다른 구현이
아니라 다른 쓰기 모델이다. 경로 계산과 append를 `features/upload_store.py`의 함수 몇 개로 모아
두면 나중에 백엔드를 끼울 자리는 남는다. 플러그인 기구는 만들지 않는다.

### 5-6. 서명 키를 전용 설정으로 뺀다

5-1이 토큰을 유일한 권한 증거로 삼으므로, 그 키의 수명이 곧 업로드의 수명이 된다. 지금은
`Signer.key = key or settings.SECRET_KEY`라 Django의 `SECRET_KEY`를 그대로 쓴다.

```python
"SIGNING_KEY": None,            # None = settings.SECRET_KEY
"SIGNING_KEY_FALLBACKS": None,  # None = settings.SECRET_KEY_FALLBACKS
```

- `wireview/core/signing.py`의 `get_signer(salt)` 하나를 두고 서명 지점 둘이 그것만 쓴다 —
  업로드 토큰(`features/uploads.py`)과 data-state(`core/state.py`). 구형 state 경로
  (`STATE_ACCEPT_LEGACY`)도 같은 키를 쓴다.
- **fallback을 같이 두는 것이 조건이다.** 지금은 `SECRET_KEY_FALLBACKS`를 공짜로 받아 키
  로테이션이 되므로, 자체 키만 만들고 fallback을 빠뜨리면 관리성이 지금보다 나빠진다.
- 호출 시점에 읽는다. 레지스트리 `__init__`에서 signer를 굳혀 두는 지금 방식은 헬퍼로 대체한다.
- 이득은 두 방향이다. `SECRET_KEY`를 돌려도 살아 있는 페이지의 data-state(14일)와 진행 중인
  업로드 토큰이 죽지 않고, 반대로 wireview 키만 돌려도 세션·CSRF가 죽지 않는다.
  salt가 이미 분리되어 있으므로 이것은 **취약점 수정이 아니라 키 수명 관리**다.
- 검사 후보: `SIGNING_KEY`가 빈 문자열이면 `key or settings.SECRET_KEY` 때문에 **조용히**
  `SECRET_KEY`로 되돌아간다. W 시리즈가 잡는 종류의 실패다.
- **대가를 적어 둔다.** "워커마다 동일해야 하는 값"이 하나 는다. `SECRET_KEY`는 Django 배포가
  이미 보장하지만 새 키는 아니다. 워커마다 다르면 청크가 403으로 실패한다 —
  `DEPLOYMENT.md`에 적는다.

## 6. 인수 조건 대응

| AC | 이 설계에서 |
|---|---|
| AC1 스티키 없이 완료 | 한 호스트 N워커에서 무조건, 다중 호스트는 공유 볼륨 조건부. 5-5를 `DEPLOYMENT.md`에 적는다 |
| AC2 통지가 소유 워커에 | 이미 된다(브로커 그룹). 여기에 상태 갱신을 얹는다(5-3) |
| AC3 경합에도 임시 파일이 안 남는다 | tombstone + 연결 디렉터리 정리 + mtime sweep(5-4) |
| AC4 다중 프로세스 검증 | 두 겹으로. (a) 단위: 인덱스를 비운 채 `UploadView`가 완주하는 테스트 — 무상태화의 직접 증명. (b) E2E: 워커 둘 + NATS, WS는 A에 붙이고 청크는 B에 POST |

## 7. 하지 않기로 한 것

- **청크를 브로커로 나르는 것(C).** ack 없는 fan-out 위에 파일 무결성을 얹지 않는다.
- **레지스트리를 Redis로 옮기는 것(B).** 바이트 겹을 남긴 채 가변 상태만 분산시킨다.
- **스티키 라우팅을 해답으로 삼는 것(D).** 문서에 남기되, 라이브러리의 답으로 두지 않는다.
- **업로드 이어받기(resume).** 클라이언트가 순차·무재시도이므로 지금 계약에 없다. 별건이다.
