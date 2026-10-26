"""Containerize from the author's pinned image: obtain, prove, stack, tag, commit.

A rebuild from the Dockerfile produces a different digest and cannot
reproduce the author's bytes. When a clone's envelope pins an image by
content digest, this lane OBTAINS it through the published chain --
registry, then the archived deposit, then a copy on this daemon --
and makes it the project's own ``<projectName>:latest``, stacking only
the agent overlays the researcher asked for that the image provably
lacks.

THE ORDER IS THE CONTRACT
-------------------------

1. Re-check the clone: still obtainable, and still pinning the image
   the project was containerized from.
2. Obtain, through ``imageAcquisition``, with every link reported.
3. PROVE the baseline -- which overlays the obtained image already
   holds. The image's own overlays label is the first proof; a
   recomputation of the recipe fingerprint over this vaibify's shipped
   texts is the second, for images built before the label existed.
   The candidate list the repository carries is a claim, never the
   authority: a header line in a cloned repository can say anything.
   A labelled image whose list differs from the candidate REFUSES,
   additions requested or not, because running it would put a
   container on screen whose preserved ``vaibify.yml`` misdescribes
   it. Unproven never fails open: with no additions the base is used
   as-is; with additions the acquisition fails before anything is
   tagged.
4. Stack the DIFFERENTIAL overlays -- the resolved chain minus the
   proven set -- on the obtained image ID, requesting the pinned
   platform, labelling the result as derived from the base. Then read
   the stacked layers back and refuse if they reach the author's
   environment (``agentLayerSeparation``).
5. Tag the result as ``<projectName>:<tag>``, by ID.
6. Describe before enabling: the agent install keys go into
   ``vaibify.yml`` and the resolved chain into the registry entry.
7. Commit: the origin record, the launch guard's admission.

Steps 1-5 are :func:`fdictObtainAndStage`, which writes NOTHING to the
registry or to ``vaibify.yml``: a pending containerization runs them
against a staging tag and commits in a transaction of its own
(``gui/conversionLifecycle``), so a failure leaves the host project
exactly as it was. :func:`fdictAcquireForProject` is the whole lane for
a project that is already a container (a re-obtain, a switch to the
pinned image): there a failure leaves a registered-but-unbuilt
project, and the tile still reads as obtained, so the next click
retries.
"""

__all__ = [
    "PinnedImageAcquisitionRefusedError",
    "S_LATEST_TAG",
    "fdictAcquireForProject",
    "fdictObtainAndStage",
    "flistProveOverlayBaseline",
    "flistResolveDifferentialChain",
]

import logging

from vaibify.docker import disposableContainer
from vaibify.docker import imageBuilder


logger = logging.getLogger(__name__)


S_ACTION_REOBTAIN_WITHOUT_ADDITIONS = "reobtain-without-additions"
S_LATEST_TAG = "latest"


class PinnedImageAcquisitionRefusedError(Exception):
    """The acquisition stopped before the project gained an image.

    Derives from ``Exception``, never ``OSError``: a refusal swallowed
    by an ``except OSError`` is how a control decision downgrades into
    an I/O hiccup. ``sAction`` names the one recovery the refusal
    offers, when there is one, so the dashboard can offer it rather
    than leave the sentence as a dead end. ``dictFailure`` and
    ``listAttempts`` are the chain's own, carried up so the window can
    say what is held on disk and what else was tried.
    """

    def __init__(self, sMessage, sAction="", dictFailure=None, listAttempts=None):
        super().__init__(sMessage)
        self.sAction = sAction
        self.dictFailure = dict(dictFailure or {})
        self.listAttempts = list(listAttempts or [])


def _fnDiscardLine(sLine):
    """Drop a progress line for callers that report nothing."""
    del sLine


def fdictAcquireForProject(
    dictProject, bAllowEmulation, fnReportLine=None, dockerDisposable=None,
    sDockerDir=None, bWithoutAdditions=False, dictDownloadControl=None,
):
    """Run the whole lane for one registered project; return its record.

    ``dictProject`` is the registry entry, whose ``dictImageSource``
    says the project's image is obtained and carries the candidate
    baseline captured at conversion. Emulation is consented TWICE: the
    entry's flag from the wizard and this call's from the button.
    ``bWithoutAdditions`` is the recovery an unproven baseline names:
    the agents the wizard added are dropped from the entry FIRST, so
    the retry asks for nothing that has to be stacked.
    ``dictDownloadControl`` lets the caller cancel and watch the
    deposit download (``resumableDownload.fdictCreateDownloadControl``).
    """
    from vaibify.config.registryManager import (
        S_IMAGE_SOURCE_ARCHIVE,
        S_IMAGE_SOURCE_KEY,
        fnUpdateImageSource,
    )
    fnReport = fnReportLine or _fnDiscardLine
    dictSource = dict(dictProject.get(S_IMAGE_SOURCE_KEY) or {})
    if dictSource.get("sSource") != S_IMAGE_SOURCE_ARCHIVE:
        raise PinnedImageAcquisitionRefusedError(
            "this project's image is built from its Dockerfile, not "
            "obtained; use Rebuild"
        )
    if bWithoutAdditions and dictSource.get("listAdditionalAgents"):
        fnReport(
            "dropping the added agents ("
            + ", ".join(dictSource["listAdditionalAgents"])
            + ") so the obtained image runs as the author pinned it"
        )
        fnUpdateImageSource(dictProject["sName"], {"listAdditionalAgents": []})
        dictSource["listAdditionalAgents"] = []
    bEmulationConsented = bool(bAllowEmulation) and bool(
        dictSource.get("bAllowEmulation"),
    )
    if dockerDisposable is None:
        dockerDisposable = disposableContainer.fdockerCreateDisposableClient()
    dictStaged = fdictObtainAndStage(
        dictProject, dictSource, bEmulationConsented, fnReport,
        dockerDisposable, sDockerDir, S_LATEST_TAG, dictDownloadControl,
    )
    _fnDescribeBeforeEnabling(
        dictProject, dictStaged["listProven"], dictStaged["listChain"], fnReport,
    )
    return _fdictCommitOriginRecord(
        dictProject, dictSource, dictStaged["dictPinned"],
        dictStaged["dictAcquired"], dictStaged["sBaseImageId"],
        dictStaged["sRunningImageId"], dictStaged["listResolvedOverlays"],
        fnReport,
    )


def fdictObtainAndStage(
    dictProject, dictSource, bEmulationConsented, fnReport, dockerDisposable,
    sDockerDir, sTag, dictDownloadControl=None,
):
    """Steps 1-5: recheck, obtain, prove, stack, tag as ``<name>:<sTag>``.

    Writes nothing to the registry and nothing to ``vaibify.yml``; the
    only mutation is the tag, which host mode never reads. Returns the
    facts the commit needs::

        {"sBaseImageId", "sRunningImageId", "listProven", "listChain",
         "listResolvedOverlays", "dictAcquired", "dictPinned"}
    """
    dictPinned = _fdictRecheckTheClone(dictProject, dictSource, fnReport)
    _fnRefuseBeforeDownloadWhenTheStackNeedsBuildx(
        dictProject, dictSource, dictPinned, sDockerDir, dockerDisposable,
    )
    dictAcquired = _fdictObtain(
        dictProject, dictPinned, bEmulationConsented, fnReport,
        dockerDisposable, dictDownloadControl,
    )
    if dictDownloadControl is not None:
        from vaibify.reproducibility import resumableDownload
        from vaibify.reproducibility.imageAcquisition import S_STAGE_PREPARING
        resumableDownload.fnEnterStage(dictDownloadControl, S_STAGE_PREPARING)
    sBaseImageId = dictAcquired["sImageId"]
    dictBase = disposableContainer.fdictInspectImage(dockerDisposable, sBaseImageId)
    listProven = flistProveOverlayBaseline(
        (dictBase or {}).get("dictLabels") or {},
        list(dictSource.get("listAuthorOverlays") or []),
        list(dictSource.get("listAdditionalAgents") or []),
        sDockerDir, fnReport,
    )
    # An unproven baseline (None) stacks nothing: the base runs as
    # obtained, which is the only honest thing to do with an image
    # whose contents cannot be established.
    listChain = [] if listProven is None else flistResolveDifferentialChain(
        dictProject["sConfigPath"], listProven,
        list(dictSource.get("listAdditionalAgents") or []),
    )
    listProven = list(listProven or [])
    sRunningImageId = _fsStackAndResolve(
        dictProject, sBaseImageId, listChain, listProven,
        dictAcquired["sRequiredPlatform"], sDockerDir, fnReport,
        dockerDisposable,
    )
    _fnRefuseAgentsThatReachTheEnvironment(
        sBaseImageId, sRunningImageId, fnReport,
    )
    fnReport(f"Tagging {sRunningImageId[:19]}... as {dictProject['sName']}:{sTag}")
    disposableContainer.fnTagImage(
        dockerDisposable, sRunningImageId, dictProject["sName"], sTag,
    )
    return {
        "sBaseImageId": sBaseImageId,
        "sRunningImageId": sRunningImageId,
        "listProven": listProven,
        "listChain": listChain,
        "listResolvedOverlays": imageBuilder.flistCanonicalizeOverlaySet(
            listProven + listChain,
        ),
        "dictAcquired": dictAcquired,
        "dictPinned": dictPinned,
    }


def _fnRefuseBeforeDownloadWhenTheStackNeedsBuildx(
    dictProject, dictSource, dictPinned, sDockerDir, dockerDisposable,
):
    """Step 1b: refuse a stack only buildx can build, before the download.

    Adding Claude Code to an Intel image on an ARM daemon uses a
    Dockerfile Docker's legacy builder cannot build, and the image the
    stack sits on can be gigabytes. The chain is resolved here against
    the CANDIDATE baseline recorded at conversion; the image's own
    labels decide the chain actually stacked, and the build refuses
    again there. The residual: a baseline that later fails its proof
    stacks nothing, so this refusal can ask for buildx a run that
    ended up not stacking would not have needed.
    """
    from vaibify.cli.configLoader import fsDockerDir
    listChain = flistResolveDifferentialChain(
        dictProject["sConfigPath"],
        list(dictSource.get("listAuthorOverlays") or []),
        list(dictSource.get("listAdditionalAgents") or []),
    )
    if not listChain:
        return
    for sDockerfile in imageBuilder.flistStackedOverlayDockerfiles(
        listChain, sDockerDir or fsDockerDir(),
        dictPinned["sRequiredPlatform"],
        disposableContainer.fsReadDaemonArchitecture(dockerDisposable),
    ):
        imageBuilder.fnRefuseWhenBuildKitIsNeeded(sDockerfile)


def _fdictRecheckTheClone(dictProject, dictSource, fnReport):
    """Step 1: the clone is still obtainable and still pins the same image."""
    from vaibify.reproducibility.reproductionSource import (
        fdictDescribePinnedEnvironment,
    )
    dictPinned = fdictDescribePinnedEnvironment(dictProject["sDirectory"])
    if not dictPinned["bObtainable"]:
        raise PinnedImageAcquisitionRefusedError(
            "the clone can no longer be containerized from its pinned "
            "image: " + dictPinned["sRefusal"]
        )
    sPinned = dictPinned["sPinnedImageReference"]
    if sPinned != str(dictSource.get("sPinnedImageReference") or ""):
        raise PinnedImageAcquisitionRefusedError(
            "the envelope now pins a different image "
            f"({sPinned}) than the one this project was containerized "
            f"from ({dictSource.get('sPinnedImageReference')}); convert "
            "the project again so the choice is made against the "
            "current envelope"
        )
    fnReport(f"Pinned image: {sPinned} ({dictPinned['sRequiredPlatform']})")
    return dictPinned


def _fdictObtain(
    dictProject, dictPinned, bEmulationConsented, fnReport, dockerDisposable,
    dictDownloadControl=None,
):
    """Step 2: walk the published chain, reporting every link."""
    from vaibify.reproducibility.environmentSnapshot import (
        fdictReadEnvironmentJson,
    )
    from vaibify.reproducibility.imageAcquisition import (
        ImageAcquisitionRefusedError,
        fdictAcquirePinnedImage,
    )
    from vaibify.reproducibility.repoFiles import HostRepoFiles
    dictEnvironment = fdictReadEnvironmentJson(
        HostRepoFiles(dictProject["sDirectory"]),
    ) or {}
    dictKeywords = (
        {"dictDownloadControl": dictDownloadControl}
        if dictDownloadControl is not None else {}
    )
    try:
        dictAcquired = fdictAcquirePinnedImage(
            dictEnvironment, dictPinned["sRequiredPlatform"],
            _ffnReportAcquisitionEventsTo(fnReport),
            bEmulationConsented, dockerDisposable, **dictKeywords,
        )
    except ImageAcquisitionRefusedError as error:
        raise PinnedImageAcquisitionRefusedError(
            f"the pinned image could not be obtained: {error}",
            dictFailure=error.dictFailure, listAttempts=error.listAttempts,
        ) from error
    if not dictAcquired.get("sImageId"):
        raise PinnedImageAcquisitionRefusedError(
            "the daemon reported no image ID for the obtained image, so "
            "nothing can be tagged as this project's"
        )
    return dictAcquired


def _ffnReportAcquisitionEventsTo(fnReport):
    """Return the chain's status callback, writing one log line per event worth one.

    The window's header shows the live bytes, rate and time left, so
    the log keeps a single line per tenth of the download -- enough to
    show it advancing -- rather than a line every few megabytes
    repeating the header.
    """
    dictLastTenth = {"iTenth": None}

    def fnReportEvent(dictEvent):
        if dictEvent.get("sPhase") == "downloading":
            iTotal = int(dictEvent.get("iTotalBytes") or 0)
            iTenth = 10 * int(dictEvent.get("iBytes") or 0) // iTotal if iTotal else 0
            if iTenth == dictLastTenth["iTenth"]:
                return
            dictLastTenth["iTenth"] = iTenth
        fnReport(_fsDescribeAcquisitionEvent(dictEvent))
    return fnReportEvent


def _fsDescribeAcquisitionEvent(dictEvent):
    """Render one chain event as a progress line."""
    sPhase = str(dictEvent.get("sPhase") or "")
    if sPhase == "attempt":
        return _fsDescribeAttempt(dictEvent)
    if sPhase == "downloading":
        return _fsDescribeDownloading(dictEvent)
    if sPhase in (
        "downloadRetrying", "downloadRestarting", "downloadSlow",
        "locatingRetrying",
    ):
        return str(dictEvent.get("sSentence") or sPhase)
    if sPhase == "locating":
        return (
            "asking Zenodo where the archived image is (try "
            f"{int(dictEvent.get('iAttempt') or 1)})"
        )
    if sPhase == "resuming":
        return (
            "resuming a download interrupted earlier: "
            f"{int(dictEvent.get('iBytes') or 0) / 1e6:.0f} MB of "
            f"{int(dictEvent.get('iTotalBytes') or 0) / 1e6:.0f} MB are "
            "already here"
        )
    if sPhase == "reused":
        return (
            "the archived image was downloaded, checked against its hash "
            f"and loaded here on {str(dictEvent.get('sLoadedIso'))[:10]}, "
            "and Docker still holds it, so it is not downloaded again"
        )
    if sPhase == "pulling":
        return f"pulling {dictEvent.get('sImageReference')} for {dictEvent.get('sPlatform')}"
    if sPhase == "acquired":
        return (
            f"obtained {dictEvent.get('sImageReference')} from the "
            f"{dictEvent.get('sObtainedFrom')}"
            + (" (emulated)" if dictEvent.get("bEmulated") else "")
        )
    return sPhase


def _fsDescribeDownloading(dictEvent):
    """One progress line: the bytes and the share done (the header has the rest)."""
    iBytes = int(dictEvent.get("iBytes") or 0)
    iTotal = int(dictEvent.get("iTotalBytes") or 0)
    sLine = (
        f"downloading the archived image: {iBytes / 1e6:.0f} MB of "
        f"{iTotal / 1e6:.0f} MB"
    )
    if iTotal:
        sLine += f" ({100 * iBytes // iTotal}%)"
    return sLine


def _fsDescribeAttempt(dictEvent):
    """Say what one source of the chain answered, and what happens next.

    A source that does not have the image is the chain working, not a
    failure: an image that was archived rather than published to a
    registry is never on one, so the first line of every such download
    used to read "registry pull: failed" above a download that then
    succeeded. Docker's own words stay in brackets for anyone
    diagnosing a source that should have answered.
    """
    from vaibify.reproducibility.imageAcquisition import (
        S_LINK_ARCHIVE,
        S_LINK_LOCAL,
        S_LINK_REGISTRY,
    )
    sLink = str(dictEvent.get("sLink") or "")
    sDetail = str(dictEvent.get("sDetail") or "")
    sSaid = f" (Docker said: {sDetail})" if sDetail else ""
    if sLink == S_LINK_REGISTRY and _fbReadsAsNotFound(sDetail):
        # A registry that simply does not have the image is the chain
        # working; its 404 stays in the hub log and the whole-chain
        # refusal, never on the progress line (2026-10-13).
        sSaid = ""
    if dictEvent.get("bSucceeded"):
        return f"{sLink}: served"
    dictNotAvailable = {
        S_LINK_REGISTRY: (
            "no registry has this image -- normal for an image that was "
            "archived rather than published -- so vaibify tries the "
            "next source"
        ),
        S_LINK_ARCHIVE: (
            "the archived copy could not be used, so vaibify looks for a "
            "copy already on this machine"
        ),
        S_LINK_LOCAL: "no copy of the image is on this machine either",
    }
    return dictNotAvailable.get(sLink, f"{sLink}: not available") + sSaid


_T_NOT_FOUND_WORDS = (
    "404", "not found", "manifest unknown", "pull access denied",
    "repository does not exist", "no such image",
)


def _fbReadsAsNotFound(sDetail):
    """True when a registry's words say only that it does not have the image."""
    sLower = sDetail.lower()
    return any(sWords in sLower for sWords in _T_NOT_FOUND_WORDS)


def flistProveOverlayBaseline(
    dictLabels, listCandidate, listAdditional, sDockerDir=None,
    fnReportLine=None,
):
    """Step 3: return the PROVEN overlay set, ``None`` if unproven, or refuse.

    The image's own label proves it (and must EQUAL the candidate); a
    recomputed recipe fingerprint over this vaibify's shipped texts
    proves the candidate for an image built before the label existed;
    otherwise the set is unproven, which is allowed only when nothing
    is to be stacked on it -- and then the answer is ``None``, so the
    caller stacks nothing at all rather than "everything but nothing".
    """
    from vaibify.reproducibility.dockerfileComposer import (
        S_OVERLAYS_IMAGE_LABEL,
        S_RECIPE_IMAGE_LABEL,
        flistParseOverlaysLabel,
    )
    fnReport = fnReportLine or _fnDiscardLine
    sLabel = (dictLabels or {}).get(S_OVERLAYS_IMAGE_LABEL)
    if sLabel is not None:
        try:
            listLabelled = flistParseOverlaysLabel(
                sLabel, imageBuilder.flistCanonicalOverlayOrder(),
            )
        except ValueError as error:
            raise PinnedImageAcquisitionRefusedError(
                f"the obtained image is malformed: {error}; nothing was "
                "tagged"
            ) from error
        if listLabelled != imageBuilder.flistCanonicalizeOverlaySet(listCandidate):
            raise PinnedImageAcquisitionRefusedError(
                "the repository says the image was built with the overlays "
                f"[{', '.join(listCandidate) or 'none'}] and the image "
                f"itself says [{', '.join(listLabelled) or 'none'}]. A "
                "recipe that disagrees with its pinned image is the defect "
                "the Dockerfile-provenance row exists to catch; running it "
                "would put a container on screen whose vaibify.yml "
                "misdescribes it. Re-export the Dockerfile in the author's "
                "repository, or switch to building from the Dockerfile."
            )
        fnReport(
            "the image's own label proves its overlays: "
            + (", ".join(listLabelled) or "none")
        )
        return listLabelled
    sRecipeLabel = str((dictLabels or {}).get(S_RECIPE_IMAGE_LABEL) or "")
    if sRecipeLabel and sDockerDir and (
        imageBuilder.fsComputeShippedRecipeFingerprint(
            sDockerDir, list(listCandidate),
        ) == sRecipeLabel
    ):
        fnReport(
            "the image's recipe fingerprint proves its overlays: "
            + (", ".join(listCandidate) or "none")
        )
        return list(listCandidate)
    if not listAdditional:
        fnReport(
            "the image carries no overlays label and its recipe could not "
            "be matched; no agents were requested, so the base is used "
            "as obtained"
        )
        # None, not []: an empty PROVEN set would let the differential
        # resolver re-stack the author's own overlays onto an image
        # that (per its recipe) already holds them, and the result
        # would be a derived image where the pin was wanted.
        return None
    raise PinnedImageAcquisitionRefusedError(
        "the obtained image carries no overlays label and its recipe does "
        "not match this vaibify's shipped Dockerfiles for "
        f"[{', '.join(listCandidate) or 'none'}], so which agents it "
        "already holds cannot be proven and nothing can safely be stacked "
        "on it. Re-obtain the pinned image without adding agents, or "
        "switch to building from the Dockerfile.",
        sAction=S_ACTION_REOBTAIN_WITHOUT_ADDITIONS,
    )


def flistResolveDifferentialChain(sConfigPath, listProven, listAdditional):
    """Step 4's plan: the resolved chain minus the proven set, in order.

    The author's config with the additions' feature keys enabled is
    built in MEMORY, resolved by the same resolver a build uses (so a
    prerequisite such as ``node`` for Gemini rides in), and the proven
    set is subtracted -- including a prerequisite the image already
    holds.
    """
    from vaibify.cli.configLoader import fconfigLoadFromPath
    configMerged = fconfigLoadFromPath(sConfigPath)
    for sAgent in listAdditional:
        sField = imageBuilder.fsFeatureFieldForOverlay(sAgent)
        if sField:
            setattr(configMerged.features, sField, True)
    setProven = set(listProven)
    return [
        sOverlay for sOverlay in imageBuilder.flistDetermineOverlays(configMerged)
        if sOverlay not in setProven
    ]


def _fsStackAndResolve(
    dictProject, sBaseImageId, listChain, listProven, sPlatform,
    sDockerDir, fnReport, dockerDisposable,
):
    """Step 4: build the chain on the base; return the running image ID."""
    if not listChain:
        fnReport("no overlay to stack; the obtained image runs as-is")
        return sBaseImageId
    from vaibify.cli.commandBuild import (
        fnDiscardBuildContext,
        fnPrepareBuildContext,
        fsStageBuildContext,
    )
    from vaibify.cli.configLoader import fconfigLoadFromPath, fsDockerDir
    fnReport(
        "stacking " + ", ".join(listChain) + " on the obtained image"
        + " (under emulation the installers run under qemu; this is slow)"
    )
    configProject = fconfigLoadFromPath(dictProject["sConfigPath"])
    sStagedDir = fsStageBuildContext(configProject, sDockerDir or fsDockerDir())
    try:
        fnPrepareBuildContext(configProject, sStagedDir, dictProject["sDirectory"])
        # The chain is stacked in BUILD order (a prerequisite before
        # the agent that needs it); each stage's label records the
        # SET so far in canonical order, which is the only form its
        # parser accepts.
        sLastReference = imageBuilder.fsStackOverlaysOnObtainedBase(
            dictProject["sName"], sBaseImageId, listChain, sStagedDir,
            sPlatform, list(listProven),
            sDaemonArchitecture=disposableContainer.fsReadDaemonArchitecture(
                dockerDisposable),
        )
    finally:
        fnDiscardBuildContext(sStagedDir)
    dictRunning = disposableContainer.fdictInspectImage(
        dockerDisposable, sLastReference,
    )
    if not dictRunning or not dictRunning.get("sId"):
        raise PinnedImageAcquisitionRefusedError(
            f"the overlay build finished but {sLastReference} could not be "
            "inspected, so nothing was tagged"
        )
    return dictRunning["sId"]


def _fnRefuseAgentsThatReachTheEnvironment(sBaseImageId, sRunningImageId, fnReport):
    """Step 4b: the stacked agents leave the author's environment untouched.

    The reader's agents are the reader's choice precisely because they
    cannot change what the author's environment computes; this is where
    that is checked, on the image actually built, before anything is
    tagged. A check that could not run refuses too -- an unrun check
    establishes nothing.
    """
    from vaibify.reproducibility.agentLayerSeparation import (
        AgentLayerSeparationError,
        fdictCheckAgentLayerSeparation,
        fsDescribeViolations,
    )
    if sRunningImageId == sBaseImageId:
        return
    fnReport("checking that the added agents leave the author's environment untouched")
    try:
        dictChecked = fdictCheckAgentLayerSeparation(sBaseImageId, sRunningImageId)
    except AgentLayerSeparationError as errorCheck:
        raise PinnedImageAcquisitionRefusedError(
            "the added agents could not be checked against the author's "
            f"environment ({errorCheck}), so nothing was tagged.",
            sAction=S_ACTION_REOBTAIN_WITHOUT_ADDITIONS,
        ) from errorCheck
    if dictChecked["listViolations"]:
        raise PinnedImageAcquisitionRefusedError(
            "the added agents reach into the author's environment: "
            + fsDescribeViolations(dictChecked["listViolations"])
            + ". Nothing was tagged. Re-obtain the pinned image without "
            "them, or choose other agents.",
            sAction=S_ACTION_REOBTAIN_WITHOUT_ADDITIONS,
        )


def _fnDescribeBeforeEnabling(dictProject, listProven, listChain, fnReport):
    """Step 6: vaibify.yml names the agents the image holds; registry the chain."""
    from vaibify.config.registryManager import fnUpdateImageSource
    listResolved = imageBuilder.flistCanonicalizeOverlaySet(
        list(listProven) + list(listChain),
    )
    listAgents = [
        sOverlay for sOverlay in listResolved
        if sOverlay in imageBuilder.T_AGENT_OVERLAY_NAMES
    ]
    _fnWriteAgentInstallKeys(dictProject["sConfigPath"], listAgents)
    fnUpdateImageSource(
        dictProject["sName"], {"listResolvedOverlays": listResolved},
    )
    fnReport(
        "vaibify.yml now names the agents the image holds: "
        + (", ".join(listAgents) or "none")
    )


def _fnWriteAgentInstallKeys(sConfigPath, listAgents):
    """Set ``features.<agent>: true`` for every agent the image holds.

    An author-installed agent cannot be uninstalled from bytes that
    hold it, so the keys are the UNION of the proven set's agents and
    the additions; every other key in the file is left exactly as the
    author committed it.
    """
    import yaml
    with open(sConfigPath, "r", encoding="utf-8") as fileHandle:
        dictConfig = yaml.safe_load(fileHandle) or {}
    dictFeatures = dict(dictConfig.get("features") or {})
    for sAgent in listAgents:
        dictFeatures[sAgent] = True
    dictConfig["features"] = dictFeatures
    from vaibify.config.projectConfig import fconfigFromYamlDict, fnSaveToFile
    fnSaveToFile(fconfigFromYamlDict(dictConfig), sConfigPath)


def _fdictCommitOriginRecord(
    dictProject, dictSource, dictPinned, dictAcquired, sBaseImageId,
    sRunningImageId, listResolved, fnReport,
):
    """Step 7: the origin record, the launch guard's admission."""
    from vaibify.config.imageOrigins import (
        fdictBuildOriginRecord,
        fnWriteOriginRecord,
    )
    dictRecord = fdictBuildOriginRecord(
        dictPinned["sPinnedImageReference"], sBaseImageId, sRunningImageId,
        listResolved, dictAcquired["sObtainedFrom"],
        dictPinned["sZenodoService"], dictPinned["sDepositVersionDoi"],
        dictAcquired["sRequiredPlatform"], dictAcquired["sObtainedPlatform"],
        dictAcquired["bEmulated"],
    )
    fnWriteOriginRecord(dictProject["sName"], dictRecord)
    fnReport(
        f"origin recorded: base {sBaseImageId[:19]}..., running "
        f"{sRunningImageId[:19]}..., obtained from the "
        f"{dictAcquired['sObtainedFrom']}"
    )
    return dictRecord
