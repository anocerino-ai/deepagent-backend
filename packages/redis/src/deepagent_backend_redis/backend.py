"""Redis backend for Deep Agents."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis
from deepagents.backends.protocol import (
    BackendProtocol,
    EditResult,
    FileDownloadResponse,
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
    build_direct_listing,
    build_glob_result,
    compile_path_filter,
    edit_text_payload,
    is_under_root,
    make_text_file_data,
    normalize_virtual_path,
    payload_lines,
    read_text_result,
    run_async_safely,
    search_root,
    utcnow_iso,
)

__all__ = ["RedisBackend", "RedisConfig"]


@dataclass
class RedisConfig:
    """Configuration for Redis/Valkey storage."""

    url: str = "redis://localhost:6379/0"
    prefix: str = ""
    namespace: str = "deepagents"


class RedisBackend(BackendProtocol):
    """Redis/Valkey backend for Deep Agents file operations."""

    def __init__(self, config: RedisConfig) -> None:
        self._config = config
        self._prefix = config.prefix.strip("/")
        if self._prefix:
            self._prefix += "/"
        self._client = None
        self._client_loop: asyncio.AbstractEventLoop | None = None
        self._namespace = config.namespace
        self._index_key = f"{self._namespace}:__index__"

    def _storage_path(self, path: str) -> str:
        return f"{self._prefix}{path.lstrip('/')}"

    def _virtual_path(self, storage_path: str) -> str:
        if self._prefix and storage_path.startswith(self._prefix):
            storage_path = storage_path[len(self._prefix) :]
        return normalize_virtual_path(storage_path)

    def _data_key(self, path: str) -> str:
        return f"{self._namespace}:file:{self._storage_path(path)}"

    async def close(self) -> None:
        """Close the Redis client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._client_loop = None

    async def _ensure_client(self):
        """Lazily initialize the Redis client inside an event loop."""
        loop = asyncio.get_running_loop()
        if self._client is not None and self._client_loop is not loop:
            with contextlib.suppress(RuntimeError):
                await self._client.aclose()
            self._client = None
            self._client_loop = None

        if self._client is None:
            self._client = redis.from_url(self._config.url, decode_responses=False)
            self._client_loop = loop
        return self._client

    async def _get_file_data(self, path: str) -> dict[str, Any] | None:
        client = await self._ensure_client()
        payload = await client.get(self._data_key(path))
        if payload is None:
            return None
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        return json.loads(payload)

    async def _put_file_data(
        self,
        path: str,
        data: dict[str, Any],
        *,
        update_modified: bool = True,
    ) -> None:
        if update_modified:
            data["modified_at"] = utcnow_iso()
        storage_path = self._storage_path(path)
        client = await self._ensure_client()
        await client.set(
            f"{self._namespace}:file:{storage_path}",
            json.dumps(data).encode("utf-8"),
        )
        await client.sadd(self._index_key, storage_path)

    async def _exists(self, path: str) -> bool:
        client = await self._ensure_client()
        return bool(await client.exists(self._data_key(path)))

    async def _list_paths(self, path: str = "/") -> list[tuple[str, int | None, str | None]]:
        storage_prefix = self._storage_path(path)
        client = await self._ensure_client()
        members = await client.smembers(self._index_key)
        storage_paths = sorted(
            (member.decode("utf-8") if isinstance(member, bytes) else str(member) for member in members),
            key=str,
        )
        matching = [
            storage_path for storage_path in storage_paths if storage_path.startswith(storage_prefix)
        ]
        payloads = await client.mget(
            [f"{self._namespace}:file:{storage_path}" for storage_path in matching]
        )

        results: list[tuple[str, int | None, str | None]] = []
        for storage_path, payload in zip(matching, payloads, strict=False):
            if payload is None:
                continue
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8")
            data = json.loads(payload)
            results.append(
                (
                    self._virtual_path(storage_path),
                    len(data.get("content", [])),
                    data.get("modified_at"),
                )
            )
        return results

    def ls(self, path: str) -> LsResult:
        return run_async_safely(self.als(path))

    async def als(self, path: str) -> LsResult:
        return LsResult(entries=build_direct_listing(path, await self._list_paths(path)))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return run_async_safely(self.aread(file_path, offset, limit))

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return read_text_result(file_path, await self._get_file_data(file_path), offset, limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        return run_async_safely(self.awrite(file_path, content))

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        try:
            existing = await self._get_file_data(file_path)
            created_at = existing.get("created_at") if existing else None
            await self._put_file_data(
                file_path, make_text_file_data(content, created_at), update_modified=False
            )
            return WriteResult(path=file_path)
        except Exception as exc:
            return WriteResult(error=f"Error writing file '{file_path}': {exc}")

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return run_async_safely(self.aedit(file_path, old_string, new_string, replace_all))

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
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
        except Exception as exc:
            return EditResult(error=f"Error editing file '{file_path}': {exc}")

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return run_async_safely(self.agrep(pattern, path, glob, max_count=max_count))

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        root = search_root(path)
        try:
            accepts = compile_path_filter(glob, root)
        except InvalidGlobPatternError as exc:
            return GrepResult(error=str(exc))

        collector = GrepCollector(pattern, max_count)
        for virtual_path, _size, _modified in await self._list_paths(root):
            if collector.done:
                break
            if not is_under_root(virtual_path, root) or not accepts(virtual_path):
                continue
            data = await self._get_file_data(virtual_path)
            if data is None:
                continue
            collector.scan(virtual_path, payload_lines(data))
        return collector.result()

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return run_async_safely(self.aglob(pattern, path))

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return build_glob_result(await self._list_paths(search_root(path)), pattern, path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return run_async_safely(self.aupload_files(files))

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                await self._put_file_data(
                    path,
                    make_text_file_data(content.decode("utf-8", errors="replace")),
                    update_modified=False,
                )
                responses.append(FileUploadResponse(path=path, error=None))
            except Exception:
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return run_async_safely(self.adownload_files(paths))

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                data = await self._get_file_data(path)
                if data is None:
                    responses.append(
                        FileDownloadResponse(path=path, content=None, error="file_not_found")
                    )
                    continue
                content = "\n".join(data.get("content", [])).encode("utf-8")
                responses.append(FileDownloadResponse(path=path, content=content, error=None))
            except Exception:
                responses.append(FileDownloadResponse(path=path, content=None, error="invalid_path"))
        return responses
