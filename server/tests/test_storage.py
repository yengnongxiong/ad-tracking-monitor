"""Object storage against the real MinIO from docker compose."""

from uuid import uuid4

import httpx

from tagmonitor.config import Settings, get_settings
from tagmonitor.storage import ObjectStorage


async def test_put_get_delete_round_trip(storage: ObjectStorage) -> None:
    key = f"test/{uuid4().hex}.json"
    await storage.put(key, b'{"ok": true}', "application/json")
    assert await storage.get(key) == b'{"ok": true}'
    await storage.delete([key])


async def test_presigned_urls_work_and_expire_quickly(storage: ObjectStorage) -> None:
    key = f"test/{uuid4().hex}.jpg"
    await storage.put(key, b"\xff\xd8fake-jpeg", "image/jpeg")
    # Sign for the address this test process can reach (the "public" one is the browser's).
    internal = get_settings().model_copy(update={"s3_public_endpoint_url": None})
    url = ObjectStorage(internal, bucket=storage.bucket).presigned_url(key, expires_s=600)
    assert "X-Amz-Expires=600" in url
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


def test_presigned_urls_use_the_public_endpoint() -> None:
    settings = Settings(
        s3_endpoint_url="http://minio:9000", s3_public_endpoint_url="http://localhost:9000"
    )
    url = ObjectStorage(settings).presigned_url("sites/x.jpg")
    assert url.startswith("http://localhost:9000/tagmonitor/sites/x.jpg?")
