"""The bind-mount deny list compares filesystem objects, not spellings.

Source: ``vaibify/config/bindMountValidator.py``.

The list was a case-sensitive string compare. On a case-insensitive
volume (APFS by default) ``~/.SSH`` is ``~/.ssh`` and was accepted; on
macOS ``/etc`` is a link to ``/private/etc``, so a mount resolved to
``/private/etc/...`` never sat under the denied string; the live ephemeral
secret store ``~/.vaibify/tmp`` was not denied at all; and a ``~`` source
passed validation (which expanded it) and reached Docker unexpanded.
"""

import os

import pytest

from vaibify.config import bindMountValidator
from vaibify.config.bindMountValidator import (
    BindMountValidationError,
    fnValidateBindMount,
)


@pytest.fixture()
def sHome(tmp_path, monkeypatch):
    sHomeDirectory = os.path.realpath(str(tmp_path / "home"))
    os.makedirs(sHomeDirectory)
    monkeypatch.setenv("HOME", sHomeDirectory)
    return sHomeDirectory


def _fnMount(sHostPath):
    fnValidateBindMount({"host": sHostPath, "container": "/mnt"})


def _fbFilesystemIsCaseInsensitive(sDirectory):
    sSwapped = sDirectory.swapcase()
    return sSwapped != sDirectory and os.path.exists(sSwapped)


class _CaseFoldingOs:
    """The ``os`` module as seen from a case-insensitive volume.

    ``stat`` answers for the lower-cased spelling, which is exactly what
    an APFS volume does for a differently-cased name. Everything else
    is the real module, so the validator's walk is the real walk.
    """

    def __init__(self, sRoot):
        self._sRoot = sRoot

    def __getattr__(self, sName):
        return getattr(os, sName)

    def stat(self, sPath, *tArguments, **dictKeywords):
        sFolded = sPath
        if sPath.startswith(self._sRoot):
            sFolded = self._sRoot + sPath[len(self._sRoot):].lower()
        return os.stat(sFolded, *tArguments, **dictKeywords)


@pytest.mark.falsification
def testADifferentlyCasedSpellingOfAProtectedDirectoryIsRefused(
    sHome, monkeypatch,
):
    """Kills: dropping the on-disk identity comparison from the deny list."""
    os.makedirs(os.path.join(sHome, ".ssh", "keys"))
    monkeypatch.setattr(
        bindMountValidator, "os", _CaseFoldingOs(sHome))
    with pytest.raises(BindMountValidationError, match="denied location"):
        _fnMount(os.path.join(sHome, ".SSH"))
    with pytest.raises(BindMountValidationError, match="denied location"):
        _fnMount(os.path.join(sHome, ".Ssh", "keys"))


def testOnARealCaseInsensitiveVolumeTheUpperCaseSpellingIsRefused(sHome):
    if not _fbFilesystemIsCaseInsensitive(sHome):
        pytest.skip("this volume is case-sensitive")
    os.makedirs(os.path.join(sHome, ".aws"))
    with pytest.raises(BindMountValidationError, match="denied location"):
        _fnMount(os.path.join(sHome, ".AWS"))


def testAMountResolvedThroughALinkedDeniedLocationIsRefused(
    sHome, tmp_path, monkeypatch,
):
    """The macOS ``/etc`` shape: the denied name is a link to the real place.

    The mount arrives already resolved (``/private/etc/x``), so it is
    not under the denied string ``/etc``; only identity relates them.
    """
    sReal = os.path.realpath(str(tmp_path / "private" / "etc"))
    os.makedirs(sReal)
    sLink = str(tmp_path / "etc")
    os.symlink(sReal, sLink)
    monkeypatch.setattr(
        bindMountValidator, "_LIST_DENY_PREFIXES", (sLink,))
    with pytest.raises(BindMountValidationError, match="denied location"):
        _fnMount(os.path.join(sReal, "passwd.d"))


@pytest.mark.falsification
def testTheEphemeralSecretStoreIsDeniedInEveryDirection(sHome):
    """Kills: removing ``.vaibify/tmp`` from the home-relative deny list."""
    sSecrets = os.path.join(sHome, ".vaibify", "tmp")
    os.makedirs(os.path.join(sSecrets, "one"))
    for sMount in (sSecrets, os.path.join(sSecrets, "one"),
                   os.path.dirname(sSecrets)):
        with pytest.raises(BindMountValidationError):
            _fnMount(sMount)


@pytest.mark.falsification
def testASourceDockerWouldNotReadAsValidatedIsRefused(sHome):
    """``~`` and relative sources reach Docker verbatim; refuse them.

    Kills: removing the absolute-path requirement, which restores the
    approve-one-place, mount-another gap.
    """
    os.makedirs(os.path.join(sHome, "data"), exist_ok=True)
    for sSource in ("~/data", "~", "data/relative"):
        with pytest.raises(BindMountValidationError, match="absolute path"):
            _fnMount(sSource)


def testTheTildeRefusalTellsTheResearcherWhatToWrite(sHome):
    with pytest.raises(BindMountValidationError, match="full path"):
        _fnMount("~/data")


def testAnOrdinaryAbsoluteDataDirectoryStillPasses(sHome):
    sData = os.path.join(sHome, "data")
    os.makedirs(sData)
    _fnMount(sData)
