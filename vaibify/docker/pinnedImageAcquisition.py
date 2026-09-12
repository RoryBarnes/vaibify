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
   platform, labelling the result as derived from the base.
5. Tag the result as ``<projectName>:latest``, by ID.
6. Describe before enabling: the agent install keys go into
   ``vaibify.yml`` and the resolved chain into the registry entry.
7. Commit: the origin record, the launch guard's admission.

A failure at any step leaves a registered-but-unbuilt project, exactly
as a failed build does; the tile still reads as obtained, so the next
click retries. Nothing about a failure is persisted beyond the
progress record.
"""

__all__ = [
    "PinnedImageAcquisitionRefusedError",
    "fdictAcquireForProject",
    "flistProveOverlayBaseline",
    "flistResolveDifferentialChain",
]

import logging

from vaibify.docker import disposableContainer
from vaibify.docker import imageBuilder


logger = logging.getLogger(__name__)


class PinnedImageAcquisitionRefusedError(Exception):
    """The acquisition stopped before the project gained an image.

    Derives from ``Exception``, never ``OSError``: a refusal swallowed
    by an ``except OSError`` is how a control decision downgrades into
    an I/O hiccup.
    """


def _fnDiscardLine(sLine):
    """Drop a progress line for callers that report nothing."""
    del sLine


def fdictAcquireForProject(
    dictProject, bAllowEmulation, fnReportLine=None, dockerDisposable=None,
    sDockerDir=None,
):
    """Run the whole lane for one registered project; return its record.

    ``dictProject`` is the registry entry, whose ``dictImageSource``
    says the project's image is obtained and carries the candidate
    baseline captured at conversion. Emulation is consented TWICE: the
    entry's flag from the wizard and this call's from the button.
    """
    from vaibify.config.registryManager import (
        S_IMAGE_SOURCE_ARCHIVE,
        S_IMAGE_SOURCE_KEY,
    )
    fnReport = fnReportLine or _fnDiscardLine
    dictSource = dict(dictProject.get(S_IMAGE_SOURCE_KEY) or {})
    if dictSource.get("sSource") != S_IMAGE_SOURCE_ARCHIVE:
        raise PinnedImageAcquisitionRefusedError(
            "this project's image is built from its Dockerfile, not "
            "obtained; use Rebuild"
        )
    bEmulationConsented = bool(bAllowEmulation) and bool(
        dictSource.get("bAllowEmulation"),
    )
    dictPinned = _fdictRecheckTheClone(dictProject, dictSource, fnReport)
    if dockerDisposable is None:
        dockerDisposable = disposableContainer.fdockerCreateDisposableClient()
    dictAcquired = _fdictObtain(
        dictProject, dictPinned, bEmulationConsented, fnReport,
        dockerDisposable,
    )
    sBaseImageId = dictAcquired["sImageId"]
    dictBase = disposableContainer.fdictInspectImage(dockerDisposable, sBaseImageId)
    listProven = flistProveOverlayBaseline(
        (dictBase or {}).get("dictLabels") or {},
        list(dictSource.get("listAuthorOverlays") or []),
        list(dictSource.get("listAdditionalAgents") or []),
        sDockerDir, fnReport,
    )
    listChain = flistResolveDifferentialChain(
        dictProject["sConfigPath"], listProven,
        list(dictSource.get("listAdditionalAgents") or []),
    )
    sRunningImageId = _fsStackAndResolve(
        dictProject, sBaseImageId, listChain, listProven,
        dictAcquired["sRequiredPlatform"], sDockerDir, fnReport,
        dockerDisposable,
    )
    fnReport(f"Tagging {sRunningImageId[:19]}... as {dictProject['sName']}:latest")
    disposableContainer.fnTagImage(
        dockerDisposable, sRunningImageId, dictProject["sName"], "latest",
    )
    _fnDescribeBeforeEnabling(dictProject, listProven, listChain, fnReport)
    return _fdictCommitOriginRecord(
        dictProject, dictSource, dictPinned, dictAcquired, sBaseImageId,
        sRunningImageId, listProven + listChain, fnReport,
    )


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
    try:
        dictAcquired = fdictAcquirePinnedImage(
            dictEnvironment, dictPinned["sRequiredPlatform"],
            lambda dictEvent: fnReport(_fsDescribeAcquisitionEvent(dictEvent)),
            bEmulationConsented, dockerDisposable,
        )
    except ImageAcquisitionRefusedError as error:
        raise PinnedImageAcquisitionRefusedError(
            f"the pinned image could not be obtained: {error}"
        ) from error
    if not dictAcquired.get("sImageId"):
        raise PinnedImageAcquisitionRefusedError(
            "the daemon reported no image ID for the obtained image, so "
            "nothing can be tagged as this project's"
        )
    return dictAcquired


def _fsDescribeAcquisitionEvent(dictEvent):
    """Render one chain event as a progress line."""
    sPhase = str(dictEvent.get("sPhase") or "")
    if sPhase == "attempt":
        return (
            f"{dictEvent.get('sLink')}: "
            + ("served" if dictEvent.get("bSucceeded") else "failed")
            + (f" ({dictEvent.get('sDetail')})" if dictEvent.get("sDetail") else "")
        )
    if sPhase == "downloading":
        iBytes = int(dictEvent.get("iBytes") or 0)
        iTotal = int(dictEvent.get("iTotalBytes") or 0)
        return f"downloading the archived image: {iBytes} of {iTotal} bytes"
    if sPhase == "pulling":
        return f"pulling {dictEvent.get('sImageReference')} for {dictEvent.get('sPlatform')}"
    if sPhase == "acquired":
        return (
            f"obtained {dictEvent.get('sImageReference')} from the "
            f"{dictEvent.get('sObtainedFrom')}"
            + (" (emulated)" if dictEvent.get("bEmulated") else "")
        )
    return sPhase


def flistProveOverlayBaseline(
    dictLabels, listCandidate, listAdditional, sDockerDir=None,
    fnReportLine=None,
):
    """Step 3: return the PROVEN overlay set, or refuse.

    The image's own label proves it (and must EQUAL the candidate); a
    recomputed recipe fingerprint over this vaibify's shipped texts
    proves the candidate for an image built before the label existed;
    otherwise the set is unproven, which is allowed only when nothing
    is to be stacked on it.
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
        if listLabelled != list(listCandidate):
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
        return []
    raise PinnedImageAcquisitionRefusedError(
        "the obtained image carries no overlays label and its recipe does "
        "not match this vaibify's shipped Dockerfiles for "
        f"[{', '.join(listCandidate) or 'none'}], so which agents it "
        "already holds cannot be proven and nothing can safely be stacked "
        "on it. Re-obtain the pinned image without adding agents, or "
        "switch to building from the Dockerfile."
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
        sLastReference = imageBuilder.fsStackOverlaysOnObtainedBase(
            dictProject["sName"], sBaseImageId, listChain, sStagedDir,
            sPlatform, list(listProven) + list(listChain),
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


def _fnDescribeBeforeEnabling(dictProject, listProven, listChain, fnReport):
    """Step 6: vaibify.yml names the agents the image holds; registry the chain."""
    from vaibify.config.registryManager import fnUpdateImageSource
    listResolved = list(listProven) + list(listChain)
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
