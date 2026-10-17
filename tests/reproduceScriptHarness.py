"""Run the container half of a generated ``reproduce.sh`` under real bash.

The heredoc body is what ``docker run ... bash -s`` executes inside the
image. Here it runs on this machine instead: the reproduction root
(``/work``) is replaced by a real repository, ``pip`` is a stand-in on
PATH that exits as told (the lock is not under test), and the assessor is
the file the generator wrote beside the script, run by this machine's
``python3`` as the image runs its own. Nothing else is replaced.

The exit trap is extended to copy the run's scratch and evidence
directories out before it removes them, so a test can read the
invocation ledger and the report the script was graded by. The copy is
an observation only: the removal it precedes is unchanged.
"""

import glob
import json
import os
import subprocess

from vaibify.reproducibility.reproduceScriptGenerator import (
    _S_HEREDOC_DELIMITER,
    S_REPRODUCE_SCRIPT_FILENAME,
    S_REPRODUCTION_REPO_ROOT,
    ftRenderReproductionFiles,
)
from tests.rerunAssessmentHarness import fnPinManifest, fnWriteText


_S_EXIT_TRAP_OPENING = "trap 'rm -rf "


def fsExtractContainerHalf(sScript):
    """Return the body ``reproduce.sh`` hands the container's bash."""
    sOpening = f"<<'{_S_HEREDOC_DELIMITER}'\n"
    sClosing = f"\n{_S_HEREDOC_DELIMITER}\n"
    return sScript.split(sOpening, 1)[1].split(sClosing, 1)[0] + "\n"


def fnPublishForTheScript(sRoot, dictWorkflow):
    """Write the script and its assessor as the generator does, then pin."""
    for sRelative, sText in ftRenderReproductionFiles(dictWorkflow):
        fnWriteText(sRoot, sRelative, sText)
    fnPinManifest(sRoot, dictWorkflow)


def fsKeepRunDirectories(sBody, sKeep):
    """Return the body with its exit trap copying the run directories out.

    The copies are opened to every user, because ``mktemp -d`` makes the
    originals private to the uid that ran the script, and a test reading
    them from the host may be another uid (a container's user is 1000;
    a CI runner's is not).
    """
    if sBody.count(_S_EXIT_TRAP_OPENING) != 1:
        raise AssertionError("the container half has no single exit trap")
    return sBody.replace(_S_EXIT_TRAP_OPENING, (
        "trap 'cp -R \"$sVaibifyScratch\" \"$sVaibifyEvidence\" "
        f"\"{sKeep}\"/; chmod -R a+rwX \"{sKeep}\"; rm -rf "), 1)


def fnInstallFakePip(sBin, iExitCode):
    """Put a ``pip`` on PATH that installs nothing and exits as told."""
    os.makedirs(sBin, exist_ok=True)
    sPath = os.path.join(sBin, "pip")
    with open(sPath, "w", encoding="utf-8") as fileOut:
        fileOut.write(f"#!/usr/bin/env bash\nexit {int(iExitCode)}\n")
    os.chmod(sPath, 0o755)


def fdictBuildRunEnvironment(sBase, sBin):
    """Return this machine's environment with no reproduction variables."""
    dictEnvironment = dict(os.environ)
    for sName in ("SOURCE_DATE_EPOCH", "PYTEST_ADDOPTS", "MPLCONFIGDIR"):
        dictEnvironment.pop(sName, None)
    sTemporary = os.path.join(sBase, "tmp")
    os.makedirs(sTemporary, exist_ok=True)
    dictEnvironment["TMPDIR"] = sTemporary
    dictEnvironment["PATH"] = sBin + os.pathsep + dictEnvironment["PATH"]
    return dictEnvironment


def flistReadKeptLedger(sKeep):
    """Return ``[(sInvocationId, iExitStatus), ...]`` from the kept ledger."""
    listPaths = glob.glob(os.path.join(sKeep, "vaibifyEvidence.*",
                                       "invocations.tsv"))
    if not listPaths:
        return None
    with open(listPaths[0], encoding="utf-8") as fileIn:
        return [(sLine.split("\t")[0], int(sLine.split("\t")[1]))
                for sLine in fileIn.read().splitlines() if sLine]


def fdictReadKeptReport(sKeep):
    """Return the assessment report the run wrote, or None."""
    listPaths = glob.glob(os.path.join(sKeep, "vaibifyScratch.*",
                                       "assessment.json"))
    if not listPaths:
        return None
    with open(listPaths[0], encoding="utf-8") as fileIn:
        return json.load(fileIn)


def fdictRunContainerHalf(sRoot, sBase, iPipExit=0, fsEditBody=None):
    """Run the repository's own ``reproduce.sh`` body; return what it did.

    Returns ``{iExitCode, sStdout, sStderr, listLedger, dictReport}``.
    ``fsEditBody`` receives the extracted body before the run, for a test
    that injects a failure into it.
    """
    with open(os.path.join(sRoot, S_REPRODUCE_SCRIPT_FILENAME),
              encoding="utf-8") as fileIn:
        sBody = fsExtractContainerHalf(fileIn.read())
    if fsEditBody is not None:
        sBody = fsEditBody(sBody)
    sBody = sBody.replace(S_REPRODUCTION_REPO_ROOT, sRoot)
    sKeep = os.path.join(sBase, "kept")
    os.makedirs(sKeep)
    sBodyPath = os.path.join(sBase, "containerHalf.sh")
    with open(sBodyPath, "w", encoding="utf-8") as fileOut:
        fileOut.write(fsKeepRunDirectories(sBody, sKeep))
    sBin = os.path.join(sBase, "bin")
    fnInstallFakePip(sBin, iPipExit)
    completed = subprocess.run(
        ["bash", sBodyPath], cwd=sRoot, capture_output=True, text=True,
        env=fdictBuildRunEnvironment(sBase, sBin), timeout=600)
    return {
        "iExitCode": completed.returncode, "sStdout": completed.stdout,
        "sStderr": completed.stderr, "listLedger": flistReadKeptLedger(sKeep),
        "dictReport": fdictReadKeptReport(sKeep),
    }
