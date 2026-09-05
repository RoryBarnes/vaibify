"""Regenerating the envelope says what it changed in the manifest.

``MANIFEST.sha256`` is the record of a scientific result: for every
pinned file, the bytes the author is claiming. Regenerating the
envelope rewrites it in place, and the response said only which
readiness gaps were still open afterwards — a description of the state
it left behind, never of what it moved. The file is tracked, so the
history is not lost, but a researcher who wanted to know which pinned
hashes had changed found out in a ``git diff`` or not at all. Silently
replacing the record of a result is the wrong default for a product
whose premise is that a researcher always knows what happened.

CHANGED is the interesting class and is reported separately for that
reason: a path joining or leaving the pinned set is bookkeeping, while
a path whose recorded hash moved is a claim that moved.
"""

from vaibify.reproducibility.manifestWriter import (
    fdictCompareManifestEntries,
)


def _flistEntries(*tPairs):
    """Return parsed-manifest-shaped entries from (path, hash) pairs."""
    return [
        {"sPath": sPath, "sExpected": sHash} for sPath, sHash in tPairs
    ]


def test_an_unchanged_manifest_reports_nothing():
    listSame = _flistEntries(("a.csv", "aa"), ("b.pdf", "bb"))
    assert fdictCompareManifestEntries(listSame, listSame) == {
        "listAdded": [], "listRemoved": [], "listChanged": [],
    }


def test_a_moved_hash_is_reported_as_changed_not_as_add_plus_remove():
    """The three classes are distinct, and only one is a moved claim.

    A path present on both sides whose hash differs is the case that
    matters: the file is still pinned, and what it is pinned TO is
    different. Reporting it as a removal plus an addition would bury
    that inside the bookkeeping.
    """
    dictDelta = fdictCompareManifestEntries(
        _flistEntries(("figure.pdf", "old")),
        _flistEntries(("figure.pdf", "new")),
    )
    assert dictDelta["listChanged"] == ["figure.pdf"]
    assert dictDelta["listAdded"] == []
    assert dictDelta["listRemoved"] == []


def test_additions_and_removals_are_kept_apart():
    dictDelta = fdictCompareManifestEntries(
        _flistEntries(("gone.csv", "aa"), ("kept.csv", "bb")),
        _flistEntries(("kept.csv", "bb"), ("new.csv", "cc")),
    )
    assert dictDelta["listAdded"] == ["new.csv"]
    assert dictDelta["listRemoved"] == ["gone.csv"]
    assert dictDelta["listChanged"] == []


def test_a_first_regeneration_compares_against_nothing():
    """An absent manifest is the ordinary before-state, not a fault.

    The delta is a report ABOUT the write, so a project that had no
    manifest reports every pinned path as added rather than failing
    the route that was about to create one.
    """
    dictDelta = fdictCompareManifestEntries(
        [], _flistEntries(("a.csv", "aa"), ("b.csv", "bb")),
    )
    assert dictDelta["listAdded"] == ["a.csv", "b.csv"]


def test_the_route_reports_the_delta_across_the_regeneration(tmp_path):
    """Read on BOTH sides of the write, or it describes nothing.

    A delta computed from one reading is not a delta. This drives the
    real handler with a generator that actually rewrites the manifest
    file between the two reads, and asserts the moved hash comes back
    — a handler that read the manifest once, or read it only
    afterwards, reports an empty change set here.
    """
    from unittest.mock import patch

    from vaibify.gui.routes import reproducibilityRoutes
    from vaibify.reproducibility.repoFiles import HostRepoFiles

    pathManifest = tmp_path / "MANIFEST.sha256"
    pathManifest.write_text(
        "a" * 64 + "  figure.pdf\n", encoding="utf-8",
    )
    filesRepo = HostRepoFiles(str(tmp_path))

    def _fdictRewriteTheManifest(*args, **kwargs):
        pathManifest.write_text(
            "b" * 64 + "  figure.pdf\n" + "c" * 64 + "  table.csv\n",
            encoding="utf-8",
        )
        return {"bWrote": True}

    with patch(
        "vaibify.reproducibility.dataArchiver."
        "fdictGenerateReproducibilityEnvelope",
        side_effect=_fdictRewriteTheManifest,
    ), patch.object(
        reproducibilityRoutes, "fdictL3ReadinessGaps", return_value={},
    ):
        dictResult = (
            reproducibilityRoutes._fdictGenerateEnvelopeThenReadGaps(
                filesRepo, {}, "probeContainer",
            )
        )
    assert dictResult["dictManifestDelta"]["listChanged"] == ["figure.pdf"]
    assert dictResult["dictManifestDelta"]["listAdded"] == ["table.csv"]
