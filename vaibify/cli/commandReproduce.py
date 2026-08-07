"""CLI subcommand: vaibify reproduce.

Read-only verification of the PROOF Level 3 reproducibility envelope
inside a project repository. Walks five tiers in sequence:

* Tier 1 — byte-exact artefact integrity via ``MANIFEST.sha256``.
* Tier 2 — hash-pinned Python dependency install via
  ``requirements.lock``.
* Tier 3 — container image digest pull via
  ``.vaibify/environment.json``.
* Tier 4 — L3 artifact coherence. Runs six of the seven verifiers
  ``levelGates.fbL3ReadinessOK`` composes; see :func:`fbVerifyTier4`
  for the one it cannot evaluate and why.
* Tier 5 — opt-in rebuild and hash compare via ``--rerun``. The
  workflow is re-run inside its container, then every
  ``MANIFEST.sha256`` entry is re-hashed *in that container*, against
  what the rerun produced; the tier passes only when the rerun exits
  zero, every hash still matches, and the manifest itself did not move
  during the run. Writes ``.vaibify/l3_attestation.json`` on completion
  (pass or fail) and archives a copy to ``.vaibify/l3_attestations/``.
  Tiers 1-4 read the host repo named by ``--repo``; tier 5 reads the
  container's project repo, because ``/workspace`` is a Docker-managed
  named volume and the two are different filesystems.

Tiers 1-4 are read-only over the project repo. Tier 5 is not: the
rerun executes the workflow, so it rewrites whatever the workflow's
steps produce, and the command then writes the attestation files.
Tier 2's ``pip install`` updates the user's Python environment.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import click

from vaibify.gui.workflowManager import (
    VAIBIFY_PROJECTS_DIR,
    VAIBIFY_WORKFLOWS_DIR,
)
from vaibify.reproducibility import manifestWriter
from vaibify.reproducibility.aiProvenanceStamp import (
    fdictBuildAiProvenanceStamp,
)
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles
from vaibify.reproducibility.l3Attestation import (
    S_STATUS_FAILED,
    S_STATUS_PASSED,
    fdictBuildAttestation,
    fnWriteAttestation,
    fsCurrentManifestDigest,
)
from vaibify.reproducibility.levelGates import (
    fbVerifyDependencyLock,
    fbVerifyDeterminismDeclared,
    fbVerifyDockerfilePinned,
    fbVerifyEnvironmentSnapshot,
    fbVerifyManifestComplete,
    fbVerifyReproduceScript,
    fbWorkflowDeclaresBinaries,
)
from vaibify.reproducibility.environmentSnapshot import (
    _fsExtractImageDigest,
)
from vaibify.reproducibility.manifestWriter import flistVerifyManifest
from vaibify.reproducibility.repoFiles import ContainerRepoFiles
from vaibify.reproducibility.rerunVerification import (
    S_DIVERGENCE_PIPELINE_FAILED,
    fdictRerunAndVerifyWorkflow,
    fdictUnrunOutcome,
)


__all__ = [
    "reproduce",
    "fbVerifyTier1",
    "fbVerifyTier2",
    "fbVerifyTier3",
    "fbVerifyTier4",
    "fdictRerunAndVerify",
    "fbIsValidImageDigest",
]


_S_MANIFEST_FILENAME = "MANIFEST.sha256"
_S_LOCK_FILENAME = "requirements.lock"
_S_ENVIRONMENT_RELATIVE = ".vaibify/environment.json"

# Conservative whitelist for OCI image references that may include a
# digest (registry/repo@sha256:<hex>) or a tag (registry/repo:tag).
# Forbids whitespace and shell metacharacters; subprocess uses argv-form
# already, so this is defense-in-depth for log readability and to catch
# malformed environment.json payloads early.
_REGEX_VALID_IMAGE_REFERENCE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._\-/:@]{0,511}$"
)


def fbIsValidImageDigest(sImageDigest):
    """Return True when ``sImageDigest`` matches the conservative whitelist."""
    if not isinstance(sImageDigest, str):
        return False
    if not sImageDigest:
        return False
    return bool(_REGEX_VALID_IMAGE_REFERENCE.match(sImageDigest))


def _fnPrintHeader(sLabel, sDescription):
    """Print a leading ``[N/5] description`` banner without a newline."""
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
    not exist. When the workflow declares paths the manifest does not
    pin (a legacy manifest written before scripts and standards joined
    the envelope), the count is reported but the tier still passes —
    the user is told their coverage is partial so they can re-run.
    """
    pathManifest = Path(sProjectRepo) / _S_MANIFEST_FILENAME
    if not pathManifest.is_file():
        _fnAbortMissingFile(_S_MANIFEST_FILENAME, sProjectRepo)
    _fnPrintHeader(
        f"1/{_S_TIER_DENOMINATOR}",
        "Verifying file integrity (MANIFEST.sha256)",
    )
    listMismatches = flistVerifyManifest(sProjectRepo)
    iEntries = manifestWriter.fiCountManifestEntries(sProjectRepo)
    if not listMismatches:
        _fnPrintPass(f"{iEntries}/{iEntries}")
        _fnReportIncompleteCoverage(sProjectRepo)
        return True
    _fnPrintFail(f"{len(listMismatches)} mismatch(es)")
    _fnReportMismatches(listMismatches)
    return False


def _fnReportIncompleteCoverage(sProjectRepo):
    """Print an advisory line when any workflow declares paths the manifest omits.

    Aggregates across every project.json in the repo's Project
    directories
    so multi-workflow projects are not silently skipped. The manifest
    is one file pinning artefacts across the whole project repo, so
    the completeness check is project-wide.
    """
    dictAggregate = _fdictAggregateAllWorkflows(sProjectRepo)
    if dictAggregate is None:
        return
    listIncomplete = manifestWriter.flistDeclaredButMissingFromManifest(
        sProjectRepo, dictAggregate,
    )
    if listIncomplete:
        click.echo(
            f"  warning: {len(listIncomplete)} workflow path(s) not "
            f"in manifest (first: {listIncomplete[0]}); re-run to "
            "refresh coverage."
        )


def _flistProjectFilesInRepo(sProjectRepo):
    """Return every Project file in a repo, canonical directory first.

    Projects live in ``.vaibify/projects``; ``.vaibify/workflows`` is
    the legacy directory that discovery still reads. Scanning only the
    legacy one — which both callers below did — made every command here
    silently vacuous for a project created by ``vaibify init``:
    aggregation returned None and the binary-declaration verifier passed
    for having checked nothing.
    """
    listFiles = []
    for sDirectory in (VAIBIFY_PROJECTS_DIR, VAIBIFY_WORKFLOWS_DIR):
        pathDirectory = Path(sProjectRepo) / sDirectory
        if pathDirectory.is_dir():
            listFiles.extend(sorted(pathDirectory.glob("*.json")))
    return listFiles


def _fdictAggregateAllWorkflows(sProjectRepo):
    """Return a synthetic workflow whose listSteps unions every workflow's steps.

    Carries forward the first non-empty ``dictDeterminism`` block so
    Tier 4's determinism check can read it; the project ladders L3 as
    a whole, not per-workflow. Returns ``None`` when no readable
    project.json files exist so the coverage check is skipped rather
    than reported as empty.
    """
    listAllSteps = []
    dictDeterminismMerged = {}
    dictAiProvenanceMerged = {}
    for pathFile in _flistProjectFilesInRepo(sProjectRepo):
        dictWorkflow = _fdictLoadWorkflowFile(pathFile)
        if dictWorkflow is None:
            continue
        listAllSteps.extend(dictWorkflow.get("listSteps", []) or [])
        dictExtra = dictWorkflow.get("dictDeterminism") or {}
        if dictExtra and not dictDeterminismMerged:
            dictDeterminismMerged = dict(dictExtra)
        dictProvenance = dictWorkflow.get("dictAiProvenance") or {}
        if dictProvenance and not dictAiProvenanceMerged:
            dictAiProvenanceMerged = dict(dictProvenance)
    if not listAllSteps and not dictDeterminismMerged:
        return None
    dictResult = {"listSteps": listAllSteps}
    if dictDeterminismMerged:
        dictResult["dictDeterminism"] = dictDeterminismMerged
    if dictAiProvenanceMerged:
        dictResult["dictAiProvenance"] = dictAiProvenanceMerged
    return dictResult


def _fdictLoadWorkflowFile(pathFile):
    """Parse one project.json or return None on read/parse failure."""
    try:
        with open(pathFile, "r", encoding="utf-8") as fileHandle:
            return json.load(fileHandle)
    except (OSError, json.JSONDecodeError):
        return None


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
    requirements.lock`` via subprocess and writes the captured stdout
    once it exits. When ``pip`` exits with a hash-related error and ``uv`` is
    on PATH, retries via ``uv pip install --require-hashes`` so users
    on uv-only environments are not stranded. Returns ``True`` only
    when an install command exits zero. Exits with code 2 when the
    lockfile is absent.
    """
    pathLock = Path(sProjectRepo) / _S_LOCK_FILENAME
    if not pathLock.is_file():
        _fnAbortMissingFile(_S_LOCK_FILENAME, sProjectRepo)
    _fnPrintHeader(
        f"2/{_S_TIER_DENOMINATOR}",
        "Reproducing Python env (requirements.lock)",
    )
    iReturnCode, sStderr = _ftRunPipInstall(pathLock)
    if iReturnCode == 0:
        _fnPrintPass("hashes verified")
        return True
    if _fbShouldFallbackToUv(sStderr):
        return _fbRunUvFallback(pathLock)
    _fnPrintFail("pip install failed")
    click.echo(sStderr.rstrip())
    return False


def _ftRunPipInstall(pathLock):
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

    Reads ``.vaibify/environment.json``, extracts the *top-level*
    ``sImageDigest``, and runs ``docker pull <image_digest>``.
    Returns ``True`` when the pull succeeds. Exits with code 2 when
    ``environment.json`` is missing, when the top-level digest field
    is unset, or when it fails ``fbIsValidImageDigest``. Note the
    narrower read than ``_fsRecordedImageDigest``, which also honours
    the nested ``dictContainer.sImageDigest`` layout: a snapshot
    carrying only the nested form exits 2 here.
    """
    pathEnvironment = Path(sProjectRepo) / _S_ENVIRONMENT_RELATIVE
    if not pathEnvironment.is_file():
        _fnAbortMissingFile(_S_ENVIRONMENT_RELATIVE, sProjectRepo)
    _fnPrintHeader(
        f"3/{_S_TIER_DENOMINATOR}",
        "Pulling pinned container image",
    )
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
    """Return the image digest recorded in environment.json or exit 2.

    Reads through ``environmentSnapshot._fsExtractImageDigest``, which
    accepts both supported layouts: the nested
    ``dictContainer.sImageDigest`` and the flat top-level key. This
    loader used to read only the flat form while its two sibling
    readers honoured both, so a snapshot written in the nested layout
    aborted the whole reproduce run with exit 2 and a "missing" message
    while the digest sat in the file.
    """
    with open(pathEnvironment, "r", encoding="utf-8") as fileHandle:
        dictEnvironment = json.load(fileHandle)
    sImageDigest = _fsExtractImageDigest(dictEnvironment)
    if not sImageDigest:
        click.echo(
            "Error: 'sImageDigest' is missing from "
            f"'{_S_ENVIRONMENT_RELATIVE}' in '{sProjectRepo}'."
        )
        sys.exit(2)
    if not fbIsValidImageDigest(sImageDigest):
        click.echo(
            "Error: 'sImageDigest' in "
            f"'{_S_ENVIRONMENT_RELATIVE}' is not a valid OCI "
            "image reference."
        )
        sys.exit(2)
    return sImageDigest


def fbVerifyTier4(sProjectRepo):
    """Verify all seven PROOF L3 readiness checks.

    Reuses the host-side ``levelGates`` verifiers so the CLI and the
    dashboard apply the same rule to every check, and now cover the
    same set. ``fbWorkflowDeclaresBinaries`` reads workflow-root fields
    (``bNoStandaloneBinaries``, ``listDeclaredBinaries``) that the
    synthetic aggregate cannot represent, so it is evaluated per
    project.json instead — see ``_fbEveryWorkflowDeclaresBinaries``.
    Until 2026-07-27 it was skipped entirely, and a repo could clear
    this tier while the dashboard's ``fbL3ReadinessOK`` still blocked
    it. Returns True iff every verifier passes; on failure
    prints a per-verifier checklist so the user sees which artefacts
    need regenerating.
    """
    dictWorkflow = _fdictAggregateAllWorkflows(sProjectRepo) or {
        "listSteps": [],
    }
    _fnPrintHeader(
        f"4/{_S_TIER_DENOMINATOR}",
        "Verifying L3 artifact coherence",
    )
    listResults = _flistRunReadinessVerifiers(
        sProjectRepo, dictWorkflow,
    )
    iPassed = sum(1 for tEntry in listResults if tEntry[1])
    iTotal = len(listResults)
    if iPassed == iTotal:
        _fnPrintPass(f"{iPassed}/{iTotal}")
        for sLabel, _bPassed in listResults:
            click.echo(f"       - {sLabel}: OK")
        return True
    _fnPrintFail(f"{iPassed}/{iTotal}")
    for sLabel, bPassed in listResults:
        sStatus = "OK" if bPassed else "FAIL"
        click.echo(f"       - {sLabel}: {sStatus}")
    return False


def _fbEveryWorkflowDeclaresBinaries(sProjectRepo):
    """Return True iff every project.json answers the binary question.

    Evaluated per workflow rather than against the synthetic aggregate.
    ``fbWorkflowDeclaresBinaries`` tests the *coherence* of one
    workflow's answer — waiver with an empty list, or no waiver with a
    well-formed list — and a union has no honest reading when two
    workflows answer differently. Worse, a union would let a clean
    workflow mask a sibling that answered nothing, which is the
    permissive gap this verifier exists to close.

    A repo with no workflow files passes vacuously: it cannot reach L1,
    so failing here would only add a confusing second diagnosis.
    """
    listWorkflows = [
        dictWorkflow for dictWorkflow in (
            _fdictLoadWorkflowFile(pathFile)
            for pathFile in _flistProjectFilesInRepo(sProjectRepo)
        )
        if dictWorkflow is not None
    ]
    return all(
        fbWorkflowDeclaresBinaries(dictWorkflow)
        for dictWorkflow in listWorkflows
    )


def _flistRunReadinessVerifiers(sProjectRepo, dictWorkflow):
    """Return ``[(label, bool)]`` for each L3 readiness verifier in order.

    All seven of ``fbL3ReadinessOK``'s verifiers, so the CLI grades a
    repo exactly as the dashboard does. The binaries check ran nowhere
    here until 2026-07-27 because the aggregate carried no declaration
    state, which let a repo clear this tier and still be blocked by the
    dashboard.
    """
    return [
        ("Manifest complete",
         fbVerifyManifestComplete(sProjectRepo, dictWorkflow)),
        ("Dependency lock",
         fbVerifyDependencyLock(sProjectRepo)),
        ("Environment snapshot digest-form",
         fbVerifyEnvironmentSnapshot(sProjectRepo)),
        ("Dockerfile pinned",
         fbVerifyDockerfilePinned(sProjectRepo)),
        ("reproduce.sh present + in manifest",
         fbVerifyReproduceScript(sProjectRepo, dictWorkflow)),
        ("Determinism declared",
         fbVerifyDeterminismDeclared(sProjectRepo, dictWorkflow)),
        ("Binaries declared or waived",
         _fbEveryWorkflowDeclaresBinaries(sProjectRepo)),
    ]


def fdictRerunAndVerify(sProjectRepo, sWorkflowName=None):
    """Tier 5: re-run the workflow in its container and hash what it wrote.

    ``sProjectRepo`` (the value of ``--repo``, or the working
    directory) resolves the *project configuration* and therefore the
    container to drive. The artefact comparison is then rooted on that
    container's project repo, never on ``sProjectRepo``: ``/workspace``
    is a Docker-managed named volume, so re-hashing the host clone
    would grade a tree the rerun never touched and pass unconditionally.

    Returns the four-field outcome dict, fail-closed when the container
    or the workflow could not be resolved. Both ``Exception`` and
    ``SystemExit`` are caught so a registry miss inside
    ``fconfigResolveProject`` (which calls ``sys.exit(1)``) does not
    short-circuit the surrounding ``vaibify reproduce`` exit-code logic.
    """
    _fnPrintHeader(
        f"5/{_S_TIER_DENOMINATOR}",
        "Re-running workflow",
    )
    try:
        return _fdictRerunInResolvedContainer(sProjectRepo, sWorkflowName)
    except (Exception, SystemExit) as error:
        click.echo(f"... failed to invoke pipeline runner: {error}")
        # Only the exception's type reaches the attestation. The record
        # is a repo artefact that gets committed and published, and the
        # message can carry a host path from the project resolver; the
        # full text goes to the researcher's console instead.
        return fdictUnrunOutcome(
            f"rerun not attempted ({type(error).__name__}); "
            "see the reproduce output"
        )


def _fdictRerunInResolvedContainer(sProjectRepo, sWorkflowName):
    """Resolve the container-side rerun target and drive the shared lane."""
    tTarget = _ftResolveRerunTarget(sProjectRepo, sWorkflowName)
    connectionDocker, sContainerName, dictWorkflow, sWorkflowPath = tTarget
    filesRepo = ContainerRepoFiles(
        connectionDocker, sContainerName,
        dictWorkflow.get("sProjectRepoPath", ""),
    )
    dictOutcome = fdictRerunAndVerifyWorkflow(
        connectionDocker, sContainerName, dictWorkflow, sWorkflowPath,
        filesRepo,
    )
    _fnReportRerunExecution(dictOutcome)
    return dictOutcome


def _fnReportRerunExecution(dictOutcome):
    """Print whether the pipeline itself ran, before the hash verdict."""
    if not dictOutcome.get("bRerunAttempted", True):
        click.echo("... rerun refused before any step executed:")
        for sReason in dictOutcome["listDivergedHashes"]:
            click.echo(f"      {sReason}")
        return
    if S_DIVERGENCE_PIPELINE_FAILED in dictOutcome["listDivergedHashes"]:
        click.echo("... pipeline runner exited non-zero")
        return
    click.echo("... workflow re-ran successfully ✓")


def _ftResolveRerunTarget(sProjectRepo, sWorkflowName):
    """Return ``(connection, container, workflow, workflowPath)`` to re-run.

    The workflow is named explicitly rather than rediscovered, because a
    container may host several project repos and attesting one while
    running another produces a record that looks complete and describes
    a run that never happened.
    """
    from .configLoader import fconfigResolveProject
    from .commandUtilsDocker import (
        fconnectionRequireDocker,
        fsRequireRunningContainer,
    )
    from vaibify.gui.workflowManager import (
        fdictLoadWorkflowFromContainer,
        flistFindWorkflowsInContainer,
    )
    configProject = _fconfigResolveProjectAtRepo(
        sProjectRepo, fconfigResolveProject,
    )
    connectionDocker = fconnectionRequireDocker()
    sContainerName = fsRequireRunningContainer(configProject)
    dictEntry = _fdictSelectWorkflowEntry(
        flistFindWorkflowsInContainer(connectionDocker, sContainerName),
        sWorkflowName,
    )
    dictWorkflow = fdictLoadWorkflowFromContainer(
        connectionDocker, sContainerName, dictEntry["sPath"],
    )
    dictWorkflow["sProjectRepoPath"] = (
        dictEntry.get("sProjectRepoPath")
        or dictWorkflow.get("sProjectRepoPath", "")
    )
    return (
        connectionDocker, sContainerName, dictWorkflow, dictEntry["sPath"],
    )


def _fdictSelectWorkflowEntry(listWorkflows, sWorkflowName):
    """Return the one discovered workflow to re-run, or raise ValueError.

    Ambiguity is refused rather than resolved by sort order: picking a
    workflow here would attest an envelope the rerun did not produce.
    """
    if not listWorkflows:
        raise ValueError(
            "no vaibify workflow found in the running container"
        )
    if sWorkflowName:
        return _fdictMatchWorkflowByName(listWorkflows, sWorkflowName)
    if len(listWorkflows) > 1:
        raise ValueError(
            f"the running container hosts {len(listWorkflows)} "
            "workflows ("
            + ", ".join(sorted(
                dictEntry.get("sName", "") for dictEntry in listWorkflows
            ))
            + "); name the one to re-run with --workflow, because "
            "attesting a workflow other than the one that ran would "
            "certify a run that never happened"
        )
    return listWorkflows[0]


def _fdictMatchWorkflowByName(listWorkflows, sWorkflowName):
    """Return the single discovered workflow matching a researcher's name."""
    listMatches = [
        dictEntry for dictEntry in listWorkflows
        if sWorkflowName in (
            dictEntry.get("sName", ""), dictEntry.get("sPath", ""),
        )
    ]
    if not listMatches:
        raise ValueError(
            f"no workflow named '{sWorkflowName}' in the running "
            "container; --workflow accepts "
            + ", ".join(sorted(
                dictEntry.get("sName", "") for dictEntry in listWorkflows
            ))
        )
    if len(listMatches) > 1:
        raise ValueError(
            f"'{sWorkflowName}' matches more than one workflow in the "
            "running container; pass the full container path to "
            "--workflow instead"
        )
    return listMatches[0]


def _fconfigResolveProjectAtRepo(sProjectRepo, fconfigResolveProject):
    """Run ``fconfigResolveProject(None)`` with cwd pinned to ``sProjectRepo``.

    ``fconfigResolveProject`` resolves from ``Path.cwd()`` when no
    project name is given, so without this guard the runner would
    silently ignore ``--repo``. The original cwd is always restored.
    The repo path is resolved and validated as an existing directory
    before chdir to avoid surprising behavior when ``--repo`` points at
    a non-directory (e.g., a regular file or a missing path).
    """
    pathResolved = Path(sProjectRepo).resolve()
    if not pathResolved.is_dir():
        raise FileNotFoundError(
            f"--repo target is not an existing directory: {sProjectRepo}"
        )
    sOriginalCwd = os.getcwd()
    try:
        os.chdir(str(pathResolved))
        return fconfigResolveProject(None)
    finally:
        os.chdir(sOriginalCwd)


# Tier registry: single source of truth for the 5-tier reproduce ladder.
# Each entry is (sLabel, sDescription, fnVerify, bRequiresRerun). A future
# Tier 6 (e.g. external attestor co-sign) is added by appending one tuple;
# every label and print site is derived from this list so the denominator
# stays in sync.
_LIST_TIERS = (
    ("1", "manifest present + clean", fbVerifyTier1, False),
    ("2", "lockfile parity",          fbVerifyTier2, False),
    ("3", "image digest pinned",      fbVerifyTier3, False),
    ("4", "L3 artifact coherence",    fbVerifyTier4, False),
    ("5", "byte-identical rerun",     fdictRerunAndVerify, True),
)
_T_TIER_CHOICES = tuple(
    sLabel for sLabel, _sDescription, _fnVerify, bRequiresRerun in _LIST_TIERS
    if not bRequiresRerun
)
_S_TIER_DENOMINATOR = str(len(_LIST_TIERS))


def _fbDispatchTier(sTier, sProjectRepo, setSkipTiers):
    """Run a single tier and return its True/False outcome (or True if skipped)."""
    if sTier in setSkipTiers:
        click.echo(f"[{sTier}/{_S_TIER_DENOMINATOR}] skipped")
        return True
    for sLabel, _sDescription, fnVerify, bRequiresRerun in _LIST_TIERS:
        if sLabel == sTier and not bRequiresRerun:
            return fnVerify(sProjectRepo)
    return True


def _fnEmitFinalSummary(bAllPassed, bRerun, bAttestationWritten):
    """Print the trailing success or advisory line.

    The four-state matrix is: rerun=yes/no × all-passed=yes/no.
    Without ``--rerun`` we never write attestation, so the "ready"
    success line tells the user what to do next.
    """
    click.echo("")
    if bAllPassed and bRerun and bAttestationWritten:
        click.echo("L3 reproduction confirmed and attested.")
        return
    if bAllPassed and not bRerun:
        click.echo(
            "L3 reproduction ready "
            "(no attestation on file — run --rerun to attest)."
        )
        return
    click.echo("L3 reproduction failed; see tier output above.")


def _fdictBuildRerunAttestation(sProjectRepo, dictOutcome, fDuration):
    """Return the attestation dict describing a rerun + hash-compare outcome.

    Every count and every diverged path comes from ``dictOutcome``, the
    post-rerun re-hash produced by
    :func:`~vaibify.reproducibility.rerunVerification.fdictVerifyRerunOutputs`.
    Nothing here may be inferred from the pipeline's exit code: that is
    the substitution that once let a byte-changing rerun attest N of N.

    ``sManifestDigest`` likewise comes from the outcome when the rerun
    produced one, because that is the manifest the comparison was made
    against — the container's, which need not be byte-identical to the
    host clone ``--repo`` names. Falling back to the host digest is for
    the tiers-only path, where no container comparison happened.
    """
    return fdictBuildAttestation(
        sStatus=(
            S_STATUS_PASSED if dictOutcome["bPassed"] else S_STATUS_FAILED
        ),
        sManifestDigest=(
            dictOutcome.get("sManifestDigest")
            or fsCurrentManifestDigest(sProjectRepo)
        ),
        sImageDigest=_fsRecordedImageDigest(sProjectRepo),
        fDurationSeconds=fDuration,
        iOutputHashesMatched=dictOutcome["iOutputHashesMatched"],
        iOutputHashesTotal=dictOutcome["iOutputHashesTotal"],
        listDivergedHashes=dictOutcome["listDivergedHashes"],
        sRunLogPath="",
        dictAiProvenance=_fdictBuildCliProvenanceStamp(sProjectRepo),
    )


def _fdictBuildCliProvenanceStamp(sProjectRepo):
    """Build the Replay-axis stamp with the facts the CLI can capture.

    The CLI has no hub or container context, so the workspace-prompt
    hash is empty and the isolation probe is ``None`` — honestly
    recorded as "not captured", never guessed.
    """
    dictWorkflow = _fdictAggregateAllWorkflows(sProjectRepo) or {}
    return fdictBuildAiProvenanceStamp(
        dictWorkflow,
        ffilesEnsureRepoFiles(sProjectRepo),
        sWorkspacePromptSha256="",
        bNetworkIsolatedAtCapture=None,
        sHubInvokerModelId="",
    )


def _fbWriteAttestationFromRun(sProjectRepo, dictOutcome, fDuration):
    """Persist an L3 attestation reflecting the rerun outcome.

    Called only when ``--rerun`` ran end-to-end so the attestation
    records an actual rebuild attempt (not just envelope coherence).
    Failures are recorded with the same schema as passes so the
    history table can show "last rebuild failed".
    """
    dictAttestation = _fdictBuildRerunAttestation(
        sProjectRepo, dictOutcome, fDuration,
    )
    try:
        fnWriteAttestation(sProjectRepo, dictAttestation)
    except OSError as error:
        click.echo(f"  warning: could not persist attestation: {error}")
        return False
    return True


def _fsRecordedImageDigest(sProjectRepo):
    """Return the image digest recorded in environment.json, or empty."""
    pathEnvironment = Path(sProjectRepo) / _S_ENVIRONMENT_RELATIVE
    if not pathEnvironment.is_file():
        return ""
    try:
        with open(pathEnvironment, "r", encoding="utf-8") as fileHandle:
            dictPayload = json.load(fileHandle)
    except (OSError, json.JSONDecodeError):
        return ""
    dictContainer = dictPayload.get("dictContainer")
    if isinstance(dictContainer, dict):
        sNested = dictContainer.get("sImageDigest") or ""
        if sNested:
            return sNested
    return dictPayload.get("sImageDigest") or ""


def _fnReportHashCompare(dictOutcome):
    """Print the post-rerun hash compare, naming every diverged artefact."""
    sCounts = (
        f"{dictOutcome['iOutputHashesMatched']}/"
        f"{dictOutcome['iOutputHashesTotal']}"
    )
    if dictOutcome["bPassed"]:
        click.echo(f"... hashes match {sCounts} OK")
        return
    click.echo(f"... hashes match {sCounts} FAIL")
    for sDiverged in dictOutcome["listDivergedHashes"]:
        click.echo(f"  diverged: {sDiverged}")


def _ftRunRerunTier(sProjectRepo, sWorkflowName):
    """Re-run, re-hash, attest; return ``(bPassed, bAttestationWritten)``.

    ``bPassed`` is the hash-compare verdict, not the pipeline's exit
    code: a step that exits zero having changed an output byte has not
    reproduced anything, and Tier 5 must say so.
    """
    fStarted = time.monotonic()
    dictOutcome = fdictRerunAndVerify(sProjectRepo, sWorkflowName)
    _fnReportHashCompare(dictOutcome)
    bAttestationWritten = _fbWriteAttestationFromRun(
        sProjectRepo, dictOutcome,
        time.monotonic() - fStarted,
    )
    return dictOutcome["bPassed"], bAttestationWritten


@click.command("reproduce")
@click.option(
    "--repo", "sRepo", default=None,
    type=click.Path(file_okay=False, dir_okay=True),
    help="Path to the project repo (defaults to the current directory).",
)
@click.option(
    "--rerun/--no-rerun", "bRerun", default=False,
    help="Also re-run the workflow (step 5), re-hash its outputs "
         "against MANIFEST.sha256, and write an L3 attestation. "
         "Off by default.",
)
@click.option(
    "--workflow", "sWorkflowName", default=None,
    help="Name (or container path) of the workflow to re-run. Required "
         "when the running container hosts more than one, so the "
         "attestation can never describe a workflow that did not run.",
)
@click.option(
    "--skip-tier", "saSkipTier", multiple=True,
    type=click.Choice(_T_TIER_CHOICES),
    help="Skip the given tier (1, 2, 3, or 4). May be repeated.",
)
def fnReproduceCommand(sRepo, bRerun, sWorkflowName, saSkipTier):
    """Verify a project's PROOF L3 reproducibility envelope."""
    sProjectRepo = sRepo or str(Path.cwd())
    setSkipTiers = set(saSkipTier)
    bAllPassed = True
    for sTier in _T_TIER_CHOICES:
        if not _fbDispatchTier(sTier, sProjectRepo, setSkipTiers):
            bAllPassed = False
    bAttestationWritten = False
    if bRerun:
        bRerunPassed, bAttestationWritten = _ftRunRerunTier(
            sProjectRepo, sWorkflowName,
        )
        if not bRerunPassed:
            bAllPassed = False
    else:
        click.echo(
            f"[5/{_S_TIER_DENOMINATOR}] "
            "Re-running workflow ... skipped (use --rerun)"
        )
    _fnEmitFinalSummary(bAllPassed, bRerun, bAttestationWritten)
    if not bAllPassed:
        sys.exit(1)
