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


def _fsExtractRealHelperScript(sSecretPathOverride):
    """Return the entrypoint's actual helper heredoc, secret path swapped.

    Extracting the real ``<< 'HELPER'`` body (rather than redefining a
    toy copy in the test) means these tests fail when the shipped
    helper regresses. The only test seam is the secret-mount path,
    which requires root to fake at ``/run/secrets``.
    """
    with open(_S_ENTRYPOINT, "r") as fileHandle:
        sSource = fileHandle.read()
    iStart = sSource.index("<< 'HELPER'\n") + len("<< 'HELPER'\n")
    iEnd = sSource.index("\nHELPER\n", iStart)
    sScript = sSource[iStart:iEnd]
    return sScript.replace("/run/secrets/gh_token", sSecretPathOverride)


def _fsRunRealHelper(tmp_path, sStdin):
    """Write the real helper with a fake secret and run ``get`` on it."""
    sSecretFile = str(tmp_path / "fake_gh_token")
    with open(sSecretFile, "w") as fileHandle:
        fileHandle.write("ghp_examplesecret123\n")
    sHelperPath = str(tmp_path / "vaibify-git-credential-helper")
    with open(sHelperPath, "w") as fileHandle:
        fileHandle.write(_fsExtractRealHelperScript(sSecretFile))
    os.chmod(sHelperPath, 0o755)
    resultProc = subprocess.run(
        ["bash", sHelperPath, "get"],
        input=sStdin, capture_output=True, text=True,
    )
    return resultProc.stdout


def test_credential_helper_returns_token_for_github_host(tmp_path):
    """The real helper answers a github.com credential request."""
    sOutput = _fsRunRealHelper(
        tmp_path, "protocol=https\nhost=github.com\n\n",
    )
    assert "username=x-access-token" in sOutput
    assert "password=ghp_examplesecret123" in sOutput


def test_credential_helper_stays_silent_for_foreign_hosts(tmp_path):
    """The real helper must NOT answer for non-GitHub remotes.

    An unconditional answer hijacks authentication for every other
    remote: git presents the GitHub token to git.overleaf.com, the
    push fails "auth", and the correct Overleaf token configured in
    a later helper is never consulted. Silence lets git fall through.
    """
    sOutput = _fsRunRealHelper(
        tmp_path, "protocol=https\nhost=git.overleaf.com\n\n",
    )
    assert "password" not in sOutput
    assert sOutput.strip() == ""


def test_installed_helper_uses_callback_not_store():
    """``credential.helper store`` must not appear in the entrypoint."""
    with open(_S_ENTRYPOINT, "r") as fileHandle:
        sSource = fileHandle.read()
    assert "credential.helper store" not in sSource
    assert "vaibify-git-credential-helper" in sSource


def test_helper_registration_is_url_scoped_to_github():
    """The helper is registered for github.com only, never hub-wide.

    An unscoped ``credential.helper`` registration makes git consult
    the GitHub helper first for EVERY https remote — config-level
    scoping is the primary guard, the helper's own host check is the
    defense in depth.
    """
    with open(_S_ENTRYPOINT, "r") as fileHandle:
        sSource = fileHandle.read()
    assert "credential.https://github.com.helper" in sSource
    listUnscoped = [
        sLine for sLine in sSource.splitlines()
        if "credential.helper" in sLine
        and "credential.https://" not in sLine
        and not sLine.lstrip().startswith("#")
        and "store" not in sLine
    ]
    assert listUnscoped == []


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
