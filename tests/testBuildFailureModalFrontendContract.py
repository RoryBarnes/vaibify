"""Frontend contract checks for the build-failure modal.

When a docker build fails from the GUI's container tile, the route
returns a structured detail with ``sStderrTail`` so the UI can show
the actual buildx output. Previously the route returned a bare
"Build failed" string and the toast was the only feedback, leaving
the user to chase the root cause through container logs. These tests
pin the wiring between the API client, the modal markup, and the
container-manager build flow. JavaScript is not executed by the
repository test suite; assertions are structural.
"""

import os

_sStaticDir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "vaibify", "gui", "static",
)


def _fsReadStaticFile(sName):
    sPath = os.path.join(_sStaticDir, sName)
    with open(sPath, "r", encoding="utf-8") as fileHandle:
        return fileHandle.read()


def test_build_failure_modal_dom_exists():
    """index.html must carry a hidden build-failure modal so the
    container manager has something to populate on a failed build."""
    sSource = _fsReadStaticFile("index.html")
    assert 'id="modalBuildFailure"' in sSource
    assert 'id="buildFailureMessage"' in sSource
    assert 'id="buildFailureTail"' in sSource
    assert 'id="buttonBuildFailureClose"' in sSource


def test_build_failure_tail_is_preformatted():
    """The tail must render in a ``<pre>`` so multi-line buildx
    output stays readable rather than reflowed into a single paragraph."""
    sSource = _fsReadStaticFile("index.html")
    iStart = sSource.find('id="buildFailureTail"')
    iWindowStart = sSource.rfind("<", 0, iStart)
    sOpenTag = sSource[iWindowStart:iStart + 30]
    assert sOpenTag.startswith("<pre"), (
        "buildFailureTail must use a <pre> element so the buildx "
        "stderr tail keeps its newlines and indentation."
    )


def test_api_client_extracts_structured_detail():
    """``_fnThrowForStatus`` must accept FastAPI's structured detail
    (a dict) without crashing — otherwise the new build-failure
    response coerces to ``[object Object]`` in the UI."""
    sSource = _fsReadStaticFile("scriptApiClient.js")
    assert "_fdictExtractDetail" in sSource
    assert "error.dictDetail" in sSource


def test_api_client_extract_detail_handles_string_and_dict():
    """The extractor must normalize both legacy string detail and
    new dict detail to a ``{sMessage, ...}`` shape, so older routes
    still produce a usable error message."""
    sSource = _fsReadStaticFile("scriptApiClient.js")
    iStart = sSource.find("function _fdictExtractDetail(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert 'typeof detail === "string"' in sBlock
    assert 'typeof detail === "object"' in sBlock


def test_container_manager_routes_build_failure_to_modal():
    """``fnBuildContainer``'s catch must consult ``_fnReportBuildFailure``
    rather than going straight to a toast, so callers with a stderr
    tail land in the modal."""
    sSource = _fsReadStaticFile("scriptContainerManager.js")
    iStart = sSource.find("async function fnBuildContainer(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "_fnReportBuildFailure(error)" in sBlock


def test_container_manager_show_failure_modal_wires_tail_and_close():
    """``_fnShowBuildFailureModal`` must populate the tail element and
    wire the close button — without those the modal opens and either
    sits blank or cannot be dismissed."""
    sSource = _fsReadStaticFile("scriptContainerManager.js")
    iStart = sSource.find("function _fnShowBuildFailureModal(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert 'getElementById("modalBuildFailure")' in sBlock
    assert 'getElementById("buildFailureTail")' in sBlock
    assert 'getElementById("buttonBuildFailureClose")' in sBlock
    assert ".onclick" in sBlock or ".addEventListener" in sBlock


def test_container_manager_falls_back_to_toast_when_tail_absent():
    """If the response has no stderr tail (legacy routes, generic
    errors), the build path must still show the toast — opening an
    empty modal would be worse UX than the original behavior."""
    sSource = _fsReadStaticFile("scriptContainerManager.js")
    iStart = sSource.find("function _fnReportBuildFailure(")
    assert iStart != -1
    iEnd = sSource.find("\n    }\n", iStart)
    sBlock = sSource[iStart:iEnd]
    assert "fnShowToast" in sBlock
    assert "sStderrTail" in sBlock
