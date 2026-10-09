"""Parse a container's own cgroup memory files into tagged numbers.

The typed read ``S_TYPED_READ_CGROUP_MEMORY`` prints four files from the
container's memory cgroup, each under a marker line naming it, and an
explicit ``MISSING`` marker for a file it could not open. This module
turns that text into numbers and says plainly which numbers it could
not get: every field is ``None`` when unreadable, never 0, because a
zero kill count read from a file that was not there would claim "no
process was killed" with nothing behind it.

Two kernel interfaces are read. cgroup v2 (``memory.current``,
``memory.max``, ``memory.events``, ``memory.stat``) is what current
Docker hosts run; cgroup v1 (``memory.usage_in_bytes``,
``memory.limit_in_bytes``, ``memory.oom_control``, ``memory.stat``) is
the fallback. v1 has no counter of limit events, so ``iOomCount`` is
always ``None`` there.

The working set is ``usage - inactive_file``, clamped at zero: the
estimate cAdvisor computes, the Kubernetes eviction manager acts on,
and ``docker stats`` reports on cgroup v2. Usage alone includes page
cache the kernel would reclaim before killing anything, so it overstates
the pressure; anonymous memory alone leaves out shared memory, tmpfs
and kernel memory, which count against the cap. The result is an
ESTIMATE, and every surface that shows it says so.
"""

from vaibify.docker.disposableSpecification import fiParseOomKillCount

__all__ = [
    "S_VERSION_MARKER",
    "S_FILE_MARKER",
    "S_MISSING_MARKER",
    "S_LIMIT_FINITE",
    "S_LIMIT_UNLIMITED",
    "S_LIMIT_UNKNOWN",
    "fdictParseCgroupMemory",
    "fiParseOomCount",
]


S_VERSION_MARKER = "@@ cgroup"
S_FILE_MARKER = "@@ file"
S_MISSING_MARKER = "MISSING"

S_LIMIT_FINITE = "finite"
S_LIMIT_UNLIMITED = "unlimited"
S_LIMIT_UNKNOWN = "unknown"

# cgroup v1 spells "no limit" as PAGE_COUNTER_MAX pages, which is
# LONG_MAX rounded down to a page boundary (9223372036854771712 with
# 4 KiB pages). Any value at or above 2**62 is that sentinel, never a
# limit a daemon could actually have set.
_I_VERSION_ONE_UNLIMITED_FLOOR = 2 ** 62

_DICT_FILES_BY_VERSION = {
    "v2": {
        "sUsage": "memory.current", "sLimit": "memory.max",
        "sEvents": "memory.events", "sStat": "memory.stat",
    },
    "v1": {
        "sUsage": "memory.usage_in_bytes", "sLimit": "memory.limit_in_bytes",
        "sEvents": "memory.oom_control", "sStat": "memory.stat",
    },
}

# Each breakdown field names the memory.stat keys that carry it, in
# order of preference. v1 reports both a cgroup's own and its
# hierarchical ("total_") figures; the hierarchical one is what the
# container as a whole holds.
_DICT_STAT_KEYS_BY_VERSION = {
    "v2": {
        "iAnonBytes": ("anon",), "iFileBytes": ("file",),
        "iShmemBytes": ("shmem",), "iKernelBytes": ("kernel",),
        "iInactiveFileBytes": ("inactive_file",),
    },
    "v1": {
        "iAnonBytes": ("total_rss", "rss"),
        "iFileBytes": ("total_cache", "cache"),
        "iShmemBytes": ("total_shmem", "shmem"),
        "iKernelBytes": (),
        "iInactiveFileBytes": ("total_inactive_file", "inactive_file"),
    },
}


def fiParseOomCount(sEventsText):
    """Return the ``oom`` count from ``memory.events`` text, or None.

    ``oom`` counts the times the cgroup reached its limit and the
    kernel's out-of-memory handling was invoked. The first token must
    equal ``oom`` exactly: the same file carries ``oom_kill`` and
    ``oom_group_kill``, and a prefix match would read the wrong number
    -- the rule :func:`fiParseOomKillCount` applies to its own token.
    """
    for sLine in (sEventsText or "").splitlines():
        listFields = sLine.split()
        if len(listFields) == 2 and listFields[0] == "oom":
            try:
                return int(listFields[1])
            except ValueError:
                return None
    return None


def fdictParseCgroupMemory(sReadText):
    """Return the tagged memory figures the typed read's text carries."""
    sVersion, dictFileTexts = _ftSplitReadText(sReadText)
    dictNames = _DICT_FILES_BY_VERSION.get(sVersion)
    if dictNames is None:
        return _fdictUnreadable()
    dictParsed = {"sCgroupVersion": sVersion}
    dictParsed["iUsageBytes"] = _fiParseCount(
        dictFileTexts.get(dictNames["sUsage"]))
    dictParsed.update(_fdictParseLimit(
        sVersion, dictFileTexts.get(dictNames["sLimit"])))
    dictParsed.update(_fdictParseEvents(
        sVersion, dictFileTexts.get(dictNames["sEvents"])))
    dictParsed.update(_fdictParseStat(
        sVersion, dictFileTexts.get(dictNames["sStat"])))
    dictParsed["iWorkingSetBytes"] = _fiWorkingSetBytes(dictParsed)
    return dictParsed


def _fdictUnreadable():
    """Return the parse of a read that named no cgroup version."""
    dictParsed = {"sCgroupVersion": None, "iUsageBytes": None}
    dictParsed.update({"sLimitKind": S_LIMIT_UNKNOWN, "iLimitBytes": None})
    dictParsed.update({"iOomCount": None, "iOomKillCount": None})
    dictParsed.update(_fdictParseStat("v2", None))
    dictParsed["iWorkingSetBytes"] = None
    return dictParsed


def _ftSplitReadText(sReadText):
    """Return ``(sVersion, {sFileName: sText or None})`` from the read."""
    sVersion = None
    dictFileTexts = {}
    sCurrentFile = None
    for sLine in (sReadText or "").splitlines():
        if sLine.startswith(S_VERSION_MARKER + " "):
            sVersion = sLine[len(S_VERSION_MARKER) + 1:].strip()
            sCurrentFile = None
        elif sLine.startswith(S_FILE_MARKER + " "):
            listFields = sLine[len(S_FILE_MARKER) + 1:].split()
            sCurrentFile = listFields[0] if listFields else None
            bMissing = listFields[1:] == [S_MISSING_MARKER]
            if sCurrentFile:
                dictFileTexts[sCurrentFile] = None if bMissing else ""
            if bMissing:
                sCurrentFile = None
        elif sCurrentFile is not None:
            dictFileTexts[sCurrentFile] += sLine + "\n"
    return sVersion, dictFileTexts


def _fiParseCount(sText):
    """Return a file holding one integer as an int, or None."""
    if sText is None:
        return None
    try:
        return int(sText.strip())
    except ValueError:
        return None


def _fdictParseLimit(sVersion, sText):
    """Return ``{sLimitKind, iLimitBytes}`` for the limit file's text."""
    if sText is not None and sVersion == "v2" and sText.strip() == "max":
        return {"sLimitKind": S_LIMIT_UNLIMITED, "iLimitBytes": None}
    iLimit = _fiParseCount(sText)
    if iLimit is None:
        return {"sLimitKind": S_LIMIT_UNKNOWN, "iLimitBytes": None}
    if sVersion == "v1" and iLimit >= _I_VERSION_ONE_UNLIMITED_FLOOR:
        return {"sLimitKind": S_LIMIT_UNLIMITED, "iLimitBytes": None}
    return {"sLimitKind": S_LIMIT_FINITE, "iLimitBytes": iLimit}


def _fdictParseEvents(sVersion, sText):
    """Return the two OOM counters, None for any the file did not carry."""
    if sText is None:
        return {"iOomCount": None, "iOomKillCount": None}
    iOomCount = fiParseOomCount(sText) if sVersion == "v2" else None
    return {"iOomCount": iOomCount, "iOomKillCount": fiParseOomKillCount(sText)}


def _fdictParseStat(sVersion, sText):
    """Return the breakdown fields from ``memory.stat`` text."""
    dictStat = {}
    for sLine in (sText or "").splitlines():
        listFields = sLine.split()
        if len(listFields) == 2 and listFields[1].isdigit():
            dictStat[listFields[0]] = int(listFields[1])
    dictBreakdown = {}
    for sField, tKeys in _DICT_STAT_KEYS_BY_VERSION[sVersion].items():
        dictBreakdown[sField] = next(
            (dictStat[sKey] for sKey in tKeys if sKey in dictStat), None)
    return dictBreakdown


def _fiWorkingSetBytes(dictParsed):
    """Return usage minus inactive file cache, or None when either is unknown."""
    iUsage = dictParsed.get("iUsageBytes")
    iInactiveFile = dictParsed.get("iInactiveFileBytes")
    if iUsage is None or iInactiveFile is None:
        return None
    return max(0, iUsage - iInactiveFile)
