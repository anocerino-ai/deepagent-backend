from unittest.mock import AsyncMock, MagicMock, patch

import deepagent_backend_gcs.backend as backend_module
import pytest
from deepagent_backend_gcs import GCSBackend


@pytest.mark.unit
class TestGCSBackendUnit:
    @pytest.fixture
    def backend(self, gcs_config_unit):
        with patch("deepagent_backend_gcs.backend.GCSStorage") as storage_cls:
            storage = MagicMock()
            storage.close = AsyncMock()
            storage_cls.return_value = storage
            backend = GCSBackend(gcs_config_unit)
            yield backend, storage

    async def test_aread_success(self, backend):
        backend_obj, storage = backend
        storage.download = AsyncMock(return_value=b'{"content": ["line1", "line2"]}')

        result = await backend_obj.aread("test.txt")

        assert result.error is None
        assert result.file_data is not None
        assert result.file_data["content"] == "line1\nline2"
        assert (result.start_line, result.end_line, result.total_lines) == (1, 2, 2)
        storage.download.assert_awaited_once_with("unit-test-bucket", "unit-test/test.txt")

    async def test_awrite_success(self, backend):
        backend_obj, storage = backend
        backend_obj._exists = AsyncMock(return_value=False)
        storage.upload = AsyncMock()

        backend_obj._get_file_data = AsyncMock(return_value=None)
        result = await backend_obj.awrite("new.txt", "content")

        assert result.error is None
        assert result.path == "new.txt"
        storage.upload.assert_awaited()

    async def test_awrite_overwrites_existing_file_keeping_created_at(self, backend):
        backend_obj = backend[0] if isinstance(backend, tuple) else backend
        backend_obj._get_file_data = AsyncMock(
            return_value={"content": ["old"], "created_at": "2024-01-01T00:00:00+00:00"}
        )
        backend_obj._put_file_data = AsyncMock()

        result = await backend_obj.awrite("exists.txt", "content")

        assert result.error is None
        assert result.path == "exists.txt"
        stored = backend_obj._put_file_data.await_args.args[1]
        assert stored["content"] == ["content"]
        assert stored["created_at"] == "2024-01-01T00:00:00+00:00"

    async def test_als_collapses_nested_entries(self, backend):
        backend_obj, _storage = backend
        backend_obj._list_objects = AsyncMock(
            return_value=[
                ("/file1.txt", 10, "2024-01-01T00:00:00+00:00"),
                ("/dir/file2.txt", 10, "2024-01-01T00:00:00+00:00"),
            ]
        )

        results = (await backend_obj.als("/")).entries

        assert {item["path"] for item in results} == {"/file1.txt", "/dir/"}

    async def test_agrep_filters_by_glob(self, backend):
        backend_obj, _storage = backend
        backend_obj._list_objects = AsyncMock(
            return_value=[("/search/match.py", 1, None), ("/search/skip.txt", 1, None)]
        )
        backend_obj._get_file_data = AsyncMock(
            side_effect=[{"content": ["needle"]}, {"content": ["needle"]}]
        )

        result = (await backend_obj.agrep("needle", "/search", "*.py")).matches

        assert result == [{"path": "/search/match.py", "line": 1, "text": "needle"}]

    async def test_aglob_matches_relative_pattern(self, backend):
        backend_obj, _storage = backend
        backend_obj._list_objects = AsyncMock(
            return_value=[("/src/main.py", 1, None), ("/src/readme.txt", 1, None)]
        )

        result = (await backend_obj.aglob("*.py", "/src")).matches

        assert result == [{"path": "/src/main.py", "is_dir": False, "size": 1}]

    async def test_upload_and_download_files(self, backend):
        backend_obj, storage = backend
        storage.upload = AsyncMock()
        storage.download = AsyncMock(return_value=b"payload")

        upload_responses = await backend_obj.aupload_files([("file.bin", b"payload")])
        download_responses = await backend_obj.adownload_files(["file.bin"])

        assert upload_responses[0].error is None
        assert download_responses[0].content == b"payload"

    @pytest.mark.parametrize(
        ("method_name", "args", "expected_coroutine_name"),
        [
            ("read", ("test.txt",), "aread"),
            ("write", ("new.txt", "content"), "awrite"),
            ("edit", ("test.txt", "old", "new"), "aedit"),
            ("ls", ("/",), "als"),
            ("grep", ("pattern",), "agrep"),
            ("glob", ("*.py",), "aglob"),
            ("upload_files", ([("file.txt", b"data")],), "aupload_files"),
            ("download_files", (["file.txt"],), "adownload_files"),
        ],
    )
    def test_sync_wrappers_delegate_to_run_async_safely(
        self, backend, method_name, args, expected_coroutine_name
    ):
        backend_obj, _storage = backend
        sentinel = object()

        with patch.object(backend_module, "run_async_safely", return_value=sentinel) as mock_run:
            result = getattr(backend_obj, method_name)(*args)

        assert result is sentinel
        coroutine = mock_run.call_args.args[0]
        try:
            assert coroutine.cr_code.co_name == expected_coroutine_name
        finally:
            coroutine.close()
