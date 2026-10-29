"""Postgres backend for Deep Agents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import psycopg_pool
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
from psycopg import sql

from deepagent_backend.common import (
    GrepCollector,
    InvalidGlobPatternError,
    build_glob_result,
    compile_path_filter,
    edit_text_payload,
    file_info,
    is_under_root,
    make_text_file_data,
    read_text_result,
    run_async_safely,
    search_root,
)

__all__ = ["PostgresBackend", "PostgresConfig"]


@dataclass
class PostgresConfig:
    """Configuration for PostgreSQL backend."""

    host: str = "localhost"
    port: int = 5432
    database: str = "deepagents"
    user: str = "postgres"
    password: str = ""
    table: str = "files"
    schema: str = "public"
    min_pool_size: int = 5
    max_pool_size: int = 20
    max_idle_seconds: float = 300.0
    connection_timeout: float = 30.0
    sslmode: str = "prefer"

    @property
    def conninfo(self) -> str:
        """Build connection string."""
        return (
            f"host={self.host} port={self.port} dbname={self.database} "
            f"user={self.user} password={self.password} sslmode={self.sslmode}"
        )


class PostgresBackend(BackendProtocol):
    """
    PostgreSQL backend for Deep Agents file operations.

    Uses psycopg3 with connection pooling for high-performance async operations.
    Files are stored in a table with path as primary key and content as JSONB.

    Table schema:
        path TEXT PRIMARY KEY,
        content JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        modified_at TIMESTAMPTZ NOT NULL
    """

    def __init__(self, config: PostgresConfig) -> None:
        self._config = config
        self._table = sql.Identifier(config.schema, config.table)
        self._pool: psycopg_pool.AsyncConnectionPool | None = None
        self._initialized = False

    def _storage_path(self, path: str) -> str:
        """Convert virtual path to storage path (strip leading /)."""
        return path.lstrip("/")

    def _virtual_path(self, path: str) -> str:
        """Convert storage path to virtual path (add leading /)."""
        return "/" + path.lstrip("/")

    async def _ensure_pool(self) -> psycopg_pool.AsyncConnectionPool:
        """Lazily initialize the connection pool."""
        if self._pool is None:
            self._pool = psycopg_pool.AsyncConnectionPool(
                conninfo=self._config.conninfo,
                min_size=self._config.min_pool_size,
                max_size=self._config.max_pool_size,
                max_idle=self._config.max_idle_seconds,
                timeout=self._config.connection_timeout,
                open=False,
            )
            await self._pool.open()
        return self._pool

    async def initialize(self) -> None:
        """Create table if not exists. Call once on startup."""
        if self._initialized:
            return

        pool = await self._ensure_pool()
        async with pool.connection() as conn:
            await conn.execute(
                sql.SQL("""
                CREATE TABLE IF NOT EXISTS {table} (
                    path TEXT PRIMARY KEY,
                    content JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    modified_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """).format(table=self._table)
            )
            await conn.execute(
                sql.SQL("""
                CREATE INDEX IF NOT EXISTS {path_index}
                ON {table} (path text_pattern_ops)
            """).format(
                    table=self._table, path_index=sql.Identifier(f"idx_{self._config.table}_path_prefix")
                )
            )
            await conn.execute(
                sql.SQL("""
                CREATE INDEX IF NOT EXISTS {modified_index}
                ON {table} (modified_at DESC)
            """).format(
                    table=self._table,
                    modified_index=sql.Identifier(f"idx_{self._config.table}_modified"),
                )
            )
            await conn.commit()
        self._initialized = True

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def _get_file_data(self, path: str) -> dict[str, Any] | None:
        """Get file data from database."""
        storage_path = self._storage_path(path)
        pool = await self._ensure_pool()
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                sql.SQL("SELECT content, created_at, modified_at FROM {table} WHERE path = %s").format(
                    table=self._table
                ),
                (storage_path,),
            )
            row = await cur.fetchone()
            if row:
                data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                data["created_at"] = row[1].isoformat() if row[1] else None
                data["modified_at"] = row[2].isoformat() if row[2] else None
                return data
            return None

    async def _put_file_data(self, path: str, data: dict[str, Any]) -> None:
        """Upsert file data to database."""
        storage_path = self._storage_path(path)
        pool = await self._ensure_pool()
        content_json = json.dumps({"content": data.get("content", [])})
        async with pool.connection() as conn:
            await conn.execute(
                sql.SQL("""
                INSERT INTO {table} (path, content, created_at, modified_at)
                VALUES (%s, %s::jsonb, NOW(), NOW())
                ON CONFLICT (path) DO UPDATE SET
                    content = EXCLUDED.content,
                    modified_at = NOW()
                """).format(table=self._table),
                (storage_path, content_json),
            )
            await conn.commit()

    async def _exists(self, path: str) -> bool:
        """Check if file exists."""
        storage_path = self._storage_path(path)
        pool = await self._ensure_pool()
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                sql.SQL("SELECT 1 FROM {table} WHERE path = %s").format(table=self._table),
                (storage_path,),
            )
            return await cur.fetchone() is not None

    # -------------------------------------------------------------------------
    # BackendProtocol Implementation
    # -------------------------------------------------------------------------

    def ls(self, path: str) -> LsResult:
        """Sync wrapper for als."""
        return run_async_safely(self.als(path))

    async def als(self, path: str) -> LsResult:
        """List direct children of a directory.

        Uses two SQL queries instead of loading the full descendant set:

        1. A direct-file query that excludes any path containing an extra
           ``/`` after the prefix (``NOT LIKE prefix + '%/%'``).
        2. A distinct-subdirectory query that extracts the first path segment
           of nested descendants via ``SPLIT_PART`` / ``SUBSTR``.

        This avoids materialising the whole subtree just to derive immediate
        children.
        """
        prefix = path if path.endswith("/") or path == "/" else path + "/"
        storage_prefix = self._storage_path(prefix)

        # LIKE patterns.  When storage_prefix is empty (root listing) we use
        # bare wildcards so we don't accidentally prepend a literal "%".
        like_all = (storage_prefix + "%") if storage_prefix else "%"
        like_nested = (storage_prefix + "%/%") if storage_prefix else "%/%"

        # SUBSTR start position (1-based): skip past storage_prefix so that
        # SPLIT_PART returns only the first path segment *after* the prefix.
        substr_start = len(storage_prefix) + 1

        pool = await self._ensure_pool()
        async with pool.connection() as conn, conn.cursor() as cur:
            # ── Direct file children ──────────────────────────────────
            await cur.execute(
                sql.SQL("""
                    SELECT path, modified_at,
                           COALESCE(jsonb_array_length(content->'content'), 0)
                    FROM {table}
                    WHERE path LIKE %s AND path NOT LIKE %s
                    ORDER BY path
                    """).format(table=self._table),
                (like_all, like_nested),
            )
            file_rows = await cur.fetchall()

            # ── Direct subdirectory names ─────────────────────────────
            await cur.execute(
                sql.SQL("""
                    SELECT DISTINCT SPLIT_PART(SUBSTR(path, %s), '/', 1)
                    FROM {table}
                    WHERE path LIKE %s
                    ORDER BY 1
                    """).format(table=self._table),
                (substr_start, like_nested),
            )
            dir_rows = await cur.fetchall()

        results: list[FileInfo] = []
        for row in file_rows:
            results.append(
                file_info(
                    self._virtual_path(row[0]),
                    row[2],
                    row[1].isoformat() if row[1] else None,
                )
            )
        for (dir_name,) in dir_rows:
            results.append(
                FileInfo(path=self._virtual_path(storage_prefix + dir_name + "/"), is_dir=True)
            )

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
        """Create a file, or overwrite it (the upsert keeps the original ``created_at``)."""
        try:
            await self._put_file_data(file_path, make_text_file_data(content))
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
        """Search for a literal string in files using PostgreSQL.

        Fetches all candidate paths and their content in a single SQL query,
        eliminating the previous per-file ``_get_file_data`` loop.
        Glob and substring filtering are applied in Python after the batch fetch.
        """
        root = search_root(path)
        try:
            accepts = compile_path_filter(glob, root)
        except InvalidGlobPatternError as exc:
            return GrepResult(error=str(exc))

        storage_prefix = self._storage_path(root)
        like_pattern = storage_prefix + "%" if storage_prefix else "%"

        pool = await self._ensure_pool()
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                sql.SQL("""
                    SELECT path, content->'content'
                    FROM {table}
                    WHERE path LIKE %s
                    ORDER BY path
                    """).format(table=self._table),
                (like_pattern,),
            )
            rows = await cur.fetchall()

        collector = GrepCollector(pattern, max_count)
        for storage_path, content_arr in rows:
            if collector.done:
                break
            virtual_path = self._virtual_path(storage_path)
            if not is_under_root(virtual_path, root) or not accepts(virtual_path):
                continue

            if isinstance(content_arr, list):
                lines = content_arr
            elif isinstance(content_arr, str):
                lines = json.loads(content_arr)
            else:
                lines = []

            collector.scan(virtual_path, lines)

        return collector.result()

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Sync wrapper for aglob."""
        return run_async_safely(self.aglob(pattern, path))

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Find files matching a glob pattern.

        Narrows the SQL candidate set using the literal suffix of *pattern*
        (e.g. ``"*.py"`` → ``AND path LIKE '%.py'``) before applying the shared
        Deep Agents glob matcher in Python, avoiding large unrelated result sets.
        """
        root = search_root(path)
        storage_prefix = self._storage_path(root)
        like_prefix = storage_prefix + "%" if storage_prefix else "%"

        # Extract a literal suffix from the pattern for SQL pre-filtering.
        # Only handles the common case: a single leading "*" followed by a
        # wildcard-free string (e.g. "*.py", "*.txt").  Complex patterns
        # (brace expansion, embedded wildcards) fall back to prefix-only SQL.
        stripped = pattern.lstrip("*")
        like_suffix: str | None = None
        if stripped and not any(c in stripped for c in "?[{*"):
            like_suffix = f"%{stripped}"

        pool = await self._ensure_pool()
        async with pool.connection() as conn, conn.cursor() as cur:
            if like_suffix:
                await cur.execute(
                    sql.SQL("""
                        SELECT path, modified_at,
                               COALESCE(jsonb_array_length(content->'content'), 0)
                        FROM {table}
                        WHERE path LIKE %s AND path LIKE %s
                        ORDER BY path
                        """).format(table=self._table),
                    (like_prefix, like_suffix),
                )
            else:
                await cur.execute(
                    sql.SQL("""
                        SELECT path, modified_at,
                               COALESCE(jsonb_array_length(content->'content'), 0)
                        FROM {table}
                        WHERE path LIKE %s
                        ORDER BY path
                        """).format(table=self._table),
                    (like_prefix,),
                )
            rows = await cur.fetchall()

        candidates = [
            (
                self._virtual_path(storage_path),
                line_count,
                modified_at.isoformat() if modified_at else None,
            )
            for storage_path, modified_at, line_count in rows
        ]
        return build_glob_result(candidates, pattern, path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Sync wrapper for aupload_files."""
        return run_async_safely(self.aupload_files(files))

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload multiple files."""
        responses: list[FileUploadResponse] = []
        pool = await self._ensure_pool()

        async with pool.connection() as conn:
            for path, content in files:
                try:
                    content_json = json.dumps(
                        {"content": content.decode("utf-8", errors="replace").splitlines()}
                    )
                    await conn.execute(
                        sql.SQL("""
                        INSERT INTO {table} (path, content, created_at, modified_at)
                        VALUES (%s, %s::jsonb, NOW(), NOW())
                        ON CONFLICT (path) DO UPDATE SET
                            content = EXCLUDED.content,
                            modified_at = NOW()
                        """).format(table=self._table),
                        (path, content_json),
                    )
                    responses.append(FileUploadResponse(path=path, error=None))
                except Exception:
                    responses.append(FileUploadResponse(path=path, error="invalid_path"))
            await conn.commit()

        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Sync wrapper for adownload_files."""
        return run_async_safely(self.adownload_files(paths))

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download multiple files."""
        responses: list[FileDownloadResponse] = []
        pool = await self._ensure_pool()

        async with pool.connection() as conn:
            for path in paths:
                try:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            sql.SQL("SELECT content FROM {table} WHERE path = %s").format(
                                table=self._table
                            ),
                            (path,),
                        )
                        row = await cur.fetchone()
                        if row:
                            data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                            content = "\n".join(data.get("content", [])).encode("utf-8")
                            responses.append(
                                FileDownloadResponse(path=path, content=content, error=None)
                            )
                        else:
                            responses.append(
                                FileDownloadResponse(path=path, content=None, error="file_not_found")
                            )
                except Exception:
                    responses.append(FileDownloadResponse(path=path, content=None, error="invalid_path"))

        return responses
