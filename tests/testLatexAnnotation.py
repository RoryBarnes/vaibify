"""Tests for LaTeX annotation functions in latexConnector.py."""

import pytest


class TestParseIncludeGraphics:
    """Test flistParseIncludeGraphics extraction."""

    def test_fbSimpleInclude(self):
        from vaibify.reproducibility.latexConnector import (
            flistParseIncludeGraphics,
        )
        sTeX = r"\includegraphics{figures/plot.pdf}"
        assert flistParseIncludeGraphics(sTeX) == ["plot.pdf"]

    def test_fbIncludeWithOptions(self):
        from vaibify.reproducibility.latexConnector import (
            flistParseIncludeGraphics,
        )
        sTeX = r"\includegraphics[width=0.8\linewidth]{Plot/corner.pdf}"
        assert flistParseIncludeGraphics(sTeX) == ["corner.pdf"]

    def test_fbMultipleIncludes(self):
        from vaibify.reproducibility.latexConnector import (
            flistParseIncludeGraphics,
        )
        sTeX = (
            r"\includegraphics{fig1.pdf}" "\n"
            r"\includegraphics[scale=0.5]{subdir/fig2.png}"
        )
        assert flistParseIncludeGraphics(sTeX) == [
            "fig1.pdf", "fig2.png"
        ]

    def test_fbNoIncludes(self):
        from vaibify.reproducibility.latexConnector import (
            flistParseIncludeGraphics,
        )
        assert flistParseIncludeGraphics(r"\section{Intro}") == []


class TestMatchFiguresToSteps:
    """Test fdictMatchFiguresToSteps mapping."""

    def test_fbMatchFound(self):
        from vaibify.reproducibility.latexConnector import (
            fdictMatchFiguresToSteps,
        )
        dictWorkflow = {
            "listSteps": [
                {
                    "sName": "Kepler FFD Corner",
                    "saPlotFiles": [
                        "Plot/CornerVariableSlope.pdf"
                    ],
                    "saDataFiles": [],
                },
            ],
        }
        dictResult = fdictMatchFiguresToSteps(
            ["CornerVariableSlope.pdf"], dictWorkflow
        )
        assert "CornerVariableSlope.pdf" in dictResult
        assert dictResult["CornerVariableSlope.pdf"][
            "sCamelCaseDir"
        ] == "KeplerFfdCorner"

    def test_fbNoMatch(self):
        from vaibify.reproducibility.latexConnector import (
            fdictMatchFiguresToSteps,
        )
        dictWorkflow = {
            "listSteps": [
                {
                    "sName": "Step One",
                    "saPlotFiles": ["other.pdf"],
                    "saDataFiles": [],
                },
            ],
        }
        dictResult = fdictMatchFiguresToSteps(
            ["missing.pdf"], dictWorkflow
        )
        assert dictResult == {}


class TestInsertGithubLinks:
    """Test fsInsertGithubLinks caption annotation."""

    def test_fbInsertLink(self):
        from vaibify.reproducibility.latexConnector import (
            fsInsertGithubLinks,
        )
        sTeX = (
            r"\includegraphics{figures/corner.pdf}" "\n"
            r"\caption{A corner plot.}"
        )
        dictMatches = {
            "corner.pdf": {
                "sCamelCaseDir": "KeplerFfd",
                "iStepIndex": 0,
            },
        }
        sResult = fsInsertGithubLinks(
            sTeX, dictMatches, "https://github.com/user/repo/tree/main"
        )
        assert "[Source Code]" in sResult
        assert "KeplerFfd" in sResult
        assert r"\href{" in sResult

    def test_fbDuplicateDetection(self):
        from vaibify.reproducibility.latexConnector import (
            fsInsertGithubLinks,
        )
        sTeX = (
            r"\includegraphics{figures/corner.pdf}" "\n"
            r"\caption{A corner plot. "
            r"\href{https://github.com/old/repo/tree/main/KeplerFfd}"
            r"{[Source Code]}}"
        )
        dictMatches = {
            "corner.pdf": {
                "sCamelCaseDir": "KeplerFfd",
                "iStepIndex": 0,
            },
        }
        sResult = fsInsertGithubLinks(
            sTeX, dictMatches, "https://github.com/new/repo/tree/main"
        )
        assert sResult.count("[Source Code]") == 1
        assert "new/repo" in sResult
        assert "old/repo" not in sResult

    def test_fbNoCaption(self):
        from vaibify.reproducibility.latexConnector import (
            fsInsertGithubLinks,
        )
        sTeX = r"\includegraphics{fig.pdf}"
        dictMatches = {
            "fig.pdf": {
                "sCamelCaseDir": "Test",
                "iStepIndex": 0,
            },
        }
        sResult = fsInsertGithubLinks(
            sTeX, dictMatches, "https://github.com/u/r/tree/main"
        )
        assert "[Source Code]" not in sResult


class TestInsertZenodoDoi:
    """Test fsInsertZenodoDoi acknowledgments insertion."""

    def test_fbInsertInAcknowledgments(self):
        from vaibify.reproducibility.latexConnector import (
            fsInsertZenodoDoi,
        )
        sTeX = (
            r"\begin{acknowledgments}" "\n"
            "We thank the reviewer.\n"
            r"\end{acknowledgments}"
        )
        sResult = fsInsertZenodoDoi(sTeX, "10.5281/zenodo.123")
        assert "10.5281/zenodo.123" in sResult
        assert "archived at" in sResult

    def test_fbNoAcknowledgments(self):
        from vaibify.reproducibility.latexConnector import (
            fsInsertZenodoDoi,
        )
        sTeX = r"\section{Intro}" "\n" r"\end{document}"
        sResult = fsInsertZenodoDoi(sTeX, "10.5281/zenodo.456")
        assert "10.5281/zenodo.456" in sResult
        assert "Zenodo archive" in sResult

    def test_fbNoDoi(self):
        from vaibify.reproducibility.latexConnector import (
            fsInsertZenodoDoi,
        )
        sTeX = r"\begin{acknowledgments}\end{acknowledgments}"
        sResult = fsInsertZenodoDoi(sTeX, "")
        assert sResult == sTeX

    def test_fbDuplicateDoiSkipped(self):
        from vaibify.reproducibility.latexConnector import (
            fsInsertZenodoDoi,
        )
        sTeX = (
            r"\begin{acknowledgments}" "\n"
            "Archived at 10.5281/zenodo.789.\n"
            r"\end{acknowledgments}"
        )
        sResult = fsInsertZenodoDoi(sTeX, "10.5281/zenodo.789")
        assert sResult.count("10.5281/zenodo.789") == 1


class TestAnnotateTexFile:
    """Test the full fsAnnotateTexFile orchestrator."""

    def test_fbFullAnnotation(self):
        from vaibify.reproducibility.latexConnector import (
            fsAnnotateTexFile,
        )
        sTeX = (
            r"\includegraphics{figures/corner.pdf}" "\n"
            r"\caption{Corner plot.}" "\n"
            r"\begin{acknowledgments}" "\n"
            "Thanks.\n"
            r"\end{acknowledgments}" "\n"
            r"\end{document}"
        )
        dictWorkflow = {
            "listSteps": [
                {
                    "sName": "Kepler FFD",
                    "saPlotFiles": ["Plot/corner.pdf"],
                    "saDataFiles": [],
                },
            ],
        }
        sResult = fsAnnotateTexFile(
            sTeX, dictWorkflow,
            "https://github.com/u/r/tree/main",
            "10.5281/zenodo.999",
        )
        assert "[Source Code]" in sResult
        assert "KeplerFfd" in sResult
        assert "10.5281/zenodo.999" in sResult
