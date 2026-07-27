"""Tests for installer agent-selection flags and their init defaults."""

import os
import subprocess


_S_SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "vaibify", "install", "installVaibify.sh",
)


def _fsRunInstallerFunction(tmp_path, sFunctionCall):
    """Source the installer without its main block and run one helper."""
    dictEnvironment = os.environ.copy()
    dictEnvironment["VC_TESTING"] = "1"
    dictEnvironment["HOME"] = str(tmp_path)
    resultProcess = subprocess.run(
        ["sh", "-c", '. "' + _S_SCRIPT_PATH + '"; ' + sFunctionCall],
        capture_output=True, text=True, check=True, env=dictEnvironment,
    )
    return resultProcess.stdout


def test_agent_argument_accepts_case_insensitive_opencode(tmp_path):
    sOutput = _fsRunInstallerFunction(
        tmp_path,
        "fnParseArguments --agent=OpenCode; "
        'printf "%s,%s" "${bInstallOpenCode}" "${bInstallCline}"',
    )
    assert sOutput == "true,false"


def test_install_flags_persist_all_selected_init_defaults(tmp_path):
    _fsRunInstallerFunction(
        tmp_path,
        "fnParseArguments --install-claude --install-codex --install-gemini "
        "--install-opencode --install-cline --install-openhands --install-pi; "
        "fnEnableAgentDefaults",
    )
    assert (tmp_path / ".vaibify" / "agent-defaults").read_text().splitlines() == [
        "claude", "codex", "gemini", "opencode", "cline", "openhands", "pi",
    ]


def test_init_applies_only_known_installer_agent_defaults(tmp_path, monkeypatch):
    from vaibify.cli.commandInit import _fnApplyInstallerAgentDefaults
    from vaibify.config.projectConfig import ProjectConfig

    pathDefaults = tmp_path / ".vaibify" / "agent-defaults"
    pathDefaults.parent.mkdir()
    pathDefaults.write_text("opencode\nopenhands\nunexpected\n")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    config = ProjectConfig()
    _fnApplyInstallerAgentDefaults(config)
    assert config.features.bOpenCode is True
    assert config.features.bOpenHands is True
    assert config.features.bClaude is False
    assert config.features.bGemini is False
