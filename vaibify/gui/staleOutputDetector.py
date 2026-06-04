"""Stale-output detection across undeclared step boundaries.

The poll loop already collects every output file's mtime and the
workflow's declared upstream graph. This module asks the complementary
question: for each consumer step, is there a *non-declared* producer
step whose outputs are newer than this step's outputs? If yes, the
plot files (or any data outputs) are stale relative to data the
workflow has implicitly come to depend on.

The detector takes no I/O. It reads ``dictNewModTimes`` (already
collected by the poll) and ``dictDeclaredUpstream`` (already built by
``workflowManager.fdictBuildDirectDependencies``). It returns a list
of per-file advisories that the dashboard renders next to the
offending files and as suggested-upstream rows in the dependency list.

The module is deliberately a leaf — zero intra-package imports — so
any module in the package can call it without a cycle.
"""

import posixpath


__all__ = [
    "flistStaleOutputAdvisories",
]


F_DEFAULT_MARGIN_SECONDS = 60.0


def flistStaleOutputAdvisories(
    dictWorkflow, dictNewModTimes, dictDeclaredUpstream,
    fMarginSeconds=F_DEFAULT_MARGIN_SECONDS,
):
    """Return advisories where a consumer's outputs trail a non-declared producer's.

    ``dictDeclaredUpstream`` maps ``iConsumerIndex -> set(iUpstreamIndex)``;
    the detector skips every pair already in that map. ``dictNewModTimes``
    is the mtime dict the poll already gathered, keyed by repo-relative
    or container-absolute output path. The detector matches outputs
    purely by step membership; it does not re-stat anything.
    """
    listSteps = dictWorkflow.get("listSteps", []) or []
    sRepoRoot = dictWorkflow.get("sProjectRepoPath", "") or ""
    dictMtimesByIndex = _fdictGroupMtimesByStep(
        listSteps, dictNewModTimes, sRepoRoot,
    )
    return _flistDetectAdvisories(
        listSteps, dictMtimesByIndex, dictDeclaredUpstream, fMarginSeconds,
    )


def _flistDetectAdvisories(
    listSteps, dictMtimesByIndex, dictDeclaredUpstream, fMarginSeconds,
):
    """Return advisories produced by the consumer/producer mtime crossover."""
    listAdvisories = []
    for iConsumer in range(len(listSteps)):
        listEntries = _flistConsumerAdvisories(
            iConsumer, listSteps, dictMtimesByIndex,
            dictDeclaredUpstream, fMarginSeconds,
        )
        listAdvisories.extend(listEntries)
    return listAdvisories


def _flistConsumerAdvisories(
    iConsumer, listSteps, dictMtimesByIndex,
    dictDeclaredUpstream, fMarginSeconds,
):
    """Return advisories for one consumer step against every viable producer."""
    dictConsumerMtimes = dictMtimesByIndex.get(iConsumer, {})
    if not dictConsumerMtimes:
        return []
    setDeclared = dictDeclaredUpstream.get(iConsumer, set()) or set()
    listAdvisories = []
    for iProducer in range(len(listSteps)):
        dictEntry = _fdictMaybeAdvisory(
            iConsumer, iProducer, dictConsumerMtimes,
            dictMtimesByIndex, setDeclared,
            listSteps, fMarginSeconds,
        )
        if dictEntry is not None:
            listAdvisories.append(dictEntry)
    return listAdvisories


def _fdictMaybeAdvisory(
    iConsumer, iProducer, dictConsumerMtimes, dictMtimesByIndex,
    setDeclared, listSteps, fMarginSeconds,
):
    """Return one advisory dict iff the producer is undeclared and newer."""
    if iProducer == iConsumer or iProducer in setDeclared:
        return None
    if _fbStepsAreSiblings(listSteps[iConsumer], listSteps[iProducer]):
        return None
    dictProducerMtimes = dictMtimesByIndex.get(iProducer, {})
    fProducerMax = _fMaxMtime(dictProducerMtimes)
    if fProducerMax <= 0:
        return None
    listOffending, fAgeDelta = _flistOffendingForPair(
        dictConsumerMtimes, fProducerMax, fMarginSeconds,
    )
    if not listOffending:
        return None
    return _fdictBuildAdvisoryEntry(
        iConsumer, iProducer, listOffending, fAgeDelta,
    )


def _fdictBuildAdvisoryEntry(
    iConsumer, iProducer, listOffending, fAgeDelta,
):
    """Build the advisory dict shape consumed by the dashboard frontend."""
    return {
        "iConsumerStepIndex": iConsumer,
        "iLikelyProducerStepIndex": iProducer,
        "listOffendingFiles": sorted(listOffending),
        "fAgeDeltaSeconds": fAgeDelta,
    }


def _flistOffendingForPair(
    dictConsumerMtimes, fProducerMax, fMarginSeconds,
):
    """Return (offending_paths, max_age_delta) for one consumer/producer pair."""
    listOffending = []
    fMaxDelta = 0.0
    for sPath, fMtime in dictConsumerMtimes.items():
        if fMtime <= 0:
            continue
        fDelta = fProducerMax - fMtime
        if fDelta > fMarginSeconds:
            listOffending.append(sPath)
            if fDelta > fMaxDelta:
                fMaxDelta = fDelta
    return listOffending, fMaxDelta


def _fbStepsAreSiblings(dictStepA, dictStepB):
    """Return True when two steps look like co-running siblings.

    Sibling heuristic: identical sets of output basenames and a common
    parent directory. Cuts noise from parallel A/B vconverge-style
    runs whose outputs land near each other in time.
    """
    setA = _fsetOutputBasenames(dictStepA)
    setB = _fsetOutputBasenames(dictStepB)
    if not setA or setA != setB:
        return False
    sParentA = posixpath.dirname(dictStepA.get("sDirectory", "") or "")
    sParentB = posixpath.dirname(dictStepB.get("sDirectory", "") or "")
    return sParentA == sParentB


def _fsetOutputBasenames(dictStep):
    """Return basenames of saDataFiles + saPlotFiles for sibling comparison."""
    setNames = set()
    for sKey in ("saDataFiles", "saPlotFiles"):
        for sPath in dictStep.get(sKey, []) or []:
            sBase = posixpath.basename(sPath)
            if sBase and "{" not in sBase:
                setNames.add(sBase)
    return setNames


def _fdictGroupMtimesByStep(listSteps, dictNewModTimes, sRepoRoot):
    """Return ``{iStepIndex: {sFilePath: fMtime}}`` for all declared outputs."""
    dictResult = {}
    for iIndex, dictStep in enumerate(listSteps):
        dictResult[iIndex] = _fdictMtimesForStep(
            dictStep, dictNewModTimes, sRepoRoot,
        )
    return dictResult


def _fdictMtimesForStep(dictStep, dictNewModTimes, sRepoRoot):
    """Return ``{sPath: fMtime}`` for one step's declared outputs."""
    dictResult = {}
    sDirectory = dictStep.get("sDirectory", "") or ""
    for sKey in ("saDataFiles", "saPlotFiles"):
        for sRelPath in dictStep.get(sKey, []) or []:
            if "{" in sRelPath:
                continue
            fMtime = _fLookupMtime(sRelPath, sDirectory, sRepoRoot, dictNewModTimes)
            if fMtime > 0:
                dictResult[sRelPath] = fMtime
    return dictResult


def _fLookupMtime(sRelPath, sStepDirectory, sRepoRoot, dictNewModTimes):
    """Look up a file's mtime under either of its plausible key forms."""
    listCandidates = _flistMtimeCandidateKeys(
        sRelPath, sStepDirectory, sRepoRoot,
    )
    for sKey in listCandidates:
        if sKey in dictNewModTimes:
            return _fParseMtime(dictNewModTimes[sKey])
    return 0.0


def _flistMtimeCandidateKeys(sRelPath, sStepDirectory, sRepoRoot):
    """Return plausible dictNewModTimes keys for a declared output file."""
    listCandidates = [sRelPath]
    if sStepDirectory:
        listCandidates.append(posixpath.join(sStepDirectory, sRelPath))
    if sRepoRoot:
        listCandidates.append(posixpath.join(sRepoRoot, sRelPath))
        if sStepDirectory:
            listCandidates.append(
                posixpath.join(sRepoRoot, sStepDirectory, sRelPath),
            )
    return listCandidates


def _fParseMtime(value):
    """Convert a dictNewModTimes value to a float; missing -> 0.0."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _fMaxMtime(dictMtimes):
    """Return the largest float mtime in a step's mtime dict (0.0 if empty)."""
    fMax = 0.0
    for fMtime in dictMtimes.values():
        if fMtime > fMax:
            fMax = fMtime
    return fMax
