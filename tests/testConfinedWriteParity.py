"""The two legs of the confined write answer every scenario the same way.

Source: ``vaibify/docker/confinedWrite.py`` (the container program) and
``vaibify/host/hostConfinedWrite.py`` (the host leg).

The container leg is text run elsewhere and the host leg is a Python
module; nothing but these tests holds them to one contract, as
``tests/testConfinedReadParity.py`` holds the readers. Each case runs
through BOTH legs over one real tree and compares one reduced outcome:
``("ok", bytes landed)`` or ``("refused", message)``.
"""

import os
import subprocess
import sys

import pytest

from vaibify.docker import confinedWrite
from vaibify.docker.confinedWrite import ContainerWriteRefusedError
from vaibify.host import hostConfinedWrite

I_PROGRAM_TIMEOUT_SECONDS = 30
BA_PAYLOAD = bytes(range(256)) * 300 + b"tail"


def _fsRealPath(pathAny):
    return os.path.realpath(str(pathAny))


def fdictBuildTree(pathTmp, sLeg):
    """One destination tree per leg, so the legs never see each other's files."""
    sRoot = _fsRealPath(pathTmp) + "/" + sLeg + "/root"
    sOutside = _fsRealPath(pathTmp) + "/" + sLeg + "/outside"
    os.makedirs(sRoot + "/nested")
    os.makedirs(sOutside)
    with open(sRoot + "/existing.txt", "wb") as fileExisting:
        fileExisting.write(b"old")
    os.symlink(sOutside + "/victim.txt", sRoot + "/linkToOutside")
    os.symlink(sOutside, sRoot + "/linkedDir")
    return {"sRoot": sRoot, "sOutside": sOutside}


def _ftOutcomeOfLanded(sPath):
    with open(sPath, "rb") as fileLanded:
        return ("ok", fileLanded.read())


class _ContainerLeg:
    sName = "container program"

    def ftWrite(self, sRoot, sPath, baContent):
        try:
            sProgram = confinedWrite.fsRenderConfinedWriteProgram(
                sPath, sAuthorizedRoot=sRoot,
            )
        except ValueError as error:
            # The renderer refuses a path outside the root before any
            # program runs; the host leg refuses the same path itself.
            return ("refused", str(error))
        resultProc = subprocess.run(
            [sys.executable, "-c", sProgram], input=baContent,
            capture_output=True, timeout=I_PROGRAM_TIMEOUT_SECONDS,
        )
        if resultProc.returncode == 0:
            return _ftOutcomeOfLanded(sPath)
        sMessage = resultProc.stderr.decode("utf-8", errors="replace").strip()
        if resultProc.returncode == confinedWrite.I_REFUSED_EXIT_CODE:
            return ("refused", sMessage)
        return ("failed", sMessage)


class _HostLeg:
    sName = "host writer"

    def ftWrite(self, sRoot, sPath, baContent):
        try:
            hostConfinedWrite.fsWriteFileInsideRoot(
                sRoot, sPath, iter([baContent[:100], baContent[100:]]),
            )
        except ContainerWriteRefusedError as error:
            return ("refused", str(error))
        return _ftOutcomeOfLanded(sPath)


LIST_LEGS = [_ContainerLeg(), _HostLeg()]


def _ftRunOnBothLegs(pathTmp, sRelative, baContent=BA_PAYLOAD):
    listOutcomes = []
    for leg in LIST_LEGS:
        dictTree = fdictBuildTree(pathTmp, leg.sName.replace(" ", ""))
        sOutcome, objAnswer = leg.ftWrite(
            dictTree["sRoot"], dictTree["sRoot"] + "/" + sRelative, baContent,
        )
        listOutcomes.append((leg.sName, sOutcome, objAnswer, dictTree))
    return listOutcomes


@pytest.mark.parametrize("sRelative", ["plain.bin", "nested/plain.bin"])
def testAFileLandsWholeOnBothLegs(tmp_path, sRelative):
    for sLeg, sOutcome, objAnswer, _ in _ftRunOnBothLegs(tmp_path, sRelative):
        assert (sOutcome, objAnswer) == ("ok", BA_PAYLOAD), sLeg


def testAnExistingFileIsReplacedOnBothLegs(tmp_path):
    for sLeg, sOutcome, objAnswer, _ in _ftRunOnBothLegs(
        tmp_path, "existing.txt", b"new",
    ):
        assert (sOutcome, objAnswer) == ("ok", b"new"), sLeg


@pytest.mark.parametrize("sRelative", [
    "linkToOutside", "linkedDir/planted.txt", "nested",
])
def testALinkOrADirectoryAtTheDestinationIsRefusedOnBothLegs(
    tmp_path, sRelative,
):
    for sLeg, sOutcome, sMessage, dictTree in _ftRunOnBothLegs(
        tmp_path, sRelative,
    ):
        assert sOutcome == "refused", (sLeg, sMessage)
        assert "symlink" in sMessage, (sLeg, sMessage)
        assert sorted(os.listdir(dictTree["sOutside"])) == [], sLeg


def testAPathOutsideTheRootIsRefusedOnBothLegs(tmp_path):
    for leg in LIST_LEGS:
        dictTree = fdictBuildTree(tmp_path, leg.sName.replace(" ", ""))
        sOutcome, sMessage = leg.ftWrite(
            dictTree["sRoot"], dictTree["sOutside"] + "/escaped.txt", b"x",
        )
        assert sOutcome == "refused", (leg.sName, sMessage)
        assert "not below" in sMessage, (leg.sName, sMessage)
        assert sorted(os.listdir(dictTree["sOutside"])) == [], leg.sName


def testNoTemporaryFileSurvivesARefusalOnEitherLeg(tmp_path):
    for sLeg, _, _, dictTree in _ftRunOnBothLegs(tmp_path, "nested"):
        assert not [
            sName for sName in os.listdir(dictTree["sRoot"])
            if sName.startswith(".vaibify-")
        ], sLeg
