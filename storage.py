"""
S3-compatible object storage for user uploads (avatars, banners).

Configure via environment variables:
- S3_BUCKET: bucket name
- S3_REGION: AWS region (e.g. us-east-1). Omit for Cloudflare R2.
- S3_ENDPOINT_URL: optional, for S3-compatible providers (R2, B2, MinIO)
- S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY: credentials
- S3_PUBLIC_BASE_URL: URL prefix clients will use to read uploaded files
  (e.g. https://cdn.yourdomain.com or https://<bucket>.s3.amazonaws.com)

If S3_BUCKET is not set, upload_bytes() returns None and callers fall back to
storing base64 in the DB.
"""
import logging
import mimetypes
import os
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(os.getenv("S3_BUCKET"))


def _client():
    """Lazy import boto3 so the rest of the app runs without it installed."""
    import boto3

    return boto3.client(
        "s3",
        region_name=os.getenv("S3_REGION"),
        endpoint_url=os.getenv("S3_ENDPOINT_URL"),
        aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
    )


def upload_bytes(data: bytes, content_type: str, prefix: str = "avatars") -> Optional[str]:
    """Upload raw bytes to S3 and return the public URL, or None if not configured."""
    if not is_configured():
        return None
    bucket = os.getenv("S3_BUCKET")
    public_base = os.getenv("S3_PUBLIC_BASE_URL", "").rstrip("/")
    ext = mimetypes.guess_extension(content_type) or ""
    key = f"{prefix}/{uuid.uuid4().hex}{ext}"
    try:
        _client().put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable",
        )
    except Exception:
        logger.exception("S3 upload failed")
        raise
    if public_base:
        return f"{public_base}/{key}"
    return f"https://{bucket}.s3.amazonaws.com/{key}"
