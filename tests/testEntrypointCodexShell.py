"""The container's Codex alias runs through the real terminal chat wrapper."""

import shlex
import subprocess
from pathlib import Path

import pytest

from tests.testAgentChatLifecycle import S_UUID_A, tBash  # noqa: F401


PATH_ENTRYPOINT = (
    Path(__file__).resolve().parents[1]
    / "vaibify" / "containerImage" / "entrypoint.sh"
)
LIST_CODEX_OPTIONS = [
    "--sandbox", "danger-full-access", "--ask-for-approval", "on-request",
]


def _fnConfigureShell(pathBashrc):
    subprocess.run(
        ["/bin/bash", "-euc", 'source "$1"\nfnConfigureCodexShell "$2"',
         "_", str(PATH_ENTRYPOINT), str(pathBashrc)],
        capture_output=True, check=True, timeout=30,
    )


def testCodexAliasPreservesExistingConfigurationAndDoesNotAccumulate(tmp_path):
    pathBashrc = tmp_path / ".bashrc"
    sExisting = "alias researcherCommand='printf kept'\n"
    pathBashrc.write_text(sExisting, encoding="utf-8")
    _fnConfigureShell(pathBashrc)
    sConfigured = pathBashrc.read_text(encoding="utf-8")
    _fnConfigureShell(pathBashrc)
    assert pathBashrc.read_text(encoding="utf-8") == sConfigured
    assert sConfigured.startswith(sExisting)


@pytest.mark.parametrize("saArguments,sSessionId", [
    (["a prompt with spaces", "$(printf unintended)", "a;b"], ""),
    (["resume", S_UUID_A, "a prompt with spaces"], S_UUID_A),
])
def testCodexAliasPreservesChatTrackingAndArguments(
    tmp_path, tBash, saArguments, sSessionId,
):
    _fnConfigureShell(tmp_path / ".bashrc")
    processBash, listEvents, _, _ = tBash(
        "codex " + shlex.join(saArguments))
    saExpected = LIST_CODEX_OPTIONS + saArguments
    assert processBash.stdout.strip() == "".join(
        "[" + sArgument + "]" for sArgument in saExpected)
    assert processBash.returncode == 3
    assert listEvents[0]["sProvider"] == "codex"
    assert listEvents[0]["sSessionId"] == sSessionId
    assert listEvents[0]["listArguments"] == saExpected
    assert listEvents[1] == {"sEvent": "exit", "iStatus": 3}


@pytest.mark.parametrize("sCommand", [
    "exec 'a prompt with spaces'", "update", "--help", "--version",
])
def testCodexAliasKeepsNonChatCommandsOutOfTheChatRecord(
    tmp_path, tBash, sCommand,
):
    _fnConfigureShell(tmp_path / ".bashrc")
    processBash, listEvents, _, _ = tBash("codex " + sCommand)
    assert processBash.returncode == 3
    assert listEvents == []


def testCodexWrapperAcceptsEmptyArgumentsWithNounset(tBash):
    processBash, listEvents, _, _ = tBash("set -u\ncodex")
    assert processBash.returncode == 3, processBash.stderr
    assert listEvents[0]["listArguments"] == []
    assert listEvents[1] == {"sEvent": "exit", "iStatus": 3}
