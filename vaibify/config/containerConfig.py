"""Pipe-delimited container.conf I/O for backwards compatibility."""

from pathlib import Path


_CONTAINER_CONF_HEADER = (
    "# Vaibify Repository Configuration\n"
    "# Format: name|url|branch|install_method\n"
    "#\n"
    "# Install methods:\n"
    "#   c_and_pip    - make opt then pip install -e . --no-deps\n"
    "#   pip_no_deps  - pip install -e . --no-deps "
    "(deps in Dockerfile)\n"
    "#   pip_editable - pip install -e . (standalone package)\n"
    "#   scripts_only - add to PYTHONPATH and PATH only\n"
    "#   reference    - clone for reference, do not install\n"
    "#\n"
    "# Order matters: repos are cloned and installed "
    "in this order.\n"
    "# Lines starting with # are comments.\n"
)

_REQUIRED_FIELDS = 4


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
    listLines = _flistReadNonCommentLines(pathFile)
    return _flistParsePipeLines(listLines, sFilePath)


def _flistReadNonCommentLines(pathFile):
    """Read file and return non-blank, non-comment lines."""
    listResult = []
    with open(pathFile, "r") as fileHandle:
        for sLine in fileHandle:
            sStripped = sLine.strip()
            if sStripped and not sStripped.startswith("#"):
                listResult.append(sStripped)
    return listResult


def _flistParsePipeLines(listLines, sFilePath):
    """Parse pipe-delimited lines into repo dictionaries."""
    listRepos = []
    for iLineIndex, sLine in enumerate(listLines):
        listParts = sLine.split("|")
        if len(listParts) != _REQUIRED_FIELDS:
            raise ValueError(
                f"Expected {_REQUIRED_FIELDS} pipe-delimited "
                f"fields in '{sFilePath}', line {iLineIndex + 1}: "
                f"'{sLine}'"
            )
        listRepos.append(_fdictBuildRepoEntry(listParts))
    return listRepos


def _fdictBuildRepoEntry(listParts):
    """Build a repo dict from a list of pipe-split fields."""
    return {
        "sName": listParts[0].strip(),
        "sUrl": listParts[1].strip(),
        "sBranch": listParts[2].strip(),
        "sInstallMethod": listParts[3].strip(),
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
    return {
        "sName": dictRepo.get("name", ""),
        "sUrl": dictRepo.get("url", ""),
        "sBranch": dictRepo.get("branch", "main"),
        "sInstallMethod": dictRepo.get(
            "installMethod", "pip_editable"
        ),
    }


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
