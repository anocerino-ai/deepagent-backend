import pytest


@pytest.mark.integration
@pytest.mark.redis
class TestRedisBackendIntegration:
    async def test_full_lifecycle(self, redis_backend):
        write_res = await redis_backend.awrite("hello.txt", "Hello Redis\nLine 2")
        assert write_res.error is None
        assert write_res.path == "hello.txt"

        read_res = (await redis_backend.aread("hello.txt")).file_data["content"]
        assert "Hello Redis" in read_res
        assert "Line 2" in read_res

        ls_res = (await redis_backend.als("/")).entries
        assert any(f["path"] == "/hello.txt" for f in ls_res)

        edit_res = await redis_backend.aedit("hello.txt", "Redis", "Valkey")
        assert edit_res.error is None
        assert edit_res.occurrences == 1

        read_res_2 = (await redis_backend.aread("hello.txt")).file_data["content"]
        assert "Hello Valkey" in read_res_2

    async def test_grep(self, redis_backend):
        await redis_backend.awrite("grep_me.txt", "match this pattern\ndon't match this")

        matches = (await redis_backend.agrep("pattern")).matches
        assert len(matches) == 1
        assert matches[0]["text"] == "match this pattern"
        assert matches[0]["line"] == 1

    async def test_overwrite_keeps_content_in_sync(self, redis_backend):
        first = await redis_backend.awrite("overwrite.txt", "v1")
        second = await redis_backend.awrite("overwrite.txt", "v2")
        assert first.error is None
        assert second.error is None

        read_res = await redis_backend.aread("overwrite.txt")
        assert read_res.file_data is not None
        assert read_res.file_data["content"] == "v2"

    async def test_grep_max_count_truncates(self, redis_backend):
        await redis_backend.awrite("max_count.txt", "hit\nhit\nhit")

        capped = await redis_backend.agrep("hit", "/", "max_count.txt", max_count=2)
        complete = await redis_backend.agrep("hit", "/", "max_count.txt", max_count=3)

        assert capped.matches is not None and len(capped.matches) == 2
        assert capped.truncated is True
        assert complete.matches is not None and len(complete.matches) == 3
        assert complete.truncated is False

    async def test_glob(self, redis_backend):
        await redis_backend.awrite("src/main.py", "print('hello')")
        await redis_backend.awrite("src/utils.py", "def util(): pass")
        await redis_backend.awrite("README.md", "# Readme")

        results = (await redis_backend.aglob("*.py", "/src")).matches
        assert len(results) == 2
        paths = sorted([r["path"] for r in results])
        assert paths == ["/src/main.py", "/src/utils.py"]
