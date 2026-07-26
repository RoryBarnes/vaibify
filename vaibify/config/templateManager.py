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
    """Resolve a named template, refusing any name that escapes the root.

    The name arrives from the project-create request body, which the
    caller jails only on ``sDirectory``. Joining it onto the templates
    root unchecked let ``../../..``-style names select an arbitrary
    host directory, which was then copied into a new project and
    mounted into a container. Resolving both sides and requiring strict
    containment closes that; symlinked roots resolve identically on
    both sides so a legitimate install still works.
    """
    pathRoot = _PATH_TEMPLATES.resolve()
    pathTemplate = (pathRoot / sTemplateName).resolve()
    if pathRoot not in pathTemplate.parents:
        raise FileNotFoundError(
            f"Template '{sTemplateName}' is not a name inside "
            f"'{_PATH_TEMPLATES}'."
        )
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
