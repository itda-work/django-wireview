# External 업로드 (S3/GCS)

파일을 Django 서버가 아니라 클라우드 스토리지로 **직접** 올린다. 서버 대역폭을 쓰지 않으므로 큰
파일에 유리하고, 청크 엔드포인트가 요구하는 공유 저장소 조건([chunked-uploads.md](./chunked-uploads.md))에서도
자유롭다.

## 흐름

1. 클라이언트가 파일을 고른다
2. 서버가 presigned URL을 만들어 준다
3. 클라이언트가 스토리지로 직접 올린다
4. 업로드가 끝나면 서버에 알린다

## 빠른 시작

### S3

```python
import boto3
from wireview import Component, ExternalUploadMeta


class FileUploader(Component):
    _template_name = "uploads/file_uploader.html"

    files: list[dict] = []

    async def joined(self):
        self.allow_upload(
            "documents",
            accept=[".pdf", ".doc", ".docx"],
            max_entries=5,
            max_file_size=50 * 1024 * 1024,  # 50MB
            external=self.presign_s3_upload,
        )

    def presign_s3_upload(self, entry, component):
        """Generate a presigned URL for the S3 upload."""
        s3 = boto3.client(
            "s3",
            aws_access_key_id="YOUR_ACCESS_KEY",
            aws_secret_access_key="YOUR_SECRET_KEY",
            region_name="us-east-1",
        )

        key = f"uploads/{entry.ref}/{entry.client_name}"

        url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": "your-bucket",
                "Key": key,
                "ContentType": entry.client_type,
            },
            ExpiresIn=3600,  # 1시간
        )

        return ExternalUploadMeta(
            uploader="S3",
            url=url,
            method="PUT",
            headers={"Content-Type": entry.client_type},
        )

    async def on_upload_complete(self, name, entry):
        """Called for each upload the client reports as finished."""
        self.files.append(
            {
                "name": entry.client_name,
                "ref": entry.ref,
                # 나중에 꺼내 쓸 S3 키를 기억해 둔다
                "key": f"uploads/{entry.ref}/{entry.client_name}",
            }
        )
```

`on_upload_complete(name, entry)`가 완료 훅이다. **external 업로드에는 서버에 파일이 없으므로**
`consume_uploads()`로 바이트를 읽을 수 없다. 스토리지의 키를 기억해 두는 것이 이 훅의 일이다.

### Google Cloud Storage

```python
from google.cloud import storage
from wireview import Component, ExternalUploadMeta


class GCSUploader(Component):
    _template_name = "uploads/gcs_uploader.html"

    async def joined(self):
        self.allow_upload(
            "images",
            accept=[".jpg", ".png", ".gif"],
            external=self.presign_gcs_upload,
        )

    def presign_gcs_upload(self, entry, component):
        """Generate a signed URL for the GCS upload."""
        client = storage.Client()
        bucket = client.bucket("your-bucket")
        blob = bucket.blob(f"uploads/{entry.ref}/{entry.client_name}")

        url = blob.generate_signed_url(
            version="v4",
            expiration=3600,
            method="PUT",
            content_type=entry.client_type,
        )

        return ExternalUploadMeta(
            uploader="GCS",
            url=url,
            method="PUT",
            headers={"Content-Type": entry.client_type},
        )
```

## ExternalUploadMeta

콜백이 돌려주는 값이다.

```python
from wireview import ExternalUploadMeta

meta = ExternalUploadMeta(
    uploader="S3",           # 디버깅·로그용 이름
    url="https://...",       # presigned 업로드 URL
    method="PUT",            # HTTP 메서드 (기본 "PUT")
    headers={                # 추가 헤더 (선택)
        "Content-Type": "application/pdf",
        "x-amz-acl": "private",
    },
)
```

| 인자 | 타입 | 필수 | 뜻 |
|------|------|:----:|-----|
| `uploader` | `str` | ✅ | 업로드 서비스 식별자 ("S3", "GCS" 등) |
| `url` | `str` | ✅ | 직접 업로드용 presigned URL |
| `method` | `str` | | HTTP 메서드 (기본 `"PUT"`) |
| `headers` | `dict` | | 함께 보낼 헤더 |

## 템플릿

```html
{% load wireview %}

<div {% tag_header %}>
    <h2>파일 올리기</h2>

    <!-- 파일 선택 input -->
    {% upload_input "documents" %}

    <!-- 드래그 앤 드롭 영역. 속성 태그이므로 엘리먼트에 붙인다 -->
    <div {% upload_drop_zone "documents" %} class="drop-area">
        <p>여기에 파일을 놓거나 클릭해서 고르세요</p>
    </div>

    <!-- 진행 상황 -->
    <ul>
    {% for entry in this.uploads.documents %}
        <li>
            {{ entry.client_name }}
            {% if entry.status == "uploading" %}
                <progress value="{{ entry.progress }}" max="100"></progress>
            {% elif entry.status == "completed" %}
                ✓ 완료
            {% elif entry.status == "error" %}
                ✗ {{ entry.errors|join:", " }}
            {% endif %}
        </li>
    {% endfor %}
    </ul>
</div>
```

드래그 중인 동안 드롭 영역에는 `wireview-drag-over` 클래스가 붙는다.

## CORS 설정

external 업로드는 브라우저가 스토리지로 직접 요청하므로 버킷에 CORS가 필요하다.

### S3

```json
{
    "CORSRules": [
        {
            "AllowedOrigins": ["https://your-domain.com"],
            "AllowedMethods": ["PUT"],
            "AllowedHeaders": ["*"],
            "ExposeHeaders": ["ETag"],
            "MaxAgeSeconds": 3600
        }
    ]
}
```

### GCS

```json
[
    {
        "origin": ["https://your-domain.com"],
        "method": ["PUT"],
        "responseHeader": ["Content-Type"],
        "maxAgeSeconds": 3600
    }
]
```

## external vs 청크 업로드

| 항목 | external | 청크 |
|------|----------|------|
| 서버 대역폭 | 안 쓴다 (스토리지로 직행) | 파일 전량이 서버를 지난다 |
| 큰 파일 | 유리하다 | 작은 파일에 적합 |
| 이어받기 | 스토리지에 달렸다 | **없다.** 클라이언트가 순차·무재시도로 보낸다 |
| 설정 | CORS와 자격증명이 필요하다 | 워커들이 청크 저장소와 서명 키를 공유해야 한다 |
| 서버에서 파일 읽기 | 못 읽는다. 스토리지 키만 남는다 | `consume_uploads()`로 읽는다 |
| 파일 크기 | 스토리지 한도까지 | `max_file_size`까지 |

## 권장 사항

1. **큰 파일에 쓴다.** 10MB를 넘으면 이점이 뚜렷하다
2. **만료를 짧게 잡는다.** 1시간 정도
3. **Content-Type을 반드시 넣는다.** 스토리지에 제대로 저장되게 한다
4. **기본을 private ACL로 둔다.** 공개가 꼭 필요할 때만 연다
5. **키를 고유하게 만든다.** `entry.ref`를 섞는다
6. **실패를 사용자에게 알린다.** 아래 오류 처리 참고

## 오류 처리

콜백에서 예외를 던지면 그 업로드가 거절된다.

```python
def presign_upload(self, entry, component):
    # 로그인하지 않은 사용자의 업로드를 막는다
    if not component.wire.user.is_authenticated:
        raise ValueError("Authentication required for uploads")

    # 요금제 한도를 넘는 파일을 막는다
    if entry.client_size > 100 * 1024 * 1024:
        raise ValueError("Files over 100MB are not allowed")

    # presigned URL 생성...
    return ExternalUploadMeta(...)
```

## 보안

1. **파일 종류를 서버에서 검증한다.** 클라이언트 검증만 믿지 않는다
2. **URL 수명을 짧게 한다.**
3. **속도 제한을 건다.** 업로드 빈도를 제한해 남용을 막는다
4. **업로드된 파일을 검사한다.** 바이러스 스캔을 고려한다
5. **사용자를 인증한다.** 콜백 안에서 권한을 확인한다

## 관련

- [튜토리얼 08 — 파일 업로드](../tutorials/08-file-uploads.md)
- [chunked-uploads.md](./chunked-uploads.md) — 서버를 지나는 청크 경로
- [Phoenix LiveView External Uploads](https://hexdocs.pm/phoenix_live_view/uploads.html#external-uploads)
