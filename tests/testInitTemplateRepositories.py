"""`vaibify init --template` must not drop a template's repositories silently.

A build clones only the repositories named in vaibify.yml, and init
writes a vaibify.yml with none. A custom template that lists
repositories in its container.conf therefore loses them; init has to
say so and name where they belong.
"""

import os

import pytest
from click.testing import CliRunner

from vaibify.cli import commandInit
from vaibify.config import registryManager, templateManager


@pytest.fixture(autouse=True)
def fixtureIsolateRegistry(tmp_path, monkeypatch):
    sDirectory = str(tmp_path / "registryHome")
    os.makedirs(sDirectory, exist_ok=True)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_DIRECTORY", sDirectory)
    monkeypatch.setattr(
        registryManager, "_S_REGISTRY_PATH",
        os.path.join(sDirectory, "registry.json"),
    )
    monkeypatch.setattr(
        registryManager, "_S_LOCK_PATH",
        os.path.join(sDirectory, "registry.lock"),
    )


def _fpathMakeTemplateRoot(tmp_path, monkeypatch, sContainerConf):
    """Install one custom template and point both resolvers at it."""
    pathRoot = tmp_path / "templates"
    pathTemplate = pathRoot / "custom"
    pathTemplate.mkdir(parents=True)
    (pathTemplate / "container.conf").write_text(sContainerConf)
    monkeypatch.setattr(
        commandInit, "fsTemplatePath",
        lambda sName: str(pathRoot / sName),
    )
    monkeypatch.setattr(templateManager, "_PATH_TEMPLATES", pathRoot)
    return pathRoot


def _fsRunInitInNewDirectory(tmp_path):
    """Run `vaibify init --template custom` in a fresh project folder."""
    pathProject = tmp_path / "project"
    pathProject.mkdir()
    sOriginal = os.getcwd()
    os.chdir(pathProject)
    try:
        resultInvoke = CliRunner().invoke(
            commandInit.fnInitCommand,
            ["--template", "custom", "--name", "customProject"],
        )
    finally:
        os.chdir(sOriginal)
    return resultInvoke.output


def testInitNamesTheRepositoriesATemplateWouldLose(tmp_path, monkeypatch):
    _fpathMakeTemplateRoot(
        tmp_path, monkeypatch,
        "analysisTools|https://example.org/analysisTools.git|main|pip_editable\n",
    )
    sOutput = _fsRunInitInNewDirectory(tmp_path)
    assert "analysisTools" in sOutput
    assert "vaibify.yml" in sOutput
    assert "Warning" in sOutput


def testInitIsQuietWhenTheTemplateListsNoRepositories(tmp_path, monkeypatch):
    _fpathMakeTemplateRoot(tmp_path, monkeypatch, "# no repositories\n")
    sOutput = _fsRunInitInNewDirectory(tmp_path)
    assert "Warning" not in sOutput
