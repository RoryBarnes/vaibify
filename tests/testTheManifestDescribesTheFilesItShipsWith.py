"""A manifest that misdescribes its own files is worse than none.

The envelope generator wrote ``MANIFEST.sha256`` FIRST and then
rewrote two of the files it had just pinned -- ``requirements.lock``
(tier 2) and ``.vaibify/environment.json`` (tier 3). Every regeneration
therefore produced a manifest that was already wrong about two of its
own entries. Measured on a real project, 2026-09-16: the manifest was
written at 23:58:40 and the two files at 23:58:51 and 23:58:52.

Nothing on the dashboard could see it. ``fbVerifyManifestComplete``
asks whether every declared path is LISTED and nothing about whether
the listed hashes are TRUE -- the same blind spot the Dependency-lock
row had, where "every entry is hashed" said nothing about whether the
entries described the image. The one thing in the product that asked
the real question was the Level 3 rerun, which a researcher reaches
only by spending a full re-run of their workflow. It reported "24 of
26 re-derived files matched" and named those two paths, and the two
answers a researcher could act on -- Verify again, Regenerate -- both
recreated the skew. Level 3 was unreachable for every project vaibify
had ever written an envelope for.

So there are two guards here, and they fail on different mutations: the
generator writes the manifest LAST, and the gate asks whether the
hashes are true.
"""

import hashlib
import subprocess
from unittest.mock import patch

import pytest

from vaibify.reproducibility import dataArchiver, levelGates, manifestWriter


_S_LOCK_FILENAME = "requirements.lock"
_S_ENVIRONMENT_RELPATH = ".vaibify/environment.json"


def _fdictWorkflow():
    """A single-step workflow declaring one output."""
    return {
        "listSteps": [
            {"sName": "OnlyStep", "saPlotFiles": [],
             "saOutputDataFiles": ["out.csv"]},
        ],
    }


def _ffnFakeUvWritesLock(pathRepo):
    """Return a side effect that writes a lock, as the real tier does."""
    def _fnRun(*args, **kwargs):
        (pathRepo / _S_LOCK_FILENAME).write_text(
            "alpha==1.0 \\\n    --hash=sha256:" + ("0" * 64) + "\n",
        )
        return subprocess.CompletedProcess(
            args=args[0] if args else [], returncode=0,
            stdout="", stderr="",
        )
    return _fnRun


def _fnGenerateEnvelopeWithEveryTier(pathRepo):
    """Run the real generator with both container tiers stubbed to write."""
    with patch(
        "vaibify.reproducibility.dependencyPinning.fbIsUvAvailable",
        return_value=True,
    ), patch(
        "vaibify.reproducibility.dependencyPinning.subprocess.run",
        side_effect=_ffnFakeUvWritesLock(pathRepo),
    ), patch(
        "vaibify.reproducibility.environmentSnapshot."
        "fdictCaptureContainerImageDigest",
        return_value={"sContainerName": "vaibify-test",
                      "sImageDigest": "fake@sha256:" + ("a" * 64)},
    ), patch(
        "vaibify.reproducibility.environmentSnapshot."
        "fdictCaptureSystemTools",
        return_value={"sPython": "Fake 3.12", "sGcc": None,
                      "sLibc": None, "sOsRelease": None},
    ):
        dataArchiver.fdictGenerateReproducibilityEnvelope(
            str(pathRepo), _fdictWorkflow(), sContainerName="vaibify-test",
        )


@pytest.mark.falsification
def test_a_freshly_written_envelope_describes_its_own_files(tmp_path):
    """Every pinned hash is the file's bytes the moment writing stops.

    Asserted over EVERY entry rather than the two that bit us: the
    defect is the ordering, and naming the two paths would let a
    fourth tier be added below the manifest with the same result.

    Kills: writing the manifest tier before the lock and environment
    tiers -- the shipped order, which made this assertion false for
    exactly those two entries on every project.
    """
    (tmp_path / "out.csv").write_text("alpha,beta\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    _fnGenerateEnvelopeWithEveryTier(tmp_path)

    listEntries = manifestWriter.flistParseManifestLines(str(tmp_path))
    assert listEntries, "the generator wrote no manifest to check"
    listPinned = [dictEntry["sPath"] for dictEntry in listEntries]
    for sPath in (_S_LOCK_FILENAME, _S_ENVIRONMENT_RELPATH):
        assert sPath in listPinned, (
            f"{sPath} is not pinned, so this test cannot observe the "
            f"ordering it exists for: {listPinned}"
        )
    listWrong = []
    for dictEntry in listEntries:
        baContent = (tmp_path / dictEntry["sPath"]).read_bytes()
        if hashlib.sha256(baContent).hexdigest() != dictEntry["sExpected"]:
            listWrong.append(dictEntry["sPath"])
    assert not listWrong, (
        "the envelope generator wrote a manifest that misdescribes "
        "files it wrote itself; a Level 3 rerun will report these as "
        f"diverged and no button will clear it: {listWrong}"
    )


@pytest.mark.falsification
def test_a_manifest_that_misdescribes_a_file_blocks_level_three(tmp_path):
    """The gate asks whether the hashes are TRUE, not merely present.

    Driven by changing a pinned file AFTER the manifest is written,
    which is precisely the state the ordering bug produced, and read
    back through the readiness flag the dashboard renders rather than
    the helper alone.

    Kills: making ``fbVerifyManifestMatchesTheFiles`` answer True --
    the state the product shipped in, where a manifest describing none
    of its files was green on every surface until a rerun was spent.
    """
    (tmp_path / "out.csv").write_text("alpha,beta\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    _fnGenerateEnvelopeWithEveryTier(tmp_path)
    assert levelGates.fbVerifyManifestMatchesTheFiles(str(tmp_path))

    (tmp_path / _S_LOCK_FILENAME).write_text("alpha==2.0\n")
    listContradicting = levelGates.flistManifestEntriesContradictingTheFiles(
        str(tmp_path),
    )
    assert [dictEntry["sPath"] for dictEntry in listContradicting] == [
        _S_LOCK_FILENAME,
    ], listContradicting
    assert not levelGates.fbVerifyManifestMatchesTheFiles(str(tmp_path))
    # The FLAG the dashboard renders, not the scalar gate beside it:
    # this fixture is unready for other reasons (no determinism
    # declaration, no binaries), so asserting the scalar would pass
    # whether or not the manifest was ever consulted -- which it did,
    # and the mutation survived it.
    dictFlags = levelGates._fdictCollectL3ReadinessFlags(
        _fdictWorkflow(), str(tmp_path), True,
    )
    assert dictFlags["bManifestMatchesTheFiles"] is False, dictFlags
    assert dictFlags["bManifestComplete"] is True, (
        "coverage went false too, so this says nothing about the new "
        f"question: {dictFlags}"
    )


def test_a_hash_nobody_could_take_is_not_a_contradiction(tmp_path):
    """Unchecked is never red, here as everywhere else.

    A pinned path the adapter cannot hash -- deleted, or simply not
    sampled by the poll's one-exec snapshot -- yields ``sActual:
    None``. Reporting that as a contradiction would redden the
    Manifest row for every project whose poll did not happen to sample
    an entry, which is the inverse of the bug this gate exists for.
    """
    (tmp_path / "out.csv").write_text("alpha,beta\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    _fnGenerateEnvelopeWithEveryTier(tmp_path)

    (tmp_path / _S_LOCK_FILENAME).unlink()
    assert levelGates.flistManifestEntriesContradictingTheFiles(
        str(tmp_path),
    ) == []
    assert levelGates.fbVerifyManifestMatchesTheFiles(str(tmp_path))
