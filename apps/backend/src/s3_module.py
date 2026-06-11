"""
s3_module.py — конфигурация и клиенты S3-хранилища вложений (вынесено из main.py).

Два клиента: внутренний (загрузка из backend в MinIO/S3 по docker-адресу) и «публичный»
(только для подписи presigned-ссылок, которые открывает браузер). Плюс хелпер
build_attachment_items — единое место сборки контрактных объектов вложений.
"""

import os

import boto3
from botocore.config import Config

S3_BUCKET       = os.environ.get("S3_BUCKET_NAME", "")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
# Endpoint used to SIGN URLs handed to the browser. The backend reaches MinIO at
# the internal docker host (minio:9000), but the browser can't resolve that — so
# presigned links must be signed against a host the browser can reach
# (e.g. http://localhost:9000). Falls back to S3_ENDPOINT_URL when not set, which
# is correct for a real cloud S3 whose endpoint is public anyway.
S3_PUBLIC_ENDPOINT_URL = os.environ.get("S3_PUBLIC_ENDPOINT_URL") or S3_ENDPOINT_URL
S3_REGION       = os.environ.get("S3_REGION", "auto")
# Path-style addressing (http://endpoint/bucket/key) instead of virtual-hosted
# (http://bucket.endpoint/key). Required by self-hosted S3 like MinIO; harmless
# for providers that support both. Off by default so existing setups are unchanged.
S3_FORCE_PATH_STYLE = os.environ.get("S3_FORCE_PATH_STYLE", "").strip().lower() in ("1", "true", "yes", "on")

_s3_client = None
_s3_public_client = None


def _make_s3(endpoint_url):
    cfg = Config(s3={"addressing_style": "path"}) if S3_FORCE_PATH_STYLE else None
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY"),
        region_name=S3_REGION,
        config=cfg,
    )


def is_configured() -> bool:
    return bool(S3_ENDPOINT_URL and S3_BUCKET)


def get_s3():
    """Client for server-side operations (upload) — uses the internal endpoint."""
    global _s3_client
    if _s3_client is None:
        _s3_client = _make_s3(S3_ENDPOINT_URL)
    return _s3_client


def get_s3_public():
    """Client used only to SIGN presigned URLs for the browser — uses the public
    endpoint so the resulting links are reachable from outside the docker network."""
    global _s3_public_client
    if _s3_public_client is None:
        _s3_public_client = _make_s3(S3_PUBLIC_ENDPOINT_URL)
    return _s3_public_client


def build_attachment_items(photos) -> list:
    """Contract attachment dicts for photo rows; presigned url when S3 is configured.

    contentType отдаётся для фронтенда: его маппер вложений различает фото/документ
    по MIME-типу (image/*), а не по полю type."""
    s3 = get_s3_public() if is_configured() else None
    return [
        {
            "id": str(p["photo_id"]),
            "applicationId": str(p["application_id"]),
            "name": p["name"],
            "type": "photo",
            "contentType": p.get("content_type"),
            "url": s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": S3_BUCKET, "Key": p["s3_key"]},
                ExpiresIn=3600,
            ) if s3 is not None else None,
        }
        for p in photos
    ]
