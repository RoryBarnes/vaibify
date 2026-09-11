"""Project-scope doctor checks: is this project's own record coherent.

Not "is the machine well" and not "is the container well", but the
third thing a researcher hits: state vaibify itself keeps about the
project, which can disagree with the project. A quarantined journal
stops every mutation with a message about reconciliation; an
environment envelope that pins an image the container no longer runs
means a published Level 3 claim no longer covers current work; a
workspace whose files the container user does not own makes ``git
push`` fail inside the container for a reason nothing else names.

The checks here call the existing authorities rather than
re-implementing them, which for the journal means TWO calls -- one
that decides what the records MEAN, one that supplies the display
records -- because that is the shape the reconciliation lane already
has.
"""

from vaibify.config import operationJournal, reconciliation

from .preflightResult import (
    S_LEVEL_FAIL, S_LEVEL_NOT_CHECKED, S_LEVEL_OK, S_LEVEL_WARN,
    S_SCOPE_PROJECT, PreflightResult,
)


__all__ = [
    "flistCheckJournalQuarantine", "flistCheckEnvelopeCurrency",
    "flistCheckWorkspaceOwnership", "fsDiscoverProjectRepoPath",
    "flistReportStartupObservations",
]


_S_ENVELOPE_RELATIVE_PATH = ".vaibify/environment.json"
_S_GIT_DIRECTORY = ".git"


def fsDiscoverProjectRepoPath(
    connectionDocker, sContainerName, sWorkspaceRoot,
):
    """Return the project repository under the workspace root, or ''.

    A repository is a directory carrying ``.git`` -- the definition
    vaibify already uses ("every vaibify workflow lives in a git
    repository"). It is NOT "a directory carrying an environment
    envelope": discovery keyed on the envelope meant that a perfectly
    ordinary project which had never reached Level 3 was reported as
    having no repository at all, and the checks below it -- workspace
    ownership, which has nothing to do with envelopes -- were skipped
    without anybody being told why.

    Discovery, not a default: an unfound repository answers ``""`` and
    the caller reports unassessed rather than falling back to the
    workspace root, which is a Docker-managed volume and not a git
    repository.
    """
    import posixpath
    try:
        listEntries = connectionDocker.flistDirectoryEntries(
            sContainerName, sWorkspaceRoot,
        )
    except Exception:
        return ""
    listCandidates = [
        posixpath.join(sWorkspaceRoot, str(sEntry), _S_GIT_DIRECTORY)
        for sEntry in listEntries
    ]
    if not listCandidates:
        return ""
    try:
        listExists = connectionDocker.flistContainerPathsExist(
            sContainerName, listCandidates,
        )
    except Exception:
        return ""
    for sCandidate, bExists in zip(listCandidates, listExists):
        if bExists:
            return posixpath.dirname(sCandidate)
    return ""


_I_MAX_NAMED_RECORDS = 5


def _fsDescribeJournalRecords(sContainerName):
    """Return a one-line-per-record description, or '' when unreadable."""
    try:
        listRecords = reconciliation.flistDescribeJournalRecords(
            sContainerName,
        )
    except reconciliation.ReconciliationRefusedError as errorJournal:
        return f"the journal itself could not be read: {errorJournal}"
    if not listRecords:
        return ""
    listLines = [
        "  " + dictRecord["sOperationId"] + " ("
        + str(dictRecord.get("sKind") or "?") + "): "
        + str(dictRecord.get("sTarget") or "?")
        for dictRecord in listRecords[:_I_MAX_NAMED_RECORDS]
    ]
    if len(listRecords) > _I_MAX_NAMED_RECORDS:
        listLines.append(
            f"  ... and {len(listRecords) - _I_MAX_NAMED_RECORDS} more"
        )
    return "\n".join(listLines)


def flistCheckJournalQuarantine(sContainerName, connectionDocker=None):
    """Report whether this container's operation journal quarantines it.

    Read-only: ``bPersistResolution=False``, because a diagnostic that
    persisted a resolution would be deciding the thing it was asked to
    describe.
    """
    try:
        dictResolution = operationJournal.fdictResolveContainerJournal(
            sContainerName, connectionDocker, bPersistResolution=False,
        )
    except Exception as errorResolve:
        return [PreflightResult(
            sName="journal-quarantine", sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_PROJECT,
            sMessage=f"the journal could not be resolved: {errorResolve}",
        )]
    return [_fpreflightJournalResolution(sContainerName, dictResolution)]


def _fpreflightJournalResolution(sContainerName, dictResolution):
    """Turn one journal resolution into a result naming its way out."""
    sResolution = dictResolution["sResolution"]
    if sResolution == operationJournal.S_RESOLUTION_SETTLED:
        return PreflightResult(
            sName="journal-quarantine", sLevel=S_LEVEL_OK,
            sScope=S_SCOPE_PROJECT,
            sMessage="no operation journal record holds this container.",
        )
    if sResolution == operationJournal.S_RESOLUTION_BUSY:
        return PreflightResult(
            sName="journal-quarantine", sLevel=S_LEVEL_OK,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                "an operation is live in this container right now; "
                "that is busy, not quarantined."
            ),
        )
    sRecords = _fsDescribeJournalRecords(sContainerName)
    return PreflightResult(
        sName="journal-quarantine", sLevel=S_LEVEL_FAIL,
        sScope=S_SCOPE_PROJECT,
        sMessage=(
            "this container is QUARANTINED: "
            + str(dictResolution.get("sQuarantineReason") or "")
        ),
        sRemediation=(
            "Every mutation is refused until the recorded operations "
            "are proven settled. The command below shows what is "
            "recorded and proves what it can."
            + (f"\nRecorded:\n{sRecords}" if sRecords else "")
        ),
        sCommand=f"vaibify reconcile {sContainerName}",
        sMechanism=(
            "Resolves the container's journal read-only and, when it "
            "quarantines, asks the reconciliation lane for the same "
            "display records `vaibify reconcile` would show."
        ),
    )


def _fsReadEnvelopePin(connectionDocker, sContainerName, sRepoPath):
    """Return the envelope's pinned image digest, or '' when absent."""
    import json
    from vaibify.reproducibility.environmentSnapshot import (
        _fsExtractImageDigest,
    )
    try:
        baPayload = connectionDocker.fbaFetchFile(
            sContainerName,
            sRepoPath.rstrip("/") + "/" + _S_ENVELOPE_RELATIVE_PATH,
        )
    except Exception:
        return ""
    try:
        jsonPayload = json.loads(baPayload.decode("utf-8", errors="replace"))
    except (ValueError, TypeError):
        return ""
    if not isinstance(jsonPayload, dict):
        return ""
    return _fsExtractImageDigest(jsonPayload)


def flistCheckEnvelopeCurrency(connectionDocker, sContainerName, sRepoPath):
    """Compare the envelope's pinned image against the running one.

    Calls the same leaf the dashboard's poll calls
    (``fdictCompareEnvelopePin``) AND the same identity capture the hub
    makes at connect (``fdictCaptureLiveImageIdentity``), so the two
    surfaces cannot come to different conclusions about one image.
    Both halves matter: an earlier draft read the container's raw
    ``Image`` field and compared it against a pin recorded in the
    repository-qualified ``name@sha256:...`` form, which reported a
    project running EXACTLY the pinned image as diverged -- a false
    red, on a live project, on the first run.

    Its three-state answer is preserved end to end: ``None`` is
    nothing determined, and this check renders that as unassessed
    rather than as agreement.
    """
    from vaibify.reproducibility.environmentSnapshot import (
        fdictCaptureLiveImageIdentity, fdictCompareEnvelopePin,
    )
    sPinned = _fsReadEnvelopePin(
        connectionDocker, sContainerName, sRepoPath,
    )
    try:
        dictIdentity = fdictCaptureLiveImageIdentity(sContainerName)
    except Exception:
        dictIdentity = {}
    dictAnswer = fdictCompareEnvelopePin(
        sPinned,
        str(dictIdentity.get("sImageDigest") or ""),
        str(dictIdentity.get("sImageId") or ""),
    )
    return [_fpreflightEnvelopePin(dictAnswer)]


def _fpreflightEnvelopePin(dictAnswer):
    """Render the three-state envelope comparison."""
    bIsLive = dictAnswer["bPinnedImageIsLive"]
    if bIsLive is None:
        return PreflightResult(
            sName="envelope-image-currency", sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                "either this project has no environment envelope yet "
                "or the daemon named no image for the container, so "
                "nothing was compared."
            ),
        )
    if bIsLive:
        return PreflightResult(
            sName="envelope-image-currency", sLevel=S_LEVEL_OK,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                "the environment envelope pins the image this "
                "container is running."
            ),
        )
    return PreflightResult(
        sName="envelope-image-currency", sLevel=S_LEVEL_WARN,
        sScope=S_SCOPE_PROJECT,
        sMessage=(
            "the environment envelope pins "
            + (dictAnswer["sPinnedImageDigest"] or "?")
            + ", but this container runs "
            + (dictAnswer["sLiveImageDigest"] or "?")
            + ". A published Level 3 claim does not cover work done "
            "in the image the container is actually running."
        ),
        sRemediation=(
            "Regenerate the environment snapshot so the envelope pins "
            "the image in use, then re-verify."
        ),
    )


# The uid the Dockerfile gives the container user, and the one every
# host->container write stamps its tar entries with. Locked to the
# image by testContainerUserUidIsOneThousand.
_I_CONTAINER_USER_UID = 1000


def flistCheckWorkspaceOwnership(connectionDocker, sContainerName, sRepoPath):
    """Report files the container user cannot write, WITHOUT overclaiming.

    Narrowed deliberately. The entrypoint's ownership migration
    triggers on a ROOT-owned path (``find ... -uid 0 -print -quit``)
    and skips entirely when ``/proc/self/mountinfo`` is unreadable, so
    "restart the container" repairs exactly one of the three cases
    this probe can find. The other two are reported as what they are:
    a file owned by some third uid survives the restart, and if
    unreadable mount information caused the skip, another restart will
    most likely skip again.

    An earlier draft of this check said "restart to fix" for all of
    them. That is the shape of advice that costs a researcher an
    afternoon and then still leaves the file unwritable.
    """
    dictAnswer = connectionDocker.fdictFindForeignOwnedPaths(
        sContainerName, sRepoPath, _I_CONTAINER_USER_UID,
    )
    if not dictAnswer.get("bAnswered"):
        return [PreflightResult(
            sName="workspace-ownership", sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                "the ownership probe could not run inside the "
                "container: " + str(dictAnswer.get("sError") or "")
            ),
        )]
    listRootOwned = list(dictAnswer.get("listRootOwned") or [])
    listOtherOwned = list(dictAnswer.get("listOtherOwned") or [])
    if not listRootOwned and not listOtherOwned:
        return [_fpreflightOwnershipClean(dictAnswer)]
    return _flistOwnershipFindings(dictAnswer, listRootOwned, listOtherOwned)


def _fpreflightOwnershipClean(dictAnswer):
    """Return the pass -- or the unassessed answer for a partial walk.

    A truncated walk found nothing wrong in the part of the tree it
    reached, and NOTHING AT ALL about the rest. Reporting that as
    ``ok`` is the "not checked rendered as ok" failure wearing a
    reassuring sentence: the files past the ceiling are exactly where
    an unnoticed ownership problem would sit.
    """
    if dictAnswer.get("bTruncated"):
        return PreflightResult(
            sName="workspace-ownership", sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                "the ownership walk stopped at its visit ceiling. "
                "Nothing was found wrong in the part of the "
                "repository it reached, and nothing is known about "
                "the rest."
            ),
        )
    return PreflightResult(
        sName="workspace-ownership", sLevel=S_LEVEL_OK,
        sScope=S_SCOPE_PROJECT,
        sMessage=(
            "every file in the project repository is owned by the "
            "container user."
        ),
    )


def _flistOwnershipFindings(dictAnswer, listRootOwned, listOtherOwned):
    """Build one finding per ownership case, each with its own remedy."""
    listResults = []
    if listRootOwned:
        listResults.append(_fpreflightRootOwned(dictAnswer, listRootOwned))
    if listOtherOwned:
        listResults.append(PreflightResult(
            sName="workspace-ownership-foreign", sLevel=S_LEVEL_WARN,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                "these paths are owned by neither root nor the "
                "container user, so the in-container agent cannot "
                "edit them: " + ", ".join(listOtherOwned)
            ),
            sRemediation=(
                "A restart will NOT repair these: the entrypoint's "
                "migration only looks for root-owned paths. Change "
                "their ownership from the host, or recreate them."
            ),
        ))
    return listResults


def _fpreflightRootOwned(dictAnswer, listRootOwned):
    """Return the root-owned finding, whose remedy depends on mountinfo."""
    sPaths = ", ".join(listRootOwned)
    if dictAnswer.get("bMountInfoReadable"):
        return PreflightResult(
            sName="workspace-ownership", sLevel=S_LEVEL_WARN,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                "these paths are root-owned, so the in-container "
                "agent and `git push` cannot write them: " + sPaths
            ),
            sRemediation=(
                "The entrypoint migrates root-owned workspace paths "
                "on every start, and this container's mount "
                "information is readable, so a restart repairs it. "
                "Note that a restart kills every live shell and agent "
                "in the container."
            ),
            sCommand="vaibify stop && vaibify start",
            sMechanism=(
                "Walks the project repository inside the container "
                "comparing st_uid against the container user's, and "
                "reads /proc/self/mountinfo -- the file the "
                "entrypoint's migration needs and skips without."
            ),
        )
    return PreflightResult(
        sName="workspace-ownership", sLevel=S_LEVEL_WARN,
        sScope=S_SCOPE_PROJECT,
        sMessage=(
            "these paths are root-owned: " + sPaths + ". This "
            "container cannot read /proc/self/mountinfo, which is "
            "what the entrypoint's ownership migration needs."
        ),
        sRemediation=(
            "A restart most likely will NOT repair this: the "
            "migration skips when mount information is unreadable, "
            "and it will most likely be unreadable again. Change the "
            "ownership from the host instead."
        ),
    )


def flistReportStartupObservations(
    connectionDocker, sContainerName, sWorkspaceRoot,
):
    """Report what the entrypoint observed at the LAST container start.

    Historical evidence, and the message says so. The entrypoint runs
    on ``docker start``, so it does not fire when a running laptop
    changes network -- a container that has been up for a week has an
    observation from a week ago, and reading it as current state is
    exactly the mistake this check exists to prevent. Every live probe
    in this report supersedes it.
    """
    import json
    import posixpath
    from vaibify.gui.startupObservations import (
        flistDescribeStartupObservations,
    )
    try:
        baMarker = connectionDocker.fbaFetchFile(
            sContainerName,
            posixpath.join(sWorkspaceRoot, ".vaibify", ".entrypoint_ready"),
        )
        jsonMarker = json.loads(baMarker.decode("utf-8", errors="replace"))
    except Exception:
        return [PreflightResult(
            sName="startup-observations", sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                "the container's readiness marker could not be read, "
                "so nothing is known about its last start."
            ),
        )]
    listDescribed = flistDescribeStartupObservations(
        (jsonMarker or {}).get("listObservations"),
    )
    return [_fpreflightStartupObservations(listDescribed)]


def _fpreflightStartupObservations(listDescribed):
    """Render the observations, or say the image records none."""
    if not listDescribed:
        return PreflightResult(
            sName="startup-observations", sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                "this container's image records no structured startup "
                "observations; it predates them."
            ),
        )
    listConcerning = [
        dictObservation for dictObservation in listDescribed
        if dictObservation["sVerdict"] == "warn"
    ]
    if not listConcerning:
        return PreflightResult(
            sName="startup-observations", sLevel=S_LEVEL_OK,
            sScope=S_SCOPE_PROJECT,
            sMessage=(
                f"the last container start recorded "
                f"{len(listDescribed)} observation(s), none concerning."
            ),
        )
    return PreflightResult(
        sName="startup-observations", sLevel=S_LEVEL_WARN,
        sScope=S_SCOPE_PROJECT,
        sMessage=(
            "the LAST container start recorded "
            f"{len(listConcerning)} concerning observation(s). This is "
            "history, not current state -- the entrypoint does not run "
            "again when this machine changes network, so the live "
            "checks above supersede it."
        ),
        sRemediation="\n".join(
            f"  [{dictObservation['sIso']}] {dictObservation['sSubject']}: "
            + dictObservation["sSummary"]
            + (
                " -- " + dictObservation["sRemedy"]
                if dictObservation["sRemedy"] else ""
            )
            for dictObservation in listConcerning
        ),
    )
