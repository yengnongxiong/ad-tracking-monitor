"""Object storage for screenshots and capture JSON (MinIO locally, any S3 bucket in prod).

boto3 is synchronous, so calls run in a thread to keep the worker's event loop responsive.
"""

import asyncio
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from tagmonitor.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class ObjectStorage:
    def __init__(self, settings: Settings, bucket: str | None = None) -> None:
        self.bucket = bucket or settings.s3_bucket
        self._client = self._make_client(settings, settings.s3_endpoint_url)
        # Presigned URLs embed the host they were signed for, so links handed to browsers must
        # be signed for the address the browser can reach (e.g. localhost:9000, not minio:9000).
        # Signing is local math, no network call.
        self._public_client = self._make_client(
            settings, settings.s3_public_endpoint_url or settings.s3_endpoint_url
        )

    @staticmethod
    def _make_client(settings: Settings, endpoint_url: str | None) -> "S3Client":
        return boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    async def ensure_bucket(self) -> None:
        def create_if_missing() -> None:
            try:
                self._client.head_bucket(Bucket=self.bucket)
            except ClientError:
                self._client.create_bucket(Bucket=self.bucket)

        await asyncio.to_thread(create_if_missing)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        def read() -> bytes:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

        return await asyncio.to_thread(read)

    async def delete(self, keys: list[str]) -> None:
        if not keys:
            return
        await asyncio.to_thread(
            self._client.delete_objects,
            Bucket=self.bucket,
            Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True},
        )

    def presigned_url(self, key: str, expires_s: int = 600) -> str:
        return self._public_client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_s
        )
