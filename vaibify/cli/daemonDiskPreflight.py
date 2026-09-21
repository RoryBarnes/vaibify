"""Ask the Docker daemon's disk how much room a build has, before building.

A build on a full daemon disk fails at whatever step next tries to
write, and that step's own words rarely say "disk": an overlay
installer blamed the network for a ``mkdir`` that had no room (a live
build, 2026-09-21), after apt, the toolchain and pip had spent their
minutes. The daemon does not report free space directly; a ``df`` run
inside any image already present answers for the filesystem every
build layer lands on. No image present is no answer, never a refusal.
"""

import subprocess

from .preflightResult import (
    PreflightResult, S_LEVEL_FAIL, S_LEVEL_NOT_CHECKED, S_LEVEL_WARN,
    S_SCOPE_HOST,
)


__all__ = [
    "I_DAEMON_FREE_DISK_FAIL_BYTES",
    "I_DAEMON_FREE_DISK_WARN_BYTES",
    "S_PREFLIGHT_NAME",
    "fiDaemonFreeDiskBytes",
    "fiParseAvailableBytesFromDf",
    "fpreflightDaemonFreeDisk",
]


S_PREFLIGHT_NAME = "docker-free-disk"
# The smallest vaibify image is over two gigabytes and each overlay
# adds more; below the first bound the build cannot finish, below the
# second it may not.
I_DAEMON_FREE_DISK_FAIL_BYTES = 4 * (2 ** 30)
I_DAEMON_FREE_DISK_WARN_BYTES = 12 * (2 ** 30)
_I_IMAGES_TO_TRY = 3
_F_PROBE_TIMEOUT_SECONDS = 20.0

S_REMEDIATION = (
    "Free space on the Docker daemon's disk: `docker system df` shows "
    "what holds it, `docker builder prune` reclaims the build cache, and "
    "images and stopped containers you no longer need can go. Do NOT run "
    "`docker system prune -a`, which removes the images your projects pin. "
    "Or enlarge the daemon's disk."
)


def fiParseAvailableBytesFromDf(sDfOutput):
    """Return the Available column of ``df -Pk`` output in bytes, or -1."""
    listLines = [sLine for sLine in (sDfOutput or "").splitlines() if sLine.strip()]
    if len(listLines) < 2:
        return -1
    listFields = listLines[1].split()
    if len(listFields) < 4 or not listFields[3].isdigit():
        return -1
    return int(listFields[3]) * 1024


def _flistLocalImageIds(fnRun):
    """Return up to a few image ids the daemon already holds."""
    try:
        processImages = fnRun(
            ["docker", "images", "-q"], capture_output=True, text=True,
            timeout=_F_PROBE_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if processImages.returncode != 0:
        return []
    return processImages.stdout.split()[:_I_IMAGES_TO_TRY]


def fiDaemonFreeDiskBytes(fnRun=subprocess.run):
    """Return the free bytes on the daemon's layer filesystem, or -1.

    Runs ``df`` inside an image the daemon already holds; the first
    image whose ``df`` answers decides. An image with no ``df`` is
    skipped, and no answering image means no answer.
    """
    for sImageId in _flistLocalImageIds(fnRun):
        try:
            processDf = fnRun(
                ["docker", "run", "--rm", "--entrypoint", "df", sImageId,
                 "-Pk", "/"],
                capture_output=True, text=True,
                timeout=_F_PROBE_TIMEOUT_SECONDS,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return -1
        if processDf.returncode != 0:
            continue
        iBytes = fiParseAvailableBytesFromDf(processDf.stdout)
        if iBytes >= 0:
            return iBytes
    return -1


def fpreflightDaemonFreeDisk(fiFreeBytes=None):
    """Return a fail, warn or not-checked result for the daemon's disk, else None.

    The probe is looked up at call time, never bound as a default, so
    a test that replaces it on the module reaches every caller.
    """
    if fiFreeBytes is None:
        fiFreeBytes = fiDaemonFreeDiskBytes
    iFreeBytes = fiFreeBytes()
    if iFreeBytes < 0:
        return PreflightResult(
            sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_NOT_CHECKED,
            sScope=S_SCOPE_HOST,
            sMessage=(
                "the daemon's free disk space could not be measured (no "
                "local image answered `df`), so the build was not checked "
                "for room."
            ),
        )
    fFreeGigabytes = iFreeBytes / (2 ** 30)
    if iFreeBytes < I_DAEMON_FREE_DISK_FAIL_BYTES:
        return PreflightResult(
            sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_FAIL, sScope=S_SCOPE_HOST,
            sMessage=(
                f"the Docker daemon's disk has {fFreeGigabytes:.1f} GB free, "
                "and a build writes several gigabytes; the build would fail "
                "at whichever step next tries to write."
            ),
            sRemediation=S_REMEDIATION, sCommand="docker system df",
        )
    if iFreeBytes < I_DAEMON_FREE_DISK_WARN_BYTES:
        return PreflightResult(
            sName=S_PREFLIGHT_NAME, sLevel=S_LEVEL_WARN, sScope=S_SCOPE_HOST,
            sMessage=(
                f"the Docker daemon's disk has {fFreeGigabytes:.1f} GB free; "
                "a build with several overlays may not fit."
            ),
            sRemediation=S_REMEDIATION, sCommand="docker system df",
        )
    return None
