import pytest


@pytest.mark.integration
@pytest.mark.gcs
class TestGCSBackendIntegration:
    async def test_full_lifecycle(self, gcs_backend):
        write_res = await gcs_backend.awrite("hello.txt", "Hello GCS\nLine 2")
        assert write_res.error is None
        assert write_res.path == "hello.txt"

        read_res = (await gcs_backend.aread("hello.txt")).file_data["content"]
        assert "Hello GCS" in read_res
        assert "Line 2" in read_res

        ls_res = (await gcs_backend.als("/")).entries
        assert any(f["path"] == "/hello.txt" for f in ls_res)

        edit_res = await gcs_backend.aedit("hello.txt", "GCS", "Storage")
        assert edit_res.error is None
        assert edit_res.occurrences == 1

        read_res_2 = (await gcs_backend.aread("hello.txt")).file_data["content"]
        assert "Hello Storage" in read_res_2

    async def test_grep(self, gcs_backend):
        await gcs_backend.awrite("grep_me.txt", "match this pattern\ndon't match this")

        matches = (await gcs_backend.agrep("pattern")).matches
        assert len(matches) == 1
        assert matches[0]["text"] == "match this pattern"
        assert matches[0]["line"] == 1

    async def test_overwrite_keeps_content_in_sync(self, gcs_backend):
        first = await gcs_backend.awrite("overwrite.txt", "v1")
        second = await gcs_backend.awrite("overwrite.txt", "v2")
        assert first.error is None
        assert second.error is None

        read_res = await gcs_backend.aread("overwrite.txt")
        assert read_res.file_data is not None
        assert read_res.file_data["content"] == "v2"

    async def test_grep_max_count_truncates(self, gcs_backend):
        await gcs_backend.awrite("max_count.txt", "hit\nhit\nhit")

        capped = await gcs_backend.agrep("hit", "/", "max_count.txt", max_count=2)
        complete = await gcs_backend.agrep("hit", "/", "max_count.txt", max_count=3)

        assert capped.matches is not None and len(capped.matches) == 2
        assert capped.truncated is True
        assert complete.matches is not None and len(complete.matches) == 3
        assert complete.truncated is False

    async def test_glob(self, gcs_backend):
        await gcs_backend.awrite("src/main.py", "print('hello')")
        await gcs_backend.awrite("src/utils.py", "def util(): pass")
        await gcs_backend.awrite("README.md", "# Readme")

        results = (await gcs_backend.aglob("*.py", "/src")).matches
        assert len(results) == 2
        paths = sorted([r["path"] for r in results])
        assert paths == ["/src/main.py", "/src/utils.py"]
