"""Shared helpers for Deep Agents remote storage backends."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from deepagents.backends.protocol import (
    FileData,
    FileInfo,
    GlobResult,
    GrepMatch,
    GrepResult,
    ReadResult,
)
from deepagents.backends.utils import (
    InvalidGlobPatternError,
    compile_grep_include_glob,
    perform_string_replacement,
    slice_read_response,
)

__all__ = [
    "GrepCollector",
    "InvalidGlobPatternError",
    "build_direct_listing",
    "build_glob_result",
    "compile_path_filter",
    "edit_text_payload",
    "file_info",
    "is_under_root",
    "make_text_file_data",
    "normalize_virtual_path",
    "payload_lines",
    "payload_to_file_data",
    "read_text_result",
    "relative_to_root",
    "run_async_safely",
    "search_root",
    "status_code_from_error",
    "utcnow_iso",
]


class _AsyncThread(threading.Thread):
    """helper thread class for running async coroutines in a separate thread"""

    def __init__(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        self.coroutine = coroutine
        self.result: Any = None
        self.exception: BaseException | None = None

        super().__init__(daemon=True)

    def run(self) -> None:
        try:
            self.result = asyncio.run(self.coroutine)
        except Exception as e:
            self.exception = e


def run_async_safely[T](coroutine: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
    """safely runs a coroutine with handling of an existing event loop.

    This function detects if there's already a running event loop and uses
    a separate thread if needed to avoid the "asyncio.run() cannot be called
    from a running event loop" error. This is particularly useful in environments
    like Jupyter notebooks, FastAPI applications, or other async frameworks.

    Args:
        coroutine: The coroutine to run
        timeout: max seconds to wait for. None means hanging forever

    Returns:
        The result of the coroutine

    Raises:
        Any exception raised by the coroutine
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # There's a running loop, use a separate thread
        thread = _AsyncThread(coroutine)
        thread.start()
        thread.join(timeout=timeout)

        if thread.is_alive():
            raise TimeoutError(f"The operation timed out after {timeout:f} seconds")

        if thread.exception:
            raise thread.exception

        return thread.result
    else:
        if timeout:
            coroutine = asyncio.wait_for(coroutine, timeout)

        return asyncio.run(coroutine)


def utcnow_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


def make_text_file_data(content: str, created_at: str | None = None) -> dict[str, Any]:
    """Build the canonical JSON payload used by text-oriented backends.

    ``created_at`` carries over the creation time when an existing file is overwritten.
    """
    now = utcnow_iso()
    return {
        "content": content.splitlines(),
        "created_at": created_at or now,
        "modified_at": now,
    }


def payload_lines(data: Mapping[str, Any]) -> list[str]:
    """Return the stored content as lines (accepts the line-array and the plain-string layout)."""
    content = data.get("content", [])
    if isinstance(content, str):
        return content.split("\n")
    return list(content)


def payload_to_file_data(data: Mapping[str, Any]) -> FileData:
    """Convert a stored payload into the ``FileData`` structure used by Deep Agents."""
    file_data = FileData(content="\n".join(payload_lines(data)), encoding="utf-8")
    if data.get("created_at"):
        file_data["created_at"] = data["created_at"]
    if data.get("modified_at"):
        file_data["modified_at"] = data["modified_at"]
    return file_data


def read_text_result(
    file_path: str,
    data: Mapping[str, Any] | None,
    offset: int = 0,
    limit: int = 2000,
) -> ReadResult:
    """Build the ``ReadResult`` for a stored payload.

    The window is returned raw: line numbers are added downstream by the middleware.
    """
    if data is None:
        return ReadResult(error=f"File '{file_path}' not found")
    return slice_read_response(payload_to_file_data(data), offset, limit)


def edit_text_payload(
    data: dict[str, Any] | None,
    *,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> tuple[dict[str, Any] | None, int | None, str | None]:
    """Apply Deep Agents string replacement semantics to stored file data."""
    if data is None:
        return None, None, "file_not_found"

    content = "\n".join(payload_lines(data))
    result = perform_string_replacement(content, old_string, new_string, replace_all)
    if isinstance(result, str):
        return None, None, result

    new_content, occurrences = result
    data["content"] = new_content.splitlines()
    return data, int(occurrences), None


def normalize_virtual_path(path: str) -> str:
    """Normalize any path into a slash-prefixed virtual path."""
    return "/" + path.lstrip("/")


def search_root(path: str | None) -> str:
    """Normalize a grep/glob search root to ``/`` or ``/dir`` (no trailing slash)."""
    if path is None or not path.strip():
        return "/"
    return "/" + path.strip().strip("/")


def is_under_root(virtual_path: str, root: str) -> bool:
    """Return whether ``virtual_path`` is ``root`` itself or lives below it."""
    return root == "/" or virtual_path == root or virtual_path.startswith(root + "/")


def relative_to_root(virtual_path: str, root: str) -> str:
    """Return ``virtual_path`` relative to a normalized search root (basename for an exact file)."""
    if root == "/":
        return virtual_path.lstrip("/")
    if virtual_path == root:
        return virtual_path.rsplit("/", maxsplit=1)[-1]
    return virtual_path[len(root) + 1 :]


def compile_path_filter(pattern: str | None, root: str) -> Callable[[str], bool]:
    """Compile a grep include-glob / ``glob`` pattern into a predicate on virtual paths.

    Matching is delegated to Deep Agents so every backend shares the same semantics
    (basename at any depth without ``/``, root-relative with ``/``, ``**`` and braces).

    Raises:
        InvalidGlobPatternError: if the pattern is refused (e.g. a ``..`` segment).
    """
    if not pattern:
        return lambda _virtual_path: True

    matcher = compile_grep_include_glob(pattern)
    return lambda virtual_path: matcher(relative_to_root(virtual_path, root))


def file_info(virtual_path: str, size: int | None, modified_at: str | None) -> FileInfo:
    """Build a ``FileInfo`` entry for a file, leaving out unknown metadata."""
    info = FileInfo(path=virtual_path, is_dir=False, size=size or 0)
    if modified_at:
        info["modified_at"] = modified_at
    return info


def build_glob_result(
    files: Iterable[tuple[str, int | None, str | None]],
    pattern: str,
    path: str | None,
) -> GlobResult:
    """Filter ``(virtual_path, size, modified_at)`` candidates with a glob pattern."""
    root = search_root(path)
    try:
        accepts = compile_path_filter(pattern, root)
    except InvalidGlobPatternError as exc:
        return GlobResult(error=str(exc))

    entries = [
        file_info(virtual_path, size, modified_at)
        for virtual_path, size, modified_at in files
        if is_under_root(virtual_path, root) and accepts(virtual_path)
    ]
    entries.sort(key=lambda entry: entry["path"])
    return GlobResult(matches=entries)


class GrepCollector:
    """Accumulates literal-substring matches and enforces ``max_count``.

    ``scan`` stops as soon as a match beyond ``max_count`` proves that more exist, flagging
    the result as truncated; exactly ``max_count`` matches is reported complete. Callers
    should stop feeding files once ``done`` is true.
    """

    def __init__(self, pattern: str, max_count: int | None = None) -> None:
        self._pattern = pattern
        self._max_count = max_count
        self.matches: list[GrepMatch] = []
        self.truncated = False

    @property
    def done(self) -> bool:
        return self.truncated

    def scan(self, virtual_path: str, lines: Iterable[str]) -> None:
        for line_number, line in enumerate(lines, 1):
            if self._pattern not in line:
                continue
            if self._max_count is not None and len(self.matches) >= self._max_count:
                self.truncated = True
                return
            self.matches.append({"path": virtual_path, "line": line_number, "text": line})

    def result(self) -> GrepResult:
        return GrepResult(matches=self.matches, truncated=self.truncated)


def build_direct_listing(
    directory_path: str,
    files: list[tuple[str, int | None, str | None]],
) -> list[FileInfo]:
    """Collapse recursive file metadata into direct child file/dir entries."""
    normalized_dir = "/" if directory_path == "/" else "/" + directory_path.strip("/")
    base = normalized_dir.strip("/")
    prefix = f"{base}/" if base else ""

    direct_files: list[FileInfo] = []
    direct_dirs: set[str] = set()

    for virtual_path, size, modified_at in files:
        clean = virtual_path.lstrip("/")
        if prefix:
            if not clean.startswith(prefix):
                continue
            rel = clean[len(prefix) :]
        else:
            rel = clean

        if not rel:
            continue

        child, sep, _rest = rel.partition("/")
        if sep:
            direct_dirs.add(child)
            continue

        direct_files.append(file_info(normalize_virtual_path(clean), size, modified_at))

    dir_prefix = "/" if normalized_dir == "/" else normalized_dir + "/"
    results = direct_files + [
        FileInfo(path=f"{dir_prefix}{name}/", is_dir=True) for name in sorted(direct_dirs)
    ]
    results.sort(key=lambda item: item["path"])
    return results


def status_code_from_error(error: Exception) -> int | None:
    """Extract an HTTP-like status code from client exceptions when available."""
    return getattr(error, "status", getattr(error, "code", None))
