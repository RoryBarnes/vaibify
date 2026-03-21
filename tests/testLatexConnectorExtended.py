"""Tests for untested functions in vaibify.reproducibility.latexConnector."""

import os
import tempfile

import pytest

from vaibify.reproducibility.latexConnector import (
    fsGenerateIncludeGraphics,
    flistGenerateFigureIncludes,
    fsGenerateMarginIcon,
    fsGenerateZenodoBadge,
    fnWriteLatexIncludes,
    flistParseIncludeGraphics,
    fdictMatchFiguresToSteps,
    fsInsertGithubLinks,
    fsInsertZenodoDoi,
    fsAnnotateTexFile,
    _fnValidateWidth,
)


def test_fsGenerateIncludeGraphics_default():
    sResult = fsGenerateIncludeGraphics("fig.pdf")
    assert "\\includegraphics" in sResult
    assert "width=1.0\\linewidth" in sResult
    assert "{fig.pdf}" in sResult


def test_fsGenerateIncludeGraphics_custom_width():
    sResult = fsGenerateIncludeGraphics("fig.pdf", dWidth=0.5)
    assert "width=0.5\\linewidth" in sResult


def test_fsGenerateIncludeGraphics_invalid_width():
    with pytest.raises(ValueError):
        fsGenerateIncludeGraphics("fig.pdf", dWidth=0.0)
    with pytest.raises(ValueError):
        fsGenerateIncludeGraphics("fig.pdf", dWidth=1.5)


def test_flistGenerateFigureIncludes_multiple():
    listPaths = ["a.pdf", "b.png"]
    listCmds = flistGenerateFigureIncludes(listPaths)
    assert len(listCmds) == 2
    assert "{a.pdf}" in listCmds[0]
    assert "{b.png}" in listCmds[1]


def test_fsGenerateMarginIcon_format():
    sResult = fsGenerateMarginIcon(
        "https://github.com/user/repo", "abc1234def5678"
    )
    assert "\\marginpar" in sResult
    assert "abc1234" in sResult
    assert "commit/abc1234def5678" in sResult


def test_fsGenerateZenodoBadge_format():
    sResult = fsGenerateZenodoBadge("10.5281/zenodo.123")
    assert "https://doi.org/10.5281/zenodo.123" in sResult
    assert "DOI: 10.5281/zenodo.123" in sResult


def test_fnWriteLatexIncludes_creates_file():
    with tempfile.TemporaryDirectory() as sTmpDir:
        sPath = os.path.join(sTmpDir, "sub", "includes.tex")
        fnWriteLatexIncludes(["fig1.pdf", "fig2.png"], sPath)
        assert os.path.isfile(sPath)
        with open(sPath) as fh:
            sContent = fh.read()
        assert "fig1.pdf" in sContent
        assert "fig2.png" in sContent


def test_flistParseIncludeGraphics_extracts():
    sTexContent = (
        "\\includegraphics[width=0.5\\linewidth]{figures/plot.pdf}\n"
        "\\includegraphics{data/chart.png}\n"
    )
    listNames = flistParseIncludeGraphics(sTexContent)
    assert "plot.pdf" in listNames
    assert "chart.png" in listNames


def test_flistParseIncludeGraphics_empty():
    assert flistParseIncludeGraphics("no figures here") == []


def test_fdictMatchFiguresToSteps_maps():
    dictWorkflow = {
        "listSteps": [
            {
                "sName": "Make Data",
                "saPlotFiles": ["figures/plot.pdf"],
                "saDataFiles": [],
            },
        ],
    }
    dictMatches = fdictMatchFiguresToSteps(
        ["plot.pdf"], dictWorkflow
    )
    assert "plot.pdf" in dictMatches
    assert dictMatches["plot.pdf"]["iStepIndex"] == 0


def test_fsInsertZenodoDoi_inserts():
    sTexContent = "\\begin{acknowledgments}\nThanks.\n"
    sResult = fsInsertZenodoDoi(sTexContent, "10.5281/zenodo.9")
    assert "10.5281/zenodo.9" in sResult


def test_fsInsertZenodoDoi_empty_doi():
    sTexContent = "some text"
    assert fsInsertZenodoDoi(sTexContent, "") == sTexContent


def test_fsInsertZenodoDoi_no_duplicate():
    sTexContent = "Already has 10.5281/zenodo.9 here"
    sResult = fsInsertZenodoDoi(sTexContent, "10.5281/zenodo.9")
    assert sResult == sTexContent


def test_fsAnnotateTexFile_combined():
    sTexContent = "\\end{document}"
    dictWorkflow = {"listSteps": []}
    sResult = fsAnnotateTexFile(
        sTexContent, dictWorkflow, "https://github.com/x/y", ""
    )
    assert "\\end{document}" in sResult
