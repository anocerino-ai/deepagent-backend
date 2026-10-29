import asyncio

import pytest

from tests.common.scalability import (
    GLOB_MATCH_SUFFIX,
    GLOB_NOMATCH_SUFFIX,
    GREP_MATCH_LINE,
    GREP_NOMATCH_LINE,
    INTEGRATION_FLAT_FILES,
    INTEGRATION_NESTED_DIRS,
)


@pytest.mark.integration
@pytest.mark.s3
class TestS3BackendIntegration:
    async def test_full_lifecycle(self, s3_backend):
        # 1. Write
        write_res = await s3_backend.awrite("hello.txt", "Hello World\nLine 2")
        assert write_res.error is None
        assert write_res.path == "hello.txt"

        # 2. Read
        read_res = (await s3_backend.aread("hello.txt")).file_data["content"]
        assert "Hello World" in read_res
        assert "Line 2" in read_res

        # 3. List
        ls_res = (await s3_backend.als("/")).entries
        assert any(f["path"] == "/hello.txt" for f in ls_res)

        # 4. Edit
        edit_res = await s3_backend.aedit("hello.txt", "World", "Integration")
        assert edit_res.error is None
        assert edit_res.occurrences == 1

        read_res_2 = (await s3_backend.aread("hello.txt")).file_data["content"]
        assert "Hello Integration" in read_res_2

    async def test_grep(self, s3_backend):
        await s3_backend.awrite("grep_me.txt", "match this pattern\ndon't match this")

        matches = (await s3_backend.agrep("pattern")).matches
        assert len(matches) == 1
        assert matches[0]["text"] == "match this pattern"
        assert matches[0]["line"] == 1

    async def test_overwrite_keeps_content_in_sync(self, s3_backend):
        first = await s3_backend.awrite("overwrite.txt", "v1")
        second = await s3_backend.awrite("overwrite.txt", "v2")
        assert first.error is None
        assert second.error is None

        read_res = await s3_backend.aread("overwrite.txt")
        assert read_res.file_data is not None
        assert read_res.file_data["content"] == "v2"

    async def test_grep_max_count_truncates(self, s3_backend):
        await s3_backend.awrite("max_count.txt", "hit\nhit\nhit")

        capped = await s3_backend.agrep("hit", "/", "max_count.txt", max_count=2)
        complete = await s3_backend.agrep("hit", "/", "max_count.txt", max_count=3)

        assert capped.matches is not None and len(capped.matches) == 2
        assert capped.truncated is True
        assert complete.matches is not None and len(complete.matches) == 3
        assert complete.truncated is False

    async def test_glob(self, s3_backend):
        await s3_backend.awrite("src/main.py", "print('hello')")
        await s3_backend.awrite("src/utils.py", "def util(): pass")
        await s3_backend.awrite("README.md", "# Readme")

        results = (await s3_backend.aglob("*.py", "/src")).matches
        assert len(results) == 2
        paths = sorted([r["path"] for r in results])
        assert paths == ["/src/main.py", "/src/utils.py"]


@pytest.mark.integration
@pytest.mark.s3
class TestS3BackendScalabilityIntegration:
    """Integration-scale correctness tests for large candidate sets.

    These tests create real objects in Garage and assert the acceptance
    invariants defined in ``tests/scalability.py``.  Optimization PRs must
    not regress these assertions.
    """

    # ── als: flat directory ──────────────────────────────────────────────

    async def test_als_large_flat_returns_exact_direct_children(
        self, s3_backend, integration_flat_paths
    ):
        """Invariant 1a: exact direct-child count, no nested entries."""
        await asyncio.gather(
            *[s3_backend.awrite(p.lstrip("/"), "content") for p in integration_flat_paths]
        )
        results = (await s3_backend.als("/int_flat")).entries

        assert len(results) == INTEGRATION_FLAT_FILES
        assert all(not r["is_dir"] for r in results)

    # ── als: nested tree ─────────────────────────────────────────────────

    async def test_als_large_nested_returns_only_direct_subdirs(
        self, s3_backend, integration_nested_paths
    ):
        """Invariant 1b: nested files are collapsed to their parent dir entries."""
        await asyncio.gather(
            *[s3_backend.awrite(p.lstrip("/"), "content") for p in integration_nested_paths]
        )
        results = (await s3_backend.als("/int_nested")).entries

        assert len(results) == INTEGRATION_NESTED_DIRS
        assert all(r["is_dir"] for r in results)

    # ── agrep: large candidate set ───────────────────────────────────────

    async def test_agrep_large_set_no_false_positives_or_negatives(
        self, s3_backend, integration_grep_dataset
    ):
        """Invariant 2: grep returns exactly the matching files."""
        matching_paths, all_paths = integration_grep_dataset
        matching_set = set(matching_paths)

        await asyncio.gather(
            *[
                s3_backend.awrite(
                    p.lstrip("/"),
                    GREP_MATCH_LINE if p in matching_set else GREP_NOMATCH_LINE,
                )
                for p in all_paths
            ]
        )

        results = (await s3_backend.agrep(GREP_MATCH_LINE, "/int_grep")).matches
        assert len(results) == len(matching_paths)
        assert {r["path"] for r in results} == matching_set

    # ── aglob: large candidate set ──────────────────────────────────────

    async def test_aglob_large_set_exact_pattern_matches(self, s3_backend, integration_glob_dataset):
        """Invariant 3: aglob returns exactly the files matching the pattern."""
        matching_paths, all_paths = integration_glob_dataset

        await asyncio.gather(*[s3_backend.awrite(p.lstrip("/"), "content") for p in all_paths])

        results = (await s3_backend.aglob(f"*{GLOB_MATCH_SUFFIX}", "/int_glob")).matches
        assert len(results) == len(matching_paths)
        assert {r["path"] for r in results} == set(matching_paths)
        assert all(GLOB_NOMATCH_SUFFIX not in r["path"] for r in results)
