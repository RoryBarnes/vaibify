"""Frontend contract checks for the shared API client.

JavaScript is not executed by the repository test suite, so these
string-presence tests pin the contracts every dashboard module inherits
from ``scriptApiClient.js``. The one pinned here is error legibility: a
refusal the researcher cannot read is a refusal they cannot act on.
"""

import os

_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsApiClientSource():
    sPath = os.path.join(_sStaticDir, "scriptApiClient.js")
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def test_a_validation_error_is_explained_not_reduced_to_its_status():
    """FastAPI sends a 422's reasons as a LIST; the client must read it.

    The detail extractor handled a string and an object, so a list fell
    through ``typeof === "object"``, carried no ``sMessage``, and every
    shape rejection in the dashboard rendered as the bare "Request
    failed (422)" — a researcher who left a model unchosen was told a
    number and nothing else (live, 2026-08-28).
    """
    sSource = _fsApiClientSource()
    assert "Array.isArray(detail)" in sSource, (
        "a 422's list detail must be recognised before the object "
        "branch swallows it")
    assert "_fdictExplainValidationErrors" in sSource


def test_the_explanation_keeps_the_field_path_that_makes_it_actionable():
    """The field is the point: "is not valid" alone names nothing."""
    sSource = _fsApiClientSource()
    iStart = sSource.find("function _fdictExplainValidationErrors")
    assert iStart != -1
    iEnd = sSource.find("\n    }", iStart)
    sBody = sSource[iStart:iEnd]
    # The path is rendered, minus the framework's own "body" segment.
    assert "dictOne.loc" in sBody
    assert '"body"' in sBody
    assert "dictOne.msg" in sBody
    # The raw list is carried too, so a caller can render it richly.
    assert "listValidationErrors" in sBody
