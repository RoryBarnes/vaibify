"""Behaviour tests for manifest-based output staleness detection.

fsetStaleOutputsAgainstManifest answers "which tracked outputs no
longer match MANIFEST.sha256?" — the signal behind the dashboard's
stale badges. These tests drive the host path with a real repo and the
container path with a fake adapter, asserting fresh/stale/missing/
untracked outcomes rather than merely executing the code.
"""

import hashlib

import pytest

from vaibify.gui import hashStaleness


def _fnWriteManifest(pathRepo, dictFiles):
    """Write files and a MANIFEST.sha256 pinning their current bytes."""
    listLines = []
    for sRel, sContent in dictFiles.items():
        (pathRepo / sRel).write_text(sContent)
        sSha = hashlib.sha256(sContent.encode()).hexdigest()
        listLines.append(f"{sSha}  {sRel}")
    (pathRepo / "MANIFEST.sha256").write_text("\n".join(listLines) + "\n")


def test_empty_set_when_manifest_absent(tmp_path):
    assert hashStaleness.fsetStaleOutputsAgainstManifest(
        str(tmp_path), ["out.dat"], {},
    ) == set()


def test_unchanged_file_is_not_stale(tmp_path):
    _fnWriteManifest(tmp_path, {"out.dat": "answer = 42\n"})
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        str(tmp_path), ["out.dat"], {},
    )
    assert setStale == set()


def test_changed_file_is_stale(tmp_path):
    _fnWriteManifest(tmp_path, {"out.dat": "answer = 42\n"})
    (tmp_path / "out.dat").write_text("answer = 43\n")
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        str(tmp_path), ["out.dat"], {},
    )
    assert setStale == {"out.dat"}


def test_missing_file_is_stale(tmp_path):
    _fnWriteManifest(tmp_path, {"out.dat": "x\n"})
    (tmp_path / "out.dat").unlink()
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        str(tmp_path), ["out.dat"], {},
    )
    assert setStale == {"out.dat"}


def test_untracked_path_is_skipped(tmp_path):
    _fnWriteManifest(tmp_path, {"tracked.dat": "x\n"})
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        str(tmp_path), ["untracked.dat"], {},
    )
    assert setStale == set()


def test_empty_manifest_file_yields_empty_set(tmp_path):
    (tmp_path / "MANIFEST.sha256").write_text("")
    assert hashStaleness.fsetStaleOutputsAgainstManifest(
        str(tmp_path), ["out.dat"], {},
    ) == set()


@pytest.mark.parametrize("value,expected", [
    (None, None),
    ("1700000000", 1700000000),
    (1700000000.9, 1700000000),
    ("not-a-number", None),
    (object(), None),
])
def test_coerce_mtime(value, expected):
    assert hashStaleness._fiCoerceMtime(value) == expected


@pytest.mark.parametrize("dictMarker,bExpected", [
    ({"dictOutputHashes": {"a.dat": "sha"}}, True),
    ({"dictOutputHashes": {}}, False),
    ({}, False),
    ("not a dict", False),
    ({"dictOutputHashes": "not a dict"}, False),
])
def test_marker_has_hashes(dictMarker, bExpected):
    assert hashStaleness.fbMarkerHasHashes(dictMarker) is bExpected


def test_stale_outputs_for_step_empty_without_hashes():
    assert hashStaleness.fsetStaleOutputsForStep(
        {"dictOutputHashes": {}}, "/root", {},
    ) == set()


def test_stale_outputs_for_step_flags_drifted_files(tmp_path):
    from vaibify.gui import mtimeCache
    (tmp_path / "match.dat").write_text("same\n")
    (tmp_path / "drift.dat").write_text("changed\n")
    # Step markers store the git BLOB SHA (not the content SHA-256 the
    # manifest uses), so the baseline is derived the same way the code
    # compares — matching for match.dat, wrong for the others.
    sMatchSha = mtimeCache.fsBlobShaForFile(str(tmp_path), "match.dat", {})
    sWrongSha = "0" * len(sMatchSha)
    dictMarker = {"dictOutputHashes": {
        "match.dat": sMatchSha,
        "drift.dat": sWrongSha,
        "gone.dat": sWrongSha,
    }}
    setStale = hashStaleness.fsetStaleOutputsForStep(
        dictMarker, str(tmp_path), {},
    )
    assert "drift.dat" in setStale
    assert "gone.dat" in setStale
    assert "match.dat" not in setStale


class _FakeContainerRepo:
    """A container-rooted adapter: no local root, hashes via fdictHashFiles."""

    def __init__(self, dictShaByPath, dictManifest):
        self._dictShaByPath = dictShaByPath
        self._dictManifest = dictManifest
        self.listHashCalls = []

    def fsLocalRootOrNone(self):
        return None

    def fdictHashFiles(self, listRelPaths):
        self.listHashCalls.append(list(listRelPaths))
        return {
            sRel: {"sSha256": self._dictShaByPath.get(sRel)}
            for sRel in listRelPaths
        }


def test_container_path_uses_cache_hit_over_rehash():
    """A path whose hinted mtime matches the cache is not re-hashed."""
    dictCache = {"out.dat": {"iMtime": 100, "sSha256": "cafe"}}
    fake = _FakeContainerRepo({"out.dat": "SHOULD_NOT_BE_USED"}, {})
    dictShas = hashStaleness._fdictContainerShas(
        fake, ["out.dat"], dictCache, {"out.dat": 100},
    )
    assert dictShas["out.dat"] == "cafe"
    assert fake.listHashCalls == [], "a cache hit must not re-hash"


def test_container_path_rehashes_on_mtime_change_and_updates_cache():
    dictCache = {"out.dat": {"iMtime": 100, "sSha256": "old"}}
    fake = _FakeContainerRepo({"out.dat": "newsha"}, {})
    dictShas = hashStaleness._fdictContainerShas(
        fake, ["out.dat"], dictCache, {"out.dat": 200},
    )
    assert dictShas["out.dat"] == "newsha"
    assert fake.listHashCalls == [["out.dat"]]
    assert dictCache["out.dat"] == {"iMtime": 200, "sSha256": "newsha"}


def test_container_staleness_end_to_end(monkeypatch):
    """A container repo whose output SHA differs from the manifest is stale."""
    fake = _FakeContainerRepo({"out.dat": "actualsha"}, {})
    monkeypatch.setattr(hashStaleness, "fbManifestExists", lambda r: True)
    monkeypatch.setattr(
        hashStaleness, "_fdictReadManifestEntries",
        lambda r: {"out.dat": "manifestsha"},
    )
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        fake, ["out.dat"], {}, {"out.dat": 5},
    )
    assert setStale == {"out.dat"}
