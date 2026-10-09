"""A container's CPU and memory limits: desired, running, and the change between.

Three questions, one authority each:

* **Desired** -- what ``vaibify.yml`` asks for, resolved exactly as
  ``docker run`` is given it. ``cpuLimit: 0`` means all host cores but
  one, never "no cap"; ``memoryLimitGigabytes: 0`` means no memory
  limit. The memory figure is computed from the very ``--memory``
  argument string the container is created with, the way Docker
  parses it, so a desired value and a running value compare exactly.
* **Running** -- what the container's ``HostConfig`` says now, tagged:
  ``finite``, ``unlimited``, ``unknown`` (the inspect failed), and for
  CPU a ``cpuQuota`` form that a ``--cpus`` change would conflict
  with. Docker spells "unlimited" as 0, so a bare number would make an
  unlimited container look like a zero limit.
* **The change** -- what saving a new desired value can do to a
  running container. ``docker update`` raises a memory limit live only
  when the swap limit moves with it, silently ignores ``--memory 0``
  and ``--cpus 0``, and cannot introduce a memory limit on a container
  that has none; a CPU cap throttles and never kills. A memory
  DECREASE is never planned live, because lowering a limit below what
  is in use makes the kernel kill a process. Desired is compared with
  RUNNING, never with the file: a file that says 1 GB beside a
  container raised by hand to 6 GB makes a save of 4 GB a decrease.

Everything here is pure. The reads and the ``docker update`` live with
the Docker gateways that own them.
"""

import functools
import os

__all__ = [
    "I_BYTES_PER_GIGABYTE",
    "I_NANO_CPUS_PER_CPU",
    "S_KIND_FINITE",
    "S_KIND_UNLIMITED",
    "S_KIND_UNKNOWN",
    "S_KIND_UNSET",
    "S_KIND_CPU_QUOTA",
    "S_FIELD_MEMORY",
    "S_FIELD_CPU",
    "S_ACTION_APPLY_LIVE",
    "S_ACTION_NEXT_START",
    "S_ACTION_NONE",
    "fiResolveCpuCount",
    "fsResolveMemoryArgument",
    "fiResolveMemoryBytes",
    "fdictResolveDesiredLimits",
    "fdictParseRunningLimits",
    "fdictUnknownRunningLimits",
    "flistPlanLimitChanges",
    "flistDescribeLimitDrift",
    "flistChangedLimitFields",
    "flistDescribeNextStartOutcomes",
    "fsFormatBytes",
]


I_BYTES_PER_GIGABYTE = 2 ** 30
I_BYTES_PER_MEGABYTE = 2 ** 20
I_NANO_CPUS_PER_CPU = 10 ** 9

S_KIND_FINITE = "finite"
S_KIND_UNLIMITED = "unlimited"
S_KIND_UNKNOWN = "unknown"
S_KIND_UNSET = "unset"
S_KIND_CPU_QUOTA = "cpuQuota"

S_FIELD_MEMORY = "memory"
S_FIELD_CPU = "cpu"

S_ACTION_APPLY_LIVE = "applyLive"
S_ACTION_NEXT_START = "nextStart"
S_ACTION_NONE = "none"

S_NEXT_START_CLAUSE = "It applies the next time the container starts."


# ---------------------------------------------------------------------
# Desired
# ---------------------------------------------------------------------

def fiResolveCpuCount(config, iHostCores=None):
    """Return the ``--cpus`` value a container is created with.

    ``cpuLimit`` of zero keeps the historical default of all host cores
    minus one; a configured cap is clamped to the host's core count so
    a file written on a larger machine cannot ask for cores that do not
    exist.
    """
    iHostCores = iHostCores or os.cpu_count() or 2
    iConfiguredLimit = getattr(config, "iCpuLimit", 0) or 0
    if iConfiguredLimit > 0:
        return min(iConfiguredLimit, iHostCores)
    return max(1, iHostCores - 1)


def fsResolveMemoryArgument(config):
    """Return the ``--memory`` argument, or None for no memory limit."""
    fMemoryGigabytes = getattr(config, "fMemoryLimitGigabytes", 0.0) or 0.0
    if fMemoryGigabytes > 0:
        return f"{fMemoryGigabytes:g}g"
    return None


def fiResolveMemoryBytes(config):
    """Return the bytes Docker sets for the ``--memory`` argument, or None.

    Docker parses ``1.5g`` as a float times 2**30 and truncates to an
    integer; the argument is formatted with ``%g`` first, so the bytes
    are computed from that string, not from the unrounded file value.
    """
    sArgument = fsResolveMemoryArgument(config)
    if sArgument is None:
        return None
    return int(float(sArgument[:-1]) * I_BYTES_PER_GIGABYTE)


def fdictResolveDesiredLimits(config, iHostCores=None):
    """Return ``{iMemoryBytes, iCpuCount}`` the file asks for."""
    return {
        "iMemoryBytes": fiResolveMemoryBytes(config),
        "iCpuCount": fiResolveCpuCount(config, iHostCores),
    }


# ---------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------

def fdictParseRunningLimits(dictHostConfig):
    """Return the tagged memory, swap and CPU limits of a HostConfig."""
    iMemory = int(dictHostConfig.get("Memory") or 0)
    iSwap = int(dictHostConfig.get("MemorySwap") or 0)
    return {
        "dictMemory": (
            {"sKind": S_KIND_FINITE, "iBytes": iMemory} if iMemory > 0
            else {"sKind": S_KIND_UNLIMITED, "iBytes": None}),
        "dictSwap": _fdictTagSwap(iSwap),
        "dictCpu": _fdictTagCpu(dictHostConfig),
    }


def _fdictTagSwap(iSwap):
    """Tag MemorySwap: -1 is unlimited, 0 is unset, positive is finite."""
    if iSwap < 0:
        return {"sKind": S_KIND_UNLIMITED, "iBytes": -1}
    if iSwap == 0:
        return {"sKind": S_KIND_UNSET, "iBytes": None}
    return {"sKind": S_KIND_FINITE, "iBytes": iSwap}


def _fdictTagCpu(dictHostConfig):
    """Tag the CPU limit, keeping the quota form apart from ``--cpus``."""
    iNanoCpus = int(dictHostConfig.get("NanoCpus") or 0)
    iQuota = int(dictHostConfig.get("CpuQuota") or 0)
    iPeriod = int(dictHostConfig.get("CpuPeriod") or 0)
    if iNanoCpus > 0:
        return {"sKind": S_KIND_FINITE, "iNanoCpus": iNanoCpus,
                "fCpus": iNanoCpus / I_NANO_CPUS_PER_CPU}
    if iQuota > 0 and iPeriod > 0:
        return {"sKind": S_KIND_CPU_QUOTA, "iNanoCpus": None,
                "fCpus": iQuota / iPeriod}
    return {"sKind": S_KIND_UNLIMITED, "iNanoCpus": None, "fCpus": None}


def fdictUnknownRunningLimits():
    """Return the tagged answer for a container whose limits are unreadable."""
    return {
        "dictMemory": {"sKind": S_KIND_UNKNOWN, "iBytes": None},
        "dictSwap": {"sKind": S_KIND_UNKNOWN, "iBytes": None},
        "dictCpu": {"sKind": S_KIND_UNKNOWN, "iNanoCpus": None,
                    "fCpus": None},
    }


# ---------------------------------------------------------------------
# The change planner
# ---------------------------------------------------------------------

def flistPlanLimitChanges(dictRunning, dictDesired):
    """Return one planned action per field: applyLive, nextStart or none."""
    return [
        _fdictPlanMemory(dictRunning, dictDesired.get("iMemoryBytes")),
        _fdictPlanCpu(dictRunning["dictCpu"], dictDesired.get("iCpuCount")),
    ]


def _fdictPlanEntry(sField, sAction, sReason, **dictValues):
    dictEntry = {"sField": sField, "sAction": sAction, "sReason": sReason}
    dictEntry.update(dictValues)
    return dictEntry


def _fdictPlanMemory(dictRunning, iDesiredBytes):
    """Plan the memory field against the running memory and swap limits."""
    dictMemory = dictRunning["dictMemory"]
    iRunning = dictMemory.get("iBytes")
    fdictEntry = functools.partial(
        _fdictPlanEntry, S_FIELD_MEMORY, iRunningBytes=iRunning,
        iDesiredBytes=iDesiredBytes)
    if dictMemory["sKind"] == S_KIND_UNKNOWN:
        return fdictEntry(S_ACTION_NEXT_START, "the running limit is unreadable")
    if dictMemory["sKind"] == S_KIND_UNLIMITED:
        if iDesiredBytes is None:
            return fdictEntry(S_ACTION_NONE, "")
        return fdictEntry(S_ACTION_NEXT_START, (
            "Docker cannot add a memory limit to a running container"))
    if iDesiredBytes is None:
        return fdictEntry(S_ACTION_NEXT_START, (
            "Docker cannot remove a memory limit from a running container"))
    if iDesiredBytes == iRunning:
        return fdictEntry(S_ACTION_NONE, "")
    if iDesiredBytes < iRunning:
        return fdictEntry(S_ACTION_NEXT_START, (
            "lowering a running container's memory limit can make the "
            "kernel kill a process"))
    return _fdictPlanMemoryRaise(
        fdictEntry, dictRunning["dictSwap"], iRunning, iDesiredBytes)


def _fdictPlanMemoryRaise(fdictEntry, dictSwap, iRunning, iDesiredBytes):
    """Plan a memory raise: live only when the swap limit can move with it."""
    if dictSwap["sKind"] == S_KIND_UNLIMITED:
        return fdictEntry(S_ACTION_APPLY_LIVE, "", iSwapBytes=-1)
    if dictSwap["sKind"] != S_KIND_FINITE:
        return fdictEntry(S_ACTION_NEXT_START, "the swap limit is unknown")
    iSwapBytes = round(dictSwap["iBytes"] * iDesiredBytes / iRunning)
    return fdictEntry(S_ACTION_APPLY_LIVE, "", iSwapBytes=iSwapBytes)


def _fdictPlanCpu(dictCpu, iDesiredCpuCount):
    """Plan the CPU field: a CPU cap throttles, so any finite change is live."""
    iDesiredNano = (
        None if iDesiredCpuCount is None
        else iDesiredCpuCount * I_NANO_CPUS_PER_CPU)
    fdictEntry = functools.partial(
        _fdictPlanEntry, S_FIELD_CPU,
        iRunningNanoCpus=dictCpu.get("iNanoCpus"),
        iDesiredCpuCount=iDesiredCpuCount)
    if dictCpu["sKind"] == S_KIND_UNKNOWN:
        return fdictEntry(S_ACTION_NEXT_START, "the running limit is unreadable")
    if dictCpu["sKind"] == S_KIND_CPU_QUOTA:
        return fdictEntry(S_ACTION_NEXT_START, (
            "the container's CPU limit is set as a quota, which a live "
            "change would conflict with"))
    if iDesiredNano is None:
        if dictCpu["sKind"] == S_KIND_UNLIMITED:
            return fdictEntry(S_ACTION_NONE, "")
        return fdictEntry(S_ACTION_NEXT_START, (
            "Docker cannot remove a CPU limit from a running container"))
    if dictCpu.get("iNanoCpus") == iDesiredNano:
        return fdictEntry(S_ACTION_NONE, "")
    return fdictEntry(S_ACTION_APPLY_LIVE, "")


# ---------------------------------------------------------------------
# Drift: the running container against the file
# ---------------------------------------------------------------------

def flistDescribeLimitDrift(dictRunning, dictDesired):
    """Return a sentence per limit that differs from the file; [] when unknown.

    An unreadable running limit determines nothing: no sentence, never
    drift, on the terms of the configuration fingerprint.
    """
    listLines = []
    sMemory = _fsDescribeMemoryDrift(
        dictRunning["dictMemory"], dictDesired.get("iMemoryBytes"))
    if sMemory:
        listLines.append(sMemory)
    sCpu = _fsDescribeCpuDrift(
        dictRunning["dictCpu"], dictDesired.get("iCpuCount"))
    if sCpu:
        listLines.append(sCpu)
    return listLines


def _fsDescribeMemoryDrift(dictMemory, iDesiredBytes):
    sKind = dictMemory["sKind"]
    if sKind not in (S_KIND_FINITE, S_KIND_UNLIMITED):
        return ""
    iRunning = dictMemory.get("iBytes")
    if iRunning == iDesiredBytes:
        return ""
    sRunning = ("no memory limit" if iRunning is None
                else f"a {fsFormatBytes(iRunning)} memory limit")
    if iDesiredBytes is None:
        return (f"This container runs with {sRunning}, but vaibify.yml "
                "sets none; the next Restart will remove it.")
    sDesired = fsFormatBytes(iDesiredBytes)
    return (f"This container runs with {sRunning}, but vaibify.yml says "
            f"{sDesired}; the next Restart will apply {sDesired}.")


def _fsDescribeCpuDrift(dictCpu, iDesiredCpuCount):
    if dictCpu["sKind"] != S_KIND_FINITE or iDesiredCpuCount is None:
        return ""
    if dictCpu["iNanoCpus"] == iDesiredCpuCount * I_NANO_CPUS_PER_CPU:
        return ""
    return (f"This container runs with {_fsFormatCpus(dictCpu['fCpus'])}, "
            f"but vaibify.yml asks for {iDesiredCpuCount}; the next "
            f"Restart will apply {iDesiredCpuCount}.")


# ---------------------------------------------------------------------
# A save: which fields changed, and what each change will do
# ---------------------------------------------------------------------

def flistChangedLimitFields(config, iCpuLimit, fMemoryLimitGigabytes):
    """Return the limit fields a save request changes relative to the file."""
    listChanged = []
    fCurrentMemory = float(getattr(config, "fMemoryLimitGigabytes", 0) or 0)
    if fMemoryLimitGigabytes is not None and (
            float(fMemoryLimitGigabytes) != fCurrentMemory):
        listChanged.append(S_FIELD_MEMORY)
    iCurrentCpu = int(getattr(config, "iCpuLimit", 0) or 0)
    if iCpuLimit is not None and int(iCpuLimit) != iCurrentCpu:
        listChanged.append(S_FIELD_CPU)
    return listChanged


def flistDescribeNextStartOutcomes(listChangedFields, config, iHostCores=None):
    """Return ``{sField, sOutcome, sSentence}`` for changes that wait for a start."""
    return [
        {"sField": sField, "sOutcome": S_ACTION_NEXT_START,
         "sSentence": _fsDescribeSavedLimit(sField, config, iHostCores)
         + " " + S_NEXT_START_CLAUSE}
        for sField in listChangedFields
    ]


def _fsDescribeSavedLimit(sField, config, iHostCores):
    """Return what the file now says about one limit."""
    if sField == S_FIELD_MEMORY:
        iBytes = fiResolveMemoryBytes(config)
        if iBytes is None:
            return "vaibify.yml now sets no memory limit."
        return f"The memory limit in vaibify.yml is now {fsFormatBytes(iBytes)}."
    iCpuCount = fiResolveCpuCount(config, iHostCores)
    if not getattr(config, "iCpuLimit", 0):
        return ("vaibify.yml now sets no CPU limit, so the container gets "
                f"all cores but one ({iCpuCount}).")
    return f"The CPU limit in vaibify.yml is now {_fsFormatCpus(iCpuCount)}."


# ---------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------

def fsFormatBytes(iBytes):
    """Return a byte count in the units the limit fields use (GB = 2**30)."""
    if iBytes < I_BYTES_PER_GIGABYTE:
        return f"{round(iBytes / I_BYTES_PER_MEGABYTE)} MB"
    fGigabytes = iBytes / I_BYTES_PER_GIGABYTE
    return f"{fGigabytes:.1f}".rstrip("0").rstrip(".") + " GB"


def _fsFormatCpus(fCpus):
    sNumber = f"{fCpus:g}"
    return f"{sNumber} CPU" if sNumber == "1" else f"{sNumber} CPUs"
