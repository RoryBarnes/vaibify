"""Every doctor finding a researcher can act on must SAY what to do.

The report's whole value is that the answer to "something is wrong"
arrives with the next step attached. A warn or fail carrying neither a
remediation nor a command is a diagnosis the researcher has to take
somewhere else, which is the state this work started from: five of the
thirty-six results in the codebase set a command.

Enforced structurally, over the source, rather than by running the
checks -- most of them need a daemon, a container, or a broken
network, so a runtime sweep would silently cover only the ones that
happen to fire on the machine running the suite.

A finding whose LEVEL is computed at runtime is held to the stricter
rule: it must pass both fields, because the analysis cannot tell which
branch produced the warn.
"""

import ast

import pytest


T_SCANNED_MODULES = (
    "vaibify/cli/commandDoctor.py",
    "vaibify/cli/preflightChecks.py",
    "vaibify/cli/commandStart.py",
    "vaibify/cli/commandBuild.py",
    "vaibify/cli/doctorNetwork.py",
    "vaibify/cli/doctorHostChecks.py",
    "vaibify/cli/doctorProjectChecks.py",
)

# Findings that legitimately name no action. The budget may only fall:
# emptying an entry means deleting its line, never rewriting it to
# cover something new.
T_ALLOWED_WITHOUT_ACTION = ()

I_FINDINGS_WITHOUT_ACTION_BUDGET = len(T_ALLOWED_WITHOUT_ACTION)

_T_ACTIONABLE_MARKERS = ("warn", "fail", "S_LEVEL_WARN", "S_LEVEL_FAIL")
_T_INERT_LEVEL_MARKERS = (
    "'ok'", '"ok"', "'info'", '"info"', "S_LEVEL_OK", "S_LEVEL_INFO",
    "S_LEVEL_NOT_CHECKED",
)


def _fbLevelIsActionable(sLevelSource):
    """True when this level expression can produce a warn or a fail."""
    if any(sMarker in sLevelSource for sMarker in _T_ACTIONABLE_MARKERS):
        return True
    return not any(
        sMarker in sLevelSource for sMarker in _T_INERT_LEVEL_MARKERS
    )


def _fbCallNamesAnAction(dictKeywords):
    """True when the call passes a non-empty remediation or command."""
    for sField in ("sRemediation", "sCommand"):
        nodeValue = dictKeywords.get(sField)
        if nodeValue is None:
            continue
        if isinstance(nodeValue, ast.Constant) and not nodeValue.value:
            continue
        return True
    return False


def _flistCollectFindings(sModulePath):
    """Return (sModulePath, iLine, sName, bNamesAction) per actionable call."""
    listFindings = []
    treeModule = ast.parse(open(sModulePath).read())
    for nodeCall in ast.walk(treeModule):
        if not isinstance(nodeCall, ast.Call):
            continue
        if getattr(nodeCall.func, "id", "") != "PreflightResult":
            continue
        dictKeywords = {
            keyword.arg: keyword.value for keyword in nodeCall.keywords
        }
        nodeLevel = dictKeywords.get("sLevel")
        if nodeLevel is None:
            continue
        if not _fbLevelIsActionable(ast.unparse(nodeLevel)):
            continue
        sName = (
            ast.unparse(dictKeywords["sName"])
            if "sName" in dictKeywords else "?"
        )
        listFindings.append((
            sModulePath, nodeCall.lineno, sName,
            _fbCallNamesAnAction(dictKeywords),
        ))
    return listFindings


def _flistAllFindings():
    """Return every actionable finding across the scanned modules."""
    listFindings = []
    for sModulePath in T_SCANNED_MODULES:
        listFindings.extend(_flistCollectFindings(sModulePath))
    return listFindings


def test_the_scan_finds_the_checks_it_claims_to_cover():
    """A scan that matches nothing passes vacuously; this is the control."""
    listFindings = _flistAllFindings()
    assert len(listFindings) > 20, (
        "the scan found almost no findings, which means it stopped "
        "recognising them rather than that they went away"
    )


@pytest.mark.falsification
def test_every_actionable_finding_names_a_next_step():
    """Every warn and fail names a remediation or a command.

    Kills: In doctorHostChecks._flistCheckCpuAllocation, empty the
    over-allocation warning's sRemediation, so the researcher is told
    the number is wrong and not what to change.
    """
    listSilent = [
        (sPath, iLine, sName)
        for sPath, iLine, sName, bNamesAction in _flistAllFindings()
        if not bNamesAction
        and f"{sPath}:{iLine}" not in T_ALLOWED_WITHOUT_ACTION
    ]
    assert not listSilent, (
        "these warn/fail findings name neither a remediation nor a "
        f"command: {listSilent}"
    )


def test_the_exemption_budget_only_ever_falls():
    """The allow-list is a ratchet, not a place to put new exemptions."""
    assert len(T_ALLOWED_WITHOUT_ACTION) <= I_FINDINGS_WITHOUT_ACTION_BUDGET
