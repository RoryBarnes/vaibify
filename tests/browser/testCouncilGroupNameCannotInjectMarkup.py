"""A directory name from the project cannot add attributes to the modal.

The council's snapshot-scope modal writes each omission group's name into
``data-directory="..."`` of HTML it builds. Its escaper was the
``textContent`` -> ``innerHTML`` idiom, which escapes ``&``, ``<`` and
``>`` but NOT the double quote, so a directory called
``x" id="btnCouncilScopeContinue" style="position:fixed;inset:0`` closed
the attribute and added its own: a second element with the real button's
id, stretched over the viewport -- a click hijack from a name an
in-container agent can create.

A name is project data, so it must reach the page as text and nothing
else: no second element with the button's id, no injected ``style``, and
the attribute must carry the whole name back out unchanged.
"""

import pytest

from .testBrowserJourneys import _fnReleaseBrowserLaneOwnership  # noqa: F401
from .testCouncilPlanningJourney import (  # noqa: F401 -- fixture wiring
    _fdictClaimAndActivate,
    _fnIsolateCouncilStore,
)
from .testCouncilSnapshotScopeModal import (  # noqa: F401 -- fixture wiring
    _fnOpenTheGate,
    fnModelALargeRepository,
)

pytestmark = pytest.mark.browser

S_HOSTILE_DIRECTORY = (
    'x" id="btnCouncilScopeContinue" style="position:fixed;inset:0'
)


@pytest.mark.falsification
def testAHostileDirectoryNameStaysTextInTheOmissionList(
        pageDashboard, serverHub, monkeypatch, fnModelALargeRepository):
    """Kills: the scope modal escaping with the textContent/innerHTML idiom."""
    _fnOpenTheGate(monkeypatch)
    fnModelALargeRepository.dictUntrackedInventory["listEntries"].append(
        [S_HOSTILE_DIRECTORY + "/payload.bin", "ignored", 1000])
    _fdictClaimAndActivate(pageDashboard, serverHub)
    pageDashboard.click("#btnAgentCouncil")
    pageDashboard.wait_for_selector("#btnCouncilScopeContinue", timeout=8000)
    pageDashboard.click(".council-missing-files > summary")
    dictPage = pageDashboard.evaluate(
        """(sHostile) => {
            const listGroups = Array.from(document.querySelectorAll(
                '.council-omission-group'));
            return {
                iButtons: document.querySelectorAll(
                    '#btnCouncilScopeContinue').length,
                listInjectedStyles: Array.from(document.querySelectorAll(
                    '[style]')).filter(el => (el.getAttribute('style') || '')
                    .includes('position:fixed;inset:0')).length,
                bRoundTrips: listGroups.some(el =>
                    el.getAttribute('data-directory') === sHostile),
                sSummaryText: listGroups.map(el => el.textContent).join('|'),
            };
        }""",
        S_HOSTILE_DIRECTORY)
    assert dictPage["iButtons"] == 1, (
        "the name added a second element carrying the real button's id")
    assert dictPage["listInjectedStyles"] == 0
    assert dictPage["bRoundTrips"], (
        "the attribute must carry the whole directory name back out")
    assert S_HOSTILE_DIRECTORY in dictPage["sSummaryText"]
    assert pageDashboard.listPageErrors == []
