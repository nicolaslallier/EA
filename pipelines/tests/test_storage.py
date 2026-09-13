"""`storage.py`: the S3 reads a flow makes, against a fake client — never a socket."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from pipelines.settings import Settings
from pipelines.storage import list_keys, read_text, s3_client

REQUIRED_SECRETS = {
    "litellm_api_key": "litellm-key",
    "s3_access_key": "s3-access",
    "s3_secret_key": "s3-secret",
    "ea_client_secret": "ea-secret",
}


@dataclass
class FakeObject:
    object_name: str


@dataclass
class FakeResponse:
    body: bytes
    closed: bool = False
    released: bool = False

    def read(self) -> bytes:
        return self.body

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


@dataclass
class FakeS3:
    objects: dict[str, bytes]
    listed: list[tuple[str, str, bool]] = field(default_factory=list)
    responses: list[FakeResponse] = field(default_factory=list)

    def list_objects(self, bucket: str, prefix: str, recursive: bool) -> list[FakeObject]:
        self.listed.append((bucket, prefix, recursive))
        return [FakeObject(key) for key in self.objects if key.startswith(prefix)]

    def get_object(self, bucket: str, key: str) -> FakeResponse:
        response = FakeResponse(self.objects[key])
        self.responses.append(response)
        return response


def test_list_keys_is_recursive_and_sorted() -> None:
    fake = FakeS3({"inbox/b.md": b"", "inbox/sub/a.md": b"", "inbox/a.md": b"", "other/c.md": b""})

    keys = list_keys(fake, "bucket", "inbox/")  # type: ignore[arg-type]

    assert keys == ["inbox/a.md", "inbox/b.md", "inbox/sub/a.md"]
    assert fake.listed == [("bucket", "inbox/", True)]


def test_read_text_decodes_utf8_and_releases_the_response() -> None:
    fake = FakeS3({"inbox/a.md": "Facturation été".encode()})

    assert read_text(fake, "bucket", "inbox/a.md") == "Facturation été"  # type: ignore[arg-type]
    assert fake.responses[0].closed and fake.responses[0].released


def test_read_text_refuses_non_utf8_and_still_releases_the_response() -> None:
    fake = FakeS3({"inbox/latin1.md": "été".encode("latin-1")})

    with pytest.raises(UnicodeDecodeError):
        read_text(fake, "bucket", "inbox/latin1.md")  # type: ignore[arg-type]
    assert fake.responses[0].closed and fake.responses[0].released


def test_s3_client_uses_the_settings_endpoint_and_default_trust() -> None:
    client = s3_client(Settings(s3_endpoint="minio.test:9000", **REQUIRED_SECRETS))  # type: ignore[arg-type]

    assert client._base_url.host == "minio.test:9000"
    assert client._http.connection_pool_kw.get("ca_certs") != "/certs/ca.pem"


def test_s3_client_trusts_the_configured_ca() -> None:
    settings = Settings(s3_ca_cert="/certs/ca.pem", **REQUIRED_SECRETS)  # type: ignore[arg-type]

    client = s3_client(settings)

    assert client._http.connection_pool_kw["ca_certs"] == "/certs/ca.pem"
