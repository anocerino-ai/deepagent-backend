import pytest


@pytest.mark.integration
@pytest.mark.mongodb
class TestMongoDBBackendIntegration:
    async def test_full_lifecycle(self, mongodb_backend):
        write_res = await mongodb_backend.awrite("hello.txt", "Hello Mongo\nLine 2")
        assert write_res.error is None
        assert write_res.path == "hello.txt"

        read_res = (await mongodb_backend.aread("hello.txt")).file_data["content"]
        assert "Hello Mongo" in read_res
        assert "Line 2" in read_res

        ls_res = (await mongodb_backend.als("/")).entries
        assert any(f["path"] == "/hello.txt" for f in ls_res)

        edit_res = await mongodb_backend.aedit("hello.txt", "Mongo", "Document")
        assert edit_res.error is None
        assert edit_res.occurrences == 1

        read_res_2 = (await mongodb_backend.aread("hello.txt")).file_data["content"]
        assert "Hello Document" in read_res_2

    async def test_grep(self, mongodb_backend):
        await mongodb_backend.awrite("grep_me.txt", "match this pattern\ndon't match this")

        matches = (await mongodb_backend.agrep("pattern")).matches
        assert len(matches) == 1
        assert matches[0]["text"] == "match this pattern"
        assert matches[0]["line"] == 1

    async def test_overwrite_keeps_content_in_sync(self, mongodb_backend):
        first = await mongodb_backend.awrite("overwrite.txt", "v1")
        second = await mongodb_backend.awrite("overwrite.txt", "v2")
        assert first.error is None
        assert second.error is None

        read_res = await mongodb_backend.aread("overwrite.txt")
        assert read_res.file_data is not None
        assert read_res.file_data["content"] == "v2"

    async def test_grep_max_count_truncates(self, mongodb_backend):
        await mongodb_backend.awrite("max_count.txt", "hit\nhit\nhit")

        capped = await mongodb_backend.agrep("hit", "/", "max_count.txt", max_count=2)
        complete = await mongodb_backend.agrep("hit", "/", "max_count.txt", max_count=3)

        assert capped.matches is not None and len(capped.matches) == 2
        assert capped.truncated is True
        assert complete.matches is not None and len(complete.matches) == 3
        assert complete.truncated is False

    async def test_glob(self, mongodb_backend):
        await mongodb_backend.awrite("src/main.py", "print('hello')")
        await mongodb_backend.awrite("src/utils.py", "def util(): pass")
        await mongodb_backend.awrite("README.md", "# Readme")

        results = (await mongodb_backend.aglob("*.py", "/src")).matches
        assert len(results) == 2
        paths = sorted([r["path"] for r in results])
        assert paths == ["/src/main.py", "/src/utils.py"]
