"""Tests for the SHA-256 manifest path in hashStaleness."""

import hashlib
import os

from vaibify.gui import hashStaleness


_MANIFEST_FILENAME = "MANIFEST.sha256"
_MANIFEST_HEADER = "# SHA-256 manifest of workflow outputs\n"


def _fnWriteFile(sRoot, sRelPath, sContent=""):
    """Create a file inside sRoot with the given text contents."""
    sAbsPath = os.path.join(sRoot, *sRelPath.split("/"))
    os.makedirs(os.path.dirname(sAbsPath), exist_ok=True)
    with open(sAbsPath, "w", encoding="utf-8") as handle:
        handle.write(sContent)


def _fsSha256(sContent):
    """Return SHA-256 hex digest of the given text content."""
    return hashlib.sha256(sContent.encode("utf-8")).hexdigest()


def _fnWriteManifest(sRoot, listEntries):
    """Persist a manifest file with header + sorted shasum lines."""
    sPath = os.path.join(sRoot, _MANIFEST_FILENAME)
    with open(sPath, "w", encoding="utf-8") as handle:
        handle.write(_MANIFEST_HEADER)
        for sHash, sRelPath in listEntries:
            handle.write(f"{sHash}  {sRelPath}\n")


def test_empty_manifest_yields_empty_stale_set(tmp_path):
    """A manifest with only a header reports nothing stale."""
    _fnWriteManifest(str(tmp_path), [])
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        str(tmp_path), ["step/out.csv"], {},
    )
    assert setStale == set()


def test_all_files_match_yields_empty_stale_set(tmp_path):
    """Files whose content matches the manifest are not stale."""
    _fnWriteFile(str(tmp_path), "step/out.csv", "alpha,beta\n")
    _fnWriteFile(str(tmp_path), "step/plot.pdf", "fake-pdf")
    _fnWriteManifest(str(tmp_path), [
        (_fsSha256("alpha,beta\n"), "step/out.csv"),
        (_fsSha256("fake-pdf"), "step/plot.pdf"),
    ])
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        str(tmp_path),
        ["step/out.csv", "step/plot.pdf"],
        {},
    )
    assert setStale == set()


def test_one_file_mutated_appears_in_stale_set(tmp_path):
    """A file whose content drifted from the manifest is stale."""
    _fnWriteFile(str(tmp_path), "step/out.csv", "drifted\n")
    _fnWriteManifest(str(tmp_path), [
        (_fsSha256("original\n"), "step/out.csv"),
    ])
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        str(tmp_path), ["step/out.csv"], {},
    )
    assert setStale == {"step/out.csv"}


def test_file_missing_on_disk_is_stale(tmp_path):
    """Files listed in the manifest but absent from disk are stale."""
    _fnWriteManifest(str(tmp_path), [
        (_fsSha256("ghost"), "step/ghost.csv"),
    ])
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        str(tmp_path), ["step/ghost.csv"], {},
    )
    assert setStale == {"step/ghost.csv"}


def test_untracked_file_in_paths_is_silently_skipped(tmp_path):
    """Paths absent from the manifest never appear in the stale set."""
    _fnWriteFile(str(tmp_path), "step/out.csv", "tracked")
    _fnWriteFile(str(tmp_path), "step/extra.csv", "untracked")
    _fnWriteManifest(str(tmp_path), [
        (_fsSha256("tracked"), "step/out.csv"),
    ])
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        str(tmp_path),
        ["step/out.csv", "step/extra.csv"],
        {},
    )
    assert setStale == set()


def test_missing_manifest_returns_empty_set(tmp_path):
    """When MANIFEST.sha256 is absent the helper short-circuits."""
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        str(tmp_path), ["step/out.csv"], {},
    )
    assert setStale == set()
    assert hashStaleness.fbManifestExists(str(tmp_path)) is False


def test_manifest_exists_true_when_file_present(tmp_path):
    """fbManifestExists reports True iff MANIFEST.sha256 is a file."""
    _fnWriteManifest(str(tmp_path), [])
    assert hashStaleness.fbManifestExists(str(tmp_path)) is True


def test_empty_repo_root_returns_empty_set():
    """Empty repo root means no manifest path to consult."""
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        "", ["step/out.csv"], {},
    )
    assert setStale == set()


def test_manifest_with_escaped_path_is_handled(tmp_path):
    """A GNU-escaped path round-trips through hashStaleness's reader."""
    from vaibify.reproducibility.manifestWriter import fnWriteManifest
    sRelativePath = "data/weird\\name.csv"
    _fnWriteFile(str(tmp_path), sRelativePath, "payload")
    dictWorkflow = {
        "listSteps": [
            {
                "sName": "S1",
                "saOutputFiles": [],
                "saPlotFiles": [],
                "saDataFiles": [sRelativePath],
            },
        ],
    }
    fnWriteManifest(str(tmp_path), dictWorkflow)
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        str(tmp_path), [sRelativePath], {},
    )
    assert setStale == set()


def test_corrupt_manifest_is_treated_as_absent(tmp_path):
    """A malformed manifest line yields an empty stale set (defensive)."""
    pathManifest = tmp_path / _MANIFEST_FILENAME
    pathManifest.write_text(
        "# header\n"
        "this line has no two-space separator\n",
        encoding="utf-8",
    )
    setStale = hashStaleness.fsetStaleOutputsAgainstManifest(
        str(tmp_path), ["step/out.csv"], {},
    )
    assert setStale == set()
