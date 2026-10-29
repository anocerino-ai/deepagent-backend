"""AzureBlob backend for Deep Agents."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob.aio import BlobServiceClient
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

__all__ = ["AzureBlobBackend", "AzureBlobConfig"]


@dataclass
class AzureBlobConfig:
    """Configuration for Azure Blob Storage."""

    container: str
    prefix: str = ""
    connection_string: str | None = None
    account_url: str | None = None
    credential: Any = None


class AzureBlobBackend(BackendProtocol):
    """Azure Blob Storage backend for Deep Agents file operations."""

    def __init__(self, config: AzureBlobConfig) -> None:
        self._config = config
        self._prefix = config.prefix.strip("/")
        if self._prefix:
            self._prefix += "/"
        self._service: BlobServiceClient | None = None
        self._service_loop: asyncio.AbstractEventLoop | None = None

    def _blob_name(self, path: str) -> str:
        return f"{self._prefix}{path.lstrip('/')}"

    def _virtual_path(self, blob_name: str) -> str:
        if self._prefix and blob_name.startswith(self._prefix):
            blob_name = blob_name[len(self._prefix) :]
        return normalize_virtual_path(blob_name)

    async def close(self) -> None:
        """Close the underlying Azure clients."""
        if self._service is not None:
            await self._service.close()
            self._service = None

    async def _ensure_container_client(self):
        """Lazily initialize the Azure Blob clients inside an event loop."""
        loop = asyncio.get_running_loop()
        if self._service is not None and self._service_loop is not loop:
            await self._service.close()
            self._service = None
            self._service_loop = None

        if self._service is None:
            if self._config.connection_string:
                self._service = BlobServiceClient.from_connection_string(self._config.connection_string)
            elif self._config.account_url:
                self._service = BlobServiceClient(
                    account_url=self._config.account_url,
                    credential=self._config.credential,
                )
            else:
                raise ValueError("AzureBlobConfig requires either connection_string or account_url")
            self._service_loop = loop
        return self._service.get_container_client(self._config.container)

    async def ensure_container(self) -> None:
        """Create the configured container when it does not already exist."""
        try:
            container = await self._ensure_container_client()
            await container.create_container()
        except ResourceExistsError:
            pass

    async def _get_file_data(self, path: str) -> dict[str, Any] | None:
        container = await self._ensure_container_client()
        blob = container.get_blob_client(self._blob_name(path))
        try:
            downloader = await blob.download_blob()
            payload = await downloader.readall()
        except ResourceNotFoundError:
            return None
        return json.loads(payload.decode("utf-8"))

    async def _put_file_data(
        self,
        path: str,
        data: dict[str, Any],
        *,
        update_modified: bool = True,
    ) -> None:
        if update_modified:
            data["modified_at"] = utcnow_iso()
        container = await self._ensure_container_client()
        blob = container.get_blob_client(self._blob_name(path))
        await blob.upload_blob(
            json.dumps(data).encode("utf-8"),
            overwrite=True,
            content_type="application/json",
        )

    async def _exists(self, path: str) -> bool:
        container = await self._ensure_container_client()
        blob = container.get_blob_client(self._blob_name(path))
        return bool(await blob.exists())

    async def _list_blobs(self, prefix: str = "") -> list[tuple[str, int | None, str | None]]:
        blob_prefix = self._blob_name(prefix)
        results: list[tuple[str, int | None, str | None]] = []
        container = await self._ensure_container_client()
        async for blob in container.list_blobs(name_starts_with=blob_prefix):
            modified = blob.last_modified.isoformat() if blob.last_modified else None
            results.append((self._virtual_path(blob.name), getattr(blob, "size", 0), modified))
        return results

    def ls(self, path: str) -> LsResult:
        return run_async_safely(self.als(path))

    async def als(self, path: str) -> LsResult:
        return LsResult(entries=build_direct_listing(path, await self._list_blobs(path)))

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
        for virtual_path, _size, _modified in await self._list_blobs(root):
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
        return build_glob_result(await self._list_blobs(search_root(path)), pattern, path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return run_async_safely(self.aupload_files(files))

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for path, content in files:
            container = await self._ensure_container_client()
            blob = container.get_blob_client(self._blob_name(path))
            try:
                await blob.upload_blob(
                    content,
                    overwrite=True,
                    content_type="application/octet-stream",
                )
                responses.append(FileUploadResponse(path=path, error=None))
            except ResourceNotFoundError:
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
            except Exception:
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return run_async_safely(self.adownload_files(paths))

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            container = await self._ensure_container_client()
            blob = container.get_blob_client(self._blob_name(path))
            try:
                downloader = await blob.download_blob()
                content = await downloader.readall()
                responses.append(FileDownloadResponse(path=path, content=content, error=None))
            except ResourceNotFoundError:
                responses.append(FileDownloadResponse(path=path, content=None, error="file_not_found"))
            except Exception:
                responses.append(FileDownloadResponse(path=path, content=None, error="invalid_path"))
        return responses
