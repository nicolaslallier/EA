"""S3 access for the flows: list the source files, read one as text.

MinIO of the `~/OpenCode/Infra` stack, read-only from this project. Nothing
here knows what a source file means — that is `catalogue.py`'s business.
"""

from __future__ import annotations

import urllib3
from minio import Minio

from pipelines.settings import Settings


def s3_client(settings: Settings) -> Minio:
    """A MinIO client, trusting `s3_ca_cert` when the endpoint's CA is a private one."""
    http_client = None
    if settings.s3_ca_cert:
        # Minio's own pool, with the CA swapped: a bare `PoolManager` would
        # also drop its timeout and its retries on a 5xx.
        http_client = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=300, read=300),
            maxsize=10,
            cert_reqs="CERT_REQUIRED",
            ca_certs=settings.s3_ca_cert,
            retries=urllib3.Retry(
                total=5, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504]
            ),
        )
    return Minio(
        settings.s3_endpoint,
        access_key=settings.s3_access_key.get_secret_value(),
        secret_key=settings.s3_secret_key.get_secret_value(),
        secure=settings.s3_secure,
        http_client=http_client,
    )


def list_keys(client: Minio, bucket: str, prefix: str) -> list[str]:
    """Every object key under `prefix`, at any depth, sorted — so a run's order is stable."""
    objects = client.list_objects(bucket, prefix=prefix, recursive=True)
    return sorted(obj.object_name for obj in objects if obj.object_name is not None)


def read_text(client: Minio, bucket: str, key: str) -> str:
    """The object's body as strict UTF-8 — `UnicodeDecodeError` rather than a guess."""
    response = client.get_object(bucket, key)
    try:
        return response.read().decode("utf-8")
    finally:
        response.close()
        response.release_conn()
