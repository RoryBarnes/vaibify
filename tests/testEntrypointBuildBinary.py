"""Tests for fnBuildSingleBinary in docker/entrypoint.sh.

Regression coverage for the vplanet-private case: a fork whose
Makefile produces a binary under a different name than the repo
must not trip the post-build sanity check. The check is now
agnostic to filename and only verifies that ``make opt`` produced
*some* executable artifact under ``bin/``.

Tests source entrypoint.sh in a subshell with WORKSPACE pointed at
a tmp dir and a fake ``make`` placed first on PATH; this is the
same pattern as testEntrypointReadinessMarker.py.
"""

import json
import os
import subprocess


_S_ENTRYPOINT = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "docker", "entrypoint.sh",
    )
)


def _fnInstallFakeMake(sFakeBinDir, sScriptBody, iExitCode=0):
    """Place a fake ``make`` executable on PATH for the test subshell.

    ``sScriptBody`` is bash that runs in lieu of a real build — it
    creates whatever bin/ contents the test wants. The returned
    exit code lets tests simulate a successful or failing build.
    """
    os.makedirs(sFakeBinDir, exist_ok=True)
    sMakePath = os.path.join(sFakeBinDir, "make")
    sBody = (
        "#!/bin/bash\n"
        + sScriptBody + "\n"
        + f"exit {iExitCode}\n"
    )
    with open(sMakePath, "w") as fileHandle:
        fileHandle.write(sBody)
    os.chmod(sMakePath, 0o755)


def _fsRunWithFakeMake(sWorkspace, sFakeBinDir, sBody):
    """Source entrypoint.sh and run sBody with fake make on PATH."""
    sScript = (
        "set +e\n"
        f"WORKSPACE={sWorkspace}\n"
        "export WORKSPACE\n"
        f"PATH={sFakeBinDir}:$PATH\n"
        "export PATH\n"
        f"source {_S_ENTRYPOINT}\n"
        + sBody
    )
    return subprocess.run(
        ["bash", "-c", sScript],
        capture_output=True, text=True,
    )


def _fdictReadMarker(sWorkspace):
    """Read and parse the readiness marker from the temp workspace."""
    sPath = os.path.join(sWorkspace, ".vaibify", ".entrypoint_ready")
    with open(sPath) as fileHandle:
        return json.loads(fileHandle.read())


def _sBuildBody(sName, sRepoDir):
    """Build the bash body that exercises fnBuildSingleBinary + marker."""
    return (
        f'fnBuildSingleBinary "{sName}" "{sRepoDir}"\n'
        f'iResult=$?\n'
        f'fnWriteReadinessMarker "ok" ""\n'
        f'echo "RESULT=${{iResult}}"\n'
        f'echo "PATH_CHECK=${{PATH}}"\n'
    )


def test_build_succeeds_with_default_named_binary(tmp_path):
    """Default case: bin/<repo-name> exists -> success, PATH exported."""
    sWorkspace = str(tmp_path)
    sRepoDir = os.path.join(sWorkspace, "vplanet")
    os.makedirs(sRepoDir, exist_ok=True)
    os.makedirs(os.path.join(sWorkspace, ".vaibify"), exist_ok=True)
    sFakeBinDir = os.path.join(sWorkspace, "fakebin")
    sBuiltBin = f"{sRepoDir}/bin/vplanet"
    _fnInstallFakeMake(
        sFakeBinDir,
        f"mkdir -p {sRepoDir}/bin && touch {sBuiltBin} "
        f"&& chmod +x {sBuiltBin}",
    )
    resultProc = _fsRunWithFakeMake(
        sWorkspace, sFakeBinDir, _sBuildBody("vplanet", sRepoDir),
    )
    assert "RESULT=0" in resultProc.stdout
    assert f"PATH_CHECK={sRepoDir}/bin" in resultProc.stdout
    dictMarker = _fdictReadMarker(sWorkspace)
    assert dictMarker["saWarnings"] == []


def test_build_succeeds_with_renamed_binary(tmp_path):
    """Regression for vplanet-private: a fork whose Makefile produces a
    differently-named binary must not trigger a false-alarm warning.

    The repo is named ``vplanet-private`` but the Makefile produces
    ``bin/vplanet`` (the upstream name). The old check looked for
    ``bin/vplanet-private`` and warned spuriously; the new check
    accepts any executable under ``bin/``.
    """
    sWorkspace = str(tmp_path)
    sRepoDir = os.path.join(sWorkspace, "vplanet-private")
    os.makedirs(sRepoDir, exist_ok=True)
    os.makedirs(os.path.join(sWorkspace, ".vaibify"), exist_ok=True)
    sFakeBinDir = os.path.join(sWorkspace, "fakebin")
    sBuiltBin = f"{sRepoDir}/bin/vplanet"
    _fnInstallFakeMake(
        sFakeBinDir,
        f"mkdir -p {sRepoDir}/bin && touch {sBuiltBin} "
        f"&& chmod +x {sBuiltBin}",
    )
    resultProc = _fsRunWithFakeMake(
        sWorkspace, sFakeBinDir,
        _sBuildBody("vplanet-private", sRepoDir),
    )
    assert "RESULT=0" in resultProc.stdout
    assert f"PATH_CHECK={sRepoDir}/bin" in resultProc.stdout
    dictMarker = _fdictReadMarker(sWorkspace)
    assert dictMarker["saWarnings"] == []


def test_build_warns_when_bin_dir_empty(tmp_path):
    """make exits 0 but bin/ is empty -> warning, no PATH export."""
    sWorkspace = str(tmp_path)
    sRepoDir = os.path.join(sWorkspace, "halfbuilt")
    os.makedirs(sRepoDir, exist_ok=True)
    os.makedirs(os.path.join(sWorkspace, ".vaibify"), exist_ok=True)
    sFakeBinDir = os.path.join(sWorkspace, "fakebin")
    _fnInstallFakeMake(
        sFakeBinDir,
        f"mkdir -p {sRepoDir}/bin",
    )
    resultProc = _fsRunWithFakeMake(
        sWorkspace, sFakeBinDir, _sBuildBody("halfbuilt", sRepoDir),
    )
    assert "RESULT=1" in resultProc.stdout
    dictMarker = _fdictReadMarker(sWorkspace)
    assert any(
        "no executables" in sWarn
        for sWarn in dictMarker["saWarnings"]
    )


def test_build_warns_when_bin_dir_missing(tmp_path):
    """make exits 0 with no bin/ at all -> warning."""
    sWorkspace = str(tmp_path)
    sRepoDir = os.path.join(sWorkspace, "nobin")
    os.makedirs(sRepoDir, exist_ok=True)
    os.makedirs(os.path.join(sWorkspace, ".vaibify"), exist_ok=True)
    sFakeBinDir = os.path.join(sWorkspace, "fakebin")
    _fnInstallFakeMake(sFakeBinDir, "true")
    resultProc = _fsRunWithFakeMake(
        sWorkspace, sFakeBinDir, _sBuildBody("nobin", sRepoDir),
    )
    assert "RESULT=1" in resultProc.stdout
    dictMarker = _fdictReadMarker(sWorkspace)
    assert any(
        "no executables" in sWarn
        for sWarn in dictMarker["saWarnings"]
    )


def test_build_warns_when_bin_contains_only_non_executable(tmp_path):
    """A bin/ holding non-executable artifacts (object files, headers)
    should still flag — we want at least one runnable binary."""
    sWorkspace = str(tmp_path)
    sRepoDir = os.path.join(sWorkspace, "objsonly")
    os.makedirs(sRepoDir, exist_ok=True)
    os.makedirs(os.path.join(sWorkspace, ".vaibify"), exist_ok=True)
    sFakeBinDir = os.path.join(sWorkspace, "fakebin")
    _fnInstallFakeMake(
        sFakeBinDir,
        f"mkdir -p {sRepoDir}/bin && touch {sRepoDir}/bin/leftover.o",
    )
    resultProc = _fsRunWithFakeMake(
        sWorkspace, sFakeBinDir, _sBuildBody("objsonly", sRepoDir),
    )
    assert "RESULT=1" in resultProc.stdout
    dictMarker = _fdictReadMarker(sWorkspace)
    assert any(
        "no executables" in sWarn
        for sWarn in dictMarker["saWarnings"]
    )


def test_build_warns_when_make_fails(tmp_path):
    """make returns nonzero -> 'make opt failed' warning."""
    sWorkspace = str(tmp_path)
    sRepoDir = os.path.join(sWorkspace, "broken")
    os.makedirs(sRepoDir, exist_ok=True)
    os.makedirs(os.path.join(sWorkspace, ".vaibify"), exist_ok=True)
    sFakeBinDir = os.path.join(sWorkspace, "fakebin")
    _fnInstallFakeMake(
        sFakeBinDir,
        'echo "compile error" >&2',
        iExitCode=2,
    )
    resultProc = _fsRunWithFakeMake(
        sWorkspace, sFakeBinDir, _sBuildBody("broken", sRepoDir),
    )
    assert "RESULT=1" in resultProc.stdout
    dictMarker = _fdictReadMarker(sWorkspace)
    assert any(
        "make opt failed" in sWarn
        for sWarn in dictMarker["saWarnings"]
    )


def test_build_warns_when_repo_missing(tmp_path):
    """Repo dir absent -> 'repository directory missing' warning."""
    sWorkspace = str(tmp_path)
    sRepoDir = os.path.join(sWorkspace, "absent")
    os.makedirs(os.path.join(sWorkspace, ".vaibify"), exist_ok=True)
    sFakeBinDir = os.path.join(sWorkspace, "fakebin")
    _fnInstallFakeMake(sFakeBinDir, "true")
    resultProc = _fsRunWithFakeMake(
        sWorkspace, sFakeBinDir, _sBuildBody("absent", sRepoDir),
    )
    assert "RESULT=1" in resultProc.stdout
    dictMarker = _fdictReadMarker(sWorkspace)
    assert any(
        "repository directory missing" in sWarn
        for sWarn in dictMarker["saWarnings"]
    )
