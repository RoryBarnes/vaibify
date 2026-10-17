"""Drive the rendered assessor inside the shadow, and check what it says.

The dashboard's Verify, ``vaibify reproduce --rerun`` and ``--from`` grade
a rerun with the same rendered file a reader's ``reproduce.sh`` runs
(``rerunAssessment``), but never the repository's copy of it: vaibify
writes its OWN render, for the policy the project recorded, into a
scratch directory beside the shadow repository and runs that. This
module is that driving, through the repo-file adapter rooted on the
shadow, so every exec it makes happens inside the shadow's own mutation
admission (``shadowRerun._fdictCompareUnderTheShadowsOwnAdmission``).

Four things here are load-bearing:

* **The policy is selected, never inherited.** Line 2 of the pinned
  ``.vaibify/rerunAssessment.py`` names it; only the strict pattern and
  the registry's own keys are accepted, and the ID selects vaibify's
  code, never a path.
* **The scratch and evidence directories are vaibify's**, created fresh
  and exclusively (``mkdir`` without ``-p``: an existing one refuses
  the run), beside the repository and never inside it. Their paths
  never come from workflow text.
* **The host re-derives the verdict.** The assessor runs in the same
  container as the published code, so its report is in-container
  evidence. The host recomputes every derived field with its own
  imported policy module and cross-checks the observed hashes and every
  command's exit status against what it saw itself. Any disagreement
  is "Could not run: inconsistent assessment".
* **Could not run is never a verdict.** A refusal, an assessor that did
  not answer, or an inconsistent report becomes an unrun outcome, and
  the lanes never write an unrun outcome as an attestation.
"""

import json
import posixpath
import secrets

from vaibify.reproducibility import rerunAssessment, rerunProcedure
from vaibify.reproducibility.repoFiles import fsRepoRootOf
from vaibify.reproducibility.rerunPreparation import (
    RerunPreparationRefusedError,
)


__all__ = [
    "S_INCONSISTENT_ASSESSMENT",
    "T_ASSESSMENT_EXIT_CODES",
    "fdictOpenAssessorLane",
    "fdictRunAssessorAssess",
    "flistCrossCheckAssessment",
    "ftResolveRerunWorkDirectories",
]


S_INCONSISTENT_ASSESSMENT = "inconsistent assessment"
T_ASSESSMENT_EXIT_CODES = (0, 1, 3, 4, 5)
_S_POLICY_SOURCE_RECORDED = "recorded"
_S_POLICY_SOURCE_CURRENT = "current; none recorded"
_F_ASSESSOR_TIMEOUT_SECONDS = 1800.0
_F_DIRECTORY_TIMEOUT_SECONDS = 60.0
_I_OUTPUT_TAIL_CHARACTERS = 400


def ftResolveRerunWorkDirectories(sRepoRoot):
    """Return fresh ``(sScratch, sEvidence)`` paths beside the repository.

    Beside, never inside: a file in the repository is something the
    comparison could grade, and the steps write the JUnit reports into
    the evidence directory. A random token makes them this run's alone.
    """
    sRoot = posixpath.normpath(sRepoRoot or "")
    sParent = posixpath.dirname(sRoot)
    if not posixpath.isabs(sRoot) or sParent == sRoot:
        raise RerunPreparationRefusedError(
            f"the repository {sRepoRoot!r} has no directory beside it for "
            "the rerun's scratch and evidence")
    sToken = secrets.token_hex(8)
    return (posixpath.join(sParent, f"vaibifyRerunScratch.{sToken}"),
            posixpath.join(sParent, f"vaibifyRerunEvidence.{sToken}"))


def fsTailOfOutput(sStdout, sStderr):
    """Return the last few hundred characters a failed command printed."""
    return ((sStderr or "") + (sStdout or "")).strip()[
        -_I_OUTPUT_TAIL_CHARACTERS:]


def ftSelectRecordedPolicy(filesRepo):
    """Return ``(sPolicy, sSource, sUnrunReason)`` from the pinned assessor."""
    sPath = rerunAssessment.S_RERUN_ASSESSMENT_REPO_PATH
    if not filesRepo.fbIsFile(sPath):
        return (rerunAssessment.S_CURRENT_COMPARISON_POLICY,
                _S_POLICY_SOURCE_CURRENT, "")
    try:
        sRecorded = rerunAssessment.fsReadRecordedComparisonPolicy(
            filesRepo.fsReadText(sPath))
    except (OSError, ValueError) as error:
        return ("", "", f"the pinned assessor could not be read: {error}")
    if sRecorded is None:
        return (rerunAssessment.S_CURRENT_COMPARISON_POLICY,
                _S_POLICY_SOURCE_CURRENT, "")
    if sRecorded not in rerunAssessment.DICT_COMPARISON_POLICIES:
        return ("", "", rerunAssessment.fsDescribeUnknownPolicy(sRecorded))
    return (sRecorded, _S_POLICY_SOURCE_RECORDED, "")


def fnWriteLaneFile(filesRepo, sAbsolutePath, sText):
    """Write one file vaibify owns into the shadow, outside the repository.

    The rendered assessor and the invocation ledger. Both paths are
    vaibify's, under directories this run created; neither comes from
    workflow text, and the adapter refuses any other target.
    """
    filesRepo.fnWriteBesideRepository(sAbsolutePath, sText)


def fdictCreateWorkDirectories(filesRepo, sRepoRoot):
    """Create the scratch and evidence directories, refusing if either exists."""
    sScratch, sEvidence = ftResolveRerunWorkDirectories(sRepoRoot)
    iExitCode, sStdout, sStderr = filesRepo.ftRunCommand(
        ["mkdir", "-m", "0700", sScratch, sEvidence],
        _F_DIRECTORY_TIMEOUT_SECONDS)
    if iExitCode != 0:
        return {"sUnrunReason": (
            "the rerun's scratch and evidence directories could not be "
            "created fresh (an earlier run's may remain): "
            + fsTailOfOutput(sStdout, sStderr))}
    return {"sScratch": sScratch, "sEvidence": sEvidence}


def fdictOpenAssessorLane(filesRepo, dictWorkflow):
    """Select the policy, write vaibify's render, and run ``prepare``.

    Returns ``{sUnrunReason}`` when the rerun cannot be graded, else
    ``{dictPlan, sComparisonPolicy, sPolicySource, sScratch, sEvidence,
    sScriptPath, iPrepareExit}``. ``iPrepareExit`` is 0, or 3 when a
    published input already differs from the manifest, in which case
    the caller skips clearing and the steps and goes straight to
    ``assess``.
    """
    sPolicy, sSource, sReason = ftSelectRecordedPolicy(filesRepo)
    if sReason:
        return {"sUnrunReason": sReason}
    try:
        dictPlan = rerunAssessment.fdictBuildAssessmentPlan(
            dictWorkflow, sPolicy)
        sScript = rerunAssessment.fsRenderRerunAssessmentScript(
            dictWorkflow, sPolicy)
    except RerunPreparationRefusedError as error:
        return {"sUnrunReason": str(error)}
    sRepoRoot = fsRepoRootOf(filesRepo)
    dictLane = fdictCreateWorkDirectories(filesRepo, sRepoRoot)
    if "sUnrunReason" in dictLane:
        return dictLane
    dictLane.update({
        "dictPlan": dictPlan, "sComparisonPolicy": sPolicy,
        "sPolicySource": sSource, "sRepoRoot": sRepoRoot,
        "sScriptPath": posixpath.join(
            dictLane["sScratch"], "rerunAssessment.py"),
    })
    fnWriteLaneFile(filesRepo, dictLane["sScriptPath"], sScript)
    iExitCode, sStdout, sStderr = filesRepo.ftRunCommand(
        ["python3", "-I", dictLane["sScriptPath"], "prepare",
         "--repo", sRepoRoot, "--scratch", dictLane["sScratch"]],
        _F_ASSESSOR_TIMEOUT_SECONDS)
    if iExitCode not in (0, 3):
        return {"sUnrunReason": (
            f"the assessor could not prepare the rerun (exit {iExitCode}): "
            + fsTailOfOutput(sStdout, sStderr))}
    dictLane["iPrepareExit"] = iExitCode
    return dictLane


def fdictParseAssessorReport(iExitCode, sStdout, sStderr):
    """Return ``{dictReport}`` from the assessor's machine line, or a reason."""
    sPrefix = rerunProcedure.S_ASSESSMENT_RESULT_PREFIX
    for sLine in reversed((sStdout or "").splitlines()):
        if not sLine.startswith(sPrefix):
            continue
        try:
            dictReport = json.loads(sLine[len(sPrefix):])
        except ValueError:
            break
        if (iExitCode in T_ASSESSMENT_EXIT_CODES and isinstance(
                dictReport, dict)
                and dictReport.get("iExitCode") == iExitCode):
            return {"dictReport": dictReport}
        break
    return {"sUnrunReason": (
        f"the assessor did not report a verdict (exit {iExitCode}): "
        + fsTailOfOutput(sStdout, sStderr))}


def fdictRunAssessorAssess(filesRepo, dictLane, listLedger,
                           dictRunContext=None):
    """Write the ledger and the run context, run ``assess``, parse the report.

    The run context is the runner's Stage A decision and Stage B probe,
    written where ``reproduce.sh``'s own sub-commands write them, so the
    report carries them in ``dictContexts`` whichever lane ran. Returns
    ``{dictReport, dictPlan, listLedger, sComparisonPolicy,
    sPolicySource}`` or ``{sUnrunReason}``.
    """
    fnWriteLaneFile(
        filesRepo,
        posixpath.join(dictLane["sEvidence"],
                       rerunProcedure.S_INVOCATION_LEDGER_FILENAME),
        "".join(f"{sId}\t{iStatus}\n" for sId, iStatus in listLedger))
    for sFilename, sKey in (
            (rerunProcedure.S_CAPABILITIES_RECORD_FILENAME, "dictSettings"),
            (rerunProcedure.S_PROBE_RECORD_FILENAME, "dictProbe")):
        if isinstance((dictRunContext or {}).get(sKey), dict):
            fnWriteLaneFile(
                filesRepo, posixpath.join(dictLane["sScratch"], sFilename),
                json.dumps(dictRunContext[sKey], sort_keys=True))
    listCommand = [
        "python3", "-I", dictLane["sScriptPath"], "assess",
        "--repo", dictLane["sRepoRoot"], "--scratch", dictLane["sScratch"],
        "--evidence", dictLane["sEvidence"], "--machine"]
    if dictLane["sPolicySource"] == _S_POLICY_SOURCE_CURRENT:
        listCommand.append("--no-recorded-policy")
    dictParsed = fdictParseAssessorReport(*filesRepo.ftRunCommand(
        listCommand, _F_ASSESSOR_TIMEOUT_SECONDS))
    dictParsed.update({
        "dictPlan": dictLane["dictPlan"], "listLedger": list(listLedger),
        "sComparisonPolicy": dictLane["sComparisonPolicy"],
        "sPolicySource": dictLane["sPolicySource"],
    })
    return dictParsed


# ----------------------------------------------------------------------
# The host's own checks of what the assessor reported
# ----------------------------------------------------------------------


def fsetPlannedOutputPaths(dictPlan):
    """Return every output path the plan grades, carried paths included."""
    return {
        dictOutput["sPath"] for dictStep in dictPlan["listSteps"]
        for dictOutput in dictStep["listOutputs"]
    } | set(dictPlan.get("listCarriedPaths") or [])


def flistCheckObservedHashes(dictReport, dictPlan, listEntries, dictObserved):
    """Return disagreements between the report's hashes and the host's."""
    setPlanned = fsetPlannedOutputPaths(dictPlan)
    dictExpected = {dictEntry["sPath"]: dictEntry["sExpected"]
                    for dictEntry in listEntries
                    if dictEntry["sPath"] in setPlanned}
    dictReported = {dictOutput["sPath"]: dictOutput
                    for dictOutput in dictReport["listOutputs"]}
    listProblems = []
    if set(dictReported) != set(dictExpected):
        listProblems.append(
            "the report grades a different set of outputs from the "
            "manifest's")
    for sPath in sorted(set(dictReported) & set(dictExpected)):
        if dictReported[sPath]["sExpectedSha256"] != dictExpected[sPath]:
            listProblems.append(f"{sPath}: expected hash differs")
        if dictReported[sPath]["sObservedSha256"] != dictObserved.get(sPath):
            listProblems.append(f"{sPath}: observed hash differs")
    return listProblems


def flistCheckExitStatuses(dictReport, listLedger):
    """Return disagreements between the report's exit statuses and the host's."""
    dictLedger = dict(listLedger)
    dictReported = {dictRecord["sInvocationId"]: dictRecord["iExitStatus"]
                    for dictRecord in dictReport["listInvocations"]}
    listProblems = [
        f"{sId}: the host saw exit {dictLedger.get(sId)}, the report "
        f"says {dictReported.get(sId)}"
        for sId in sorted(set(dictLedger) | set(dictReported))
        if dictLedger.get(sId) != dictReported.get(sId)
    ]
    return listProblems


def flistCheckIntegrity(dictReport, dictPlan, listEntries, dictObserved,
                        bManifestMoved):
    """Return disagreements between the report's integrity and the host's."""
    setRegenerated = set(dictPlan["dictClearingJob"]["listProduced"]) | set(
        dictPlan.get("listCarriedPaths") or [])
    listInputs = [dictEntry for dictEntry in listEntries
                  if dictEntry["sPath"] not in setRegenerated]
    setChanged = {dictEntry["sPath"] for dictEntry in listInputs
                  if dictObserved.get(dictEntry["sPath"]) not in (
                      None, dictEntry["sExpected"])}
    setMissing = {dictEntry["sPath"] for dictEntry in listInputs
                  if dictObserved.get(dictEntry["sPath"]) is None}
    dictIntegrity = dictReport["dictIntegrity"]
    listProblems = []
    if not setChanged <= set(dictIntegrity.get("listChangedInputs") or []):
        listProblems.append("a changed input the host saw is not reported")
    if not setMissing <= set(dictIntegrity.get("listMissingInputs") or []):
        listProblems.append("a missing input the host saw is not reported")
    if bool(dictIntegrity.get("bManifestMoved")) != bool(bManifestMoved):
        listProblems.append("the manifest's movement is misreported")
    return listProblems


def flistCrossCheckAssessment(dictAssessment, listEntries, dictObserved,
                              bManifestMoved):
    """Return every reason the report cannot be trusted; empty when it can.

    The host re-derives every derived field with vaibify's own policy
    module, and compares the observations the assessor made with the
    ones the host made itself: the hashes of every graded output, the
    exit status of every command, and the pinned inputs it found
    changed or missing.
    """
    dictReport = dictAssessment["dictReport"]
    dictPlan = dictAssessment["dictPlan"]
    try:
        if dictReport.get("sComparisonPolicy") != dictAssessment[
                "sComparisonPolicy"]:
            return ["the report was graded under another policy"]
        listProblems = (
            flistCheckObservedHashes(
                dictReport, dictPlan, listEntries, dictObserved)
            + flistCheckExitStatuses(dictReport, dictAssessment["listLedger"])
            + flistCheckIntegrity(
                dictReport, dictPlan, listEntries, dictObserved,
                bManifestMoved))
    except (KeyError, TypeError, AttributeError) as error:
        return [f"the report is malformed ({type(error).__name__}: {error})"]
    dictRederived = rerunAssessment.fdictRederiveAssessment(
        dictReport, dictPlan)
    if not dictRederived["bConsistent"]:
        listProblems.append(
            "re-derivation disagrees on "
            + ", ".join(dictRederived["listDisagreements"]))
    return listProblems
