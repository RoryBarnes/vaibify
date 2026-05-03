"""CLI subcommand: vaibify reproduce.

Read-only verification of the AICS Level 3 reproducibility envelope
inside a project repository. Walks the three tiers in sequence:

* Tier 1 - byte-exact artefact integrity via ``MANIFEST.sha256``.
* Tier 2 - hash-pinned Python dependency install via
  ``requirements.lock``.
* Tier 3 - container image digest pull via
  ``.vaibify/environment.json``.

Step 4 (re-running the workflow) is opt-in via ``--rerun``. The
command never modifies files inside the project repo; the only
write is to the user's Python environment when Tier 2 installs
pinned dependencies.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import click

from vaibify.reproducibility.manifestWriter import flistVerifyManifest


__all__ = [
    "reproduce",
    "fbVerifyTier1",
    "fbVerifyTier2",
    "fbVerifyTier3",
    "fbRerunWorkflow",
]


_S_MANIFEST_FILENAME = "MANIFEST.sha256"
_S_LOCK_FILENAME = "requirements.lock"
_S_ENVIRONMENT_RELATIVE = ".vaibify/environment.json"
_T_TIER_CHOICES = ("1", "2", "3")


def _fnPrintHeader(sLabel, sDescription):
    """Print a leading ``[N/4] description`` banner without a newline."""
    click.echo(f"[{sLabel}] {sDescription} ", nl=False)


def _fnPrintPass(sDetails):
    """Print a checkmark plus optional summary text after a tier banner."""
    click.echo(f"... {sDetails} OK")


def _fnPrintFail(sDetails):
    """Print a failure marker plus diagnostic text after a tier banner."""
    click.echo(f"... {sDetails} FAIL")


def _fnAbortMissingFile(sFilename, sProjectRepo):
    """Emit a usage-error message naming a missing repo file and exit 2."""
    click.echo(
        f"Error: required file '{sFilename}' not found in '{sProjectRepo}'."
    )
    sys.exit(2)


def fbVerifyTier1(sProjectRepo):
    """Verify ``MANIFEST.sha256`` against artefacts on disk.

    Calls :func:`flistVerifyManifest` and reports the count of clean
    entries vs. mismatches. Returns ``True`` when the manifest is
    fully consistent. Exits with code 2 when the manifest file does
    not exist.
    """
    pathManifest = Path(sProjectRepo) / _S_MANIFEST_FILENAME
    if not pathManifest.is_file():
        _fnAbortMissingFile(_S_MANIFEST_FILENAME, sProjectRepo)
    _fnPrintHeader("1/4", "Verifying file integrity (MANIFEST.sha256)")
    listMismatches = flistVerifyManifest(sProjectRepo)
    iEntries = _fiCountManifestEntries(pathManifest)
    if not listMismatches:
        _fnPrintPass(f"{iEntries}/{iEntries}")
        return True
    _fnPrintFail(f"{len(listMismatches)} mismatch(es)")
    _fnReportMismatches(listMismatches)
    return False


def _fiCountManifestEntries(pathManifest):
    """Return the number of non-comment, non-blank lines in the manifest."""
    iCount = 0
    with open(pathManifest, "r", encoding="utf-8") as fileHandle:
        for sLine in fileHandle:
            sStripped = sLine.strip()
            if sStripped and not sStripped.startswith("#"):
                iCount += 1
    return iCount


def _fnReportMismatches(listMismatches):
    """Print one diagnostic line per manifest mismatch."""
    for dictMismatch in listMismatches:
        sActual = dictMismatch["sActual"] or "<missing>"
        click.echo(
            f"  {dictMismatch['sPath']}: expected "
            f"{dictMismatch['sExpected'][:12]}..., got {sActual[:12]}..."
        )


def fbVerifyTier2(sProjectRepo):
    """Install hash-pinned dependencies from ``requirements.lock``.

    Runs ``<python> -m pip install --require-hashes -r
    requirements.lock`` via subprocess and streams the output to the
    user. When ``pip`` exits with a hash-related error and ``uv`` is
    on PATH, retries via ``uv pip install --require-hashes`` so users
    on uv-only environments are not stranded. Returns ``True`` only
    when an install command exits zero. Exits with code 2 when the
    lockfile is absent.
    """
    pathLock = Path(sProjectRepo) / _S_LOCK_FILENAME
    if not pathLock.is_file():
        _fnAbortMissingFile(_S_LOCK_FILENAME, sProjectRepo)
    _fnPrintHeader("2/4", "Reproducing Python env (requirements.lock)")
    iReturnCode, sStderr = _fiRunPipInstall(pathLock)
    if iReturnCode == 0:
        _fnPrintPass("hashes verified")
        return True
    if _fbShouldFallbackToUv(sStderr):
        return _fbRunUvFallback(pathLock)
    _fnPrintFail("pip install failed")
    click.echo(sStderr.rstrip())
    return False


def _fiRunPipInstall(pathLock):
    """Invoke ``pip install --require-hashes`` and return (returncode, stderr)."""
    saCommand = [
        sys.executable, "-m", "pip", "install",
        "--require-hashes", "-r", str(pathLock),
    ]
    completed = subprocess.run(saCommand, capture_output=True, text=True)
    sys.stdout.write(completed.stdout)
    return completed.returncode, completed.stderr


def _fbShouldFallbackToUv(sStderr):
    """Return True when uv is available and stderr suggests a hash failure."""
    if shutil.which("uv") is None:
        return False
    sLower = sStderr.lower()
    return "hash" in sLower


def _fbRunUvFallback(pathLock):
    """Retry the install through ``uv pip install --require-hashes``."""
    saCommand = [
        "uv", "pip", "install",
        "--require-hashes", "-r", str(pathLock),
    ]
    completed = subprocess.run(saCommand, capture_output=True, text=True)
    sys.stdout.write(completed.stdout)
    if completed.returncode == 0:
        _fnPrintPass("hashes verified (uv)")
        return True
    _fnPrintFail("uv install failed")
    click.echo(completed.stderr.rstrip())
    return False


def fbVerifyTier3(sProjectRepo):
    """Pull the pinned container image recorded in ``environment.json``.

    Reads ``.vaibify/environment.json``, extracts ``sImageDigest``,
    and runs ``docker pull <image_digest>``. Returns ``True`` when
    the pull succeeds. Exits with code 2 when ``environment.json``
    is missing or the digest field is unset.
    """
    pathEnvironment = Path(sProjectRepo) / _S_ENVIRONMENT_RELATIVE
    if not pathEnvironment.is_file():
        _fnAbortMissingFile(_S_ENVIRONMENT_RELATIVE, sProjectRepo)
    _fnPrintHeader("3/4", "Pulling pinned container image")
    sImageDigest = _fsLoadImageDigest(pathEnvironment, sProjectRepo)
    completed = subprocess.run(
        ["docker", "pull", sImageDigest],
        capture_output=True, text=True,
    )
    sys.stdout.write(completed.stdout)
    if completed.returncode == 0:
        _fnPrintPass(sImageDigest)
        return True
    _fnPrintFail("docker pull failed")
    click.echo(completed.stderr.rstrip())
    return False


def _fsLoadImageDigest(pathEnvironment, sProjectRepo):
    """Return the ``sImageDigest`` recorded in environment.json or exit 2."""
    with open(pathEnvironment, "r", encoding="utf-8") as fileHandle:
        dictEnvironment = json.load(fileHandle)
    sImageDigest = dictEnvironment.get("sImageDigest")
    if not sImageDigest:
        click.echo(
            "Error: 'sImageDigest' is missing from "
            f"'{_S_ENVIRONMENT_RELATIVE}' in '{sProjectRepo}'."
        )
        sys.exit(2)
    return sImageDigest


def fbRerunWorkflow(sProjectRepo):
    """Re-run the workflow end to end against a running container.

    Resolves the project from sProjectRepo via fconfigResolveProject,
    requires a running container, and invokes the same pipeline runner
    that ``vaibify run`` uses. Returns True on success, False on any
    failure (configuration, missing container, non-zero pipeline exit).
    Failures print actionable messages but do not raise.
    """
    _fnPrintHeader("4/4", "Re-running workflow")
    try:
        return _fbInvokePipelineRunner(sProjectRepo)
    except Exception as error:
        click.echo(f"... failed to invoke pipeline runner: {error}")
        return False


def _fbInvokePipelineRunner(sProjectRepo):
    """Invoke commandRun's pipeline machinery against the resolved project."""
    from .configLoader import fconfigResolveProject
    from .commandUtilsDocker import (
        fconnectionRequireDocker,
        fsRequireRunningContainer,
    )
    from .commandRun import _fiRunPipeline
    configProject = fconfigResolveProject(_fsResolveProjectName(sProjectRepo))
    connectionDocker = fconnectionRequireDocker()
    sContainerName = fsRequireRunningContainer(configProject)
    iExitCode = _fiRunPipeline(
        connectionDocker, sContainerName, None, None,
    )
    if iExitCode != 0:
        click.echo(f"... pipeline runner exited with code {iExitCode}")
        return False
    click.echo("... workflow re-ran successfully ✓")
    return True


def _fsResolveProjectName(sProjectRepo):
    """Return the project directory's basename for fconfigResolveProject."""
    return os.path.basename(os.path.abspath(sProjectRepo))


def _fbDispatchTier(sTier, sProjectRepo, setSkipTiers):
    """Run a single tier and return its True/False outcome (or True if skipped)."""
    if sTier in setSkipTiers:
        click.echo(f"[{sTier}/4] skipped")
        return True
    if sTier == "1":
        return fbVerifyTier1(sProjectRepo)
    if sTier == "2":
        return fbVerifyTier2(sProjectRepo)
    return fbVerifyTier3(sProjectRepo)


def _fnEmitFinalSummary(bAllPassed):
    """Print the trailing success or failure line."""
    click.echo("")
    if bAllPassed:
        click.echo("L3 reproduction confirmed.")
    else:
        click.echo("L3 reproduction failed; see tier output above.")


@click.command("reproduce")
@click.option(
    "--repo", "sRepo", default=None,
    type=click.Path(file_okay=False, dir_okay=True),
    help="Path to the project repo (defaults to the current directory).",
)
@click.option(
    "--rerun/--no-rerun", "bRerun", default=False,
    help="Also re-run the workflow (step 4). Off by default.",
)
@click.option(
    "--skip-tier", "saSkipTier", multiple=True,
    type=click.Choice(_T_TIER_CHOICES),
    help="Skip the given tier (1, 2, or 3). May be repeated.",
)
def reproduce(sRepo, bRerun, saSkipTier):
    """Verify a project's AICS L3 reproducibility envelope."""
    sProjectRepo = sRepo or str(Path.cwd())
    setSkipTiers = set(saSkipTier)
    bAllPassed = True
    for sTier in _T_TIER_CHOICES:
        if not _fbDispatchTier(sTier, sProjectRepo, setSkipTiers):
            bAllPassed = False
    if bRerun:
        if not fbRerunWorkflow(sProjectRepo):
            bAllPassed = False
    else:
        click.echo("[4/4] Re-running workflow ... skipped (use --rerun)")
    _fnEmitFinalSummary(bAllPassed)
    if not bAllPassed:
        sys.exit(1)
