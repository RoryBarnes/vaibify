"""A conversion writes both network spellings from one helper, so they always agree.

Source: ``vaibify/config/projectConfig.py`` (``fdictNetworkKeysForMode``),
``vaibify/gui/registryRoutes.py`` (the build conversion) and
``vaibify/gui/pinnedEnvironmentConversion.py`` (the pinned-image one).

A host project's ``vaibify.yml`` may carry the old spelling,
``networkIsolation: false``. The build conversion overlaid only
``networkMode``, so converting with "isolated" left the two keys
contradicting each other and validation refused the conversion with a
400; the pinned-image conversion dropped ``networkIsolation`` instead.
The expected pairs come from ruling D1, not from the code under test:
isolated is the only mode ``networkIsolation: true`` names.
"""

import os

import pytest

from vaibify.config.projectConfig import fconfigLoadFromFile, fbValidateConfig
from vaibify.gui import pinnedEnvironmentConversion
from vaibify.gui.registryRoutes import (
    CreateProjectRequest,
    _fdictOverlayContainerFieldsOntoHostConfig,
    _fnRewriteConfigForConversion,
)


S_HOST_CONFIG = "projectName: hostProject\nnetworkIsolation: false\n"
LIST_MODE_CASES = [
    ("", "web", False),
    ("isolated", "isolated", True),
    ("open", "open", False),
]


def _fsWriteHostConfig(tmp_path):
    sConfigPath = os.path.join(str(tmp_path), "vaibify.yml")
    with open(sConfigPath, "w", encoding="utf-8") as fileConfig:
        fileConfig.write(S_HOST_CONFIG)
    return sConfigPath


def _frequestConversion(sNetworkMode):
    """The wizard's body: an empty mode is what its default sends."""
    return CreateProjectRequest(
        sMode="container", sDirectory="/unused/by/the/overlay",
        sProjectName="convertedProject", sTemplateName="",
        sNetworkMode=sNetworkMode,
    )


@pytest.mark.falsification
@pytest.mark.parametrize("sRequested, sMode, bIsolation", LIST_MODE_CASES)
def testTheBuildConversionWritesAgreeingNetworkKeys(
    tmp_path, sRequested, sMode, bIsolation,
):
    """Kills: the conversion overlaying only networkMode onto the host file."""
    sConfigPath = _fsWriteHostConfig(tmp_path)
    dictMerged = _fdictOverlayContainerFieldsOntoHostConfig(
        sConfigPath, _frequestConversion(sRequested))
    assert (dictMerged["networkMode"], dictMerged["networkIsolation"]) == (
        sMode, bIsolation)
    assert fbValidateConfig(dictMerged)
    _fnRewriteConfigForConversion(sConfigPath, _frequestConversion(sRequested))
    configWritten = fconfigLoadFromFile(sConfigPath)
    assert (configWritten.sNetworkMode, configWritten.bNetworkIsolation) == (
        sMode, bIsolation)


@pytest.mark.falsification
@pytest.mark.parametrize("sRequested, sMode, bIsolation", LIST_MODE_CASES)
def testThePinnedImageConversionWritesAgreeingNetworkKeys(
    tmp_path, sRequested, sMode, bIsolation,
):
    """Kills: the runtime overlay dropping networkIsolation from the file."""
    sConfigPath = _fsWriteHostConfig(tmp_path)
    dictMerged = pinnedEnvironmentConversion.fdictOverlayRuntimeFieldsOnly(
        sConfigPath, _frequestConversion(sRequested))
    assert (dictMerged["networkMode"], dictMerged["networkIsolation"]) == (
        sMode, bIsolation)
    assert fbValidateConfig(dictMerged)
    pinnedEnvironmentConversion.fnRewriteConfigForObtainedImage(
        sConfigPath, _frequestConversion(sRequested))
    configWritten = fconfigLoadFromFile(sConfigPath)
    assert (configWritten.sNetworkMode, configWritten.bNetworkIsolation) == (
        sMode, bIsolation)
