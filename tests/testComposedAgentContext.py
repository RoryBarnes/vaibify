"""Host-side guards around the composed agent context.

`.vaibify/agentContext.md` is vaibify's own file: the entrypoint
composes it at container start from the shipped guidance plus the
researcher's `.vaibify/AGENTS.md`, and every provider's repo-root name
is a symlink onto it. Two consequences land on the host.

The adopt affordance becomes dangerous. It offers to import a repo-root
`CLAUDE.md` as the researcher's project context, and existence checks
follow symlinks — so in a repository with no project context yet, the
root name resolves to vaibify's OWN composed guidance and adopting it
would install the craft guide as the researcher's instructions.

And a context saved from the editor must reach the composed file, or
the researcher's own instructions silently lag every agent until the
container restarts.
"""

import posixpath

import pytest

from vaibify.gui.routes import pipelineRoutes, replayRoutes


class _FakeRepoFiles:
    """Existence oracle over a set of repo-relative paths.

    Mirrors the symlink-following semantics of the real backends: a
    root name that resolves to the composed file reads as an existing
    file, which is exactly what makes the corruption possible.
    """

    def __init__(self, setPaths):
        self.setPaths = set(setPaths)

    def fbIsFile(self, sRelPath):
        return sRelPath in self.setPaths


def testAdoptIsOfferedForAResearchersOwnRootFile():
    """The affordance still works where it is meant to."""
    filesRepo = _FakeRepoFiles({"CLAUDE.md"})
    assert pipelineRoutes._fbRootContextCandidateDetected(filesRepo)


def testAdoptIsSuppressedOnceTheContextExists():
    """Nothing to adopt when the canonical file is already there."""
    filesRepo = _FakeRepoFiles({".vaibify/AGENTS.md", "CLAUDE.md"})
    assert not pipelineRoutes._fbRootContextCandidateDetected(filesRepo)


def testAdoptNeverOffersVaibifysOwnComposedGuidance():
    """The corruption guard: a linked root is not adoptable content.

    A repository with no project context has root names symlinked onto
    the composed file. Offering to adopt that would import vaibify's
    shipped craft guidance as the researcher's own instructions, and
    the researcher would have no way to tell from the dialog.
    """
    filesRepo = _FakeRepoFiles({
        ".vaibify/agentContext.md", "CLAUDE.md", "AGENTS.md",
    })
    assert not pipelineRoutes._fbRootContextCandidateDetected(filesRepo), (
        "the adopt affordance would import vaibify's own craft guide "
        "as the researcher's project context"
    )


def testTheComposedPathIsSampledByThePollSnapshot():
    """An unsampled path would raise KeyError on the snapshot backend.

    `SnapshotRepoFiles.fbIsFile` refuses paths the poll never sampled,
    so adding the guard above without extending the sampled set would
    turn the whole envelope poll into an error.
    """
    from vaibify.reproducibility import repoFiles
    assert (
        replayRoutes.S_COMPOSED_CONTEXT_RELATIVE_PATH
        in repoFiles.TUPLE_SNAPSHOT_CONTENT_PATHS
    )


class _RecordingDocker:
    """Captures writes; answers the workspace context read."""

    def __init__(self, sWorkspaceContext):
        self.sWorkspaceContext = sWorkspaceContext
        self.dictWritten = {}

    def fbaFetchFile(self, sContainerId, sAbsPath):
        if sAbsPath == "/workspace/CLAUDE.md":
            return self.sWorkspaceContext.encode("utf-8")
        raise FileNotFoundError(sAbsPath)


def testSavingContextRewritesTheComposedFile(monkeypatch):
    """A live save must reach the file the agents actually read."""
    dictCtx = {"docker": _RecordingDocker("SHIPPED GUIDANCE\n")}
    listCommitted = []

    def _fnFakeCommit(
        dictCtxArg, sContainerId, sAbsPath, sContent, requestHttp,
        sOperationName,
    ):
        listCommitted.append((sAbsPath, sContent))

    monkeypatch.setattr(
        replayRoutes, "_fnCommitContextWrite", _fnFakeCommit,
    )
    replayRoutes._fnRecomposeAgentContext(
        dictCtx, "abc123", {"sProjectRepoPath": "/workspace/someRepo"},
        "RESEARCHER TEXT\n", object(),
    )

    assert len(listCommitted) == 1, "the composed file was not rewritten"
    sPath, sContent = listCommitted[0]
    assert sPath == posixpath.join(
        "/workspace/someRepo", replayRoutes.S_COMPOSED_CONTEXT_RELATIVE_PATH,
    )
    assert "SHIPPED GUIDANCE" in sContent
    assert "RESEARCHER TEXT" in sContent
    assert sContent.index("SHIPPED GUIDANCE") < sContent.index(
        "RESEARCHER TEXT",
    ), "the researcher's context must come last so it wins"


def testARecomposeFailureDoesNotFailTheResearchersSave(monkeypatch):
    """Their edit is already committed; this refresh is best-effort."""
    dictCtx = {"docker": _RecordingDocker("SHIPPED GUIDANCE\n")}

    def _fnRaise(*args, **kwargs):
        raise RuntimeError("container went away")

    monkeypatch.setattr(
        replayRoutes, "_fnCommitContextWrite", _fnRaise,
    )
    replayRoutes._fnRecomposeAgentContext(
        dictCtx, "abc123", {"sProjectRepoPath": "/workspace/someRepo"},
        "RESEARCHER TEXT\n", object(),
    )


def testARefusalIsNotSwallowedByTheBestEffortPath(monkeypatch):
    """A control-plane refusal must not be mistaken for an I/O failure.

    `except Exception` around a container write is exactly how a
    refusal came to silently downgrade a reproducibility badge before;
    refusals derive from ControlPlaneRefusalError so they can be let
    through, and this pins that they are.
    """
    from vaibify.config.mutationAdmission import (
        ControlPlaneRefusalError,
    )
    dictCtx = {"docker": _RecordingDocker("SHIPPED GUIDANCE\n")}

    def _fnRefuse(*args, **kwargs):
        raise ControlPlaneRefusalError("not admitted")

    monkeypatch.setattr(replayRoutes, "_fnCommitContextWrite", _fnRefuse)
    with pytest.raises(ControlPlaneRefusalError):
        replayRoutes._fnRecomposeAgentContext(
            dictCtx, "abc123",
            {"sProjectRepoPath": "/workspace/someRepo"},
            "RESEARCHER TEXT\n", object(),
        )
