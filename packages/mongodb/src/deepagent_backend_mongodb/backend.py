"""MongoDB backend for Deep Agents."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

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
from motor.motor_asyncio import AsyncIOMotorClient

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
)

__all__ = ["MongoDBBackend", "MongoDBConfig"]


@dataclass
class MongoDBConfig:
    """Configuration for MongoDB storage."""

    connection_uri: str = "mongodb://localhost:27017"
    database: str = "deepagents"
    collection: str = "files"
    prefix: str = ""
    server_selection_timeout_ms: int = 5000


class MongoDBBackend(BackendProtocol):
    """MongoDB backend for Deep Agents file operations."""

    def __init__(self, config: MongoDBConfig) -> None:
        self._config = config
        self._prefix = config.prefix.strip("/")
        if self._prefix:
            self._prefix += "/"
        self._client: AsyncIOMotorClient | None = None
        self._collection = None
        self._client_loop: asyncio.AbstractEventLoop | None = None
        self._initialized = False

    def _storage_path(self, path: str) -> str:
        return f"{self._prefix}{path.lstrip('/')}"

    def _virtual_path(self, path: str) -> str:
        if self._prefix and path.startswith(self._prefix):
            path = path[len(self._prefix) :]
        return normalize_virtual_path(path)

    async def initialize(self) -> None:
        """Create indexes used by the backend."""
        collection = await self._ensure_collection()
        if self._initialized:
            return
        await collection.create_index("path", unique=True)
        await collection.create_index("modified_at")
        self._initialized = True

    async def close(self) -> None:
        """Close the MongoDB client."""
        if self._client is not None:
            self._client.close()
            self._client = None
            self._collection = None
            self._client_loop = None
            self._initialized = False

    async def _ensure_collection(self):
        """Lazily initialize the MongoDB client inside an event loop."""
        loop = asyncio.get_running_loop()
        if self._client is not None and self._client_loop is not loop:
            self._client.close()
            self._client = None
            self._collection = None
            self._client_loop = None
            self._initialized = False

        if self._client is None:
            self._client = AsyncIOMotorClient(
                self._config.connection_uri,
                serverSelectionTimeoutMS=self._config.server_selection_timeout_ms,
            )
            self._collection = self._client[self._config.database][self._config.collection]
            self._client_loop = loop

        return self._collection

    async def _get_file_data(self, path: str) -> dict[str, Any] | None:
        collection = await self._ensure_collection()
        document = await collection.find_one({"path": self._storage_path(path)})
        if document is None:
            return None
        return {
            "content": document.get("content", []),
            "created_at": (
                document.get("created_at").isoformat() if document.get("created_at") else None
            ),
            "modified_at": (
                document.get("modified_at").isoformat() if document.get("modified_at") else None
            ),
        }

    async def _put_file_data(
        self,
        path: str,
        data: dict[str, Any],
        *,
        update_modified: bool = True,
    ) -> None:
        storage_path = self._storage_path(path)
        now = datetime.now(UTC)
        collection = await self._ensure_collection()
        existing = await collection.find_one({"path": storage_path}, {"created_at": 1})
        created_at = existing.get("created_at") if existing else now
        document = {
            "path": storage_path,
            "content": data.get("content", []),
            "created_at": created_at,
            "modified_at": now if update_modified else created_at,
        }
        await collection.replace_one({"path": storage_path}, document, upsert=True)

    async def _exists(self, path: str) -> bool:
        collection = await self._ensure_collection()
        return bool(await collection.find_one({"path": self._storage_path(path)}, {"_id": 1}))

    async def _list_documents(self, path: str = "/") -> list[tuple[str, int | None, str | None]]:
        storage_prefix = self._storage_path(path)
        regex = f"^{re.escape(storage_prefix)}"
        results: list[tuple[str, int | None, str | None]] = []
        collection = await self._ensure_collection()
        cursor = collection.find({"path": {"$regex": regex}})
        async for document in cursor:
            results.append(
                (
                    self._virtual_path(document["path"]),
                    len(document.get("content", [])),
                    document.get("modified_at").isoformat() if document.get("modified_at") else None,
                )
            )
        return results

    def ls(self, path: str) -> LsResult:
        return run_async_safely(self.als(path))

    async def als(self, path: str) -> LsResult:
        return LsResult(entries=build_direct_listing(path, await self._list_documents(path)))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return run_async_safely(self.aread(file_path, offset, limit))

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return read_text_result(file_path, await self._get_file_data(file_path), offset, limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        return run_async_safely(self.awrite(file_path, content))

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        try:
            # replace_one keeps the original created_at of an existing document
            await self._put_file_data(file_path, make_text_file_data(content))
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
        collection = await self._ensure_collection()
        cursor = collection.find({"path": {"$regex": f"^{re.escape(self._storage_path(root))}"}})
        async for document in cursor:
            if collector.done:
                break
            virtual_path = self._virtual_path(document["path"])
            if not is_under_root(virtual_path, root) or not accepts(virtual_path):
                continue
            collector.scan(virtual_path, payload_lines(document))
        return collector.result()

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return run_async_safely(self.aglob(pattern, path))

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return build_glob_result(await self._list_documents(search_root(path)), pattern, path)

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
