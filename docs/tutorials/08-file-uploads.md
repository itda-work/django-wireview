# 08. File Uploads 심화

파일 업로드의 고급 기능과 보안 전략을 다룹니다.

## 학습 목표

- 업로드 설정 상세
- 진행률 추적
- 다중 파일 업로드
- 보안 및 검증
- 저장소 통합

## 업로드 설정

### UploadConfig 옵션

```python
from wireview.features.uploads import UploadConfig

config = UploadConfig(
    name="avatar",              # 업로드 필드 식별자
    accept=[".jpg", ".png"],    # 허용 확장자
    max_entries=1,              # 동시 업로드 수
    max_file_size=5*1024*1024,  # 5MB
    chunk_size=64*1024,         # 64KB 청크
    auto_upload=True,           # 선택 즉시 업로드
)
```

### 다양한 설정 예

```python
# 프로필 이미지 (단일, 작은 파일)
avatar_config = UploadConfig(
    name="avatar",
    accept=[".jpg", ".jpeg", ".png", ".gif", ".webp"],
    max_entries=1,
    max_file_size=2 * 1024 * 1024,  # 2MB
)

# 문서 업로드 (다중, 큰 파일)
docs_config = UploadConfig(
    name="documents",
    accept=[".pdf", ".doc", ".docx", ".xls", ".xlsx"],
    max_entries=10,
    max_file_size=50 * 1024 * 1024,  # 50MB
    chunk_size=256 * 1024,  # 256KB 청크
)

# 갤러리 이미지 (다중)
gallery_config = UploadConfig(
    name="photos",
    accept=[".jpg", ".jpeg", ".png"],
    max_entries=20,
    max_file_size=10 * 1024 * 1024,  # 10MB
    auto_upload=False,  # 수동 업로드
)
```

## 컴포넌트 구현

### 기본 구조

```python
from wireview.component import Component
from wireview.features.uploads import UploadConfig


class XFileUploader(Component):
    _template_name = 'uploader/upload_form.html'

    async def joined(self):
        self.allow_upload(UploadConfig(
            name="files",
            accept=[".pdf", ".jpg", ".png"],
            max_entries=5,
            max_file_size=10 * 1024 * 1024,
        ))

    async def upload_files(self):
        """업로드된 파일 처리"""
        for upload in self.consume_uploads("files"):
            # upload는 ConsumedUpload 인스턴스
            path = await upload.save_to("uploads/")
            print(f"Saved: {path}")
```

### 템플릿

```html
{% load wireview %}
<div {% tag_header %} class="uploader">
  <div class="upload-zone">
    <input
      type="file"
      wire-upload="files"
      accept=".pdf,.jpg,.png"
      multiple
    >
    <p>Drop files here or click to browse</p>
  </div>

  <!-- 업로드 목록 -->
  <ul class="upload-list">
    {% for entry in this.uploads.files %}
      <li class="upload-entry {{ entry.status }}">
        <span class="name">{{ entry.client_name }}</span>
        <span class="size">{{ entry.client_size|filesizeformat }}</span>

        {% if entry.status == 'uploading' %}
          <div class="progress">
            <div class="bar" style="width: {{ entry.progress }}%"></div>
          </div>
        {% elif entry.status == 'completed' %}
          <span class="status">✓ Ready</span>
        {% elif entry.status == 'error' %}
          <span class="error">{{ entry.errors|join:", " }}</span>
        {% endif %}

        <button {% on "click" "cancel_file" ref=entry.ref %}>×</button>
      </li>
    {% endfor %}
  </ul>

  <button {% on "click" "upload_files" %}>Save Files</button>
</div>
```

## 진행률 추적

### UploadEntry 속성

| 속성 | 타입 | 설명 |
|------|------|------|
| `ref` | str | 고유 참조 ID |
| `client_name` | str | 원본 파일명 |
| `client_size` | int | 파일 크기 (bytes) |
| `client_type` | str | MIME 타입 |
| `status` | str | pending/uploading/completed/error |
| `progress` | int | 0-100 |
| `errors` | list | 에러 메시지 목록 |

### 상태별 처리

```html
{% if entry.status == 'pending' %}
  <span>Waiting...</span>

{% elif entry.status == 'uploading' %}
  <div class="progress">{{ entry.progress }}%</div>

{% elif entry.status == 'completed' %}
  <span class="success">Upload complete</span>

{% elif entry.status == 'error' %}
  <span class="error">
    {% for error in entry.errors %}
      {{ error }}<br>
    {% endfor %}
  </span>

{% elif entry.status == 'cancelled' %}
  <span>Cancelled</span>
{% endif %}
```

## 파일 처리

### ConsumedUpload API

```python
async def process_upload(self):
    for upload in self.consume_uploads("files"):
        # 파일 정보
        print(upload.name)         # 원본 파일명
        print(upload.size)         # 크기 (bytes)
        print(upload.content_type) # MIME 타입
        print(upload.ref)          # 참조 ID

        # 파일 읽기
        content = upload.read()    # bytes로 읽기

        # 파일 핸들
        with upload.open("rb") as f:
            data = f.read()

        # 저장
        path = await upload.save_to("uploads/")

        # 커스텀 파일명으로 저장
        path = await upload.save_to(
            "avatars/",
            filename=f"user_{self.user_id}.jpg"
        )
```

### Django Storage 통합

`save_to()`는 Django의 default storage를 사용합니다:

```python
# settings.py
DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'

# 컴포넌트
async def save_to_s3(self):
    for upload in self.consume_uploads("files"):
        # S3에 저장됨
        path = await upload.save_to("media/uploads/")
```

### 직접 처리

```python
from PIL import Image

async def process_image(self):
    for upload in self.consume_uploads("avatar"):
        # Pillow로 이미지 처리
        with upload.open("rb") as f:
            img = Image.open(f)
            img = img.resize((200, 200))

            # 처리된 이미지 저장
            from io import BytesIO
            buffer = BytesIO()
            img.save(buffer, format='JPEG')
            # ...
```

## 보안

### 확장자 검증

```python
UploadConfig(
    name="images",
    accept=[".jpg", ".png"],  # 허용된 확장자만
)
```

### Magic Bytes 검증

Wireview는 파일 시그니처를 자동 검증합니다:

```python
# wireview/features/uploads.py
MAGIC_BYTES = {
    ".jpg": [b"\xff\xd8\xff"],
    ".png": [b"\x89PNG\r\n\x1a\n"],
    ".pdf": [b"%PDF"],
    # ...
}
```

`.jpg` 확장자지만 실제로 PNG인 경우 → 에러

### 크기 제한

```python
UploadConfig(
    max_file_size=5 * 1024 * 1024,  # 5MB
)
```

### 수동 검증

```python
async def save_files(self):
    for upload in self.consume_uploads("files"):
        # 추가 검증
        if upload.size > 1024 * 1024:
            self.add_error("File too large")
            continue

        if not self.is_safe_filename(upload.name):
            self.add_error("Invalid filename")
            continue

        await upload.save_to("uploads/")

def is_safe_filename(self, name: str) -> bool:
    import re
    # 안전한 문자만 허용
    return bool(re.match(r'^[\w\-. ]+$', name))
```

## 다중 업로드 필드

### 여러 업로드 설정

```python
async def joined(self):
    self.allow_upload(UploadConfig(name="avatar", max_entries=1))
    self.allow_upload(UploadConfig(name="documents", max_entries=10))
    self.allow_upload(UploadConfig(name="gallery", max_entries=20))
```

### 필드별 처리

```python
async def save_all(self):
    # 아바타
    for upload in self.consume_uploads("avatar"):
        await upload.save_to("avatars/")

    # 문서
    for upload in self.consume_uploads("documents"):
        await upload.save_to("documents/")

    # 갤러리
    for upload in self.consume_uploads("gallery"):
        await upload.save_to("gallery/")
```

## 업로드 취소

### 사용자 취소

```python
async def cancel_file(self, ref: str):
    """특정 파일 업로드 취소"""
    self.cancel_upload("files", ref)
```

### 템플릿

```html
<button {% on "click" "cancel_file" ref=entry.ref %}>Cancel</button>
```

## 드래그 앤 드롭

### CSS

```css
.upload-zone {
  border: 2px dashed #ccc;
  padding: 2rem;
  text-align: center;
}

.upload-zone.dragover {
  border-color: #007bff;
  background: #f0f7ff;
}
```

### JavaScript (자동 처리)

`wire-upload` 속성이 있는 input은 자동으로 드래그 앤 드롭을 지원합니다.

## 이미지 미리보기

`{% upload_preview %}` 태그로 업로드 전 이미지 미리보기를 표시합니다.

### 기본 사용법

```html
{% load wireview %}

<div {% tag_header %}>
  <h3>프로필 사진 업로드</h3>

  {% upload_input "avatar" %}

  <!-- 업로드된 이미지 미리보기 -->
  {% for entry in this.uploads.avatar %}
    <div class="preview-container">
      {% upload_preview entry class="w-32 h-32 rounded-full object-cover" %}
      <p>{{ entry.client_name }}</p>
    </div>
  {% endfor %}
</div>
```

### 갤러리 미리보기

```html
<div class="gallery-upload">
  {% upload_input "photos" multiple %}

  <div class="grid grid-cols-4 gap-4">
    {% for entry in this.uploads.photos %}
      <div class="relative">
        {% upload_preview entry class="w-full h-32 object-cover rounded" %}

        <!-- 업로드 진행률 오버레이 -->
        {% if entry.status == 'uploading' %}
          <div class="absolute inset-0 bg-black/50 flex items-center justify-center">
            <span class="text-white">{{ entry.progress }}%</span>
          </div>
        {% endif %}

        <!-- 취소 버튼 -->
        <button
          {% on "click" "cancel_file" ref=entry.ref %}
          class="absolute top-1 right-1 bg-red-500 text-white rounded-full w-6 h-6"
        >×</button>
      </div>
    {% endfor %}
  </div>
</div>
```

### 미리보기가 표시되지 않는 경우

- **비이미지 파일**: PDF, DOC 등은 미리보기가 표시되지 않습니다
- **파일 미선택**: 아직 파일을 선택하지 않은 경우
- **브라우저 호환성**: 구형 브라우저에서는 Blob URL을 지원하지 않을 수 있습니다

## 외부 스토리지 업로드 (S3/GCS)

대용량 파일은 Django 서버를 거치지 않고 직접 클라우드 스토리지로 업로드할 수 있습니다.

### S3 직접 업로드

```python
import boto3
from wireview import Component, ExternalUploadMeta


class XDocumentUploader(Component):
    _template_name = "documents/uploader.html"

    async def joined(self):
        self.allow_upload(
            "documents",
            accept=[".pdf", ".doc", ".docx"],
            max_file_size=100 * 1024 * 1024,  # 100MB
            external=self.presign_s3_upload,  # 외부 업로드 콜백
        )

    def presign_s3_upload(self, entry, component):
        """S3 presigned URL 생성"""
        s3 = boto3.client("s3")

        key = f"documents/{entry.ref}/{entry.client_name}"
        url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": "my-bucket",
                "Key": key,
                "ContentType": entry.client_type,
            },
            ExpiresIn=3600,
        )

        return ExternalUploadMeta(
            uploader="S3",
            url=url,
            headers={"Content-Type": entry.client_type},
        )

    async def save_document(self):
        # 업로드 완료 후 DB에 기록
        for upload in self.consume_uploads("documents"):
            key = f"documents/{upload.ref}/{upload.name}"
            await Document.objects.acreate(
                name=upload.name,
                s3_key=key,
                size=upload.size,
            )
```

### GCS 직접 업로드

```python
from google.cloud import storage
from wireview import Component, ExternalUploadMeta


class XImageUploader(Component):
    _template_name = "images/uploader.html"

    async def joined(self):
        self.allow_upload(
            "images",
            accept=[".jpg", ".png", ".webp"],
            external=self.presign_gcs_upload,
        )

    def presign_gcs_upload(self, entry, component):
        """GCS signed URL 생성"""
        client = storage.Client()
        bucket = client.bucket("my-bucket")
        blob = bucket.blob(f"images/{entry.ref}/{entry.client_name}")

        url = blob.generate_signed_url(
            version="v4",
            expiration=3600,
            method="PUT",
            content_type=entry.client_type,
        )

        return ExternalUploadMeta(
            uploader="GCS",
            url=url,
            headers={"Content-Type": entry.client_type},
        )
```

### CORS 설정

외부 업로드를 위해 스토리지 버킷에 CORS 설정이 필요합니다:

**S3 CORS:**
```json
{
  "CORSRules": [{
    "AllowedOrigins": ["https://your-domain.com"],
    "AllowedMethods": ["PUT"],
    "AllowedHeaders": ["*"],
    "MaxAgeSeconds": 3600
  }]
}
```

### 일반 업로드 vs 외부 업로드

| 항목 | 일반 업로드 | 외부 업로드 (S3/GCS) |
|------|------------|---------------------|
| 서버 대역폭 | 파일이 서버를 통과 | 직접 스토리지로 전송 |
| 속도 | 소용량 파일에 적합 | 대용량 파일에 최적 |
| 설정 | 즉시 사용 가능 | CORS 설정 필요 |
| 파일 처리 | 서버에서 가공 가능 | 업로드 후 별도 처리 |

## 에러 처리

### 공통 에러

| 에러 | 원인 | 해결 |
|------|------|------|
| "Invalid file type" | 허용되지 않은 확장자 | accept 설정 확인 |
| "File too large" | max_file_size 초과 | 제한 늘리거나 파일 압축 |
| "Maximum entries reached" | max_entries 초과 | 기존 파일 제거 |
| "File content doesn't match" | Magic bytes 불일치 | 올바른 파일 사용 |

### 에러 표시

```html
{% if entry.errors %}
  <div class="errors">
    {% for error in entry.errors %}
      <p class="error">{{ error }}</p>
    {% endfor %}
  </div>
{% endif %}
```

## 완성된 예제

```python
class XProfileEditor(Component):
    _template_name = 'profile/editor.html'

    user_id: int
    avatar_url: str = ""

    async def joined(self):
        self.allow_upload(UploadConfig(
            name="avatar",
            accept=[".jpg", ".jpeg", ".png", ".gif"],
            max_file_size=2 * 1024 * 1024,
            max_entries=1,
        ))

    async def save_avatar(self):
        for upload in self.consume_uploads("avatar"):
            try:
                path = await upload.save_to(
                    "avatars/",
                    filename=f"user_{self.user_id}.jpg"
                )
                self.avatar_url = path

                # DB 업데이트
                await User.objects.filter(id=self.user_id).aupdate(
                    avatar=path
                )

            except ValueError as e:
                self.add_flash_message(str(e), "error")
```

## 다음 단계

[← 이전: 07. Presence API 심화](07-presence-api.md) | [다음: 09. 테스트 가이드 →](09-testing-components.md)
