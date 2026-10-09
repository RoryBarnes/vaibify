"""Measure what a recreate would discard, for the confirmation that asks first.

Restart, Rebuild and the image switches create a new container, so the
old container's writable layer -- an agent's scratch files in /tmp above
all -- is discarded. The dashboard asks this module before it lets the
researcher confirm one of them, and shows the sentence it answers.

The container is found by NAME with a fresh ``docker inspect`` rather
than through the gateway's handle cache, because a recreate gives the
name a new id and a cached handle would keep pointing at the old one.
The measurement is a typed read under the memory watch's bounded
deadline, with at most one read in flight per container, so a hung
daemon answers "timeout" and never a size.
"""

import asyncio

from vaibify.docker import writableLayerLoss as loss

from . import containerMemorySampler
from . import containerMemoryWatch

__all__ = [
    "fdictPreviewWritableLayer",
]


async def fdictPreviewWritableLayer(connectionDocker, sName, dictReadsInFlight):
    """Return ``{sState, iTmpBytes, sSentence}`` for the container named sName."""
    from vaibify.docker import containerManager
    dictInspect = await asyncio.to_thread(
        containerManager.fjsonInspectContainer, sName)
    if not dictInspect:
        return _fdictAnswer(
            loss.S_STATE_UNREADABLE, sReason=(
                "Docker did not describe the container"))
    if not (dictInspect.get("State") or {}).get("Running"):
        return _fdictAnswer(loss.S_STATE_NOT_RUNNING)
    try:
        iTmpBytes = await containerMemorySampler.fgenericReadWithDeadline(
            dictReadsInFlight, sName, connectionDocker.fiReadTmpBytes,
            dictInspect.get("Id", ""))
    except Exception as error:  # noqa: BLE001 -- a failed measure never blocks
        return _fdictAnswer(loss.S_STATE_UNREADABLE, sReason=(
            f"its /tmp could not be read ({type(error).__name__})"))
    if iTmpBytes is None:
        return _fdictAnswer(loss.S_STATE_TIMEOUT, sReason=(
            "the container did not answer within "
            f"{containerMemoryWatch.F_MEMORY_READ_TIMEOUT_SECONDS:g} seconds"))
    return _fdictAnswer(loss.S_STATE_MEASURED, iTmpBytes=iTmpBytes)


def _fdictAnswer(sState, iTmpBytes=None, sReason=""):
    return {
        "sState": sState, "iTmpBytes": iTmpBytes,
        "sSentence": loss.fsDescribeWritableLayerLoss(
            sState, iTmpBytes, sReason),
    }
