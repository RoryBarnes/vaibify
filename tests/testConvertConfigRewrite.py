"""The config-rewrite helper behind host->container conversion.

Conversion rewrites an existing host ``vaibify.yml`` in place rather
than scaffolding a new one, so the helper must overlay the container
fields the wizard collected WITHOUT discarding the settings a host
project already carried. The keys are kept distinct on purpose (the
host name is not the new container name), so a helper that echoed the
wrong field could not pass by coincidence.
"""

import os

from vaibify.config.projectConfig import (
    fbValidateConfig,
    fconfigFromYamlDict,
    fnSaveToFile,
)
from vaibify.gui.registryRoutes import (
    CreateProjectRequest,
    _fdictOverlayContainerFieldsOntoHostConfig,
)


def _fsWriteHostConfig(tmp_path, sBody):
    """Write a host vaibify.yml and return its path."""
    sConfigPath = os.path.join(str(tmp_path), "vaibify.yml")
    with open(sConfigPath, "w") as fileHandle:
        fileHandle.write(sBody)
    return sConfigPath


def _frequestContainerFields(sProjectName):
    """A create-shaped request carrying only container fields."""
    return CreateProjectRequest(
        sMode="container",
        sDirectory="/unused/by/the/overlay",
        sProjectName=sProjectName,
        sTemplateName="",
        sPythonVersion="3.11",
        listPythonPackages=["numpy"],
    )


def testOverlaySetsTheDockerSafeProjectNameFromTheRequest(tmp_path):
    """The new container name replaces the host basename in projectName."""
    sConfigPath = _fsWriteHostConfig(
        tmp_path, "projectName: ai greenhouse\n",
    )
    dictMerged = _fdictOverlayContainerFieldsOntoHostConfig(
        sConfigPath, _frequestContainerFields("aiGreenhouse"),
    )
    assert dictMerged["projectName"] == "aiGreenhouse"
    assert dictMerged["pythonVersion"] == "3.11"


def testOverlaidConfigPassesValidation(tmp_path):
    """The rewritten config validates, so a later load will not strand it."""
    sConfigPath = _fsWriteHostConfig(
        tmp_path, "projectName: ai greenhouse\n",
    )
    dictMerged = _fdictOverlayContainerFieldsOntoHostConfig(
        sConfigPath, _frequestContainerFields("aiGreenhouse"),
    )
    assert fbValidateConfig(dictMerged)


def testOverlayPreservesUnmanagedHostFields(tmp_path):
    """Fields the container translation does not manage must survive.

    ``reproducibility``, ``bindMounts``, ``ports`` and ``binaries`` are
    absent from the overlay, so a helper that rebuilt from scratch would
    silently drop the researcher's existing settings.
    """
    sBody = (
        "projectName: ai greenhouse\n"
        "reproducibility:\n"
        "  zenodoService: production\n"
        "ports:\n"
        "  - {container: 8888, host: 8888}\n"
        "binaries:\n"
        "  - {name: solver, path: /usr/local/bin/solver}\n"
    )
    sConfigPath = _fsWriteHostConfig(tmp_path, sBody)
    dictMerged = _fdictOverlayContainerFieldsOntoHostConfig(
        sConfigPath, _frequestContainerFields("aiGreenhouse"),
    )
    assert dictMerged["reproducibility"]["zenodoService"] == "production"
    assert dictMerged["ports"] == [{"container": 8888, "host": 8888}]
    assert dictMerged["binaries"] == [
        {"name": "solver", "path": "/usr/local/bin/solver"},
    ]


def testOverlaidConfigRoundTripsThroughTheDataclass(tmp_path):
    """The merged dict survives fconfigFromYamlDict + fnSaveToFile + reload.

    This is the exact round-trip the convert route performs, so a field
    the dataclass cannot represent would surface here rather than in
    production.
    """
    sBody = (
        "projectName: ai greenhouse\n"
        "reproducibility:\n"
        "  zenodoService: production\n"
    )
    sConfigPath = _fsWriteHostConfig(tmp_path, sBody)
    dictMerged = _fdictOverlayContainerFieldsOntoHostConfig(
        sConfigPath, _frequestContainerFields("aiGreenhouse"),
    )
    configProject = fconfigFromYamlDict(dictMerged)
    fnSaveToFile(configProject, sConfigPath)
    from vaibify.config.projectConfig import fconfigLoadFromFile
    configReloaded = fconfigLoadFromFile(sConfigPath)
    assert configReloaded.sProjectName == "aiGreenhouse"
    assert configReloaded.reproducibility.sZenodoService == "production"
