"""Guards against injection through paths, origins, and template names.

Each of these is a prefix- or interpolation-shaped hole: a check that
looked right because it compared the beginning of a string, or a value
that was pasted into a shell command without quoting.
"""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from vaibify.config import templateManager
from vaibify.gui import mtimeCache
from vaibify.gui import pipelineServer
from tests.sessionTokenTestHelper import fsBootstrapCredential
from vaibify.gui import testStatusManager
from vaibify.gui.pipelineServer import (
    fbOriginIsLoopback,
    fsValidatePathWithinRoot,
)


# ── Control characters in validated paths (heredoc escape) ───────


@pytest.mark.falsification
def testPathValidationRejectsControlCharacters():
    """A path carrying a newline must not survive validation.

    ``_fdictTestExistenceBatch`` feeds validated paths into a
    ``<<'__VAIBIFY_EOF__'`` heredoc. A path containing a newline, the
    terminator, and a command closes the heredoc early and runs the
    remainder under ``/bin/bash -c``. Rejecting the class at the shared
    validator protects every interpolating caller at once.

    Kills: pipelineServer.fsValidatePathWithinRoot: the guard call
    `_fnRejectControlCharactersInPath(sResolvedPath)` removed.
    """
    sPayload = (
        "/workspace/ok\n__VAIBIFY_EOF__\ntouch /tmp/pwned\n"
        "cat <<'__VAIBIFY_EOF__'"
    )
    with pytest.raises(HTTPException) as excInfo:
        fsValidatePathWithinRoot(sPayload, "/workspace")
    assert excInfo.value.status_code == 403


def testPathValidationRejectsNulByte():
    """A NUL byte truncates paths in C APIs; refuse it too."""
    with pytest.raises(HTTPException) as excInfo:
        fsValidatePathWithinRoot("/workspace/ok\x00.png", "/workspace")
    assert excInfo.value.status_code == 403


def testPathValidationStillAcceptsOrdinaryPaths():
    """Ordinary workflow paths, including spaces, remain valid."""
    assert fsValidatePathWithinRoot(
        "/workspace/My Step/output 1.csv", "/workspace",
    ) == "/workspace/My Step/output 1.csv"


# ── Loopback Origin (prefix attack) ──────────────────────────────


@pytest.mark.falsification
def testLoopbackOriginRejectsASuffixDomain():
    """``http://localhost.evil.example`` must not read as loopback.

    The check compared prefixes, so an attacker-registered domain
    beginning with a loopback name passed -- the same prefix-attack
    class ``fsValidatePathWithinRoot`` explicitly defends against.

    Kills: pipelineServer.fbOriginIsLoopback: the parsed host equality
    `return (tParsed.hostname or "") in _SET_LOOPBACK_ORIGIN_HOSTS`
    replaced by a prefix compare.
    """
    assert fbOriginIsLoopback("http://localhost.evil.example") is False
    assert fbOriginIsLoopback("http://127.0.0.1.evil.example") is False
    assert fbOriginIsLoopback("https://localhost-evil.example") is False


def testLoopbackOriginAcceptsTheRealLoopbackForms():
    """The origins a real browser sends must still be accepted."""
    for sOrigin in (
        "http://127.0.0.1:8050",
        "http://localhost:8050",
        "http://[::1]:8050",
        "https://127.0.0.1",
    ):
        assert fbOriginIsLoopback(sOrigin) is True, sOrigin


def testLoopbackOriginRejectsNonHttpSchemes():
    """A non-http scheme is never a browser page origin."""
    assert fbOriginIsLoopback("file://localhost") is False
    assert fbOriginIsLoopback("") is False


# ── Template names (path traversal into an arbitrary directory) ──


@pytest.mark.falsification
def testTemplateNameCannotEscapeTheTemplateRoot():
    """A traversing template name must not select a host directory.

    ``POST /api/projects/create`` jails ``sDirectory`` but not
    ``sTemplateName``, so an escaping name copied an arbitrary host
    directory into a new project that is then mounted into a container.

    Kills: templateManager._fpathResolveTemplate: the containment check
    `if pathRoot not in pathTemplate.parents:` neutralized to
    `if False:`.
    """
    with pytest.raises(FileNotFoundError):
        templateManager._fpathResolveTemplate("../../../../etc")
    with pytest.raises(FileNotFoundError):
        templateManager._fpathResolveTemplate("..")


def testTemplateNameStillResolvesARealTemplate():
    """A genuine template name keeps resolving after the guard."""
    listNames = templateManager.flistAvailableTemplates()
    assert listNames
    pathTemplate = templateManager._fpathResolveTemplate(listNames[0])
    assert pathTemplate.is_dir()


# ── Shell interpolation of paths ─────────────────────────────────


@pytest.mark.falsification
def testPersistedTestCommandQuotesItsPath():
    """The stored pytest command must quote its file path.

    ``saTestCommands`` is persisted into project.json and re-executed on
    every later test run, so an unquoted path with shell metacharacters
    becomes a stored, repeatedly-executed injection.

    Kills: testStatusManager._fnRegisterTestCommand: the quoting call
    `{fsShellQuote(sFilePath)}` replaced by the bare `{sFilePath}`.
    """
    dictStep = {}
    testStatusManager._fnRegisterTestCommand(
        dictStep, True, "step/tests/t.py; rm -rf /",
    )
    sCommand = dictStep["saTestCommands"][0]
    assert sCommand == (
        "python -m pytest 'step/tests/t.py; rm -rf /' -v"
    )


class _RecordingDocker:
    """Capture the commands a caller asks the container to run."""

    def __init__(self):
        self.listCommands = []

    def fnWriteFile(self, sContainerId, sPath, baContent):
        pass

    def ftResultExecuteCommand(
        self, sContainerId, sCommand, sWorkdir=None,
    ):
        self.listCommands.append(sCommand)
        return (0, "")


@pytest.mark.falsification
def testContainerCacheRenameQuotesBothPaths():
    """The cache's atomic rename must quote the paths it moves.

    ``sProjectRepoPath`` comes from the workflow, so an unquoted
    ``mv {a} {b}`` is command injection through a repo path.

    Kills: mtimeCache.fnSaveContainerCache: the quoted rename
    `f"mv {fsShellQuote(sPathTemp)} {fsShellQuote(sPath)}"` replaced by
    the unquoted interpolation.
    """
    connectionDocker = _RecordingDocker()
    mtimeCache.fnSaveContainerCache(
        connectionDocker, "cid", "/workspace/my repo", {},
    )
    assert connectionDocker.listCommands
    sCommand = connectionDocker.listCommands[0]
    assert sCommand.count("'") == 4
    assert "mv '/workspace/my repo/" in sCommand


# ── Host-header check must not fail open ─────────────────────────


def _fappBareMiddlewareApplication():
    """Build a FastAPI app with the session middleware and no state."""
    from fastapi import FastAPI
    from vaibify.gui import browserSession
    app = FastAPI()
    app.state.sSessionToken = "shared-token"
    app.state.dictBrowserSessions = (
        browserSession.fdictCreateBrowserSessionStore()
    )
    app.add_middleware(pipelineServer.SessionTokenMiddleware)

    @app.get("/api/probe")
    async def fnProbe():
        return {"bOk": True}

    return app


@pytest.mark.falsification
def testUndeclaredExpectedPortFailsTheHostCheckClosed():
    """An app that never declares iExpectedPort must refuse requests.

    The DNS-rebinding defence used to be skipped whenever the expected
    port was falsy, and a missing value read as falsy, so a wiring
    mistake silently disabled it for every request the app served.

    Kills: serverMiddleware._fbRequestHasAllowedHost: the
    undeclared-state branch `if iExpectedPort is None: return False`
    replaced by `return True`.
    """
    client = TestClient(
        _fappBareMiddlewareApplication(),
        headers={"X-Session-Token": "shared-token"},
    )
    assert client.get("/api/probe").status_code == 400


def testDeclaredZeroPortKeepsTheDocumentedOptOut():
    """Declaring 0 remains the explicit in-process test-harness opt-out."""
    app = _fappBareMiddlewareApplication()
    app.state.iExpectedPort = 0
    client = TestClient(
        app, headers={"X-Session-Token": fsBootstrapCredential(app)},
    )
    assert client.get("/api/probe").status_code == 200
