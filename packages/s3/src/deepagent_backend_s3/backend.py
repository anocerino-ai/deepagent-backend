"""S3 backend for Deep Agents."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aioboto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)

from deepagent_backend.common import (
    GrepCollector,
    InvalidGlobPatternError,
    build_glob_result,
    compile_path_filter,
    edit_text_payload,
    file_info,
    is_under_root,
    make_text_file_data,
    payload_lines,
    read_text_result,
    run_async_safely,
    search_root,
)

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client

__all__ = ["S3Backend", "S3Config"]


@dataclass
class S3Config:
    """Configuration for S3-compatible storage."""

    bucket: str
    prefix: str = ""
    region: str = "us-east-1"
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    use_ssl: bool = True
    max_pool_connections: int = 50
    connect_timeout: float = 5.0
    read_timeout: float = 30.0
    max_retries: int = 3


class S3Backend(BackendProtocol):
    """
    S3-compatible backend for Deep Agents file operations.

    Supports AWS S3, MinIO, and any S3-compatible object storage.
    All operations are async-native using aioboto3.

    Files are stored as objects with paths mapping to S3 keys.
    Content is stored as JSON with the structure:
    {"content": [...lines], "created_at": "...", "modified_at": "..."}
    """

    def __init__(self, config: S3Config) -> None:
        self._config = config
        self._prefix = config.prefix.strip("/")
        if self._prefix:
            self._prefix += "/"

        self._boto_config = BotoConfig(
            region_name=config.region,
            signature_version="s3v4",
            retries={"max_attempts": config.max_retries, "mode": "adaptive"},
            max_pool_connections=config.max_pool_connections,
            connect_timeout=config.connect_timeout,
            read_timeout=config.read_timeout,
        )

        session_kwargs: dict[str, Any] = {}
        if config.access_key_id:
            session_kwargs["aws_access_key_id"] = config.access_key_id
        if config.secret_access_key:
            session_kwargs["aws_secret_access_key"] = config.secret_access_key

        self._session = aioboto3.Session(**session_kwargs)
        self._bucket = config.bucket

    def _s3_key(self, path: str) -> str:
        """Convert virtual path to S3 key."""
        clean = path.lstrip("/")
        return f"{self._prefix}{clean}"

    def _virtual_path(self, key: str) -> str:
        """Convert S3 key to virtual path."""
        if self._prefix and key.startswith(self._prefix):
            key = key[len(self._prefix) :]
        return "/" + key.lstrip("/")

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[S3Client, None]:
        """Get S3 client context."""
        async with self._session.client(
            "s3",
            config=self._boto_config,
            endpoint_url=self._config.endpoint_url,
            use_ssl=self._config.use_ssl,
        ) as client:
            yield client

    async def _get_file_data(self, path: str) -> dict[str, Any] | None:
        """Get file data dict from S3."""
        key = self._s3_key(path)
        try:
            async with self._client() as client:
                response = await client.get_object(Bucket=self._bucket, Key=key)
                async with response["Body"] as stream:
                    content = await stream.read()
                return json.loads(content.decode("utf-8"))
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise

    async def _put_file_data(
        self, path: str, data: dict[str, Any], *, update_modified: bool = True
    ) -> None:
        """Put file data dict to S3."""
        key = self._s3_key(path)
        if update_modified:
            data["modified_at"] = datetime.now(UTC).isoformat()
        content = json.dumps(data).encode("utf-8")
        async with self._client() as client:
            await client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentType="application/json",
            )

    async def _exists(self, path: str) -> bool:
        """Check if file exists in S3."""
        key = self._s3_key(path)
        try:
            async with self._client() as client:
                await client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    async def _list_keys(self, prefix: str = "") -> list[dict[str, Any]]:
        """List all keys with a prefix."""
        full_prefix = self._s3_key(prefix)
        results: list[dict[str, Any]] = []
        async with self._client() as client:
            paginator = client.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
                for obj in page.get("Contents", []):
                    results.append(obj)
        return results

    # -------------------------------------------------------------------------
    # BackendProtocol Implementation
    # -------------------------------------------------------------------------

    def ls(self, path: str) -> LsResult:
        """Sync wrapper for als."""
        return run_async_safely(self.als(path))

    async def als(self, path: str) -> LsResult:
        """List direct children of a directory.

        Uses the S3 ``Delimiter='/'`` parameter so that:

        * ``Contents`` returns only objects *directly* under the prefix
          (no recursive descent into sub-prefixes).
        * ``CommonPrefixes`` returns the virtual sub-directory entries.

        This avoids scanning the full subtree just to derive immediate
        children.
        """
        prefix = path.lstrip("/")
        if prefix and not prefix.endswith("/"):
            prefix += "/"

        full_prefix = self._s3_key(prefix)
        results: list[FileInfo] = []

        async with self._client() as client:
            paginator = client.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix, Delimiter="/"):
                for obj in page.get("Contents", []):
                    results.append(
                        file_info(
                            self._virtual_path(obj["Key"]),
                            obj.get("Size", 0),
                            obj["LastModified"].isoformat() if "LastModified" in obj else None,
                        )
                    )
                for cp in page.get("CommonPrefixes", []):
                    results.append(FileInfo(path=self._virtual_path(cp["Prefix"]), is_dir=True))

        results.sort(key=lambda entry: entry["path"])
        return LsResult(entries=results)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """Sync wrapper for aread."""
        return run_async_safely(self.aread(file_path, offset, limit))

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """Read the requested line window (raw content; line numbers are added by the middleware)."""
        return read_text_result(file_path, await self._get_file_data(file_path), offset, limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        """Sync wrapper for awrite."""
        return run_async_safely(self.awrite(file_path, content))

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """Create a file, or overwrite it while keeping its creation time."""
        try:
            existing = await self._get_file_data(file_path)
            created_at = existing.get("created_at") if existing else None
            await self._put_file_data(
                file_path, make_text_file_data(content, created_at), update_modified=False
            )
            return WriteResult(path=file_path)
        except Exception as e:
            return WriteResult(error=f"Error writing file '{file_path}': {e}")

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Sync wrapper for aedit."""
        return run_async_safely(self.aedit(file_path, old_string, new_string, replace_all))

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Edit file by replacing strings."""
        updated, occurrences, error = edit_text_payload(
            await self._get_file_data(file_path),
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )
        if error == "file_not_found":
            return EditResult(error=f"Error: File '{file_path}' not found")
        if error:
            return EditResult(error=error)

        try:
            await self._put_file_data(file_path, updated or {})
            return EditResult(path=file_path, occurrences=occurrences or 0)
        except Exception as e:
            return EditResult(error=f"Error editing file '{file_path}': {e}")

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """Sync wrapper for agrep."""
        return run_async_safely(self.agrep(pattern, path, glob, max_count=max_count))

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """Search for a literal string in files.

        Applies filename (glob) filtering immediately after listing — before
        any object GET — and reuses a single S3 client session for both the
        listing and all subsequent content fetches, eliminating N separate
        session setups.
        """
        root = search_root(path)
        try:
            accepts = compile_path_filter(glob, root)
        except InvalidGlobPatternError as exc:
            return GrepResult(error=str(exc))

        collector = GrepCollector(pattern, max_count)

        async with self._client() as client:
            # ── Step 1: list candidate objects ───────────────────────────
            paginator = client.get_paginator("list_objects_v2")
            candidates: list[tuple[str, str]] = []  # (s3_key, virtual_path)
            async for page in paginator.paginate(
                Bucket=self._bucket, Prefix=self._s3_key(root.lstrip("/"))
            ):
                for obj in page.get("Contents", []):
                    vpath = self._virtual_path(obj["Key"])
                    if is_under_root(vpath, root) and accepts(vpath):
                        candidates.append((obj["Key"], vpath))

            # ── Step 2: fetch content — shared session, no per-file setup ─
            for key, vpath in candidates:
                if collector.done:
                    break
                try:
                    response = await client.get_object(Bucket=self._bucket, Key=key)
                    async with response["Body"] as stream:
                        raw = await stream.read()
                    data = json.loads(raw.decode("utf-8"))
                except ClientError as e:
                    if e.response["Error"]["Code"] == "NoSuchKey":
                        continue
                    raise

                collector.scan(vpath, payload_lines(data))

        return collector.result()

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Sync wrapper for aglob."""
        return run_async_safely(self.aglob(pattern, path))

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Find files matching a glob pattern.

        A pattern that contains ``/`` is matched against the path relative to *path*, so its
        longest literal prefix (the part before the first wildcard) can narrow the S3 key
        scan, e.g. ``"models/v2*.json"`` or ``"src/**"``. Patterns without ``/`` match the
        basename at any depth and need the full listing under *path*.
        """
        root = search_root(path)
        base_prefix = root.lstrip("/")
        if base_prefix:
            base_prefix += "/"

        literal = ""
        if "/" in pattern:
            for c in pattern.lstrip("/"):
                if c in "*?[{\\":
                    break
                literal += c

        objects = await self._list_keys(base_prefix + literal)
        candidates = [
            (
                self._virtual_path(obj["Key"]),
                obj.get("Size", 0),
                obj["LastModified"].isoformat() if "LastModified" in obj else None,
            )
            for obj in objects
        ]
        return build_glob_result(candidates, pattern, path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Sync wrapper for aupload_files."""
        return run_async_safely(self.aupload_files(files))

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload multiple files."""
        responses: list[FileUploadResponse] = []
        async with self._client() as client:
            for path, content in files:
                try:
                    key = self._s3_key(path)
                    await client.put_object(Bucket=self._bucket, Key=key, Body=content)
                    responses.append(FileUploadResponse(path=path, error=None))
                except ClientError as e:
                    code = e.response["Error"]["Code"]
                    if code == "AccessDenied":
                        responses.append(FileUploadResponse(path=path, error="permission_denied"))
                    else:
                        responses.append(FileUploadResponse(path=path, error="invalid_path"))
                except Exception:
                    responses.append(FileUploadResponse(path=path, error="invalid_path"))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Sync wrapper for adownload_files."""
        return run_async_safely(self.adownload_files(paths))

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download multiple files."""
        responses: list[FileDownloadResponse] = []
        async with self._client() as client:
            for path in paths:
                try:
                    key = self._s3_key(path)
                    response = await client.get_object(Bucket=self._bucket, Key=key)
                    async with response["Body"] as stream:
                        content = await stream.read()
                    responses.append(FileDownloadResponse(path=path, content=content, error=None))
                except ClientError as e:
                    code = e.response["Error"]["Code"]
                    if code == "NoSuchKey":
                        responses.append(
                            FileDownloadResponse(path=path, content=None, error="file_not_found")
                        )
                    elif code == "AccessDenied":
                        responses.append(
                            FileDownloadResponse(path=path, content=None, error="permission_denied")
                        )
                    else:
                        responses.append(
                            FileDownloadResponse(path=path, content=None, error="invalid_path")
                        )
        return responses
