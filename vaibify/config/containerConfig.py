"""Pipe-delimited container.conf I/O for backwards compatibility."""

import os
import re
import tempfile
from pathlib import Path


# Where the build bakes the list into the image, and where the
# entrypoint reads it on every start.
S_IMAGE_REPOSITORY_LIST_PATH = "/etc/vaibify/container.conf"

_S_REPOSITORY_LISTS_DIRECTORY = os.path.join(
    os.path.expanduser("~"), ".vaibify", "repositoryLists",
)
_REGEX_PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

_CONTAINER_CONF_HEADER = (
    "# Vaibify Repository Configuration\n"
    "# Format: name|url|branch|install_method[|destination]\n"
    "#\n"
    "# Install methods:\n"
    "#   c_and_pip    - make opt then pip install -e . --no-deps\n"
    "#   pip_no_deps  - pip install -e . --no-deps "
    "(deps in Dockerfile)\n"
    "#   pip_editable - pip install -e . (standalone package)\n"
    "#   scripts_only - add to PYTHONPATH and PATH only\n"
    "#   reference    - clone for reference, do not install\n"
    "#\n"
    "# destination (optional): move the repo to this path\n"
    "#   after cloning (e.g. .claude to create a dot-directory)\n"
    "#\n"
    "# Order matters: repos are cloned and installed "
    "in this order.\n"
    "# Lines starting with # are comments.\n"
)

_MIN_FIELDS = 4
_MAX_FIELDS = 5


def flistParseContainerConf(sFilePath):
    """Read a pipe-delimited container.conf and return a list of repo dicts.

    Parameters
    ----------
    sFilePath : str
        Path to the container.conf file.

    Returns
    -------
    list of dict
        Each dict has keys: sName, sUrl, sBranch, sInstallMethod.
    """
    pathFile = Path(sFilePath)
    if not pathFile.exists():
        raise FileNotFoundError(
            f"Container config not found: '{sFilePath}'"
        )
    return flistParseContainerConfText(
        pathFile.read_text(encoding="utf-8"), sFilePath
    )


def flistParseContainerConfText(sText, sSourceName="container.conf"):
    """Parse container.conf text into a list of repo dicts.

    The file-path reader above and the tracked-repos manager, which
    fetches the same document from inside a running container, share
    this one implementation of the pipe format.

    Parameters
    ----------
    sText : str
        The full container.conf document.
    sSourceName : str
        Where the text came from, named in parse errors.

    Returns
    -------
    list of dict
        Each dict has keys: sName, sUrl, sBranch, sInstallMethod,
        sDestination.
    """
    listLines = _flistSelectNonCommentLines(sText)
    return _flistParsePipeLines(listLines, sSourceName)


def _flistSelectNonCommentLines(sText):
    """Return non-blank, non-comment lines of a conf document."""
    listResult = []
    for sLine in sText.splitlines():
        sStripped = sLine.strip()
        if sStripped and not sStripped.startswith("#"):
            listResult.append(sStripped)
    return listResult


def _flistParsePipeLines(listLines, sFilePath):
    """Parse pipe-delimited lines into repo dictionaries."""
    listRepos = []
    for iLineIndex, sLine in enumerate(listLines):
        listParts = sLine.split("|")
        if len(listParts) < _MIN_FIELDS or len(listParts) > _MAX_FIELDS:
            raise ValueError(
                f"Expected {_MIN_FIELDS}-{_MAX_FIELDS} pipe-delimited "
                f"fields in '{sFilePath}', line {iLineIndex + 1}: "
                f"'{sLine}'"
            )
        listRepos.append(_fdictBuildRepoEntry(listParts))
    return listRepos


def _fdictBuildRepoEntry(listParts):
    """Build a repo dict from a list of pipe-split fields."""
    sDestination = ""
    if len(listParts) >= 5:
        sDestination = listParts[4].strip()
    return {
        "sName": listParts[0].strip(),
        "sUrl": listParts[1].strip(),
        "sBranch": listParts[2].strip(),
        "sInstallMethod": listParts[3].strip(),
        "sDestination": sDestination,
    }


def fnWriteContainerConf(listRepos, sFilePath):
    """Write a list of repo dicts to a pipe-delimited container.conf file.

    Parameters
    ----------
    listRepos : list of dict
        Each dict must have keys: sName, sUrl, sBranch, sInstallMethod.
    sFilePath : str
        Destination file path.
    """
    pathOutput = Path(sFilePath)
    pathOutput.parent.mkdir(parents=True, exist_ok=True)
    listLines = _flistFormatRepoLines(listRepos)
    with open(pathOutput, "w", encoding="utf-8") as fileHandle:
        fileHandle.write(_CONTAINER_CONF_HEADER)
        for sLine in listLines:
            fileHandle.write(sLine + "\n")


def _flistFormatRepoLines(listRepos):
    """Format repo dicts as pipe-delimited strings."""
    listLines = []
    for dictRepo in listRepos:
        sLine = (
            f"{dictRepo['sName']}|{dictRepo['sUrl']}|"
            f"{dictRepo['sBranch']}|{dictRepo['sInstallMethod']}"
        )
        sDestination = dictRepo.get("sDestination", "")
        if sDestination:
            sLine += f"|{sDestination}"
        listLines.append(sLine)
    return listLines


def flistConvertFromProjectConfig(config):
    """Convert ProjectConfig repositories to container.conf format.

    Parameters
    ----------
    config : ProjectConfig
        A ProjectConfig instance whose listRepositories field
        contains dicts with keys: name, url, branch, installMethod.

    Returns
    -------
    list of dict
        Each dict has keys: sName, sUrl, sBranch, sInstallMethod.
    """
    listResult = []
    for dictRepo in config.listRepositories:
        listResult.append(_fdictConvertRepoEntry(dictRepo))
    return listResult


def _fdictConvertRepoEntry(dictRepo):
    """Convert a single YAML-style repo dict to Hungarian notation."""
    dictResult = {
        "sName": dictRepo.get("name", ""),
        "sUrl": dictRepo.get("url", ""),
        "sBranch": dictRepo.get("branch", "main"),
        "sInstallMethod": dictRepo.get(
            "installMethod", "pip_editable"
        ),
        "sDestination": dictRepo.get("destination", ""),
    }
    return dictResult


def fnGenerateContainerConf(config, sOutputPath):
    """Generate a container.conf file from a ProjectConfig instance.

    Parameters
    ----------
    config : ProjectConfig
        Source configuration.
    sOutputPath : str
        Path where the container.conf file will be written.
    """
    listRepos = flistConvertFromProjectConfig(config)
    fnWriteContainerConf(listRepos, sOutputPath)


def fsRemoveReferenceClonesOfDirectory(sConfText, sDirectoryName):
    """Return the list without the reference entries that land in one directory.

    For a project whose folder was copied in from the researcher's own
    directory: the build added the project's own repository as a
    ``reference`` entry, so the entrypoint cloned it into that folder
    while the copy was still arriving, and pulled the author's branch
    onto the copy at every later start. Each line is read the way the
    entrypoint reads it (``IFS='|' read -r``, nothing trimmed), and an
    entry lands in the directory when it is cloned there (its name) or
    moved there (its destination). Every other line, comments included,
    is kept byte for byte, so the image still does everything else it
    was built to do. Only ``reference`` entries are removed: any other
    method also installs the package, and removing one of those would
    remove the install without a word.
    """
    if not sDirectoryName:
        return sConfText
    return "\n".join(
        sLine for sLine in sConfText.split("\n")
        if not _fbReferenceEntryLandsIn(sLine, sDirectoryName)
    )


def _fbReferenceEntryLandsIn(sLine, sDirectoryName):
    """True iff one list line is a reference entry cloned or moved into the directory."""
    listFields = sLine.split("|", 4)
    sName = listFields[0]
    if not sName or sName.startswith("#") or len(listFields) < 4:
        return False
    sDestination = listFields[4] if len(listFields) == 5 else ""
    return listFields[3] == "reference" and sDirectoryName in (
        sName, sDestination)


def fsRepositoryListPathFor(sProjectName):
    """Return where one project's repository list lives, present or not."""
    if not _REGEX_PROJECT_NAME.match(str(sProjectName or "")):
        raise ValueError(
            f"{sProjectName!r} is not a registry-safe project name"
        )
    return os.path.join(_S_REPOSITORY_LISTS_DIRECTORY, f"{sProjectName}.conf")


def fsWriteRepositoryListForProject(sProjectName, sConfText):
    """Write the repository list one project's container starts with; return its path.

    Kept under ``~/.vaibify``, which Colima shares with its VM as it
    does the secret files, and replaced atomically, so a container that
    is already running keeps the file it started with. Readable by
    everyone because the container user reads it as well as root; it
    holds only what the image already carries.
    """
    sPath = fsRepositoryListPathFor(sProjectName)
    os.makedirs(_S_REPOSITORY_LISTS_DIRECTORY, mode=0o700, exist_ok=True)
    iDescriptor, sTemporaryPath = tempfile.mkstemp(
        dir=_S_REPOSITORY_LISTS_DIRECTORY, suffix=".tmp",
    )
    try:
        with os.fdopen(iDescriptor, "w", encoding="utf-8") as fileHandle:
            fileHandle.write(sConfText)
        os.chmod(sTemporaryPath, 0o644)
        os.replace(sTemporaryPath, sPath)
    except BaseException:
        if os.path.exists(sTemporaryPath):
            os.unlink(sTemporaryPath)
        raise
    return sPath
