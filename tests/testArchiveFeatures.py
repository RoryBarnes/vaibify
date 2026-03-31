"""Tests for figure categorization, script detection, and archival."""

import pytest
from vaibify.gui.workflowManager import (
    fsGetPlotCategory,
    flistCollectArchivePlots,
    flistCollectSupportingPlots,
    fdictAutoDetectScripts,
    fsCamelCaseDirectory,
)
from vaibify.reproducibility.dataArchiver import (
    fsGenerateArchiveReadme,
    fsGenerateChecksums,
    fdictBuildZenodoMetadata,
)


# ---------------------------------------------------------------------------
# Figure categorization
# ---------------------------------------------------------------------------


class TestPlotCategorization:
    """Test archive vs. supporting plot categorization."""

    def test_fbDefaultIsArchive(self):
        dictStep = {"saPlotFiles": ["Plot/fig1.pdf"]}
        assert fsGetPlotCategory(dictStep, "Plot/fig1.pdf") == "archive"

    def test_fbExplicitSupporting(self):
        dictStep = {
            "saPlotFiles": ["Plot/fig1.pdf"],
            "dictPlotFileCategories": {
                "Plot/fig1.pdf": "supporting",
            },
        }
        assert fsGetPlotCategory(dictStep, "Plot/fig1.pdf") == "supporting"

    def test_fbExplicitArchive(self):
        dictStep = {
            "saPlotFiles": ["Plot/fig1.pdf"],
            "dictPlotFileCategories": {
                "Plot/fig1.pdf": "archive",
            },
        }
        assert fsGetPlotCategory(dictStep, "Plot/fig1.pdf") == "archive"

    def test_fbCollectArchivePlots(self):
        dictWorkflow = {
            "listSteps": [
                {
                    "saPlotFiles": ["fig1.pdf", "fig2.pdf"],
                    "dictPlotFileCategories": {
                        "fig2.pdf": "supporting",
                    },
                },
            ],
        }
        assert flistCollectArchivePlots(dictWorkflow) == ["fig1.pdf"]

    def test_fbCollectSupportingPlots(self):
        dictWorkflow = {
            "listSteps": [
                {
                    "saPlotFiles": ["fig1.pdf", "fig2.pdf"],
                    "dictPlotFileCategories": {
                        "fig2.pdf": "supporting",
                    },
                },
            ],
        }
        assert flistCollectSupportingPlots(dictWorkflow) == ["fig2.pdf"]

    def test_fbAllArchiveByDefault(self):
        dictWorkflow = {
            "listSteps": [
                {"saPlotFiles": ["a.pdf", "b.pdf"]},
            ],
        }
        assert flistCollectArchivePlots(dictWorkflow) == [
            "a.pdf", "b.pdf"
        ]
        assert flistCollectSupportingPlots(dictWorkflow) == []


# ---------------------------------------------------------------------------
# Script prefix detection
# ---------------------------------------------------------------------------


class TestScriptDetection:
    """Test data*/plot* prefix-based script detection."""

    def test_fbDetectDataScripts(self):
        dictResult = fdictAutoDetectScripts([
            "dataRunSampler.py", "plotCorner.py", "utils.py",
        ])
        assert dictResult["listDataScripts"] == ["dataRunSampler.py"]
        assert dictResult["listPlotScripts"] == ["plotCorner.py"]

    def test_fbNoScripts(self):
        dictResult = fdictAutoDetectScripts([
            "README.md", "config.json",
        ])
        assert dictResult["listDataScripts"] == []
        assert dictResult["listPlotScripts"] == []

    def test_fbCaseInsensitive(self):
        dictResult = fdictAutoDetectScripts([
            "DataAnalysis.py", "PlotResults.py",
        ])
        assert len(dictResult["listDataScripts"]) == 1
        assert len(dictResult["listPlotScripts"]) == 1

    def test_fbNonPyIgnored(self):
        dictResult = fdictAutoDetectScripts([
            "data.txt", "plot.csv", "dataScript.py",
        ])
        assert dictResult["listDataScripts"] == ["dataScript.py"]
        assert dictResult["listPlotScripts"] == []

    def test_fbPathsPreserved(self):
        dictResult = fdictAutoDetectScripts([
            "subdir/dataAnalysis.py", "subdir/plotFigure.py",
        ])
        assert "subdir/dataAnalysis.py" in dictResult["listDataScripts"]
        assert "subdir/plotFigure.py" in dictResult["listPlotScripts"]


# ---------------------------------------------------------------------------
# Archive README and metadata
# ---------------------------------------------------------------------------


class TestArchiveGeneration:
    """Test archive README, checksums, and metadata generation."""

    def test_fbReadmeContainsTitle(self):
        dictWorkflow = {
            "sProjectTitle": "Test Project",
            "listSteps": [{"sName": "Step One"}],
        }
        sReadme = fsGenerateArchiveReadme(dictWorkflow)
        assert "Test Project" in sReadme
        assert "Step One" in sReadme

    def test_fbReadmeContainsReproduction(self):
        dictWorkflow = {
            "sWorkflowName": "Test",
            "listSteps": [],
        }
        sReadme = fsGenerateArchiveReadme(dictWorkflow)
        assert "director.py" in sReadme

    def test_fbChecksumFormat(self, tmp_path):
        sTestFile = tmp_path / "test.txt"
        sTestFile.write_text("hello world")
        sChecksums = fsGenerateChecksums([str(sTestFile)])
        assert "test.txt" in sChecksums
        assert len(sChecksums.split("  ")[0]) == 64

    def test_fbChecksumSkipsMissing(self, tmp_path):
        sChecksums = fsGenerateChecksums(["/nonexistent/file.txt"])
        assert sChecksums.strip() == ""

    def test_fbMetadataFields(self):
        dictWorkflow = {
            "sProjectTitle": "My Study",
            "listCreators": [{"name": "Scientist, A."}],
            "sLicense": "CC-BY-4.0",
            "listKeywords": ["data-science"],
        }
        dictMeta = fdictBuildZenodoMetadata(dictWorkflow)
        assert dictMeta["title"] == "Data for: My Study"
        assert dictMeta["upload_type"] == "dataset"
        assert dictMeta["license"] == "CC-BY-4.0"
        assert dictMeta["keywords"] == ["data-science"]
        assert dictMeta["creators"][0]["name"] == "Scientist, A."

    def test_fbMetadataDefaults(self):
        dictWorkflow = {"sWorkflowName": "Test"}
        dictMeta = fdictBuildZenodoMetadata(dictWorkflow)
        assert dictMeta["title"] == "Data for: Test"
        assert dictMeta["license"] == "CC-BY-4.0"
        assert dictMeta["creators"][0]["name"] == "Vaibify User"
