"""Re-run a workflow in a SHADOW container built from its pinned image.

A PROOF Level 3 attestation claims that the workflow ran again and that
what it produced is byte-identical to what ``MANIFEST.sha256`` pins.
Until this module existed, tier 5 made that claim after re-running the
workflow **inside the researcher's own project container** -- which has
two defects, and only one of them is about safety.

The safety one is obvious: the rerun overwrites the researcher's real
outputs. Attesting reproducibility should not cost them their working
tree, and a researcher who declines that trade simply never attests.

The epistemic one is the reason this module is not merely a
convenience. ``reproduce.sh`` -- the artefact the envelope publishes,
and the thing a third party will actually run -- pulls the *pinned image
digest* and executes the workflow in a container made from it. A rerun
in the live project container instead exercises whatever that container
has become since: packages installed during a debugging session, files
left by an interactive step, an image tag repointed months ago. It can
therefore pass where ``reproduce.sh`` would fail, and an attestation
that certifies a procedure nobody published is worse than no
attestation.

So the shadow container is not a sandbox that happens to be safer. It
is the closest thing the hub can build to *the reproduction a stranger
would perform*: a fresh container from the digest ``environment.json``
records, carrying a copy of the repository and nothing else, with no
network, and destroyed with proof when the comparison is done.

**What it still is not, stated rather than implied.** The shadow runs
on the researcher's own daemon, from an image already in their local
store, over a repository copied out of their container rather than
cloned from a published mirror. It cannot detect a digest that is
unreachable from a fresh host, an artefact that exists only locally, or
a dependency the lock file omits -- those are what tiers 1 through 4
are for. What it adds is that the *execution* half of the claim is made
in an environment the researcher did not shape by hand.

**Ordering is the contract.** The expected manifest is snapshotted from
the shadow BEFORE the rerun and re-read after, so a step that re-pins
the manifest over its own changed output is a divergence rather than a
self-consistent tree. That property belongs to ``rerunVerification``,
which this module drives rather than reimplements: a second derivation
of "reproduced" is exactly the drift that module was written to end.

**The seed is coherence-pinned.** The repository copy is taken through
``coherentExport``, which observes every path git can enumerate
immediately before and after the stream and REFUSES a copy the
repository moved under. Without it a concurrent write -- an agent, a
terminal, a running step -- yields a shadow holding a mixture of two
moments, and the attestation that follows describes a tree that never
existed. The usual symptom is not a false pass but a baffling one: the
rerun computes from half-stale inputs, the hashes diverge, and the
researcher concludes their workflow is non-deterministic. A named
refusal is the whole point.
"""

import posixpath

from vaibify.docker import coherentExport
from vaibify.docker import daemonCapacity
from vaibify.docker import disposableContainer
from vaibify.docker import disposableSpecification
from vaibify.reproducibility.environmentSnapshot import (
    _fsExtractImageDigest,
    fdictReadEnvironmentJson,
)
from vaibify.reproducibility.repoFiles import ContainerRepoFiles


__all__ = [
    "ShadowRerunRefusedError",
    "S_SHADOW_ROLE",
    "S_SHADOW_WORKSPACE_ROOT",
    "fdictRerunAndVerifyThroughShadow",
    "flistNameCommandsMissingFromTheImage",
    "fdictRerunInShadowContainer",
    "fsResolvePinnedImageReference",
    "ftResolveShadowPaths",
]


class ShadowRerunRefusedError(Exception):
    """A shadow rerun was refused before any container was created.

    Derives from ``Exception``, never ``OSError``: a refusal swallowed
    by an ``except OSError`` is how a control decision silently
    downgrades into an I/O hiccup, which is how a reproducibility badge
    once quietly lost a level.
    """


S_SHADOW_ROLE = "shadow"

# Where the repository copy lands inside the shadow. NOT ``/workspace``:
# that names a Docker-managed volume in a project container and this
# container has no volumes at all, so borrowing the name would invite a
# reader to assume a mount that is not there. The directory is created
# by the archive itself, owned by the unprivileged container user.
S_SHADOW_WORKSPACE_ROOT = "/shadow"


def fsResolvePinnedImageReference(dictEnvironmentPayload):
    """Return the image reference ``environment.json`` pins, or refuse.

    The digest is the whole point of the shadow: a tag can be repointed
    without any container changing, so a shadow built from a tag would
    attest an environment nobody pinned. A payload with no usable digest
    is refused rather than falling back to the live container's image --
    the fallback is precisely the substitution this module exists to
    remove.
    """
    sDigest = _fsExtractImageDigest(dictEnvironmentPayload or {})
    if not sDigest:
        raise ShadowRerunRefusedError(
            "environment.json pins no container image digest, so no "
            "shadow container can be built from it. Regenerate the "
            "environment snapshot before attesting at tier 5."
        )
    return sDigest


def ftResolveShadowPaths(sProjectRepoPath, sWorkflowPath):
    """Return ``(sArchivePrefix, sShadowRepoPath, sShadowWorkflowPath)``.

    ``container.get_archive`` names its members relative to the parent
    of the exported directory, so a repository at ``/anything/myRepo``
    arrives as ``myRepo/...`` and lands at
    ``<workspace root>/myRepo``. The workflow file keeps its position
    relative to the repository root, because every step directory in
    the workflow is repo-relative and would otherwise resolve against a
    root that moved.
    """
    sRepoRoot = posixpath.normpath(sProjectRepoPath or "")
    if not posixpath.isabs(sRepoRoot) or sRepoRoot == "/":
        raise ShadowRerunRefusedError(
            f"The project repository path {sProjectRepoPath!r} is not an "
            "absolute container directory, so its shadow copy cannot be "
            "placed."
        )
    sWorkflowNormalized = posixpath.normpath(sWorkflowPath or "")
    sRelativeWorkflow = posixpath.relpath(sWorkflowNormalized, sRepoRoot)
    if sRelativeWorkflow.startswith(".."):
        raise ShadowRerunRefusedError(
            f"The workflow file {sWorkflowPath!r} lies outside its "
            f"project repository {sProjectRepoPath!r}, so the shadow "
            "copy would not contain it."
        )
    sShadowRepoPath = posixpath.join(
        S_SHADOW_WORKSPACE_ROOT, posixpath.basename(sRepoRoot))
    return (
        S_SHADOW_WORKSPACE_ROOT.lstrip("/"),
        sShadowRepoPath,
        posixpath.join(sShadowRepoPath, sRelativeWorkflow),
    )


def fdictRerunInShadowContainer(
    connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
    sProjectRepoPath, dictEnvironmentPayload, sResourceName="",
    fnStatusCallback=None, fdictRunAndVerify=None,
):
    """Re-run one workflow in a shadow container and hash what it wrote.

    Creates the shadow from the digest ``environment.json`` pins, copies
    the project repository into it, drives the shared
    ``rerunVerification`` comparison against the SHADOW's filesystem,
    and destroys the shadow with proof. Returns that comparison's
    outcome with two fields added: ``bShadowContainerUsed``, and
    ``sShadowTeardown`` naming the destruction outcome, so a quarantined
    shadow is reported rather than absorbed.

    ``fdictRunAndVerify`` is the comparison to drive; it defaults to
    :func:`~vaibify.reproducibility.rerunVerification.fdictRerunAndVerifyWorkflow`
    and exists as a parameter so a test can drive the real container
    lifecycle without also running a workflow inside it. The import is
    deferred for the reason the runner import is deferred one level
    down: it pulls the GUI pipeline machinery into the CLI's import
    graph otherwise.
    """
    if fdictRunAndVerify is None:
        from vaibify.reproducibility.rerunVerification import (
            fdictRerunAndVerifyWorkflow,
        )
        fdictRunAndVerify = fdictRerunAndVerifyWorkflow
    sImageReference = fsResolvePinnedImageReference(dictEnvironmentPayload)
    tShadowPaths = ftResolveShadowPaths(sProjectRepoPath, sWorkflowPath)
    dictCapacity = daemonCapacity.fdictResolveDaemonCapacity(
        connectionDocker)
    baRepositoryArchive = (
        coherentExport.fbaExportRepositoryCoherently(
            connectionDocker, sContainerId, sProjectRepoPath,
            dictCapacity["iArchiveTotalBytes"],
        )
    )
    return _fdictDriveShadowLifecycle(
        connectionDocker, dictWorkflow, sImageReference, tShadowPaths,
        baRepositoryArchive, dictCapacity, sResourceName,
        fnStatusCallback, fdictRunAndVerify,
    )


def _fdictDriveShadowLifecycle(
    connectionDocker, dictWorkflow, sImageReference, tShadowPaths,
    baRepositoryArchive, dictCapacity, sResourceName,
    fnStatusCallback, fdictRunAndVerify,
):
    """Create, load, compare, and destroy one shadow container.

    The teardown sits in ``finally`` deliberately: a comparison that
    raised has still left a container on the researcher's daemon, and a
    destruction that cannot be PROVEN leaves the gateway's reservation
    visibly quarantined rather than reporting a clean completion.
    """
    dockerDisposable = (
        disposableContainer.fdockerCreateDisposableClient())
    dictGateway = disposableContainer.fdictCreateDisposableGateway(
        dockerDisposable, sResourceName)
    _fnSweepShadowsLeftByACrash(dockerDisposable, sResourceName)
    dictCreated = disposableContainer.fdictReserveAndCreateContainer(
        dictGateway, S_SHADOW_ROLE, sImageReference,
        disposableSpecification.fdictBuildDefaultLimits(dictCapacity),
    )
    try:
        disposableContainer.fnCopyArchiveIntoContainer(
            dictGateway, dictCreated["sHandle"], baRepositoryArchive,
            sDestinationDirectory="/", sPathPrefix=tShadowPaths[0],
        )
        dictOutcome = _fdictCompareUnderTheShadowsOwnAdmission(
            connectionDocker, dictCreated, tShadowPaths, dictWorkflow,
            fnStatusCallback, fdictRunAndVerify,
        )
    finally:
        dictTeardown = _fdictTearDownShadow(
            dictGateway, dictCreated["sHandle"])
    _fnExplainAMissingCommandAsAnImageGap(dictOutcome)
    dictOutcome["bShadowContainerUsed"] = True
    dictOutcome["sShadowTeardown"] = dictTeardown["sOutcome"]
    if dictTeardown["sOutcome"] != (
            disposableSpecification.S_OUTCOME_DESTROYED):
        dictOutcome["sShadowTeardownReason"] = dictTeardown["sReason"]
    return dictOutcome


def _fdictTearDownShadow(dictGateway, sHandle):
    """Destroy the shadow, converting a teardown fault into an outcome.

    A teardown that raises inside a ``finally`` would replace the
    comparison's own exception with its own, hiding the reason the rerun
    failed behind the reason the cleanup failed. The fault is recorded
    as a quarantine instead, which is what an unproven destruction means
    everywhere else in this lane.
    """
    try:
        return disposableContainer.fdictDestroyAndSettle(
            dictGateway, sHandle)
    except Exception as error:
        return {
            "sOutcome": disposableSpecification.S_OUTCOME_QUARANTINED,
            "sReason": (
                "The shadow container teardown raised: "
                f"{type(error).__name__}: {error}"
            ),
        }


def fdictRerunAndVerifyThroughShadow(
    connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
    filesRepoLive, fnStatusCallback=None,
):
    """Drive a shadow rerun for a workflow discovered in a live container.

    The single entry point both attestation lanes use -- the dashboard's
    ``/level3/verify`` route and ``vaibify reproduce --rerun``. It
    exists so the two cannot disagree about what "reproduced" means, the
    same reason
    :func:`~vaibify.reproducibility.rerunVerification.fdictRerunAndVerifyWorkflow`
    exists one level down.

    ``filesRepoLive`` is the LIVE project repository -- the source of
    the image pin, and NOT the root the comparison is made against. That
    distinction is the whole point of this lane and is worth the extra
    parameter name: the comparison root is built inside, rooted on the
    shadow, because a verification rooted on anything the rerun did not
    write finds every entry clean and certifies a reproduction nobody
    observed.

    The crash-sweep stamp is ``sContainerId``, taken from here rather
    than accepted from a caller, because the two lanes reach this
    function holding that identifier in different forms and a stamp
    chosen per-lane would not match across them. What a mismatch costs
    is a MISSED sweep -- a shadow left by a crashed CLI run waits for
    the next CLI run rather than being reclaimed by the dashboard --
    and that is the failure worth having: the opposite error, a stamp
    broad enough to match another hub's work, would destroy a live
    attestation. An UNSTAMPED survivor is swept by either lane, and
    since both lanes always stamp, an unstamped one is a leak by
    construction.
    """
    dictEnvironmentPayload = fdictReadEnvironmentJson(filesRepoLive)
    return fdictRerunInShadowContainer(
        connectionDocker, sContainerId, dictWorkflow, sWorkflowPath,
        dictWorkflow.get("sProjectRepoPath", ""), dictEnvironmentPayload,
        sContainerId, fnStatusCallback,
    )


def flistNameCommandsMissingFromTheImage(listErrors):
    """Return the commands a shadow preflight could not find.

    Recovered from the preflight message through the two constants
    that COMPOSE it, never by re-typing its punctuation here: a parser
    carrying its own copy of the format drifts silently the first time
    the wording is edited, and this one is pinned against the real
    validator's output.
    """
    from vaibify.gui.pipelineValidator import (
        S_PREFLIGHT_COMMAND_LOCATION,
        S_PREFLIGHT_COMMAND_MISSING,
    )

    listCommands = []
    for sError in listErrors or []:
        iStart = sError.find(S_PREFLIGHT_COMMAND_MISSING)
        if iStart < 0:
            continue
        sRest = sError[iStart + len(S_PREFLIGHT_COMMAND_MISSING):]
        iEnd = sRest.find(S_PREFLIGHT_COMMAND_LOCATION)
        sCommand = (sRest if iEnd < 0 else sRest[:iEnd]).strip()
        if sCommand and sCommand not in listCommands:
            listCommands.append(sCommand)
    return listCommands


def _fnExplainAMissingCommandAsAnImageGap(dictOutcome):
    """Say what a command missing from the SHADOW actually means.

    This is the finding the shadow container exists to produce, and
    reporting it as "command not found" throws it away. The shadow is
    built from the image digest the envelope pins -- not from the
    researcher's running container -- so a tool that was pip-installed
    into that container after the image was built is absent here and
    would be absent for anyone reproducing from the published
    envelope. Verified in exactly that form: a project whose live
    container had ``pytest`` on its PATH pinned an image with no
    ``pytest`` at all (2026-09-01).

    The note names BOTH causes rather than assuming the obvious one.
    In the case that produced it the package was already declared and
    already in ``requirements.lock`` -- the pinned image was simply
    two days older than the declaration -- so "add it to your
    dependencies" would have sent the researcher to edit a file that
    was already correct. Vaibify does not currently check that a
    pinned image satisfies its own lock, which is the gap underneath
    all of this.

    The note is attached rather than substituted: the original errors
    still name the steps, and a researcher who disagrees with the
    explanation can still read what was actually checked.
    """
    dictFailure = dictOutcome.get("dictRerunFailure") or {}
    listMissing = flistNameCommandsMissingFromTheImage(
        dictFailure.get("listErrors") or [],
    )
    if not listMissing:
        return
    dictFailure["listCommandsMissingFromImage"] = listMissing
    dictFailure["sImageGapNote"] = (
        "These were looked for in the container image your envelope "
        "pins, not in the project container you have open. A tool "
        "installed into a running container is not part of its image, "
        "so nobody reproducing your work would have it. Two things "
        "cause this and they need different fixes: the tool is not "
        "declared as a dependency, or it IS declared and the pinned "
        "image predates the declaration. Check the project's "
        "requirements against the image, then rebuild."
    )
    dictOutcome["dictRerunFailure"] = dictFailure


def _fdictCompareUnderTheShadowsOwnAdmission(
    connectionDocker, dictCreated, tShadowPaths, dictWorkflow,
    fnStatusCallback, fdictRunAndVerify,
):
    """Run the comparison under an admission naming the SHADOW container.

    Not optional plumbing. The rerun drives the ordinary
    ``DockerConnection``, whose every exec asks the mutation gate
    whether THIS container id is admitted -- and the dashboard lane
    reaches here inside a durable carrier opened for the RESEARCHER's
    project container, whose admission names a different id. Without an
    admission of its own, every step of the rerun would raise
    ``MutationNotAdmittedError`` from inside a background task, and the
    attestation would report an unexplained failure.

    The narrowness is the safety property, and it runs in both
    directions: this admission authorizes the shadow and nothing else,
    so a rerun cannot reach back into the project container it was
    seeded from, and the project's admission never reaches the shadow.

    The tokens are closed in ``finally`` because an admission left
    active would outlive the container it names and be inherited by
    whatever ran next on this context.
    """
    from vaibify.gui.commitCarrier import (
        fnCloseRequestAdmission,
        ftOpenDisposableContainerAdmission,
    )

    tTokens = ftOpenDisposableContainerAdmission(
        dictCreated["sContainerName"], dictCreated["sContainerName"])
    try:
        return fdictRunAndVerify(
            connectionDocker, dictCreated["sContainerName"], dictWorkflow,
            tShadowPaths[2],
            ContainerRepoFiles(
                connectionDocker, dictCreated["sContainerName"],
                tShadowPaths[1]),
            fnStatusCallback,
        )
    finally:
        fnCloseRequestAdmission(tTokens)


def _fnSweepShadowsLeftByACrash(dockerDisposable, sResourceName):
    """Destroy shadows a previous, crashed rerun of THIS project left.

    The lifecycle's ``finally`` covers every ordinary exit. What it
    cannot cover is the hub dying mid-rerun, which leaves a labeled
    container running on the researcher's daemon with nothing left to
    remember it. Swept here rather than at hub startup for two reasons:
    at startup the hub does not yet know which project containers it
    serves, and a daemon-wide sweep at boot would destroy a live peer
    hub's work -- a mistake this repository has already made once.

    Narrowed to survivors stamped with THIS project container's name.
    A survivor carrying no stamp at all is swept too, because an
    unattributable container is precisely the leak worth cleaning; that
    is the fail-closed direction here, the opposite of elsewhere, since
    the cost of an error is one destroyed disposable rather than a lost
    claim.

    Failures are swallowed deliberately: a sweep that cannot reach the
    daemon must not prevent the rerun the researcher asked for. The
    quarantine it would have reported is re-derivable on the next
    attempt, where an unswept survivor is still visible.
    """
    if not sResourceName:
        return
    try:
        disposableContainer.fdictSweepLabeledSurvivors(
            dockerDisposable, sResourceName)
    except Exception:
        pass
