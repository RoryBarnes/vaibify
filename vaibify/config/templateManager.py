"""Project template copier for Vaibify."""

import shutil
from pathlib import Path

from vaibify.config.containerConfig import (
    flistParseContainerConf,
)
from vaibify.resources import (
    S_TEMPLATES_TREE,
    fnRequirePackagedTree,
    fpathPackagedTree,
)


_PATH_TEMPLATES = fpathPackagedTree(S_TEMPLATES_TREE)


def flistAvailableTemplates():
    """Return a sorted list of available template names.

    Scans the templates directory for subdirectories that contain
    at least a container.conf file.

    Returns
    -------
    list of str
        Template names (directory basenames).
    """
    fnRequirePackagedTree(_PATH_TEMPLATES, S_TEMPLATES_TREE)
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

    Refuses before the first write when the destination already carries
    a Project file, and finishes by relocating the template's root
    ``project.json`` into ``.vaibify/projects/`` — the directory
    discovery treats as canonical. ``vaibify init`` gained that
    relocation first and this GUI-serving copier was missed, so every
    dashboard-created project was born in the legacy root layout
    (2026-08-20; the same fix landing in one of two places is the
    divergence this now shares one implementation to prevent).

    Parameters
    ----------
    sTemplateName : str
        Name of the template (must exist in templates directory).
    sDestination : str
        Path to the destination directory.
    """
    pathSource = _fpathResolveTemplate(sTemplateName)
    pathDestination = Path(sDestination)
    _fnRefuseIfProjectFileExists(pathDestination)
    pathDestination.mkdir(parents=True, exist_ok=True)
    _fnCopyDirectoryContents(pathSource, pathDestination)
    fnMoveProjectFileWhereDiscoveryLooks(str(pathDestination))


def _fpathCanonicalProjectFile(pathDestination):
    """Return the canonical Project-file path for a destination."""
    from vaibify.gui.workflowManager import VAIBIFY_PROJECTS_DIR
    return pathDestination / VAIBIFY_PROJECTS_DIR / "project.json"


def _fnRefuseIfProjectFileExists(pathDestination):
    """Refuse before any write when a Project file is already there.

    Scaffolding over an existing ``.vaibify/projects/project.json``
    would replace a Project somebody may have built steps into, and a
    refusal AFTER the copy would leave template debris behind in a
    directory the caller was just told was refused.
    """
    pathTarget = _fpathCanonicalProjectFile(pathDestination)
    if not pathTarget.exists():
        return
    raise FileExistsError(
        f"{pathTarget} already exists; scaffolding over it would "
        "replace an existing Project. Move or delete it first."
    )


def fnMoveProjectFileWhereDiscoveryLooks(sDestination):
    """Relocate a scaffolded project.json into the discovered directory.

    Templates keep their Project file at the tree root, where it is
    the first thing a reader opens; ``.vaibify/projects/`` is where
    discovery, the reproduce sweeps, and the sync globs treat it as
    canonical (a root-level file is admitted only through the legacy
    fallback). The single implementation serves both the CLI scaffold
    and the GUI create.
    """
    pathDestination = Path(sDestination)
    pathSourceFile = pathDestination / "project.json"
    if not pathSourceFile.is_file():
        return
    pathTarget = _fpathCanonicalProjectFile(pathDestination)
    pathTarget.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pathSourceFile), str(pathTarget))


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
    """Copy all items from source to destination directory.

    ``__pycache__`` is skipped: pip byte-compiles the shipped template
    scripts at install time, so copying the tree verbatim seeds every
    new project with stale ``.pyc`` files compiled against
    site-packages paths.
    """
    for pathItem in pathSource.iterdir():
        if pathItem.name == "__pycache__":
            continue
        sDestItem = str(pathDestination / pathItem.name)
        if pathItem.is_dir():
            shutil.copytree(
                str(pathItem), sDestItem,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
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
