"""A poll that cannot hash what it reads rewrites a file forever.

Measured on a real project, 2026-09-16: ``.vaibify/ai_provenance.json``
was rewritten every five seconds, for weeks. Every tick the poll asked
whether the stamp still matched the declaration, every tick the answer
was no, and every tick it wrote the same content back. ``git status``
showed the file permanently modified and the hub log carried one line
per tick.

Nothing about the stamp was wrong. ``fbStampMatchesDeclaration``
compares, among other things, the SHA-256 of the project context file
(``.vaibify/AGENTS.md``), and on the poll path it asks the one-exec
snapshot for that hash. The snapshot sampled that path for CONTENT and
not for HASH -- and ``SnapshotRepoFiles.fdictHashFiles`` is the single
lenient accessor on an otherwise strict class: where its siblings raise
``KeyError`` for an unsampled path, it answers ``sSha256: None``. So a
SAMPLING GAP arrived at the caller as a fact about a file, the
comparison was ``"" != <the real hash>``, and the rewrite was
unconditional.

The class of bug is the lesson, not the file: any predicate that hashes
a content path on the poll path had the same hole. So the guard below
is structural -- everything the snapshot reads is hashed -- rather than
a check that this one path is.
"""

import pytest

from vaibify.reproducibility import repoFiles


def _flistDecodeSnapshotHashPaths(sCommand):
    """Return the hash batch out of the built snapshot command.

    The command is a base64-embedded script carrying a base64-embedded
    argument payload, so the paths are read back the way the container
    reads them rather than matched as substrings of the wrapper.
    """
    import base64
    import json
    import re
    sInner = base64.b64decode(
        re.search(r"b64decode\('([^']+)'\)", sCommand).group(1),
    ).decode("utf-8")
    jsonArgs = json.loads(base64.b64decode(
        re.search(r"b64decode\('([^']+)'\)", sInner).group(1),
    ))
    return jsonArgs["listHashPaths"]


@pytest.mark.falsification
def test_every_path_the_snapshot_reads_is_also_hashed():
    """A content path that is not a hash path is a silent wrong answer.

    Asserted over the SET rather than over the one path that bit us:
    the defect is that the two lists could differ at all, and naming
    ``.vaibify/AGENTS.md`` would let the next content path be added
    with the same hole.

    Kills: dropping ``TUPLE_SNAPSHOT_CONTENT_PATHS`` from the hash
    batch, which is the state that rewrote a researcher's provenance
    stamp every five seconds for weeks.
    """
    listHashed = _flistDecodeSnapshotHashPaths(
        repoFiles._fsBuildSnapshotScriptCommand("/repo", [], []),
    )
    setUnhashed = set(repoFiles.TUPLE_SNAPSHOT_CONTENT_PATHS) - set(listHashed)
    assert not setUnhashed, (
        "the poll snapshot reads these paths but cannot hash them, so "
        "every caller that hashes one is told 'no such content' about "
        f"a file the same exec just read: {sorted(setUnhashed)}"
    )


@pytest.mark.falsification
def test_an_unchanged_stamp_is_not_rewritten_on_the_next_poll(tmp_path):
    """The predicate must answer True about the stamp it just wrote.

    Driven through the REAL writer and the REAL predicate over a real
    directory, because the bug lived in the disagreement between the
    two -- one wrote through a full adapter, the other compared through
    the snapshot -- and a test that stubbed either would have agreed
    with itself exactly as the shipped code did.

    Kills: hashing the project context to ``""`` on the comparison
    side, which is what an unsampled path produced.
    """
    from vaibify.reproducibility import aiProvenanceStamp
    (tmp_path / ".vaibify").mkdir()
    (tmp_path / aiProvenanceStamp.S_PROJECT_CONTEXT_RELATIVE_PATH).write_text(
        "# project context\n", encoding="utf-8",
    )
    dictWorkflow = {
        "sProjectRepoPath": str(tmp_path),
        "dictAiProvenance": {
            "listDeclaredModels": [
                {"sVendor": "Anthropic", "sModelId": "some-model",
                 "sUseStartDate": "2026-01-01", "sUseEndDate": "2026-01-02"},
            ],
        },
    }
    dictStamp = aiProvenanceStamp.fdictBuildAiProvenanceStamp(
        dictWorkflow, str(tmp_path),
        sWorkspacePromptSha256="a" * 64,
        bNetworkIsolatedAtCapture=False,
    )
    aiProvenanceStamp.fnWriteAiProvenanceStamp(str(tmp_path), dictStamp)

    import json
    dictReadBack = json.loads(
        (tmp_path / aiProvenanceStamp.fsStampRelativePath()).read_text(
            encoding="utf-8",
        ),
    )
    assert aiProvenanceStamp.fbStampMatchesDeclaration(
        dictReadBack, dictWorkflow, str(tmp_path),
    ), (
        "the stamp did not match the declaration it was built from, so "
        "the poll rewrites it on every tick forever"
    )
