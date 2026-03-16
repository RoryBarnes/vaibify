"""Project template copier for Vaibify."""

import shutil
from pathlib import Path

from vaibify.config.containerConfig import (
    flistParseContainerConf,
)


_PATH_TEMPLATES = Path(__file__).resolve().parents[2] / "templates"


def flistAvailableTemplates():
    """Return a sorted list of available template names.

    Scans the templates directory for subdirectories that contain
    at least a container.conf file.

    Returns
    -------
    list of str
        Template names (directory basenames).
    """
    if not _PATH_TEMPLATES.is_dir():
        raise FileNotFoundError(
            f"Templates directory not found: '{_PATH_TEMPLATES}'"
        )
    return _flistScanTemplateDirectories()


def _flistScanTemplateDirectories():
    """Return sorted names of subdirectories in the templates dir."""
    listNames = []
    for pathEntry in sorted(_PATH_TEMPLATES.iterdir()):
        if pathEntry.is_dir():
            listNames.append(pathEntry.name)
    return listNames


def fnCopyTemplate(sTemplateName, sDestination):
    """Copy all files from a template into the destination directory.

    Parameters
    ----------
    sTemplateName : str
        Name of the template (must exist in templates directory).
    sDestination : str
        Path to the destination directory.
    """
    pathSource = _fpathResolveTemplate(sTemplateName)
    pathDestination = Path(sDestination)
    pathDestination.mkdir(parents=True, exist_ok=True)
    _fnCopyDirectoryContents(pathSource, pathDestination)


def _fpathResolveTemplate(sTemplateName):
    """Resolve and validate the path to a named template."""
    pathTemplate = _PATH_TEMPLATES / sTemplateName
    if not pathTemplate.is_dir():
        raise FileNotFoundError(
            f"Template '{sTemplateName}' not found in "
            f"'{_PATH_TEMPLATES}'."
        )
    return pathTemplate


def _fnCopyDirectoryContents(pathSource, pathDestination):
    """Copy all items from source to destination directory."""
    for pathItem in pathSource.iterdir():
        sDestItem = str(pathDestination / pathItem.name)
        if pathItem.is_dir():
            shutil.copytree(str(pathItem), sDestItem)
        else:
            shutil.copy2(str(pathItem), sDestItem)


def fdictLoadTemplateConfig(sTemplateName):
    """Load a template's container.conf as a dictionary.

    Parameters
    ----------
    sTemplateName : str
        Name of the template.

    Returns
    -------
    dict
        Dictionary with key "listRepositories" containing
        the parsed repo entries from container.conf.
    """
    pathTemplate = _fpathResolveTemplate(sTemplateName)
    pathConf = pathTemplate / "container.conf"
    _fnVerifyContainerConfExists(pathConf, sTemplateName)
    listRepos = flistParseContainerConf(str(pathConf))
    return {"listRepositories": listRepos}


def _fnVerifyContainerConfExists(pathConf, sTemplateName):
    """Raise FileNotFoundError if container.conf is missing."""
    if not pathConf.exists():
        raise FileNotFoundError(
            f"Template '{sTemplateName}' has no container.conf "
            f"at '{pathConf}'."
        )
