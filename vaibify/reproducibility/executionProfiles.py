"""Execution profiles: what a run asks of its numerical libraries.

A researcher's answer to the determinism question names one profile by a
versioned ID (``dictDeterminism["sExecutionProfile"]``, design I6). The
table is append-only. A released profile is never edited: an envelope
recorded under it is replayed from its own recorded values, never looked
up here again (F9), and an attestation made under one profile is retired
once the workflow asks for another (F8). A change is a new ID.

Every lane applies a profile the same way (I10): Stage A reads the
processor in a process that loads no numerical library, and
:func:`fdictDecideExecutionSettings` keeps the floor only on a processor
that has the profile's ``x86-64`` level; Stage B then observes what the
libraries chose under exactly the applied settings. The thread pins
always apply.

A profile that reaches vaibify from a cloned file (an envelope's
``dictExecutionProfile``) is validated against the one allowlist before
anything is applied: :func:`flistDescribeProfileProblems` names every
key or value outside it, and ``reproduce.sh`` renders its own check from
the same tuple (attack surface item 2).
"""

import re

from vaibify.reproducibility.rerunProcedure import (
    S_EXECUTION_SETTING_VALUE_PATTERN,
    TUPLE_EXECUTION_SETTING_KEYS,
    TUPLE_X86_64_LEVELS,
    fdictDecideAppliedSettings,
)


__all__ = [
    "S_PROFILE_SPEED",
    "S_PROFILE_ACROSS",
    "S_DEFAULT_EXECUTION_PROFILE",
    "S_EXECUTION_PROFILE_KEY",
    "DICT_EXECUTION_PROFILES",
    "ExecutionProfileError",
    "fsSelectedProfileId",
    "fdictProfileFromTable",
    "fdictWorkflowExecutionProfile",
    "flistDescribeProfileProblems",
    "fdictRequireValidProfile",
    "fdictDecideExecutionSettings",
    "fsBuildExecutionSettingsPrefix",
]


S_PROFILE_SPEED = "speed-v1"
S_PROFILE_ACROSS = "across-v1"
S_DEFAULT_EXECUTION_PROFILE = S_PROFILE_SPEED
S_EXECUTION_PROFILE_KEY = "sExecutionProfile"

# Append-only. ``across-v1`` requests no glibc variable: glibc's scalar
# math dispatches on AVX2/FMA, which the x86-64-v3 floor already holds
# fixed (design fact 21).
DICT_EXECUTION_PROFILES = {
    S_PROFILE_SPEED: {
        "dictRequested": {},
        "sRequiredLevel": "",
        "listFloorKeys": [],
    },
    S_PROFILE_ACROSS: {
        "dictRequested": {
            "OPENBLAS_CORETYPE": "Haswell",
            "NPY_DISABLE_CPU_FEATURES": "AVX512_ICL,X86_V4",
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
        },
        "sRequiredLevel": "x86-64-v3",
        "listFloorKeys": ["OPENBLAS_CORETYPE", "NPY_DISABLE_CPU_FEATURES"],
    },
}

_RE_PROFILE_ID = re.compile(r"^[a-z0-9-]{1,32}$")
_RE_SETTING_VALUE = re.compile(S_EXECUTION_SETTING_VALUE_PATTERN)


class ExecutionProfileError(ValueError):
    """A profile that is unknown here, or that names a setting it may not."""


def fsSelectedProfileId(dictWorkflow):
    """Return the profile ID the workflow asks for; absent is ``speed-v1``."""
    dictDeterminism = (dictWorkflow or {}).get("dictDeterminism") or {}
    if not isinstance(dictDeterminism, dict):
        return S_DEFAULT_EXECUTION_PROFILE
    return dictDeterminism.get(S_EXECUTION_PROFILE_KEY) or (
        S_DEFAULT_EXECUTION_PROFILE)


def fdictProfileFromTable(sProfileId):
    """Return the concrete profile for an ID, or raise naming the remedy."""
    if sProfileId not in DICT_EXECUTION_PROFILES:
        raise ExecutionProfileError(
            f"this project asks for the execution profile {sProfileId!r}, "
            "which this version of vaibify does not include; upgrade "
            "vaibify.")
    dictEntry = DICT_EXECUTION_PROFILES[sProfileId]
    return {
        "sProfileId": sProfileId,
        "dictRequested": dict(dictEntry["dictRequested"]),
        "sRequiredLevel": dictEntry["sRequiredLevel"],
        "listFloorKeys": list(dictEntry["listFloorKeys"]),
    }


def fdictWorkflowExecutionProfile(dictWorkflow):
    """Return the concrete profile the workflow's answer names."""
    return fdictProfileFromTable(fsSelectedProfileId(dictWorkflow))


def flistDescribeProfileProblems(dictProfile):
    """Return why a recorded profile may not be applied; empty when it may.

    Every problem names the key or value it is about, because the reader
    of a refusal is the person who has to find it in the file.
    """
    if not isinstance(dictProfile, dict):
        return ["the execution profile is not an object"]
    listProblems = []
    sProfileId = dictProfile.get("sProfileId")
    if not isinstance(sProfileId, str) or not _RE_PROFILE_ID.match(sProfileId):
        listProblems.append(f"profile ID {sProfileId!r} is not valid")
    dictRequested = dictProfile.get("dictRequested")
    if not isinstance(dictRequested, dict):
        dictRequested = {}
        listProblems.append("the requested settings are not an object")
    for sKey, sValue in dictRequested.items():
        if sKey not in TUPLE_EXECUTION_SETTING_KEYS:
            listProblems.append(f"setting {sKey!r} is not allowed")
        elif not isinstance(sValue, str) or not _RE_SETTING_VALUE.match(sValue):
            listProblems.append(f"the value of {sKey} is not allowed")
    sRequiredLevel = dictProfile.get("sRequiredLevel")
    if sRequiredLevel not in ("",) + TUPLE_X86_64_LEVELS:
        listProblems.append(f"required level {sRequiredLevel!r} is not known")
    listFloorKeys = dictProfile.get("listFloorKeys")
    if not isinstance(listFloorKeys, list):
        listFloorKeys = []
        listProblems.append("the floor keys are not a list")
    for sKey in listFloorKeys:
        if sKey not in TUPLE_EXECUTION_SETTING_KEYS:
            listProblems.append(f"floor key {sKey!r} is not allowed")
    return listProblems


def fdictRequireValidProfile(dictProfile):
    """Return a validated copy of a recorded profile, or raise naming why."""
    listProblems = flistDescribeProfileProblems(dictProfile)
    if listProblems:
        raise ExecutionProfileError(
            "the recorded execution profile cannot be applied: "
            + "; ".join(listProblems))
    return {
        "sProfileId": dictProfile["sProfileId"],
        "dictRequested": dict(dictProfile["dictRequested"]),
        "sRequiredLevel": dictProfile["sRequiredLevel"],
        "listFloorKeys": list(dictProfile["listFloorKeys"]),
    }


def fdictDecideExecutionSettings(dictProfile, dictCapabilities):
    """Return Stage A's decision for a profile on these capabilities.

    The requested, the dropped and the applied settings are kept apart
    (F7), so a record says what was asked for, what this processor could
    not honor, and what the steps received.
    """
    dictDecision = fdictDecideAppliedSettings(
        dictCapabilities, dictProfile["sRequiredLevel"],
        dictProfile["listFloorKeys"])
    listDropped = list(dictDecision["listDroppedKeys"])
    return {
        "sProfileId": dictProfile["sProfileId"],
        "dictRequested": dict(dictProfile["dictRequested"]),
        "sRequiredLevel": dictProfile["sRequiredLevel"],
        "listFloorKeys": list(dictProfile["listFloorKeys"]),
        "dictCapabilities": dict(dictCapabilities),
        "dictDecision": dictDecision,
        "listDropped": listDropped,
        "dictApplied": {
            sKey: sValue for sKey, sValue in dictProfile["dictRequested"].items()
            if sKey not in listDropped},
    }


def fsBuildExecutionSettingsPrefix(dictSettings):
    """Return the shell text that gives a command exactly the applied settings.

    A dropped key is unset as well as not exported, so a value the image
    itself sets cannot stand in for the floor this processor could not
    honor -- the same treatment ``reproduce.sh`` gives it. Every key and
    value passed the allowlist, and each value is quoted anyway.
    """
    from vaibify.gui.pipelineUtils import fsShellQuote
    listParts = []
    for sKey in dictSettings.get("listDropped") or []:
        if sKey in TUPLE_EXECUTION_SETTING_KEYS:
            listParts.append(f"unset {sKey} && ")
    for sKey, sValue in sorted((dictSettings.get("dictApplied") or {}).items()):
        if sKey in TUPLE_EXECUTION_SETTING_KEYS and _RE_SETTING_VALUE.match(
                str(sValue)):
            listParts.append(f"export {sKey}={fsShellQuote(str(sValue))} && ")
    return "".join(listParts)
