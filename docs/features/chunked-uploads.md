# 청크 업로드의 서버 쪽 (#83)

> 컴포넌트에서 업로드를 **쓰는 법**은 [튜토리얼 08](../tutorials/08-file-uploads.md)이 정본이다.
> 이 문서는 청크가 서버에서 어떻게 처리되고, 워커가 여럿일 때 무엇이 필요한지를 다룬다.
> 저장소를 아예 거치지 않는 방식은 [external-uploads.md](./external-uploads.md).

## 문제

청크는 WebSocket이 아니라 HTTP로 온다.

```
/__wireview_upload__/<connection_id>/<component_id>/<upload_name>/
```

WebSocket은 워커 하나에 고정되지만 HTTP 요청은 로드밸런서가 아무 워커에나 보낸다. #83 이전에는
업로드 레지스트리가 **프로세스 안의 dict**였고, 엔드포인트가 토큰을 검사하기 **전에** 그 dict를
조회했다. 그래서 다른 워커에 닿은 청크는 유효한 토큰이어도 `404 Component not found`였다.

## 지금 (#83 이후)

**HTTP 핸들러는 업로드 상태를 하나도 들고 있지 않다.** 판단 근거는 서명된 토큰뿐이고, 바이트는
그 토큰에서 **계산한** 경로로 간다. 어느 워커든 같은 답에 도달한다.

### 1. 토큰이 유일한 권한 증거

토큰은 `allow_upload()`한 컴포넌트가 엔트리를 등록할 때 발급된다(v2, `sign_object`).

| 담긴 것 | 왜 |
|---------|-----|
| `connection_id`, `component_id`, `config_name`, `ref` | 어느 업로드인지. URL 세그먼트와 전부 대조한다. 하나라도 다르면 403 |
| `max_bytes` | 클라이언트가 신고한 크기. 이 값을 넘겨 쓰려 하면 413이고, 도달하면 완료다 |
| `ext` | 클라이언트 파일명의 확장자. 마지막 청크에서 magic bytes 검사에 쓴다 |

문자열을 구분자로 잇지 않고 객체로 서명한다. 컴포넌트 id와 업로드 이름은 앱 템플릿에서 오므로
콜론이 들어갈 수 있고, 그러면 **누가 무엇을 쓸 수 있는지 정하는 자리**에서 파싱이 어긋난다.

#83 이전의 4토막 문자열 토큰(`conn:comp:config:ref`)도 아직 받는다. 롤링 배포 중 옛 워커가 그린
페이지의 업로드가 끝나게 하기 위해서다. 크기가 없으므로 전역 `UPLOAD_MAX_FILE_SIZE`가 한도가 되고
확장자가 없으므로 magic bytes 검사가 헐거워진다 — 지원 형식이 아니라 전환 장치다.

### 2. 경로는 계산한다

```
<UPLOAD_TEMP_DIR 또는 시스템 temp>/wireview-uploads/<connection_id>/<digest>.part
digest = sha256(f"{component_id}\0{config_name}\0{ref}")[:32]
```

컴포넌트 id·업로드 이름·ref는 템플릿과 클라이언트에서 오므로 경로에 그대로 넣지 않고 해시한다
(경로 탈출 차단). `connection_id`는 서버가 `secrets.token_urlsafe(16)`으로 만든 값이고 디렉터리
이름이 되므로 URLconf와 `upload_store`가 둘 다 문자 집합을 강제한다.

계산은 전부 `wireview/features/upload_store.py`에 있다. 나중에 오브젝트 스토리지 백엔드를 끼운다면
그 자리다.

### 3. 가변 상태는 소유 워커가 브로커로 받는다

HTTP 워커는 컴포넌트 객체를 갖고 있지 않으므로 `this.uploads`를 갱신할 수 없다. 그래서 자기가 한
일을 연결의 진행률 그룹(`wireview_upload_<connection_id>`)에 publish하고, WebSocket을 쥔 워커가
그것을 엔트리에 적용한다.

| 메시지 | HTTP 워커가 보낼 때 | 소유 워커가 하는 일 |
|--------|---------------------|---------------------|
| `upload.progress` | 청크마다 | `bytes_received`·`progress` 갱신, 브라우저에 전달 |
| `upload.completed` | 마지막 청크가 검증을 통과 | 파일을 stat해 크기가 맞으면 `COMPLETED`로 승격 |
| `upload.error` | 크기 초과, 형식 불일치 | 엔트리를 `ERROR`로, 브라우저에 전달 |

모든 메시지에 `component` id가 들어간다. 그룹은 연결당 하나라 페이지의 업로드 전부가 같은 그룹으로
오기 때문이다.

**완료는 두 경로로 알려진다.** 브라우저의 `upload_complete` WS 명령과 브로커의 `upload.completed`는
순서가 보장되지 않는다. 소유 워커는 둘 중 어느 것도 그대로 믿지 않고 파일을 stat해서 크기가
`client_size`에 도달했을 때만 승격한다. `on_upload_complete` 콜백은 WS 명령 쪽에서만 호출된다 —
양쪽에서 부르면 두 번 실행된다.

### 4. 취소는 마커로 프로세스를 넘는다

파일을 지우는 것으로는 취소가 안 된다. 무상태 엔드포인트는 다음 청크에서 파일을 다시 만든다.

| 일 | 남기는 것 |
|----|-----------|
| `cancel_upload()` / 컴포넌트 leave | `<digest>.cancelled` |
| disconnect | 연결 디렉터리의 `.gone`, 그리고 그 안의 `.part` 전부 삭제 |

쓰기 워커는 청크를 쓰기 **전과 후에** 둘 다 확인하고, 있으면 자기가 쓴 것을 지우고 `410`을 준다.
쓰기가 요청 안의 유일한 await이므로 그 사이에 취소가 끼어들 수 있기 때문이다.

### 5. 남는 것은 나이로 정리한다

워커가 죽으면 그 워커의 취소 경로도 같이 사라진다. 무상태 엔드포인트는 이미 떠난 컴포넌트의 청크를
토큰이 만료될 때까지 받아 준다. 그래서 남을 수 있는 것은 `UPLOAD_TOKEN_MAX_AGE`로 묶이고, 정리 기준은
나이 하나다 — 그보다 오래된 파일은 아직 끝날 수 있는 업로드의 것일 수 없다.

```bash
python manage.py wireview_upload_gc            # 지금 청소
python manage.py wireview_upload_gc --dry-run  # 뭐가 지워질지만
```

쓰기 경로도 프로세스당 10분에 한 번 기회적으로 청소하므로, cron을 걸지 않아도 남는 양은 유계다.

## 배포에 필요한 것

| 배포 | 되나 |
|------|------|
| 한 호스트에 워커 N개 | **된다.** 같은 temp 디렉터리를 보므로 추가 인프라 0 |
| 여러 호스트 | 공유 볼륨(NFS/EFS)을 `UPLOAD_TEMP_DIR`로 지정하거나, external 업로드 |
| 공유 볼륨 없는 여러 호스트 | 오브젝트 스토리지 백엔드가 필요하다. 아직 없다 |

워커들이 **같아야 하는 값**은 두 개다. 청크 저장소(`UPLOAD_TEMP_DIR`)와 서명 키다. 서명 키가
어긋나면 청크가 403으로 실패한다. `docs/DEPLOYMENT.md`가 이것을 다룬다.

## 설정

| 키 | 기본값 | 뜻 |
|----|--------|-----|
| `UPLOAD_TEMP_DIR` | `None` | 청크 저장소의 부모 디렉터리. `None`이면 시스템 temp. 빈 문자열은 미설정으로 취급한다(`Path("")`가 cwd이므로). 없으면 만들고, 쓸 수 없으면 `ImproperlyConfigured` |
| `UPLOAD_MAX_FILE_SIZE` | 10MB | 구형 토큰의 한도이자 `allow_upload`의 기본 한도 |
| `UPLOAD_CHUNK_SIZE` | 64KB | 클라이언트가 자르는 크기 |
| `UPLOAD_TOKEN_MAX_AGE` | 1시간 | 토큰 유효 기간이자 sweep 기준 나이 |
| `SIGNING_KEY` | `None` | `None`이면 Django의 `SECRET_KEY`. 아래 참고 |
| `SIGNING_KEY_FALLBACKS` | `None` | `None`이면 `SECRET_KEY_FALLBACKS` |

### 왜 전용 서명 키인가

토큰이 유일한 권한 증거이므로 그 키의 수명이 곧 업로드의 수명이다. `SECRET_KEY`를 그대로 쓰면
세션 때문에 키를 돌리는 순간 진행 중인 업로드와 열려 있는 페이지의 `data-state`(기본 14일)가 같이
죽는다. 전용 키는 그 둘을 분리한다 — 양방향으로.

**fallback을 같이 두는 것이 조건이다.** 지금은 `SECRET_KEY_FALLBACKS`를 공짜로 받아 로테이션이
되므로, 자체 키만 만들고 fallback을 빠뜨리면 오히려 나빠진다. `SIGNING_KEY`가 빈 문자열이면
`key or SECRET_KEY` 때문에 조용히 되돌아가므로 `manage.py check`의 `wireview.W009`가 잡는다.

salt(`wireview.upload` / `wireview.state.v1`)는 이미 분리되어 있어 토큰 교차 사용은 전부터 막혀
있었다. 그러므로 이것은 취약점 수정이 아니라 **키 수명 관리**다.

## 함정

- **`ATOMIC_REQUESTS = True`.** Django의 핸들러는 async 뷰를 트랜잭션으로 감쌀 수 없어 뷰를 부르기도
  전에 실패한다. `wireview.urls`가 엔드포인트를 모든 alias에 대해 `non_atomic_requests`로 등록하므로
  앱에서 할 일은 없지만, 업로드 URL을 직접 등록한다면 같은 처리가 필요하다.
- **워커마다 `UPLOAD_TEMP_DIR`이 다르면** 청크는 200을 받지만 소유 워커가 파일을 못 찾아 완료가
  되지 않는다. 한 호스트라면 기본값(시스템 temp)이 이미 공유다.
- **external 업로드는 이 경로를 쓰지 않는다.** 바이트가 워커를 아예 지나지 않으므로 저장소 조건도,
  청소도 해당하지 않는다.

## 관련

- [튜토리얼 08 — 파일 업로드](../tutorials/08-file-uploads.md)
- [external-uploads.md](./external-uploads.md)
- [checks.md](./checks.md) — `wireview.W008`, `wireview.W009`
- [design/distributed-uploads.md](../design/distributed-uploads.md) — 왜 이 설계인지
- [DEPLOYMENT.md](../DEPLOYMENT.md)
