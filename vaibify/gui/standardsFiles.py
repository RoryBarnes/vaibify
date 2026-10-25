"""A step's standards files: who wrote one, and whether it is valid JSON.

Each generated test category reads its criteria from a standards JSON
beside it in the step's ``tests`` directory. Two questions about those
files recur across the hub:

* whether vaibify wrote the file and nobody has edited it since, which
  decides whether regeneration may remove it without asking. A file
  carries a digest of its own content for that;
* whether it parses at all. Text reaches a standards file verbatim
  through the dashboard's file editor, ``save-and-run-test`` and the
  agent's shell, so a malformed one used to sit there until the test
  that loads it failed with an error nobody could place.
"""

import hashlib
import json
import posixpath

__all__ = [
    "S_STANDARDS_DIGEST_KEY",
    "fsComputeStandardsDigest",
    "fsSerializeStandards",
    "fbStandardsTextIsUnmodified",
    "flistStepStandardsPaths",
    "fbIsStepStandardsPath",
    "fsDescribeInvalidStandardsText",
    "fnRefuseInvalidStandardsWrite",
    "S_REFUSAL_INVALID_STANDARDS",
]

S_REFUSAL_INVALID_STANDARDS = "invalid-standards-json"

S_STANDARDS_DIGEST_KEY = "sContentDigest"


def fsComputeStandardsDigest(dictStandards):
    """Return the sha256 of a standards document, its digest field excluded.

    Canonical JSON (sorted keys, no whitespace), so the digest names the
    content, not one serialization of it.
    """
    dictContent = {
        sKey: dictStandards[sKey] for sKey in dictStandards
        if sKey != S_STANDARDS_DIGEST_KEY
    }
    sCanonical = json.dumps(
        dictContent, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(sCanonical.encode("utf-8")).hexdigest()


def fsSerializeStandards(dictStandards):
    """Return the standards JSON vaibify writes, carrying its own digest."""
    dictStamped = dict(dictStandards)
    dictStamped[S_STANDARDS_DIGEST_KEY] = fsComputeStandardsDigest(
        dictStamped,
    )
    return json.dumps(dictStamped, indent=4)


def fbStandardsTextIsUnmodified(sText):
    """Return True when a standards file is vaibify's own, unedited.

    No digest, a different digest, or text that does not parse all mean
    someone other than the generator wrote it.
    """
    try:
        dictStandards = json.loads(sText)
    except (ValueError, TypeError):
        return False
    if not isinstance(dictStandards, dict):
        return False
    sRecorded = dictStandards.get(S_STANDARDS_DIGEST_KEY)
    return bool(sRecorded) and sRecorded == fsComputeStandardsDigest(
        dictStandards,
    )


def flistStepStandardsPaths(sStepDirectory):
    """Return the three standards paths the generator writes for a step."""
    from .testGenerator import (
        fsIntegrityStandardsPath,
        fsQualitativeStandardsPath,
        fsQuantitativeStandardsPath,
    )
    return [
        fsIntegrityStandardsPath(sStepDirectory),
        fsQualitativeStandardsPath(sStepDirectory),
        fsQuantitativeStandardsPath(sStepDirectory),
    ]


def fbIsStepStandardsPath(sPath):
    """Return True when a path is one of the generator's standards files.

    Decided by the generator's own path builders, applied to the step
    directory the path sits in, so the predicate can never drift from
    the names actually written.
    """
    sTestsDirectory = posixpath.dirname(sPath)
    if posixpath.basename(sTestsDirectory) != "tests":
        return False
    return sPath in flistStepStandardsPaths(
        posixpath.dirname(sTestsDirectory),
    )


def fsDescribeInvalidStandardsText(sText, sPath):
    """Return why a standards file is not valid JSON, or "" when it is."""
    try:
        json.loads(sText)
    except ValueError as error:
        return (
            f"standards file {posixpath.basename(sPath)} is not valid "
            f"JSON: {getattr(error, 'msg', str(error))} (line "
            f"{getattr(error, 'lineno', '?')}, column "
            f"{getattr(error, 'colno', '?')})"
        )
    return ""


def fnRefuseInvalidStandardsWrite(sPath, sText):
    """Raise 422, before anything is written, for a malformed standards file.

    The one refusal every write route that can reach a standards file
    gives -- the dashboard's file editor and ``save-and-run-test``. Only
    a step's standards file is checked; every other file, JSON or not,
    is written as before.
    """
    if not fbIsStepStandardsPath(sPath):
        return
    sInvalid = fsDescribeInvalidStandardsText(sText, sPath)
    if sInvalid:
        from fastapi import HTTPException
        raise HTTPException(422, {
            "sRefusal": S_REFUSAL_INVALID_STANDARDS,
            "sMessage": "Not saved: " + sInvalid + ".",
        })
