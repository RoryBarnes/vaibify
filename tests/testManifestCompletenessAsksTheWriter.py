"""The manifest's completeness check must ask the writer what it pins.

They kept two copies of the rule and the copies disagreed. The writer
unions ``reproduce.sh`` into the set it pins once the script exists;
``flistDeclaredButMissingFromManifest`` asked only for the declared
artefacts. So a manifest written before the script existed, and never
rewritten, reported COMPLETE while omitting the one file a reproducer
actually executes.

This is the divergence-bug shape the repo's epistemics section names:
two descriptions of one fact, each internally consistent, and nothing
comparing them. The fix is not a third description — it is to delete
one, so "should be in the manifest" has a single definition that the
writer and the checker both call.
"""

import os
import subprocess

import pytest

from vaibify.reproducibility import manifestWriter
from vaibify.reproducibility.reproduceScriptGenerator import (
    S_REPRODUCE_SCRIPT_FILENAME,
)


def _fdictBuildWorkflow():
    return {
        "sWorkflowName": "project",
        "sPlotDirectory": "Plot",
        "listSteps": [{
            "sName": "Make Data",
            "sDirectory": "MakeData",
            "saDataCommands": ["python makeData.py"],
            "saOutputDataFiles": ["out.json"],
        }],
    }


@pytest.fixture
def sProjectRepo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    os.makedirs(os.path.join(str(tmp_path), "MakeData"), exist_ok=True)
    for sRelPath in ("MakeData/makeData.py", "MakeData/out.json"):
        with open(os.path.join(str(tmp_path), sRelPath), "w") as fileOut:
            fileOut.write("# fixture\n")
    return str(tmp_path)


def _fnWriteScript(sProjectRepo):
    with open(
        os.path.join(sProjectRepo, S_REPRODUCE_SCRIPT_FILENAME), "w",
    ) as fileOut:
        fileOut.write("#!/usr/bin/env bash\nexit 0\n")


@pytest.mark.falsification
def test_a_manifest_missing_the_reproduce_script_is_incomplete(
    sProjectRepo,
):
    """The exact divergence: written before the script, never rewritten.

    Kills: pointing ``flistDeclaredButMissingFromManifest`` back at
    ``_flistCollectManifestPaths`` instead of at
    ``flistManifestPathsToPin`` -- the checker stops asking about the
    file the writer pins, and the gap becomes invisible again.
    """
    dictWorkflow = _fdictBuildWorkflow()
    # A manifest written while no script existed pins the artefacts
    # only -- which is exactly what a legacy manifest looks like.
    manifestWriter.fnWriteManifest(sProjectRepo, dictWorkflow)
    _fnWriteScript(sProjectRepo)
    listMissing = manifestWriter.flistDeclaredButMissingFromManifest(
        sProjectRepo, dictWorkflow,
    )
    assert listMissing == [S_REPRODUCE_SCRIPT_FILENAME]


def test_the_writer_and_the_checker_agree_by_construction(sProjectRepo):
    """Whatever the writer pins is what the checker requires.

    Asserted over the SET rather than over one filename, so a future
    addition to either side cannot reintroduce the divergence without
    failing here.
    """
    dictWorkflow = _fdictBuildWorkflow()
    _fnWriteScript(sProjectRepo)
    manifestWriter.fnWriteManifest(sProjectRepo, dictWorkflow)
    setPinned = {
        dictEntry["sPath"]
        for dictEntry in manifestWriter.flistParseManifestLines(sProjectRepo)
    }
    setRequired = set(manifestWriter.flistManifestPathsToPin(
        sProjectRepo, dictWorkflow,
    ))
    assert setRequired <= setPinned
    assert manifestWriter.flistDeclaredButMissingFromManifest(
        sProjectRepo, dictWorkflow,
    ) == []


def test_a_project_with_no_script_yet_is_not_incomplete(sProjectRepo):
    """Presence-gated: the script is generated after the first write."""
    dictWorkflow = _fdictBuildWorkflow()
    manifestWriter.fnWriteManifest(sProjectRepo, dictWorkflow)
    assert S_REPRODUCE_SCRIPT_FILENAME not in (
        manifestWriter.flistManifestPathsToPin(sProjectRepo, dictWorkflow)
    )
    assert manifestWriter.flistDeclaredButMissingFromManifest(
        sProjectRepo, dictWorkflow,
    ) == []


def test_the_level_gate_follows_the_shared_definition(sProjectRepo):
    """fbVerifyManifestComplete must move with the one rule."""
    from vaibify.reproducibility import levelGates
    dictWorkflow = _fdictBuildWorkflow()
    manifestWriter.fnWriteManifest(sProjectRepo, dictWorkflow)
    _fnWriteScript(sProjectRepo)
    assert levelGates.fbVerifyManifestComplete(
        sProjectRepo, dictWorkflow,
    ) is False
    manifestWriter.fnWriteManifest(sProjectRepo, dictWorkflow)
    assert levelGates.fbVerifyManifestComplete(
        sProjectRepo, dictWorkflow,
    ) is True


# ── The envelope is pinned, and a comment elsewhere depends on it ──


def test_the_envelope_files_are_pinned_when_they_exist(sProjectRepo):
    """reproduce.sh runs `sha256sum -c MANIFEST.sha256` at the end.

    Until 2026-09-14 that integrity check covered the results and not
    the environment that produced them: the digest a reader most needs
    to trust -- the one in environment.json naming the image -- was
    the one nothing pinned.
    """
    dictWorkflow = _fdictBuildWorkflow()
    os.makedirs(os.path.join(sProjectRepo, ".vaibify"), exist_ok=True)
    for sRelPath in (
        ".vaibify/environment.json", "requirements.lock", "Dockerfile",
    ):
        with open(os.path.join(sProjectRepo, sRelPath), "w") as fileOut:
            fileOut.write("{}\n")
    _fnWriteScript(sProjectRepo)
    listPinned = manifestWriter.flistManifestPathsToPin(
        sProjectRepo, dictWorkflow,
    )
    for sRelPath in (
        ".vaibify/environment.json", "requirements.lock", "Dockerfile",
        S_REPRODUCE_SCRIPT_FILENAME,
    ):
        assert sRelPath in listPinned, sRelPath


def test_the_archive_record_writers_repin_justification_holds(
    sProjectRepo,
):
    """``imageDeposit.fdictStampArchiveRecord`` re-pins the manifest
    because writing the deposit record changes a pinned file. That
    sentence was FALSE when it was written, and is only true while
    ``environment.json`` stays in the pinned set — so the dependency
    is asserted here rather than left in a comment nobody can check.
    """
    dictWorkflow = _fdictBuildWorkflow()
    os.makedirs(os.path.join(sProjectRepo, ".vaibify"), exist_ok=True)
    with open(
        os.path.join(sProjectRepo, ".vaibify/environment.json"), "w",
    ) as fileOut:
        fileOut.write("{}\n")
    assert ".vaibify/environment.json" in (
        manifestWriter.flistManifestPathsToPin(sProjectRepo, dictWorkflow)
    ), (
        "environment.json left the pinned set, so imageDeposit."
        "fdictStampArchiveRecord's re-pin now protects nothing and "
        "its docstring asserts something false again"
    )


def test_the_manifest_never_pins_itself_or_the_attestation(sProjectRepo):
    """Both would be circular, and for different reasons.

    The manifest cannot record its own hash. The attestation is
    written AFTER the manifest and records the manifest's digest
    inside itself, so pinning it would have each claiming to know the
    other.
    """
    dictWorkflow = _fdictBuildWorkflow()
    os.makedirs(os.path.join(sProjectRepo, ".vaibify"), exist_ok=True)
    for sRelPath in ("MANIFEST.sha256", ".vaibify/l3_attestation.json"):
        with open(os.path.join(sProjectRepo, sRelPath), "w") as fileOut:
            fileOut.write("{}\n")
    listPinned = manifestWriter.flistManifestPathsToPin(
        sProjectRepo, dictWorkflow,
    )
    assert "MANIFEST.sha256" not in listPinned
    assert ".vaibify/l3_attestation.json" not in listPinned
