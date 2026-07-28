"""Derive the L3 hash-compare outcome of a workflow rerun.

An L3 attestation claims two independent things: that the workflow ran
again, and that the artefacts it produced are byte-identical to the ones
``MANIFEST.sha256`` pins. Only the first is observable from a pipeline
exit code. A step that exits zero and writes one different byte is
exactly the case an exit-code check cannot see, and it is the case an
attestation must never certify — so the re-hash has to happen *after*
the rerun, against the artefacts the rerun just produced.

Both lanes that can write an attestation — the dashboard's
``/level3/verify`` route and the ``vaibify reproduce --rerun`` CLI — call
:func:`fdictVerifyRerunOutputs` for that comparison. It exists as a
shared function because the two lanes previously derived the same four
fields separately and drifted: the CLI recorded ``N of N matched`` from
the exit code alone and never opened a file. The counts, the diverged
paths, and the pass decision now have one derivation, so a lane cannot
quietly disagree with the other about what "reproduced" means.
"""

from vaibify.reproducibility.manifestWriter import (
    fiCountManifestEntries,
    flistVerifyManifest,
)
from vaibify.reproducibility.repoFiles import ffilesEnsureRepoFiles


__all__ = [
    "S_DIVERGENCE_MANIFEST_UNREADABLE",
    "S_DIVERGENCE_PIPELINE_FAILED",
    "fdictVerifyRerunOutputs",
    "fiCountManifestEntriesOrZero",
]


# Non-path entries that may appear in ``listDivergedHashes``. They name a
# failure of the comparison itself rather than a file whose hash moved,
# and are always listed before any diverged path so the first line of a
# failed attestation states the dominant cause.
S_DIVERGENCE_PIPELINE_FAILED = "pipeline rerun exited non-zero"
S_DIVERGENCE_MANIFEST_UNREADABLE = "MANIFEST.sha256 missing or unreadable"


def fdictVerifyRerunOutputs(filesRepo, bRerunSucceeded):
    """Re-hash every manifest entry and return the attestation's hash fields.

    Call this only *after* the rerun has finished, so the hashes come
    from the artefacts the rerun produced. Returns ``bPassed``,
    ``iOutputHashesMatched``, ``iOutputHashesTotal`` and
    ``listDivergedHashes`` — the exact shape
    :func:`~vaibify.reproducibility.l3Attestation.fdictBuildAttestation`
    consumes. ``bPassed`` requires *both* a zero-exit rerun and a clean
    re-hash; either alone is not a reproduction. An unreadable manifest
    fails closed with zero entries counted, because a comparison that
    could not be performed must never be recorded as one that passed.
    """
    filesRepo = ffilesEnsureRepoFiles(filesRepo)
    listMismatches = _flistVerifyManifestOrNone(filesRepo)
    if listMismatches is None:
        return _fdictUnreadableManifestOutcome(bRerunSucceeded)
    iTotalEntries = fiCountManifestEntriesOrZero(filesRepo)
    listDiverged = [
        dictMismatch["sPath"] for dictMismatch in listMismatches
    ]
    if not bRerunSucceeded:
        listDiverged = [S_DIVERGENCE_PIPELINE_FAILED] + listDiverged
    return {
        "bPassed": bool(bRerunSucceeded) and not listMismatches,
        "iOutputHashesMatched": max(
            iTotalEntries - len(listMismatches), 0,
        ),
        "iOutputHashesTotal": iTotalEntries,
        "listDivergedHashes": listDiverged,
    }


def _fdictUnreadableManifestOutcome(bRerunSucceeded):
    """Return the fail-closed outcome for a manifest that cannot be read."""
    listDiverged = [S_DIVERGENCE_MANIFEST_UNREADABLE]
    if not bRerunSucceeded:
        listDiverged = [S_DIVERGENCE_PIPELINE_FAILED] + listDiverged
    return {
        "bPassed": False,
        "iOutputHashesMatched": 0,
        "iOutputHashesTotal": 0,
        "listDivergedHashes": listDiverged,
    }


def _flistVerifyManifestOrNone(filesRepo):
    """Return the mismatch list, or ``None`` when the manifest is unusable.

    A missing manifest, an IO error, or a malformed line all mean the
    same thing to the caller: no comparison was possible. Distinguishing
    them here would only let the pass decision depend on which flavour of
    unusable the manifest was.
    """
    try:
        return flistVerifyManifest(filesRepo)
    except (FileNotFoundError, OSError, ValueError):
        return None


def fiCountManifestEntriesOrZero(filesRepo):
    """Return the manifest entry count; an unusable manifest counts zero."""
    try:
        return fiCountManifestEntries(filesRepo)
    except (FileNotFoundError, OSError, ValueError):
        return 0
