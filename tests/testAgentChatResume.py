"""A saved agent chat is resumed from a fixed table, with honest permissions.

Source: ``vaibify/gui/agentChatResume.py`` and
``vaibify/gui/agentChatCatalog.py``.

The expected permission verdicts follow from what each CLI was measured
to do (plan section 13): Claude's ``--dangerously-skip-permissions``
beats ``--permission-mode default``; ``--permission-mode default``
overrides a bypass ``defaultMode``; Codex is bypass only for approval
``never`` with the ``danger-full-access`` sandbox; Gemini's settings
cannot enable yolo. They are not read back from the code under test.

The catalog program runs for real, in this interpreter, against a
private HOME holding transcripts in each CLI's measured format.
"""

import json
import os
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vaibify.docker.execLane import S_HUB_PYTHON_COMMAND
from vaibify.gui import actionCatalog, agentChatCatalog, agentChatResume
from vaibify.gui.routes import dashboardStateRoutes

S_UUID = "1c37be47-a36d-45b8-9688-6f971b50c106"
S_CODEX_UUID = "01a1b787-2163-76c1-bd9b-db4794ad03e1"
S_GEMINI_UUID = "fe43d206-d2e5-4fef-9988-1f42eaa741d8"
S_REPO = "/workspace/analysisRepo"


def _fdictChat(sProvider="claude", listArguments=(), sSessionId=S_UUID):
    return {"sProvider": sProvider, "sSessionId": sSessionId,
            "sCwd": S_REPO + "/sub dir", "listArguments": list(listArguments)}


def _flistWords(sCommand):
    sCd, sResume = sCommand.split(" && ", 1)
    assert shlex.split(sCd) == ["cd", "--", S_REPO + "/sub dir"]
    return shlex.split(sResume)


# ---------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------


@pytest.mark.parametrize("sProvider,listExpected", [
    ("claude", ["claude", "--resume", S_UUID]),
    ("gemini", ["gemini", "--resume", S_UUID]),
    ("codex", ["codex", "resume", S_UUID]),
])
def testEachProviderResumesFromTheFixedTable(sProvider, listExpected):
    dictPlan = agentChatResume.fdictBuildResumePlan(
        _fdictChat(sProvider), {}, "")
    assert _flistWords(dictPlan["sResumeCommand"]) == listExpected


def testTheOriginalPermissionFlagsAreReplayed():
    dictPlan = agentChatResume.fdictBuildResumePlan(_fdictChat(
        listArguments=["--dangerously-skip-permissions", "--model", "opus"]),
        {}, "")
    assert _flistWords(dictPlan["sResumeCommand"]) == [
        "claude", "--resume", S_UUID, "--dangerously-skip-permissions",
        "--model", "opus"]


@pytest.mark.falsification
def testStandardModeStripsTheBypassAndAddsTheExplicitStandard():
    """Standard never just removes flags; it removes AND adds.

    Kills: emitting the kept flags without the explicit standard pair,
    which left a config-level bypass in force.
    """
    for sProvider, listArguments, listStandard in (
        ("claude", ["--dangerously-skip-permissions", "--model", "opus"],
         ["--model", "opus", "--permission-mode", "default"]),
        ("gemini", ["--yolo"], ["--approval-mode", "default"]),
        ("codex", ["--dangerously-bypass-approvals-and-sandbox"],
         ["-a", "on-request", "-s", "workspace-write"]),
    ):
        dictPlan = agentChatResume.fdictBuildResumePlan(
            _fdictChat(sProvider, listArguments), {}, "")
        assert _flistWords(dictPlan["sStandardCommand"])[3:] == listStandard


@pytest.mark.falsification
def testAnArgvBypassAndAConfigBypassBothTriggerTheGate():
    """Kills: ignoring the provider's config defaults."""
    dictFlags = agentChatResume.fdictBuildResumePlan(_fdictChat(
        listArguments=["--dangerously-skip-permissions"]), {}, "")["dictPermission"]
    assert dictFlags["bBypass"] and dictFlags["listBypassSources"] == ["flags"]
    dictConfig = agentChatResume.fdictBuildResumePlan(
        _fdictChat(), {"sClaudeDefaultMode": "bypassPermissions"}, "")["dictPermission"]
    assert dictConfig["bBypass"] and dictConfig["listBypassSources"] == ["config"]
    dictCodex = agentChatResume.fdictBuildResumePlan(
        _fdictChat("codex"), {"sCodexApprovalPolicy": "never",
                              "sCodexSandboxMode": "danger-full-access"},
        "")["dictPermission"]
    assert dictCodex["listBypassSources"] == ["config"]


def testAnExplicitModeFlagOverridesTheConfigDefault():
    dictPermission = agentChatResume.fdictBuildResumePlan(_fdictChat(
        listArguments=["--permission-mode", "default"]),
        {"sClaudeDefaultMode": "bypassPermissions"}, "")["dictPermission"]
    assert dictPermission == {"sMode": "default", "bBypass": False,
                              "listBypassSources": []}


def testTheTranscriptsRecordedModeCountsAsBypass():
    dictPermission = agentChatResume.fdictBuildResumePlan(
        _fdictChat(), {}, "bypassPermissions")["dictPermission"]
    assert dictPermission["listBypassSources"] == ["transcript"]
    assert agentChatResume.fdictBuildResumePlan(
        _fdictChat("codex"), {}, "never:danger-full-access",
    )["dictPermission"]["listBypassSources"] == ["transcript"]


def testAutoAndPlanAreNotBypass():
    for sMode in ("auto", "plan", "acceptEdits", "manual"):
        assert agentChatResume.fdictBuildResumePlan(_fdictChat(
            listArguments=["--permission-mode", sMode]), {}, sMode,
        )["dictPermission"]["bBypass"] is False


def testGeminiSettingsCannotMakeABypass():
    assert agentChatResume.fdictBuildResumePlan(
        _fdictChat("gemini"), {"sClaudeDefaultMode": "bypassPermissions"}, "",
    )["dictPermission"]["bBypass"] is False


def testEverythingOutsideTheAllowListIsDroppedAndNamed():
    dictPlan = agentChatResume.fdictBuildResumePlan(_fdictChat(listArguments=[
        "fix the bug", "--add-dir", "/etc", "--model", "$(id)",
        "--resume", S_UUID]), {}, "")
    assert dictPlan["listDroppedArguments"] == [
        "fix the bug", "--add-dir", "/etc", "--model $(id)"]
    assert _flistWords(dictPlan["sResumeCommand"]) == [
        "claude", "--resume", S_UUID]


@pytest.mark.falsification
def testAChatWithNoIdIsNeverGivenAResumeCommand():
    """An inferred identity never resumes: there is nothing to name.

    Kills: dropping the session-id check from the command builder, which
    typed a bare ``claude --resume`` that opens a picker or the latest.
    """
    for sSessionId in ("", "not-a-uuid", "$(id)"):
        with pytest.raises(ValueError):
            agentChatResume.fdictBuildResumePlan(
                _fdictChat(sSessionId=sSessionId), {}, "")


# ---------------------------------------------------------------------
# The catalog program, for real
# ---------------------------------------------------------------------


def _fnWriteLines(sPath, listLines):
    os.makedirs(os.path.dirname(sPath), exist_ok=True)
    with open(sPath, "w", encoding="utf-8") as fileTranscript:
        for dictLine in listLines:
            fileTranscript.write(json.dumps(dictLine) + "\n")


@pytest.fixture()
def sHome(tmp_path):
    sHomePath = str(tmp_path / "home")
    sRepo = str(tmp_path / "repo")
    _fnWriteLines(os.path.join(
        sHomePath, ".claude/projects/-repo", S_UUID + ".jsonl"), [
        {"type": "user", "isMeta": True, "cwd": sRepo,
         "message": {"content": "<command-name>/clear</command-name>"}},
        {"type": "user", "cwd": sRepo, "permissionMode": "default",
         "message": {"content": [{"type": "text", "text": "Plot the  data"}]}},
        {"type": "assistant", "permissionMode": "bypassPermissions"},
    ])
    _fnWriteLines(os.path.join(
        sHomePath, ".codex/sessions/2026/11/07",
        "rollout-2026-11-07T19-40-15-" + S_CODEX_UUID + ".jsonl"), [
        {"type": "session_meta", "payload": {"id": S_CODEX_UUID, "cwd": sRepo}},
        {"type": "event_msg", "payload": {"type": "user_message",
                                          "message": "Fit the model"}},
        {"type": "turn_context", "payload": {
            "approval_policy": "never",
            "sandbox_policy": {"type": "danger-full-access"}}},
    ])
    sGeminiDirectory = os.path.join(sHomePath, ".gemini/tmp/repo")
    _fnWriteLines(os.path.join(
        sGeminiDirectory, "chats", "session-2026-11-07T19-39-fe43d206.jsonl"), [
        {"sessionId": S_GEMINI_UUID, "kind": "main"},
        {"type": "user", "content": [{"text": "<session_context>x"}]},
        {"type": "user", "content": [{"text": "Summarize the run"}]},
    ])
    with open(os.path.join(sGeminiDirectory, ".project_root"), "w") as fileRoot:
        fileRoot.write(sRepo)
    _fnWriteLines(os.path.join(sHomePath, ".claude/projects/-other",
                               "96969a67-6545-426c-b128-aeacfda91223.jsonl"),
                  [{"type": "user", "cwd": "/elsewhere",
                    "message": {"content": "unrelated"}}])
    os.makedirs(os.path.join(sHomePath, ".codex"), exist_ok=True)
    with open(os.path.join(sHomePath, ".codex/config.toml"), "w") as fileConfig:
        fileConfig.write('approval_policy = "never"\n[profiles.x]\n'
                         'sandbox_mode = "read-only"\n')
    return sHomePath, sRepo


def _fdictRunCatalog(sHomePath, sRepo, listRequested=()):
    def ftRunWhereTheTranscriptsLive(sId, sCommand):
        processCatalog = subprocess.run(
            ["/bin/sh", "-c", sCommand.replace(
                S_HUB_PYTHON_COMMAND, shlex.quote(sys.executable), 1)],
            capture_output=True, text=True, timeout=60,
            env={"HOME": sHomePath, "PATH": "/usr/bin:/bin"})
        assert processCatalog.returncode == 0, processCatalog.stderr
        return SimpleNamespace(iExitCode=0, sStdout=processCatalog.stdout,
                               sStderr="")

    connection = SimpleNamespace(
        ftRunInContainerStreamed=ftRunWhereTheTranscriptsLive)
    return agentChatCatalog.fdictListAgentChats(
        connection, "a1b2", sRepo, list(listRequested))


def testTheCatalogListsThisProjectsChatsInEachCLIsFormat(sHome):
    sHomePath, sRepo = sHome
    dictListing = _fdictRunCatalog(sHomePath, sRepo)
    dictByProvider = {d["sProvider"]: d for d in dictListing["listChats"]}
    assert set(dictByProvider) == {"claude", "codex", "gemini"}
    assert dictByProvider["claude"]["sFirstPrompt"] == "Plot the data"
    assert dictByProvider["claude"]["sRecordedMode"] == "bypassPermissions"
    assert dictByProvider["codex"]["sSessionId"] == S_CODEX_UUID
    assert dictByProvider["codex"]["sFirstPrompt"] == "Fit the model"
    assert dictByProvider["codex"]["sRecordedMode"] == "never:danger-full-access"
    assert dictByProvider["gemini"]["sSessionId"] == S_GEMINI_UUID
    assert dictByProvider["gemini"]["sFirstPrompt"] == "Summarize the run"
    assert dictListing["dictConfig"] == {
        "sClaudeDefaultMode": "", "sCodexApprovalPolicy": "never",
        "sCodexSandboxMode": ""}


def testAChatFromAnotherProjectIsListedOnlyWhenASavedChatNamesIt(sHome):
    sHomePath, sRepo = sHome
    sOther = "96969a67-6545-426c-b128-aeacfda91223"
    assert sOther not in [d["sSessionId"] for d in _fdictRunCatalog(
        sHomePath, sRepo)["listChats"]]
    dictListing = _fdictRunCatalog(sHomePath, sRepo, [
        {"sProvider": "claude", "sSessionId": sOther}])
    listOther = [d for d in dictListing["listChats"] if d["sSessionId"] == sOther]
    assert listOther and listOther[0]["bInProject"] is False
    assert listOther[0]["bRequested"] is True


def testTheAnswerGivesCatalogChatsNoFlagsAndSavedChatsTheirOwn(sHome):
    sHomePath, sRepo = sHome
    dictSaved = {"sProvider": "claude", "sSessionId": S_UUID,
                 "sCwd": sRepo, "listArguments": ["--model", "opus"],
                 "sIdentityClass": "tracked"}
    dictAnswer = dashboardStateRoutes._fdictComposeCatalogAnswer(
        _fdictRunCatalog(sHomePath, sRepo, [dictSaved]), [dictSaved])
    dictCatalogClaude = [d for d in dictAnswer["listCatalog"]
                         if d["sProvider"] == "claude"][0]
    assert "--model" not in dictCatalogClaude["dictPlan"]["sResumeCommand"]
    dictResolved = dictAnswer["dictResolved"]["claude:" + S_UUID]
    assert dictResolved["bTranscriptFound"] is True
    assert "--model opus" in dictResolved["dictPlan"]["sResumeCommand"]
    assert dictResolved["dictPlan"]["dictPermission"]["listBypassSources"] == [
        "transcript"]
    dictMissing = {"sProvider": "gemini", "sSessionId": S_UUID, "sCwd": sRepo,
                   "listArguments": []}
    dictAnswer = dashboardStateRoutes._fdictComposeCatalogAnswer(
        _fdictRunCatalog(sHomePath, sRepo, [dictMissing]), [dictMissing])
    assert dictAnswer["dictResolved"]["gemini:" + S_UUID][
        "bTranscriptFound"] is False


def testTheCatalogProgramTakesOnlyValidatedArguments():
    with pytest.raises(ValueError):
        agentChatCatalog.flistCatalogArguments("/repo/../etc", [])
    assert agentChatCatalog.flistCatalogArguments(S_REPO, [
        {"sProvider": "claude", "sSessionId": "$(id)"},
        {"sProvider": "sh", "sSessionId": S_UUID},
        {"sProvider": "codex", "sSessionId": S_CODEX_UUID}]) == [
        S_REPO, "codex:" + S_CODEX_UUID]


def testOnlyAProgramBuiltOnTheSharedLaunchRulesIsRun():
    from vaibify.gui import promptRecordManager
    listRan = []
    connection = SimpleNamespace(
        ftRunInContainerStreamed=lambda sId, sCommand: listRan.append(sCommand))
    with pytest.raises(ValueError):
        promptRecordManager.ftRunTranscriptProgram(
            connection, "a1b2", "import os; os.system('id')")
    promptRecordManager.ftRunTranscriptProgram(
        connection, "a1b2", promptRecordManager._S_LIST_PROGRAM)
    assert listRan == [promptRecordManager._S_LIST_SCRIPT]


def testAListedChatWithAMalformedFieldIsDropped():
    sStdout = json.dumps({"listChats": [
        {"sProvider": "claude", "sSessionId": "x; rm", "sLaunchDirectory": S_REPO},
        {"sProvider": "claude", "sSessionId": S_UUID,
         "sLaunchDirectory": "relative"},
        {"sProvider": "claude", "sSessionId": S_UUID, "sLaunchDirectory": S_REPO,
         "bInProject": True}], "dictConfig": {}})
    connection = SimpleNamespace(ftRunInContainerStreamed=lambda sId, sCmd: (
        SimpleNamespace(iExitCode=0, sStdout=sStdout, sStderr="")))
    listChats = agentChatCatalog.fdictListAgentChats(
        connection, "a1b2", S_REPO, [])["listChats"]
    assert [d["sSessionId"] for d in listChats] == [S_UUID]


def testTheAgentLaneCannotListChats(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    app = FastAPI()
    dashboardStateRoutes.fnRegisterAll(app, {
        "docker": SimpleNamespace(), "require": lambda sContainerId: None})
    responseHttp = TestClient(app).post(
        "/api/dashboard-state/a1b2/chat-catalog",
        json={"sProjectRepoPath": S_REPO, "listRequested": []},
        headers={"X-Vaibify-Session": "agent-token"})
    assert responseHttp.status_code == 403
    assert actionCatalog.fbAgentLanePermitsRoute(
        "POST", "/api/dashboard-state/{sContainerId}/chat-catalog") is False
