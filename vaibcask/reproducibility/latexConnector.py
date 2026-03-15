"""LaTeX figure-inclusion helpers.

Generates includegraphics commands, margin icons, DOI badges, and
writes assembled include files for use in reproducible papers.
"""

from pathlib import Path


# ------------------------------------------------------------------
# Individual LaTeX snippets
# ------------------------------------------------------------------


def fsGenerateIncludeGraphics(sFilePath, dWidth=1.0):
    """Return a LaTeX includegraphics command.

    Parameters
    ----------
    sFilePath : str
        Path to the figure file (relative to the TeX root).
    dWidth : float
        Fraction of linewidth (0.0 to 1.0).

    Returns
    -------
    str
        LaTeX includegraphics command string.
    """
    _fnValidateWidth(dWidth)
    return (
        f"\\includegraphics[width={dWidth}\\linewidth]"
        f"{{{sFilePath}}}"
    )


def flistGenerateFigureIncludes(listFigurePaths):
    """Return a list of includegraphics commands.

    Parameters
    ----------
    listFigurePaths : list of str
        Paths to figure files.

    Returns
    -------
    list of str
        One includegraphics command per figure.
    """
    return [
        fsGenerateIncludeGraphics(sPath) for sPath in listFigurePaths
    ]


def fsGenerateMarginIcon(sGithubUrl, sCommitHash):
    """Return a LaTeX macro for a clickable margin icon.

    Parameters
    ----------
    sGithubUrl : str
        Base URL of the GitHub repository.
    sCommitHash : str
        Git commit hash to link to.

    Returns
    -------
    str
        LaTeX href command for the margin.
    """
    sFullUrl = f"{sGithubUrl}/commit/{sCommitHash}"
    sShortHash = sCommitHash[:7]
    return (
        f"\\marginpar{{\\href{{{sFullUrl}}}"
        f"{{\\texttt{{{sShortHash}}}}}}}"
    )


def fsGenerateZenodoBadge(sDoi):
    """Return a LaTeX macro for a Zenodo DOI badge.

    Parameters
    ----------
    sDoi : str
        Full DOI string (e.g. "10.5281/zenodo.1234567").

    Returns
    -------
    str
        LaTeX href command rendering the DOI as a badge.
    """
    sUrl = f"https://doi.org/{sDoi}"
    return (
        f"\\href{{{sUrl}}}"
        f"{{\\texttt{{DOI: {sDoi}}}}}"
    )


# ------------------------------------------------------------------
# File writing
# ------------------------------------------------------------------


def fnWriteLatexIncludes(listFigurePaths, sOutputPath):
    """Write all includegraphics commands to a .tex file.

    Parameters
    ----------
    listFigurePaths : list of str
        Paths to figure files.
    sOutputPath : str
        Destination .tex file path.
    """
    listCommands = flistGenerateFigureIncludes(listFigurePaths)
    sContent = _fsJoinCommands(listCommands)
    _fnWriteTexFile(sOutputPath, sContent)


def _fsJoinCommands(listCommands):
    """Join a list of LaTeX commands with newlines."""
    return "\n".join(listCommands) + "\n"


def _fnWriteTexFile(sOutputPath, sContent):
    """Write string content to a file, creating parents as needed."""
    pathOutput = Path(sOutputPath)
    pathOutput.parent.mkdir(parents=True, exist_ok=True)
    with open(pathOutput, "w") as fileHandle:
        fileHandle.write(sContent)


# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------


def _fnValidateWidth(dWidth):
    """Raise ValueError if width is outside the valid range."""
    if not (0.0 < dWidth <= 1.0):
        raise ValueError(
            f"Width must be between 0.0 (exclusive) and 1.0 "
            f"(inclusive), got {dWidth}."
        )
