"""The browser lane's Docker fake must stay fail-closed and declared.

Lane 1 is only worth running if its Docker stand-in cannot invent
answers. This suite already carries about twenty hand-rolled Docker
mocks; the most-copied ends in `return (0, "")`, so changing `test -d`
to `[ -d ]` leaves it green. `testDockerConnectionLive.py` records
where that habit led -- mocks that accepted every attribute access
while a real daemon raised `URLSchemeUnknown` on every call.

These tests run in the ordinary suite: they need no browser, and the
property they protect must hold whether or not Playwright is
installed.
"""

import inspect

import pytest

from tests.browser import fakeDockerAdapter


@pytest.mark.falsification
def testTheFakeRaisesRatherThanInventingAnAnswer():
    """Kills: giving the adapter a permissive catch-all return.

    Mutation: replace the ``raise UnmodelledContainerCall`` with
    ``return (0, "")`` -- the exact shape of the mock this one exists
    not to become.
    """
    adapter = fakeDockerAdapter.FailClosedDockerAdapter()
    with pytest.raises(fakeDockerAdapter.UnmodelledContainerCall):
        adapter.ftResultExecuteCommand("cid", "some unmodelled command")


def testEveryModelledCommandNamesItsLaneTwoAssertion():
    """A modelled call with no real-container check is a contract hole.

    Declaring the contract as "whatever Lane 2 happened to exercise"
    would let a conditional path -- an error branch, a retry -- be
    modelled here and never confirmed against a real container. Each
    row must name the assertion that does confirm it.
    """
    listMissing = [
        dictCommand for dictCommand in
        fakeDockerAdapter.LIST_MODELLED_COMMANDS
        if not dictCommand.get("sLaneTwoAssertion", "").strip()
    ]
    assert not listMissing, (
        "These modelled commands name no Lane 2 assertion, so nothing "
        "proves a real container answers them the same way: "
        f"{[d.get('sMatch') for d in listMissing]}"
    )


def testEveryNamedLaneTwoAssertionExists():
    """The named Lane 2 test must be a real test, not a comment.

    Without this, ``sLaneTwoAssertion`` could point at a function
    nobody ever wrote and the "declared contract" would be decorative
    -- the precise failure mode this repository keeps rediscovering:
    a guarantee stated in prose that no code enforces.
    """
    from tests import testContainerAcceptance
    listMissing = [
        dictCommand["sLaneTwoAssertion"]
        for dictCommand in fakeDockerAdapter.LIST_MODELLED_COMMANDS
        if not hasattr(
            testContainerAcceptance, dictCommand["sLaneTwoAssertion"]
        )
    ]
    assert not listMissing, (
        "The fake claims confirmation from Lane 2 tests that do not "
        f"exist in tests/testContainerAcceptance.py: {listMissing}"
    )


def testEveryModelledCommandIsActuallyMatched():
    """The declared contract must not drift from the implementation.

    A row nobody dispatches on is documentation of a capability the
    fake does not have.
    """
    sSource = inspect.getsource(
        fakeDockerAdapter.FailClosedDockerAdapter.ftResultExecuteCommand
    )
    listUnmatched = [
        dictCommand["sMatch"]
        for dictCommand in fakeDockerAdapter.LIST_MODELLED_COMMANDS
        if dictCommand["sMatch"] not in sSource
    ]
    assert not listUnmatched, (
        "Declared in LIST_MODELLED_COMMANDS but never dispatched on: "
        f"{listUnmatched}"
    )


def testTheFakeKeepsContainerNameDistinctFromId():
    """name != id, for the reason AGENTS.md records.

    The owner-of-record map is name-keyed while every URL carries the
    id; a fixture with name == id once hid a bug that would have closed
    every real hub session.
    """
    assert (
        fakeDockerAdapter.S_CONTAINER_NAME
        != fakeDockerAdapter.S_CONTAINER_ID
    )
    dictContainer = (
        fakeDockerAdapter.FailClosedDockerAdapter()
        .flistGetRunningContainers()[0]
    )
    assert dictContainer["sName"] != dictContainer["sContainerId"]
