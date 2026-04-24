"""Unit tests for vaibify.gui.pathContract.

The contract: container paths cross the wire and live in workflow.json
in repo-relative form. Backend internals stay absolute. The four
helpers in pathContract are the only place that conversion happens.
"""

from vaibify.gui.pathContract import (
    fdictAbsKeysToRepoRelative,
    flistNormalizeModifiedFiles,
    fsAbsToRepoRelative,
    fsRepoRelativeToAbs,
)


# -----------------------------------------------------------------------
# fsAbsToRepoRelative
# -----------------------------------------------------------------------


def test_fsAbsToRepoRelative_strips_repo_prefix():
    sResult = fsAbsToRepoRelative(
        "/workspace/proj/dir/file.dat", "/workspace/proj",
    )
    assert sResult == "dir/file.dat"


def test_fsAbsToRepoRelative_strips_with_trailing_slash_root():
    sResult = fsAbsToRepoRelative(
        "/workspace/proj/dir/file.dat", "/workspace/proj/",
    )
    assert sResult == "dir/file.dat"


def test_fsAbsToRepoRelative_idempotent_on_relative_input():
    sResult = fsAbsToRepoRelative(
        "dir/file.dat", "/workspace/proj",
    )
    assert sResult == "dir/file.dat"


def test_fsAbsToRepoRelative_normalizes_relative_input():
    sResult = fsAbsToRepoRelative(
        "./dir/file.dat", "/workspace/proj",
    )
    assert sResult == "dir/file.dat"


def test_fsAbsToRepoRelative_empty_root_returns_input():
    assert fsAbsToRepoRelative("/abs/path", "") == "/abs/path"


def test_fsAbsToRepoRelative_empty_path_returns_input():
    assert fsAbsToRepoRelative("", "/workspace/proj") == ""


def test_fsAbsToRepoRelative_path_equals_root_returns_empty():
    sResult = fsAbsToRepoRelative(
        "/workspace/proj", "/workspace/proj",
    )
    assert sResult == ""


def test_fsAbsToRepoRelative_outside_root_returns_unchanged(caplog):
    """Defensive: paths outside the repo root come back unchanged + warn."""
    import logging
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        sResult = fsAbsToRepoRelative(
            "/etc/passwd", "/workspace/proj",
        )
    assert sResult == "/etc/passwd"
    assert any(
        "is not under repo root" in record.message
        for record in caplog.records
    )


def test_fsAbsToRepoRelative_handles_redundant_slashes():
    sResult = fsAbsToRepoRelative(
        "/workspace/proj//dir/file.dat", "/workspace/proj",
    )
    assert sResult == "dir/file.dat"


# -----------------------------------------------------------------------
# fsRepoRelativeToAbs
# -----------------------------------------------------------------------


def test_fsRepoRelativeToAbs_joins_with_root():
    sResult = fsRepoRelativeToAbs(
        "dir/file.dat", "/workspace/proj",
    )
    assert sResult == "/workspace/proj/dir/file.dat"


def test_fsRepoRelativeToAbs_idempotent_on_absolute_input():
    sResult = fsRepoRelativeToAbs(
        "/already/absolute", "/workspace/proj",
    )
    assert sResult == "/already/absolute"


def test_fsRepoRelativeToAbs_collapses_dot_segments():
    sResult = fsRepoRelativeToAbs(
        "./dir/./file.dat", "/workspace/proj",
    )
    assert sResult == "/workspace/proj/dir/file.dat"


def test_fsRepoRelativeToAbs_empty_path_returns_root():
    assert fsRepoRelativeToAbs("", "/workspace/proj") == "/workspace/proj"


def test_fsRepoRelativeToAbs_empty_root_normalizes_path():
    assert fsRepoRelativeToAbs("./dir/file", "") == "dir/file"


def test_fsRepoRelativeToAbs_empty_path_and_root_returns_empty():
    assert fsRepoRelativeToAbs("", "") == ""


# -----------------------------------------------------------------------
# fdictAbsKeysToRepoRelative
# -----------------------------------------------------------------------


def test_fdictAbsKeysToRepoRelative_rekeys_all_entries():
    dictInput = {
        "/workspace/proj/a.dat": "100",
        "/workspace/proj/sub/b.dat": "200",
    }
    dictResult = fdictAbsKeysToRepoRelative(
        dictInput, "/workspace/proj",
    )
    assert dictResult == {"a.dat": "100", "sub/b.dat": "200"}


def test_fdictAbsKeysToRepoRelative_preserves_values():
    dictInput = {"/r/a": {"nested": True}}
    dictResult = fdictAbsKeysToRepoRelative(dictInput, "/r")
    assert dictResult["a"] == {"nested": True}


def test_fdictAbsKeysToRepoRelative_empty_root_unchanged_keys():
    dictInput = {"/abs/path": "1"}
    assert fdictAbsKeysToRepoRelative(dictInput, "") == dictInput


def test_fdictAbsKeysToRepoRelative_empty_input_returns_empty():
    assert fdictAbsKeysToRepoRelative({}, "/workspace/proj") == {}


# -----------------------------------------------------------------------
# flistNormalizeModifiedFiles
# -----------------------------------------------------------------------


def test_flistNormalizeModifiedFiles_converts_mixed_input():
    listInput = [
        "/workspace/proj/dir/a.dat",
        "dir/b.dat",
    ]
    listResult = flistNormalizeModifiedFiles(
        listInput, "/workspace/proj",
    )
    assert listResult == ["dir/a.dat", "dir/b.dat"]


def test_flistNormalizeModifiedFiles_dedupes_equivalent_paths():
    listInput = [
        "/workspace/proj/dir/a.dat",
        "dir/a.dat",
    ]
    listResult = flistNormalizeModifiedFiles(
        listInput, "/workspace/proj",
    )
    assert listResult == ["dir/a.dat"]


def test_flistNormalizeModifiedFiles_returns_sorted():
    listInput = ["z.dat", "a.dat", "m.dat"]
    listResult = flistNormalizeModifiedFiles(
        listInput, "/workspace/proj",
    )
    assert listResult == ["a.dat", "m.dat", "z.dat"]


def test_flistNormalizeModifiedFiles_empty_input_returns_empty():
    assert flistNormalizeModifiedFiles([], "/workspace/proj") == []


def test_flistNormalizeModifiedFiles_skips_empty_entries():
    listResult = flistNormalizeModifiedFiles(
        ["", "dir/a.dat", ""], "/workspace/proj",
    )
    assert listResult == ["dir/a.dat"]


def test_flistNormalizeModifiedFiles_idempotent():
    listInput = ["dir/a.dat", "dir/b.dat"]
    listOnce = flistNormalizeModifiedFiles(
        listInput, "/workspace/proj",
    )
    listTwice = flistNormalizeModifiedFiles(
        listOnce, "/workspace/proj",
    )
    assert listOnce == listTwice == ["dir/a.dat", "dir/b.dat"]


def test_flistNormalizeModifiedFiles_empty_root_keeps_paths():
    listResult = flistNormalizeModifiedFiles(
        ["/abs/path", "rel/path"], "",
    )
    assert listResult == ["/abs/path", "rel/path"]
