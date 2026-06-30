"""Mutation-coverage tests for vaibify/reproducibility/dataArchiver.py.

Each test pins a guarantee that a surviving mutant violated:
- CHECKSUMS records the file basename, never the absolute host path
  (a public Zenodo deposit must not leak /Users/... and the integrity
  check must stay portable across machines).
- The CHECKSUMS body terminates with a trailing newline.
- Title precedence reads sProjectTitle BEFORE sWorkflowName, so a
  public archive and its README carry the publication title, not the
  internal workflow slug.
"""

import hashlib
import os

from vaibify.reproducibility.dataArchiver import (
    fdictBuildZenodoMetadata,
    fsGenerateArchiveReadme,
    fsGenerateChecksums,
)


class TestChecksumLineShape:
    """fsGenerateChecksums records the basename and a trailing newline."""

    def test_fsGenerateChecksums_records_basename_only(self, tmp_path):
        pathFile = tmp_path / "test.dat"
        pathFile.write_text("payload")
        sExpectedHash = hashlib.sha256(b"payload").hexdigest()

        sResult = fsGenerateChecksums([str(pathFile)])

        assert sResult == f"{sExpectedHash}  test.dat\n"
        sName = sResult.split("  ", 1)[1].rstrip("\n")
        assert sName == "test.dat"
        assert os.sep not in sName

    def test_fsGenerateChecksums_does_not_leak_absolute_path(self, tmp_path):
        pathFile = tmp_path / "test.dat"
        pathFile.write_text("payload")

        sResult = fsGenerateChecksums([str(pathFile)])

        assert str(tmp_path) not in sResult

    def test_fsGenerateChecksums_has_trailing_newline(self, tmp_path):
        pathFile = tmp_path / "test.dat"
        pathFile.write_text("payload")

        sResult = fsGenerateChecksums([str(pathFile)])

        assert sResult.endswith("\n")


class TestTitlePrecedence:
    """sProjectTitle is preferred over sWorkflowName in title and README."""

    def test_fdictBuildZenodoMetadata_prefers_project_title(self):
        dictWorkflow = {
            "sProjectTitle": "Preferred",
            "sWorkflowName": "Fallback",
        }
        dictMeta = fdictBuildZenodoMetadata(dictWorkflow)
        assert dictMeta["title"] == "Data for: Preferred"

    def test_fsGenerateArchiveReadme_prefers_project_title(self):
        dictWorkflow = {
            "sProjectTitle": "Preferred",
            "sWorkflowName": "Fallback",
            "listSteps": [],
        }
        sReadme = fsGenerateArchiveReadme(dictWorkflow)
        assert "# Preferred" in sReadme
        assert "Fallback" not in sReadme
