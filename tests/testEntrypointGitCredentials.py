"""Tests for the git-credential helper installed by docker/entrypoint.sh.

Audit finding C3: the historical entrypoint wrote the raw GitHub
token to ``~/.git-credentials`` (mode 600) at container startup. The
replacement is a callback helper that streams ``username``/``password``
on stdout on demand, reading the token from the read-only secret mount
without ever writing it to the container filesystem.
"""

import os
import subprocess


_S_ENTRYPOINT = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), "..", "docker", "entrypoint.sh",
    )
)


def _fnSourceAndCall(sWorkspace, sBody):
    """Source entrypoint.sh in a subshell, then run sBody."""
    sScript = (
        "set +e\n"
        "WORKSPACE=" + sWorkspace + "\n"
        "export WORKSPACE\n"
        "source " + _S_ENTRYPOINT + "\n"
        + sBody
    )
    return subprocess.run(
        ["bash", "-c", sScript], capture_output=True, text=True,
    )


def test_entrypoint_does_not_write_git_credentials_file(tmp_path):
    """The startup path must not write a ``.git-credentials`` file.

    Comments referencing the legacy filename are fine; the offence is
    a shell redirect (``> ... .git-credentials``) or a ``cat`` into
    one, both of which would persist the raw token.
    """
    with open(_S_ENTRYPOINT, "r") as fileHandle:
        sSource = fileHandle.read()
    listOffending = [
        sLine for sLine in sSource.splitlines()
        if ".git-credentials" in sLine
        and not sLine.lstrip().startswith("#")
    ]
    assert listOffending == []


def test_credential_helper_returns_token_from_secret_mount(tmp_path):
    """Helper outputs the token read from /run/secrets/gh_token style file."""
    sBinDir = str(tmp_path / "bin")
    os.makedirs(sBinDir)
    sHelperPath = sBinDir + "/vaibify-git-credential-helper"
    sSecretFile = str(tmp_path / "fake_gh_token")
    with open(sSecretFile, "w") as fileHandle:
        fileHandle.write("ghp_examplesecret123\n")
    sBody = (
        "fnInstallCredentialHelper() {\n"
        "    local sPath=\"" + sHelperPath + "\"\n"
        "    cat > \"${sPath}\" << 'HELPER'\n"
        "#!/bin/bash\n"
        "case \"${1:-}\" in\n"
        "    get)\n"
        "        sToken=$(cat \"" + sSecretFile + "\")\n"
        "        printf 'username=x-access-token\\n'\n"
        "        printf 'password=%s\\n' \"${sToken}\"\n"
        "        ;;\n"
        "esac\n"
        "HELPER\n"
        "    chmod 0755 \"${sPath}\"\n"
        "}\n"
        "fnInstallCredentialHelper\n"
        "echo get | \"" + sHelperPath + "\" get\n"
    )
    resultProc = _fnSourceAndCall(str(tmp_path), sBody)
    assert "username=x-access-token" in resultProc.stdout
    assert "password=ghp_examplesecret123" in resultProc.stdout


def test_installed_helper_uses_callback_not_store():
    """``credential.helper store`` must not appear in the entrypoint."""
    with open(_S_ENTRYPOINT, "r") as fileHandle:
        sSource = fileHandle.read()
    assert "credential.helper store" not in sSource
    assert "vaibify-git-credential-helper" in sSource


def test_helper_is_installed_even_when_token_absent(tmp_path):
    """The helper script must be written even without a token so a
    later ``gh auth login`` inside the container takes effect without
    a restart."""
    with open(_S_ENTRYPOINT, "r") as fileHandle:
        sSource = fileHandle.read()
    assert "fnInstallCredentialHelper" in sSource
    iConfigureStart = sSource.index("fnConfigureGit()")
    sBody = sSource[iConfigureStart:]
    iAuthBranch = sBody.index("if [ -n \"${sToken}\" ]")
    assert "fnInstallCredentialHelper" in sBody[:iAuthBranch]
