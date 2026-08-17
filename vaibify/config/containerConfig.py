"""Pipe-delimited container.conf I/O for backwards compatibility."""

from pathlib import Path


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
        pathFile.read_text(), sFilePath
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
    with open(pathOutput, "w") as fileHandle:
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
