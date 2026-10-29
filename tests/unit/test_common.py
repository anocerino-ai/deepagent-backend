import pytest

from deepagent_backend.common import (
    GrepCollector,
    build_direct_listing,
    build_glob_result,
    compile_path_filter,
    edit_text_payload,
    make_text_file_data,
    read_text_result,
    relative_to_root,
    search_root,
)

pytestmark = pytest.mark.unit

FILES = [
    ("/src/app/main.py", 3, "2024-01-01T00:00:00+00:00"),
    ("/src/readme.md", 1, None),
    ("/top.py", 2, None),
    ("/.github/ci.yml", 1, None),
]


class TestPaths:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            (None, "/"),
            ("", "/"),
            ("/", "/"),
            ("src", "/src"),
            ("/src/", "/src"),
            (" /src/app ", "/src/app"),
        ],
    )
    def test_search_root(self, path, expected):
        assert search_root(path) == expected

    def test_relative_to_root(self):
        assert relative_to_root("/src/app/main.py", "/") == "src/app/main.py"
        assert relative_to_root("/src/app/main.py", "/src") == "app/main.py"
        assert relative_to_root("/src/app/main.py", "/src/app/main.py") == "main.py"


class TestGlob:
    def paths(self, pattern, path=None):
        result = build_glob_result(FILES, pattern, path)
        assert result.error is None
        return [entry["path"] for entry in result.matches]

    def test_bare_pattern_matches_basename_at_any_depth(self):
        assert self.paths("*.py") == ["/src/app/main.py", "/top.py"]

    def test_pattern_with_slash_is_relative_to_root(self):
        assert self.paths("src/**/*.py") == ["/src/app/main.py"]
        assert self.paths("*.py", "/src") == ["/src/app/main.py"]

    def test_leading_slash_anchors_to_root(self):
        assert self.paths("/*.py") == ["/top.py"]

    def test_dot_directories_only_match_explicitly(self):
        assert self.paths("*.yml") == ["/.github/ci.yml"]
        assert self.paths("**/*.yml") == []

    def test_results_only_include_known_metadata(self):
        (entry,) = build_glob_result(FILES, "readme.md", None).matches
        assert entry == {"path": "/src/readme.md", "is_dir": False, "size": 1}

    def test_traversal_is_reported_not_raised(self):
        result = build_glob_result(FILES, "../*.py", None)
        assert result.matches is None
        assert "traversal" in result.error

    def test_path_filter_without_pattern_accepts_everything(self):
        assert compile_path_filter(None, "/")("/anything")


class TestGrepCollector:
    def test_collects_literal_matches_with_line_numbers(self):
        collector = GrepCollector("a.c")
        collector.scan("/f", ["abc", "a.c", "x a.c y"])

        assert [(m["line"], m["text"]) for m in collector.matches] == [(2, "a.c"), (3, "x a.c y")]
        assert collector.result().truncated is False

    def test_exactly_max_count_is_not_truncated(self):
        collector = GrepCollector("hit", max_count=2)
        collector.scan("/f", ["hit", "hit"])

        assert len(collector.matches) == 2
        assert collector.done is False

    def test_more_than_max_count_is_truncated_and_stops(self):
        collector = GrepCollector("hit", max_count=2)
        collector.scan("/f", ["hit", "hit", "hit"])

        assert len(collector.matches) == 2
        assert collector.result().truncated is True
        assert collector.done is True


class TestRead:
    DATA = {"content": ["l1", "l2", "l3"], "created_at": "2024-01-01T00:00:00+00:00"}

    def test_missing_file(self):
        assert read_text_result("/x", None).error == "File '/x' not found"

    def test_window_carries_pagination_and_timestamps(self):
        result = read_text_result("/x", self.DATA, offset=1, limit=1)

        assert result.file_data["content"] == "l2\n"
        assert result.file_data["created_at"] == "2024-01-01T00:00:00+00:00"
        assert "modified_at" not in result.file_data
        assert (result.start_line, result.end_line, result.total_lines, result.next_offset) == (
            2,
            2,
            3,
            2,
        )

    def test_last_window_has_no_next_offset(self):
        assert read_text_result("/x", self.DATA, offset=2).next_offset is None

    def test_offset_past_end_is_an_error(self):
        assert (
            read_text_result("/x", self.DATA, offset=3).error
            == "Line offset 3 exceeds file length (3 lines)"
        )

    def test_degenerate_windows_do_not_raise(self):
        assert read_text_result("/x", self.DATA, offset=-5).start_line == 1
        assert read_text_result("/x", self.DATA, limit=0).no_lines_requested is True

    def test_plain_string_content_is_accepted(self):
        assert read_text_result("/x", {"content": "a\nb"}).file_data["content"] == "a\nb"


class TestEditAndWrite:
    def test_edit_updates_lines_and_counts_occurrences(self):
        data, count, error = edit_text_payload(
            {"content": ["old old", "old"]}, old_string="old", new_string="new", replace_all=True
        )

        assert error is None
        assert count == 3
        assert data["content"] == ["new new", "new"]

    def test_edit_reports_missing_file_and_bad_replacement(self):
        assert edit_text_payload(None, old_string="a", new_string="b")[2] == "file_not_found"
        assert edit_text_payload({"content": ["x"]}, old_string="a", new_string="b")[2]

    def test_make_text_file_data_keeps_created_at_on_overwrite(self):
        data = make_text_file_data("a\nb", created_at="2024-01-01T00:00:00+00:00")

        assert data["content"] == ["a", "b"]
        assert data["created_at"] == "2024-01-01T00:00:00+00:00"
        assert data["modified_at"] != data["created_at"]


class TestListing:
    def test_direct_children_and_collapsed_dirs(self):
        entries = build_direct_listing("/", FILES)

        assert [(e["path"], e["is_dir"]) for e in entries] == [
            ("/.github/", True),
            ("/src/", True),
            ("/top.py", False),
        ]
