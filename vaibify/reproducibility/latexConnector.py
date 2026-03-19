"""LaTeX figure-inclusion helpers.

Generates includegraphics commands, margin icons, DOI badges, and
writes assembled include files for use in reproducible papers.
Annotates TeX files with GitHub source links and Zenodo DOI badges.
"""

import os
import re

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
# TeX annotation — parse, match, and insert links
# ------------------------------------------------------------------


def flistParseIncludeGraphics(sTexContent):
    """Extract figure filenames from includegraphics commands."""
    listMatches = re.findall(
        r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}",
        sTexContent,
    )
    return [os.path.basename(s) for s in listMatches]


def fdictMatchFiguresToSteps(listFigureNames, dictWorkflow):
    """Map figure basenames to step camelCase directory names."""
    from vaibify.gui.workflowManager import (
        fsCamelCaseDirectory,
    )
    dictMatches = {}
    for iStep, dictStep in enumerate(
        dictWorkflow.get("listSteps", [])
    ):
        sStepName = dictStep.get("sName", "")
        sCamelDir = fsCamelCaseDirectory(sStepName)
        for sKey in ("saPlotFiles", "saDataFiles"):
            for sFile in dictStep.get(sKey, []):
                sBasename = os.path.basename(sFile)
                if sBasename in listFigureNames:
                    dictMatches[sBasename] = {
                        "sCamelCaseDir": sCamelDir,
                        "iStepIndex": iStep,
                    }
    return dictMatches


_SOURCE_CODE_MARKER = "[Source Code]"


def fsInsertGithubLinks(sTexContent, dictMatches, sGithubBaseUrl):
    """Insert Source Code href after each matched figure caption."""
    for sBasename, dictInfo in dictMatches.items():
        sTexContent = _fsInsertLinkForFigure(
            sTexContent, sBasename,
            dictInfo["sCamelCaseDir"], sGithubBaseUrl,
        )
    return sTexContent


def _fsInsertLinkForFigure(
    sTexContent, sBasename, sCamelDir, sGithubBaseUrl,
):
    """Insert or replace a Source Code link near a figure."""
    sUrl = f"{sGithubBaseUrl}/{sCamelDir}"
    sLink = f"\\href{{{sUrl}}}{{{_SOURCE_CODE_MARKER}}}"
    sOldMarker = sCamelDir + "}" + "{" + _SOURCE_CODE_MARKER + "}"
    if sOldMarker in sTexContent:
        iStart = sTexContent.index(sOldMarker)
        iHrefStart = sTexContent.rfind("\\href{", 0, iStart)
        if iHrefStart >= 0:
            iEnd = iStart + len(sOldMarker)
            return sTexContent[:iHrefStart] + sLink + sTexContent[iEnd:]
    sEscaped = re.escape(sBasename)
    sFigurePattern = (
        r"(\\includegraphics(?:\[[^\]]*\])?\{[^}]*"
        + sEscaped + r"[^}]*\})"
    )
    sCaption = r"(\\caption\{(?:[^{}]|\{[^{}]*\})*\})"
    sCombined = sFigurePattern + r"(.*?)" + sCaption
    match = re.search(sCombined, sTexContent, re.DOTALL)
    if not match:
        return sTexContent
    sOrigCaption = match.group(3)
    sNewCaption = sOrigCaption[:-1] + " " + sLink + "}"
    return sTexContent.replace(sOrigCaption, sNewCaption, 1)


def fsInsertZenodoDoi(sTexContent, sDoi):
    """Insert a Zenodo DOI link in the acknowledgments section."""
    if not sDoi:
        return sTexContent
    sDoiLink = fsGenerateZenodoBadge(sDoi)
    sDoiSentence = (
        "The data products associated with this work are "
        f"archived at {sDoiLink}."
    )
    sExistingDoi = re.escape(sDoi)
    if re.search(sExistingDoi, sTexContent):
        return sTexContent
    sAckPattern = (
        r"(\\begin\{acknowledgments\}|"
        r"\\section\*?\{[Aa]cknowledg[e]?ments?\})"
    )
    match = re.search(sAckPattern, sTexContent)
    if match:
        iInsertPos = match.end()
        return (
            sTexContent[:iInsertPos] + "\n" + sDoiSentence
            + "\n" + sTexContent[iInsertPos:]
        )
    sEndDoc = r"\\end\{document\}"
    match = re.search(sEndDoc, sTexContent)
    if match:
        return (
            sTexContent[:match.start()]
            + "% Zenodo archive\n" + sDoiSentence + "\n\n"
            + sTexContent[match.start():]
        )
    return sTexContent


def fsAnnotateTexFile(
    sTexContent, dictWorkflow, sGithubBaseUrl, sDoi,
):
    """Annotate a TeX file with GitHub links and Zenodo DOI."""
    listFigureNames = flistParseIncludeGraphics(sTexContent)
    dictMatches = fdictMatchFiguresToSteps(
        listFigureNames, dictWorkflow
    )
    sTexContent = fsInsertGithubLinks(
        sTexContent, dictMatches, sGithubBaseUrl
    )
    sTexContent = fsInsertZenodoDoi(sTexContent, sDoi)
    return sTexContent


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
