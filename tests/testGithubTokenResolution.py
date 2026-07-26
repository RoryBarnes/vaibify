"""Regression tests for host-side GitHub credential resolution.

Every test here drives the REAL ``secretManager`` validation and the
REAL ``githubAuth.fsResolveToken``; only the credential *sources* (the
``gh`` subprocess and the OS keyring) are stubbed. That distinction is
the point of the file. The defects it guards shipped under a fully
green suite precisely because the existing tests patched
``fsResolveToken`` wholesale or passed a non-empty secret name that no
production caller ever passes:

* the ``gh auth token`` fallback passed an EMPTY secret name, which
  ``fsRetrieveSecret`` rejects before it ever dispatches on the
  method, so the fallback was dead code and every dashboard push was
  refused "No GitHub token available" on a host whose ``gh`` login
  worked perfectly;
* the same dead line was embedded in the generated askpass helper, so
  host-side git auth was silently anonymous;
* the push route resolved its token through an unguarded
  ``fsKeyringSlotFor``/``fsResolveToken`` pair, so anything either
  raised escaped as a bare 500;
* the connectivity pre-flight probed the CONTAINER while the push runs
  on the HOST, so the dashboard reported "Connected" right before the
  push was refused.
"""

import ast
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from vaibify.config import secretManager
from vaibify.gui import syncDispatcher
from vaibify.reproducibility import githubAuth


_S_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _fpatchCredentialSources(sGhToken, bKeyringHasSlot=False):
    """Stub only the gh subprocess and the keyring lookup."""
    return patch.multiple(
        "vaibify.config.secretManager",
        _fbKeyringHasSecret=lambda sName: bKeyringHasSlot,
        _fsRetrieveViaGhAuth=lambda: sGhToken,
    )


# ---------------------------------------------------------------------
# 1.1 — the gh auth token fallback must actually be reachable
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_resolve_token_reaches_gh_auth_fallback_with_real_validation():
    """A per-repo slot with no keyring entry falls back to ``gh``.

    Drives the real ``fsRetrieveSecret`` — including the secret-name
    validation that used to reject the fallback's empty name — so the
    assertion cannot pass while the fallback is unreachable.

    Kills: githubAuth ``_S_GH_AUTH_SLOT_NAME = "gh_token"`` reverted to
    the empty name ``""``, which ``_fnValidateSecretName`` rejects.
    """
    with _fpatchCredentialSources("gho_hostToken"):
        sToken = githubAuth.fsResolveToken(
            githubAuth.fsKeyringSlotFor("exampleOwner", "exampleRepo"),
        )
    assert sToken == "gho_hostToken"


def test_resolve_token_reaches_gh_auth_fallback_with_no_slot():
    """An empty slot (no remote parsed) still reaches ``gh auth token``."""
    with _fpatchCredentialSources("gho_hostToken"):
        assert githubAuth.fsResolveToken("") == "gho_hostToken"


def test_resolve_token_prefers_the_keyring_slot_over_gh_auth():
    """A populated per-repo slot wins; the fallback is not consulted."""
    with patch.multiple(
        "vaibify.config.secretManager",
        _fbKeyringHasSecret=lambda sName: True,
        _fsRetrieveViaKeyring=lambda sName: "ghp_perRepoToken",
        _fsRetrieveViaGhAuth=lambda: "gho_hostToken",
    ):
        sToken = githubAuth.fsResolveToken(
            githubAuth.fsKeyringSlotFor("exampleOwner", "exampleRepo"),
        )
    assert sToken == "ghp_perRepoToken"


def test_resolve_token_never_raises_on_an_unusable_slot():
    """The docstring promises "never raises"; hold it to that.

    ``fbSecretExists`` validates the slot name and raises on a shape it
    dislikes, so the keyring probe has to sit inside the guarded block
    too. A slot name outside the alphabet must degrade to the fallback
    rather than escaping to the caller.
    """
    with _fpatchCredentialSources("gho_hostToken"):
        assert githubAuth.fsResolveToken("bad name;rm -rf") == "gho_hostToken"


@pytest.mark.falsification
def test_askpass_helper_passes_a_valid_secret_name_to_gh_auth():
    """The generated askpass helper's fallback must survive validation.

    The helper is source text executed in a separate interpreter, so a
    dead fallback inside it is invisible to every import-time check.
    This extracts the literal it will pass and runs it through the real
    validator.

    Kills: ``sGhAuthNameRepr=repr(_S_GH_AUTH_SLOT_NAME)`` in
    ``_fsBuildAskpassSource`` reverted to the empty name ``repr("")``.
    """
    sSource = githubAuth._fsBuildAskpassSource("github_token:owner/repo")
    matchCall = re.search(
        r"fsRetrieveSecret\((.+?), 'gh_auth'\)", sSource,
    )
    assert matchCall is not None, sSource
    secretManager._fnValidateSecretName(
        ast.literal_eval(matchCall.group(1)),
    )


def test_askpass_helper_is_syntactically_valid_python():
    """A template edit that breaks the helper must fail loudly here."""
    ast.parse(
        githubAuth._fsBuildAskpassSource("github_token:owner/repo")
        .split("\n", 1)[1],
    )


# ---------------------------------------------------------------------
# 1.3 — the push route resolves through the hardened resolver
# ---------------------------------------------------------------------


def test_push_route_does_not_resolve_tokens_unguarded():
    """``syncRoutes`` must not call the raw slot/resolve pair.

    ``githubMirror._fsResolveTokenSafely`` wraps exactly this call pair
    and degrades to an empty token with a WARNING. A second, unguarded
    copy in the route is how a keyring failure became a bare 500.
    """
    sSource = (
        _S_REPOSITORY_ROOT / "vaibify/gui/routes/syncRoutes.py"
    ).read_text(encoding="utf-8")
    listCode = [
        sLine for sLine in sSource.split("\n")
        if not sLine.lstrip().startswith("#")
    ]
    sCode = "\n".join(listCode)
    assert "_fsResolveTokenSafely" in sCode
    assert "fsKeyringSlotFor" not in sCode
    assert "fsResolveToken(" not in sCode


# ---------------------------------------------------------------------
# 1.4 — the pre-flight check must probe the host, not only the container
# ---------------------------------------------------------------------


def _fmockDocker(iExitCode, sOutput):
    """Return a Docker double whose every exec yields the same result."""
    class _MockConnection:
        def ftResultExecuteCommand(self, sContainerId, sCommand):
            return (iExitCode, sOutput)
    return _MockConnection()


@pytest.mark.falsification
def test_github_check_is_not_connected_without_a_host_credential():
    """A reachable container plus no host token is NOT "Connected".

    The push runs on the host. Reporting Connected on the strength of
    a container-side ``git ls-remote`` is the dashboard asserting
    something it has not checked.

    Kills: ``"bConnected": bContainerReaches and bHostCredential``
    reduced to ``"bConnected": bContainerReaches``.
    """
    with patch.object(
        syncDispatcher, "_fbHostGithubCredentialAvailable",
        return_value=False,
    ):
        dictResult = syncDispatcher._fdictCheckGithub(
            _fmockDocker(0, "https://github.com/exampleOwner/exampleRepo"),
            "cid",
        )
    assert dictResult["bConnected"] is False
    assert dictResult["bContainerReachesGithub"] is True
    assert dictResult["bHostCredentialAvailable"] is False
    assert "host" in dictResult["sMessage"].lower()


def test_github_check_reports_both_lanes_when_connected():
    """A healthy check names both lanes so the UI can stay specific."""
    with patch.object(
        syncDispatcher, "_fbHostGithubCredentialAvailable",
        return_value=True,
    ):
        dictResult = syncDispatcher._fdictCheckGithub(
            _fmockDocker(0, "https://github.com/exampleOwner/exampleRepo"),
            "cid",
        )
    assert dictResult["bConnected"] is True
    assert dictResult["sMessage"] == "Connected"


def test_github_check_credential_probe_uses_the_push_resolution_path():
    """The host lane resolves the same per-repo slot the push resolves."""
    listSlots = []

    def _fsRecordSlot(sSlot):
        listSlots.append(sSlot)
        return "gho_hostToken"

    with patch(
        "vaibify.reproducibility.githubAuth.fsResolveToken",
        side_effect=_fsRecordSlot,
    ):
        bAvailable = syncDispatcher._fbHostGithubCredentialAvailable(
            "https://github.com/exampleOwner/exampleRepo.git",
        )
    assert bAvailable is True
    assert listSlots == ["github_token:exampleOwner/exampleRepo"]


def test_github_check_host_lane_survives_an_unparseable_remote():
    """An unknown remote shape still exercises the ``gh`` fallback."""
    with _fpatchCredentialSources("gho_hostToken"):
        assert syncDispatcher._fbHostGithubCredentialAvailable(
            "ftp://example.org/foo/bar",
        ) is True


def test_github_check_host_lane_is_false_when_no_credential_exists():
    """No keyring entry and no gh token means the push will be refused."""
    with _fpatchCredentialSources(""):
        assert syncDispatcher._fbHostGithubCredentialAvailable(
            "https://github.com/exampleOwner/exampleRepo.git",
        ) is False


def test_read_github_remote_url_returns_empty_on_a_failed_probe():
    """A container with no git repository yields no remote URL."""
    assert syncDispatcher._fsReadGithubRemoteUrl(
        _fmockDocker(1, "fatal: not a git repository"), "cid",
    ) == ""


# ---------------------------------------------------------------------
# The secret-name alphabet must agree with the keyring-slot alphabet.
# ``githubAuth._PATTERN_SEGMENT`` has always allowed dots in owner and
# repository names; ``secretManager._RE_SECRET_NAME`` did not, and
# capped names at 64 characters. A dotted repository therefore raised
# ValueError out of the push route as a bare HTTP 500, and a long
# owner/repo pair was rejected outright.
# ---------------------------------------------------------------------


@pytest.mark.falsification
def test_dotted_repository_slot_passes_real_secret_name_validation():
    """A dotted repo must validate; it used to 500 the push route.

    Kills: in secretManager, drop "." from the _RE_SECRET_NAME
    character class (restoring ^[a-zA-Z0-9_:/-]).
    """
    sSlot = githubAuth.fsKeyringSlotFor("exampleOwner", "example.repo")
    assert sSlot == "github_token:exampleOwner/example.repo"
    secretManager._fnValidateSecretName(sSlot)


@pytest.mark.falsification
def test_widest_real_keyring_slot_fits_the_length_cap():
    """A 39-char owner and 100-char repo must fit the cap.

    Kills: in secretManager, set _I_MAXIMUM_SECRET_NAME_LENGTH to 64.
    """
    sSlot = githubAuth.fsKeyringSlotFor("o" * 39, "r" * 100)
    assert len(sSlot) == 153
    secretManager._fnValidateSecretName(sSlot)


@pytest.mark.falsification
@pytest.mark.parametrize("sName", [
    "github_token:owner/../etc/passwd",
    "github_token:owner/./passwd",
    "github_token:owner//passwd",
])
def test_widened_alphabet_still_refuses_path_traversal(sName):
    """Admitting "." must not admit "." or ".." as a path SEGMENT.

    sName reaches /run/secrets/{sName} in _fsRetrieveViaDockerSecret,
    so a segment that escapes the directory must still be refused.

    Kills: in secretManager._fnValidateSecretName, drop the
    '"." in listParts or' conjunct from the path-segment guard.
    """
    with pytest.raises(ValueError, match="Invalid secret name"):
        secretManager._fnValidateSecretName(sName)
