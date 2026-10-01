"""The entrypoint's root phase must not resolve commands through a PATH the
container user can write to.

Source: ``vaibify/containerImage/entrypoint.sh``.

The image's ``ENV PATH`` leads with the container user's ``~/.local/bin``
and the container starts as root, so before this contract a program planted
there as ``chown``, ``git`` or ``find`` ran as root on the next start.
``/etc/vaibify/binaries.env`` named further directories that the root phase
prepended as well. The root phase now pins a fixed system PATH first, and
``binaries.env`` is only recorded in the profile file the container user
sources after the privilege drop.
"""

import os
import re
import subprocess


_S_ENTRYPOINT = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "vaibify",
        "containerImage", "entrypoint.sh",
    )
)


def _fsReadFunctionBody(sFunctionName):
    with open(_S_ENTRYPOINT, encoding="utf-8") as fileEntrypoint:
        sSource = fileEntrypoint.read()
    matchBody = re.search(
        r"^" + sFunctionName + r"\(\) \{\n(.*?)^\}\n", sSource,
        re.DOTALL | re.MULTILINE,
    )
    assert matchBody, sFunctionName + " is not defined in entrypoint.sh"
    return matchBody.group(1)


def _fsReadMainBlock():
    with open(_S_ENTRYPOINT, encoding="utf-8") as fileEntrypoint:
        sSource = fileEntrypoint.read()
    return sSource[sSource.index('if [[ "${BASH_SOURCE[0]}" == "${0}" ]]'):]


def _fsRunHelperScript(sBody, dictEnvironment=None):
    sScript = (
        "set +e\n"
        "source " + _S_ENTRYPOINT + "\n"
        + sBody
    )
    dictFull = dict(os.environ)
    dictFull.update(dictEnvironment or {})
    return subprocess.run(
        ["bash", "-c", sScript], capture_output=True, text=True,
        env=dictFull,
    )


def testRootPhasePinsTheFixedPathBeforeAnyOtherStep():
    sBody = _fsReadFunctionBody("fnRunRootPhase")
    listLines = [
        sLine.strip() for sLine in sBody.splitlines() if sLine.strip()
    ]
    assert listLines[0] == "fnUseFixedSystemPath"


def testFixedPathHoldsOnlySystemDirectories():
    sBody = _fsReadFunctionBody("fnUseFixedSystemPath")
    matchPath = re.search(r'export PATH="([^"]+)"', sBody)
    assert matchPath
    listDirectories = matchPath.group(1).split(":")
    assert listDirectories
    for sDirectory in listDirectories:
        assert sDirectory.startswith(("/usr/", "/sbin", "/bin")), sDirectory
    assert "$" not in matchPath.group(1)


def testPlantedUserBinaryIsNotResolvedAfterThePathIsPinned(tmp_path):
    sPlantedDirectory = tmp_path / "userBin"
    sPlantedDirectory.mkdir()
    sPlanted = sPlantedDirectory / "chown"
    sPlanted.write_text("#!/bin/sh\necho PLANTED_RAN\n")
    sPlanted.chmod(0o755)
    sBody = "fnUseFixedSystemPath\ncommand -v chown\n"
    resultProc = _fsRunHelperScript(
        sBody,
        {"PATH": str(sPlantedDirectory) + ":" + os.environ["PATH"]},
    )
    assert resultProc.returncode == 0, resultProc.stderr
    assert str(sPlantedDirectory) not in resultProc.stdout
    assert resultProc.stdout.strip().startswith("/")


def testEveryRootPhaseStepSeesTheFixedPath(tmp_path):
    sBody = (
        'for sStep in fnConfigureGit fnLoadBinariesEnv '
        'fnMigrateWorkspaceOwnership; do\n'
        '    eval "${sStep}() { echo \\"${sStep}=\\${PATH}\\"; }"\n'
        'done\n'
        'fnRunRootPhase\n'
    )
    resultProc = _fsRunHelperScript(
        sBody, {"PATH": "/home/user/.local/bin:" + os.environ["PATH"]},
    )
    listReports = resultProc.stdout.strip().splitlines()
    assert len(listReports) == 3, resultProc.stdout + resultProc.stderr
    for sReport in listReports:
        assert ".local" not in sReport, sReport


def testBinariesEnvIsRecordedNeverExportedByTheRootPhase():
    sBody = _fsReadFunctionBody("fnLoadBinariesEnv")
    assert "export PATH=" not in re.sub(r'echo "export PATH=.*', "", sBody)
    assert 'export "${sVarName}' not in sBody
    assert ">> \"${sProfilePath}\"" in sBody


def testRootPhaseHandsTheContainerPathBackBeforeDroppingPrivileges():
    sMain = _fsReadMainBlock()
    iRootPhase = sMain.index("    fnRunRootPhase\n")
    iRestore = sMain.index('export PATH="${sContainerPath}"')
    iDrop = sMain.index('exec "${sGosuProgram}"')
    assert sMain.index('sContainerPath="${PATH}"') < iRootPhase
    assert iRootPhase < sMain.index("command -v gosu") < iRestore < iDrop
    assert "exec gosu" not in sMain


def testWorkspacePhaseStillSourcesTheRecordedBinaries():
    sBody = _fsReadFunctionBody("fnRunWorkspacePhase")
    assert "fnSourceBinariesInEnv" in sBody
