"""What the deterministic generator writes for each test tier, and when it may.

Every requested tier is PLANNED before anything is written: its test
file, its standards, and whether it applies at all. Three answers are
possible, and each is a claim the dashboard will show:

* the tier applies -- its standards and stamped test file are written;
* the tier is N/A -- every declared output loaded and was analyzed and
  none holds anything of the tier's kind; nothing is written, and
  vaibify's own unedited files from an earlier generation are named
  for removal (ruling R3, R5);
* the answer is unknown -- an output could not be read; the tier is a
  named error, never N/A and never a placeholder test that passes.

Then every file a plan would replace or remove is checked for a
researcher's edits (the test file by its body hash, the standards by
their digest), and a single pending confirmation touches nothing.

Every generator helper is looked up on the ``testGenerator`` module at
call time, as ``llmTestGeneration`` does, so a patch of a
``testGenerator`` name applies here too.
"""

import posixpath

from . import testGenerator as moduleGenerator


__all__ = [
    "fdictWriteAllDeterministicTests",
]


def _fbEveryOutputWasAnalyzed(listdictReports):
    """Return True when every declared output loaded and was analyzed.

    Only then can "nothing of this kind was found" mean the category is
    inapplicable; an unloadable or unsupported output means the answer
    is unknown, which is never N/A.
    """
    return all(
        dictReport.get("bLoadable") and not dictReport.get("sError")
        for dictReport in listdictReports
    )


def _fsIntegrityNotApplicableReason(listdictReports, dictStandards):
    """Return why integrity is N/A, or ""; raise on a contradiction."""
    if dictStandards["listStandards"]:
        return ""
    if not listdictReports:
        return "This step declares no output data files to check."
    raise RuntimeError(
        "Integrity standards came out empty although the step's outputs "
        "exist; an existing output always yields existence and size "
        "checks."
    )


def _fsQualitativeNotApplicableReason(listdictReports, dictStandards):
    """Return why qualitative checks are N/A, or "" when they apply."""
    if dictStandards["listStandards"]:
        return ""
    if not listdictReports:
        return "This step declares no output data files to check."
    if not _fbEveryOutputWasAnalyzed(listdictReports):
        return ""
    return (
        "The outputs have no column names or JSON keys to check."
    )


def _fsQuantitativeNotApplicableReason(listdictReports):
    """Return why quantitative checks are N/A, or "" when they apply."""
    if not listdictReports:
        return "This step declares no output data files to check."
    if not _fbEveryOutputWasAnalyzed(listdictReports):
        return ""
    if any(dictReport.get("listBenchmarks") for dictReport in listdictReports):
        return ""
    return "The outputs hold no numeric values to compare."


def _fsQualitativeUnknownReason(listdictReports, dictStandards):
    """Return why qualitative checks could not be derived, or "".

    No column names or keys were found, but an output could not be read
    -- so whether it has any is unknown. That is an error to report,
    never N/A, and never a placeholder test that passes.
    """
    if dictStandards["listStandards"] or not listdictReports:
        return ""
    if _fbEveryOutputWasAnalyzed(listdictReports):
        return ""
    listUnread = [
        f"{dictReport.get('sFileName', '')}: "
        f"{dictReport.get('sError') or 'could not be loaded'}"
        for dictReport in listdictReports
        if not dictReport.get("bLoadable") or dictReport.get("sError")
    ]
    return (
        "Qualitative checks could not be derived because these outputs "
        "could not be read: " + "; ".join(listUnread) + "."
    )


def _fdictPlanIntegrity(listdictReports, sDirectory):
    dictStandards = moduleGenerator._fdictBuildIntegrityStandards(
        listdictReports,
    )
    dictStandards.setdefault(
        "sGenerator", moduleGenerator.S_GENERATOR_DETERMINISTIC,
    )
    return {
        "sKey": "dictIntegrity",
        "sTestPath": moduleGenerator.fsIntegrityTestPath(sDirectory),
        "sTestCode": moduleGenerator.fsBuildIntegrityTestCode(),
        "sStandardsPath": moduleGenerator.fsIntegrityStandardsPath(
            sDirectory,
        ),
        "dictStandards": dictStandards,
        "sNotApplicableReason": _fsIntegrityNotApplicableReason(
            listdictReports, dictStandards,
        ),
        "bRecordsStandardsPath": False,
    }


def _fdictPlanQualitative(listdictReports, sDirectory):
    dictStandards = moduleGenerator._fdictBuildQualitativeStandards(
        listdictReports,
    )
    dictStandards.setdefault(
        "sGenerator", moduleGenerator.S_GENERATOR_DETERMINISTIC,
    )
    return {
        "sError": _fsQualitativeUnknownReason(
            listdictReports, dictStandards,
        ),
        "sKey": "dictQualitative",
        "sTestPath": moduleGenerator.fsQualitativeTestPath(sDirectory),
        "sTestCode": moduleGenerator.fsBuildQualitativeTestCode(),
        "sStandardsPath": moduleGenerator.fsQualitativeStandardsPath(
            sDirectory,
        ),
        "dictStandards": dictStandards,
        "sNotApplicableReason": _fsQualitativeNotApplicableReason(
            listdictReports, dictStandards,
        ),
        "bRecordsStandardsPath": False,
    }


def _fsQuantitativeUnknownReason(sClassification, dictStandards):
    """Return why no quantitative standards could be derived, or "".

    No output could be read, so there is nothing to compare -- and that
    is unknown, never N/A. It used to write an empty standards file
    whose test skipped, which a run recorded as a pass. The researcher's
    route may still hand it to a consented model; the classification
    rides along so it can.
    """
    if sClassification != "unintrospectable":
        return ""
    return (
        "No quantitative standards could be derived ("
        + (dictStandards.get("sIntrospectorError")
           or "no output could be loaded") + ")."
    )


def _fdictPlanQuantitative(
    connectionDocker, sContainerId, listdictReports, sDirectory,
    fTolerance, sClassification,
):
    sStandardsPath = moduleGenerator.fsQuantitativeStandardsPath(sDirectory)
    dictStandards = moduleGenerator._fdictBuildQuantitativeStandards(
        listdictReports, fTolerance, sClassification,
    )
    dictStandards.setdefault(
        "sGenerator", moduleGenerator.S_GENERATOR_DETERMINISTIC,
    )
    return {
        "sError": _fsQuantitativeUnknownReason(sClassification, dictStandards),
        "dictErrorFields": {
            "sStochasticityClassification": sClassification,
            "sIntrospectorError": dictStandards.get("sIntrospectorError", ""),
        },
        "sKey": "dictQuantitative",
        "sTestPath": moduleGenerator.fsQuantitativeTestPath(sDirectory),
        "sTestCode": moduleGenerator.fsBuildQuantitativeTestCode(),
        "sStandardsPath": sStandardsPath,
        "dictStandards": moduleGenerator._fdictMergeWithExistingStandards(
            connectionDocker, sContainerId, sStandardsPath, dictStandards,
        ),
        "sNotApplicableReason": _fsQuantitativeNotApplicableReason(
            listdictReports,
        ),
        "bRecordsStandardsPath": True,
    }


def _flistPlanCategories(
    connectionDocker, sContainerId, sDirectory, listdictReports,
    fTolerance, sClassification, setCategories,
):
    """Return one write plan per requested category, in a fixed order."""
    listPlans = []
    if "integrity" in setCategories:
        listPlans.append(_fdictPlanIntegrity(listdictReports, sDirectory))
    if "qualitative" in setCategories:
        listPlans.append(_fdictPlanQualitative(listdictReports, sDirectory))
    if "quantitative" in setCategories:
        listPlans.append(_fdictPlanQuantitative(
            connectionDocker, sContainerId, listdictReports, sDirectory,
            fTolerance, sClassification,
        ))
    return listPlans


def _flistFilesAPlanWouldOverwrite(connectionDocker, sContainerId, dictPlan):
    """Return the researcher-modified files a plan would replace or remove.

    A category that applies replaces its test file, as it always has. A
    category that has become N/A removes both of vaibify's files, so
    BOTH must be vaibify's unedited output: the test file by its body
    hash, the standards file by its digest.
    """
    listModified = []
    if dictPlan.get("sError"):
        return listModified
    if not moduleGenerator._fbFileMatchesTemplate(
        connectionDocker, sContainerId, dictPlan["sTestPath"],
        dictPlan["sTestCode"],
    ):
        listModified.append(dictPlan["sTestPath"])
    if dictPlan["sNotApplicableReason"]:
        sStandardsText = moduleGenerator.fsReadFileFromContainer(
            connectionDocker, sContainerId, dictPlan["sStandardsPath"],
        )
        if isinstance(sStandardsText, str) and sStandardsText and not (
            moduleGenerator.fbStandardsTextIsUnmodified(sStandardsText)
        ):
            listModified.append(dictPlan["sStandardsPath"])
    return listModified


def _flistExistingFilesOfPlan(connectionDocker, sContainerId, dictPlan):
    """Return which of a plan's two files exist, for an N/A removal."""
    listPaths = [dictPlan["sTestPath"], dictPlan["sStandardsPath"]]
    listExists = connectionDocker.flistContainerPathsExist(
        sContainerId, listPaths,
    )
    return [
        sPath for sPath, bExists in zip(listPaths, listExists) if bExists
    ]


def _fdictCarryOutPlan(connectionDocker, sContainerId, dictPlan):
    """Write an applicable category's files, or describe an N/A one.

    An N/A category writes nothing: no placeholder that passes by
    asserting True. Its old files, already proven unmodified, are
    returned for the route to remove under its own admission.
    """
    if dictPlan.get("sError"):
        return dict(
            moduleGenerator._fdictErrorResult(dictPlan["sError"]),
            **dictPlan.get("dictErrorFields", {}),
        )
    if dictPlan["sNotApplicableReason"]:
        return {
            "sFilePath": "", "sStandardsPath": "", "saCommands": [],
            "sNotApplicableReason": dictPlan["sNotApplicableReason"],
            "listFilesToRemove": _flistExistingFilesOfPlan(
                connectionDocker, sContainerId, dictPlan,
            ),
        }
    sJsonContent = moduleGenerator.fsSerializeStandards(
        dictPlan["dictStandards"],
    )
    connectionDocker.fnWriteFile(
        sContainerId, dictPlan["sStandardsPath"],
        sJsonContent.encode("utf-8"),
    )
    connectionDocker.fnWriteFile(
        sContainerId, dictPlan["sTestPath"],
        dictPlan["sTestCode"].encode("utf-8"),
    )
    dictResult = {
        "sFilePath": dictPlan["sTestPath"],
        "sContent": dictPlan["sTestCode"],
        "sStandardsPath": dictPlan["sStandardsPath"],
        "sStandardsContent": sJsonContent,
        "saCommands": [
            "python3 -m pytest tests/"
            + posixpath.basename(dictPlan["sTestPath"])
        ],
    }
    if dictPlan["bRecordsStandardsPath"]:
        dictResult.update(moduleGenerator._fdictQuantitativeSummary(
            dictPlan["dictStandards"],
        ))
    return dictResult


def fdictWriteAllDeterministicTests(
    connectionDocker, sContainerId, sDirectory,
    listdictReports, fTolerance, bForceOverwrite, sProjectRepoPath,
    sClassification="deterministic", setCategories=None,
):
    """Write the requested deterministic test files; return result dict.

    ``setCategories`` names the short category names to write; None
    means all three, which is what every pre-existing caller wants. A
    category left out is absent from the result rather than empty in
    it, so ``_fnApplyGeneratedTests`` -- which copies only the keys it
    finds -- leaves that category's existing declaration untouched. A
    researcher asking for one tier must not have the other two
    silently rewritten underneath them.

    Every category is planned, and every file it would replace or
    remove is checked, BEFORE anything is written: a pending overwrite
    confirmation touches nothing at all, where it used to leave the
    other categories' files, and its own standards, already rewritten.
    """
    if setCategories is None:
        setCategories = set(moduleGenerator.T_GENERATED_TEST_CATEGORIES)
    listPlans = _flistPlanCategories(
        connectionDocker, sContainerId, sDirectory, listdictReports,
        fTolerance, sClassification, setCategories,
    )
    if not bForceOverwrite:
        dictConfirm = _fdictConfirmationNeeded(
            connectionDocker, sContainerId, listPlans,
        )
        if dictConfirm:
            return dictConfirm
    if any(
        not dictPlan["sNotApplicableReason"] and not dictPlan.get("sError")
        for dictPlan in listPlans
    ):
        moduleGenerator.fnEnsureTestsDirectory(
            connectionDocker, sContainerId, sDirectory,
        )
        moduleGenerator.fnWriteConftestMarker(
            connectionDocker, sContainerId, sDirectory, sProjectRepoPath,
        )
    return {
        dictPlan["sKey"]: _fdictCarryOutPlan(
            connectionDocker, sContainerId, dictPlan,
        )
        for dictPlan in listPlans
    }


def _fdictConfirmationNeeded(connectionDocker, sContainerId, listPlans):
    """Return the overwrite-confirmation answer, or {} when none is needed."""
    dictConfirm = {}
    listModified = []
    for dictPlan in listPlans:
        listPlanModified = _flistFilesAPlanWouldOverwrite(
            connectionDocker, sContainerId, dictPlan,
        )
        if listPlanModified:
            dictConfirm[dictPlan["sKey"]] = {
                "bNeedsOverwriteConfirm": True,
                "sFilePath": listPlanModified[0],
            }
            listModified.extend(listPlanModified)
    if listModified:
        dictConfirm["bNeedsOverwriteConfirm"] = True
        dictConfirm["listModifiedFiles"] = listModified
    return dictConfirm
