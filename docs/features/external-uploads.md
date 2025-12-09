# External Uploads (S3/GCS)

django-wireview supports external uploads directly to cloud storage services like Amazon S3 or Google Cloud Storage. This bypasses the Django server, reducing bandwidth and improving upload performance for large files.

## Overview

External uploads work by:
1. Client selects files for upload
2. Server generates presigned URLs for direct upload
3. Client uploads directly to cloud storage
4. Server is notified when upload completes

## Quick Start

### S3 Example

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
        """Generate presigned URL for S3 upload."""
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
            ExpiresIn=3600,  # 1 hour
        )

        return ExternalUploadMeta(
            uploader="S3",
            url=url,
            method="PUT",
            headers={"Content-Type": entry.client_type},
        )

    async def upload_completed(self):
        """Called when all uploads complete."""
        # Process completed uploads
        for upload in self.uploads["documents"]:
            self.files.append({
                "name": upload.name,
                "ref": upload.ref,
                # Store the S3 key for later retrieval
                "key": f"uploads/{upload.ref}/{upload.name}",
            })
```

### Google Cloud Storage Example

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
        """Generate signed URL for GCS upload."""
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

The `ExternalUploadMeta` dataclass configures external uploads:

```python
from wireview import ExternalUploadMeta

meta = ExternalUploadMeta(
    uploader="S3",           # Name for debugging/logging
    url="https://...",       # Presigned upload URL
    method="PUT",            # HTTP method (default: "PUT")
    headers={                # Optional additional headers
        "Content-Type": "application/pdf",
        "x-amz-acl": "private",
    },
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `uploader` | `str` | Yes | Identifier for the upload service (e.g., "S3", "GCS") |
| `url` | `str` | Yes | Presigned URL for direct upload |
| `method` | `str` | No | HTTP method (default: "PUT") |
| `headers` | `dict` | No | Additional HTTP headers to include |

## Template Usage

```html
{% load wireview %}

<div {% tag_header %}>
    <h2>Upload Files</h2>

    <!-- Upload input -->
    {% upload_input "documents" %}

    <!-- Or drag and drop zone -->
    {% upload_drop_zone "documents" %}
        <p>Drop files here or click to select</p>
    {% end_upload_drop_zone %}

    <!-- Show upload progress -->
    <ul>
    {% for entry in uploads.documents %}
        <li>
            {{ entry.client_name }}
            {% if entry.status == "uploading" %}
                <progress value="{{ entry.progress }}" max="100"></progress>
            {% elif entry.status == "completed" %}
                ✓ Uploaded
            {% elif entry.status == "error" %}
                ✗ {{ entry.errors|join:", " }}
            {% endif %}
        </li>
    {% endfor %}
    </ul>
</div>
```

## CORS Configuration

For external uploads to work, you must configure CORS on your storage bucket.

### S3 CORS Configuration

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

### GCS CORS Configuration

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

## Comparison: External vs Chunked Uploads

| Feature | External Upload | Chunked Upload |
|---------|-----------------|----------------|
| Server bandwidth | None (direct to storage) | Full file passes through server |
| Upload speed | Faster for large files | Good for small files |
| Resumability | Depends on storage | Built-in chunk resume |
| Configuration | Requires CORS setup | Works out of the box |
| File size | Up to storage limits | Limited by chunk memory |

## Best Practices

1. **Use for large files**: External uploads are most beneficial for files >10MB
2. **Set appropriate expiration**: Use short expiration times (1 hour) for security
3. **Include Content-Type**: Always set the Content-Type header for proper storage
4. **Use private ACLs**: Default to private unless public access is needed
5. **Generate unique keys**: Use the entry `ref` to ensure unique storage keys
6. **Handle errors gracefully**: Provide user feedback for upload failures

## Error Handling

The external upload callback can raise exceptions to reject uploads:

```python
def presign_upload(self, entry, component):
    # Reject uploads from unauthenticated users
    if not component.wire.user.is_authenticated:
        raise ValueError("Authentication required for uploads")

    # Reject files that are too large for your storage plan
    if entry.client_size > 100 * 1024 * 1024:
        raise ValueError("Files over 100MB are not allowed")

    # Generate presigned URL...
    return ExternalUploadMeta(...)
```

## Security Considerations

1. **Validate file types server-side**: Don't rely solely on client-side validation
2. **Use short-lived URLs**: Presigned URLs should expire quickly
3. **Implement rate limiting**: Prevent abuse by limiting upload frequency
4. **Scan uploaded files**: Consider virus scanning for uploaded content
5. **Authenticate users**: Ensure only authorized users can upload

## See Also

- [File Uploads Guide](../guides/file-uploads.md)
- [Phoenix LiveView External Uploads](https://hexdocs.pm/phoenix_live_view/uploads.html#external-uploads)
