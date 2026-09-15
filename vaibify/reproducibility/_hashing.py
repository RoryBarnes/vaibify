"""Shared streaming SHA-256 helpers for the reproducibility subsystem.

Centralises the chunked-hash idiom that previously appeared in
``arxivClient``, ``dataArchiver``, ``githubMirror``, ``overleafMirror``,
and ``zenodoClient``. Keeping a single implementation removes the risk
that a future tweak (chunk size, error handling) updates one site and
silently leaves the others on the old behaviour.

The module is private (leading underscore) — callers stay inside the
reproducibility package. For the symlink-hardened hash used by
``provenanceTracker`` see ``provenanceTracker._fsHashFileContents``,
which intentionally diverges to add ``O_NOFOLLOW``.
"""

import hashlib

_I_DEFAULT_CHUNK_BYTES = 65536


def fsHashFileSha256(sPath, iChunkBytes=_I_DEFAULT_CHUNK_BYTES):
    """Return the SHA-256 hex digest of the file at sPath, streamed in chunks."""
    hasher = hashlib.sha256()
    with open(sPath, "rb") as fileHandle:
        _fnFeedHasher(hasher, fileHandle.read, iChunkBytes)
    return hasher.hexdigest()


def ftHashFileSha256AndMd5(sPath, iChunkBytes=_I_DEFAULT_CHUNK_BYTES):
    """Return ``(sha256_hex, md5_hex)`` from ONE read of the file.

    Two passes would describe two reads, and a file that changed
    between them would be recorded with a sha256 of the old bytes and
    an md5 of the new. That is worse than either hash alone: the md5
    is what a remote archive is asked to confirm, so the confirmation
    would succeed while the sha256 -- the actual integrity claim --
    named bytes nobody uploaded.

    MD5 is requested with ``usedforsecurity=False`` because it is not
    used for security here: it exists only to speak the checksum
    vocabulary Zenodo publishes. Without that flag ``hashlib.md5()``
    RAISES on a FIPS-enabled host, which several institutional Linux
    builds are -- so the deposit would die on the researcher's own
    cluster and nowhere in testing.
    """
    hasherSha256 = hashlib.sha256()
    hasherMd5 = hashlib.md5(usedforsecurity=False)
    with open(sPath, "rb") as fileHandle:
        while True:
            baChunk = fileHandle.read(iChunkBytes)
            if not baChunk:
                break
            hasherSha256.update(baChunk)
            hasherMd5.update(baChunk)
    return hasherSha256.hexdigest(), hasherMd5.hexdigest()


def fsHashFileObjectSha256(fileObject, iChunkBytes=_I_DEFAULT_CHUNK_BYTES):
    """Return the SHA-256 hex digest of bytes read from fileObject."""
    hasher = hashlib.sha256()
    _fnFeedHasher(hasher, fileObject.read, iChunkBytes)
    return hasher.hexdigest()


def fsHashChunkIteratorSha256(iterChunks):
    """Return the SHA-256 hex digest of bytes yielded by iterChunks."""
    hasher = hashlib.sha256()
    for baChunk in iterChunks:
        if baChunk:
            hasher.update(baChunk)
    return hasher.hexdigest()


def _fnFeedHasher(hasher, fnRead, iChunkBytes):
    """Feed hasher with successive ``fnRead(iChunkBytes)`` results until EOF."""
    while True:
        baChunk = fnRead(iChunkBytes)
        if not baChunk:
            break
        hasher.update(baChunk)
