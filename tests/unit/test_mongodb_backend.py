from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import deepagent_backend_mongodb.backend as backend_module
import pytest
from deepagent_backend_mongodb import MongoDBBackend


def _async_cursor(documents):
    class Cursor:
        def __aiter__(self):
            self._iterator = iter(documents)
            return self

        async def __anext__(self):
            try:
                return next(self._iterator)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    return Cursor()


@pytest.mark.unit
class TestMongoDBBackendUnit:
    @pytest.fixture
    def backend(self, mongodb_config_unit):
        with patch("deepagent_backend_mongodb.backend.AsyncIOMotorClient") as client_cls:
            client = MagicMock()
            database = MagicMock()
            collection = MagicMock()
            client.__getitem__.return_value = database
            database.__getitem__.return_value = collection
            collection.find_one = AsyncMock()
            collection.replace_one = AsyncMock()
            collection.create_index = AsyncMock()
            collection.find = MagicMock(return_value=_async_cursor([]))
            client_cls.return_value = client
            backend = MongoDBBackend(mongodb_config_unit)
            yield backend, collection, client

    async def test_initialize_creates_indexes(self, backend):
        backend_obj, collection, _client = backend

        await backend_obj.initialize()

        assert collection.create_index.await_count == 2

    async def test_aread_success(self, backend):
        backend_obj, collection, _client = backend
        collection.find_one.return_value = {
            "content": ["line1", "line2"],
            "created_at": datetime.now(UTC),
            "modified_at": datetime.now(UTC),
        }

        result = await backend_obj.aread("test.txt")

        assert result.error is None
        assert result.file_data is not None
        assert result.file_data["content"] == "line1\nline2"
        assert (result.start_line, result.end_line, result.total_lines) == (1, 2, 2)

    async def test_awrite_success(self, backend):
        backend_obj, collection, _client = backend
        collection.find_one.side_effect = [None, None]

        backend_obj._get_file_data = AsyncMock(return_value=None)
        result = await backend_obj.awrite("new.txt", "content")

        assert result.error is None
        assert result.path == "new.txt"
        collection.replace_one.assert_awaited()

    async def test_awrite_overwrites_existing_file_keeping_created_at(self, backend):
        backend_obj, collection, _client = backend
        created = datetime(2024, 1, 1, tzinfo=UTC)
        collection.find_one.return_value = {
            "content": ["old"],
            "created_at": created,
            "modified_at": created,
        }

        result = await backend_obj.awrite("exists.txt", "content")

        assert result.error is None
        assert result.path == "exists.txt"
        document = collection.replace_one.await_args.args[1]
        assert document["content"] == ["content"]
        assert document["created_at"] == created

    async def test_als_collapses_nested_entries(self, backend):
        backend_obj, collection, _client = backend
        collection.find.return_value = _async_cursor(
            [
                {"path": "unit-test/file1.txt", "content": ["x"], "modified_at": None},
                {"path": "unit-test/dir/file2.txt", "content": ["x"], "modified_at": None},
            ]
        )

        results = (await backend_obj.als("/")).entries

        assert {item["path"] for item in results} == {"/file1.txt", "/dir/"}

    async def test_agrep_filters_by_glob(self, backend):
        backend_obj, collection, _client = backend
        collection.find.return_value = _async_cursor(
            [
                {"path": "unit-test/search/match.py", "content": ["needle"]},
                {"path": "unit-test/search/skip.txt", "content": ["needle"]},
            ]
        )

        result = (await backend_obj.agrep("needle", "/search", "*.py")).matches

        assert result == [{"path": "/search/match.py", "line": 1, "text": "needle"}]

    async def test_aglob_matches_relative_pattern(self, backend):
        backend_obj, collection, _client = backend
        collection.find.return_value = _async_cursor(
            [
                {"path": "unit-test/src/main.py", "content": ["x"], "modified_at": None},
                {"path": "unit-test/src/readme.txt", "content": ["x"], "modified_at": None},
            ]
        )

        result = (await backend_obj.aglob("*.py", "/src")).matches

        assert result == [{"path": "/src/main.py", "is_dir": False, "size": 1}]

    async def test_upload_and_download_files(self, backend):
        backend_obj, collection, _client = backend
        collection.find_one.side_effect = [
            None,
            {
                "content": ["payload"],
                "created_at": datetime.now(UTC),
                "modified_at": datetime.now(UTC),
            },
        ]

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
        backend_obj, _collection, _client = backend
        sentinel = object()

        with patch.object(backend_module, "run_async_safely", return_value=sentinel) as mock_run:
            result = getattr(backend_obj, method_name)(*args)

        assert result is sentinel
        coroutine = mock_run.call_args.args[0]
        try:
            assert coroutine.cr_code.co_name == expected_coroutine_name
        finally:
            coroutine.close()
