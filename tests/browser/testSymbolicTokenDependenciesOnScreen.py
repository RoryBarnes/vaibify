"""A step's dependency declared by a canonical token appears in its row.

The page read only the deprecated ``{StepNN.x}`` token, so a dependency
declared the canonical way, ``{step:<id>.<stem>}``, was missing from the
Dependencies row (and from every staleness walk that reads the same
list). The real page code is driven with the real seeded workflow; only
the second step's command is given a symbolic token in the page.
"""

import posixpath

import pytest

from tests.browser.conftest import (
    S_HOST_STEP_OUTPUT,
    fnOpenTheSeededHostWorkflow,
)


pytestmark = pytest.mark.browser

_S_CLEAR_THE_MEMO = """
    const dictWorkflow = VaibifyApp.fdictGetWorkflow();
    const listSteps = dictWorkflow.listSteps;
    dictWorkflow.listSteps = null;
    VaibifyApp.fnRenderStepList();
    await new Promise((fnResolve) => requestAnimationFrame(
        () => requestAnimationFrame(fnResolve)));
    dictWorkflow.listSteps = listSteps;
"""

_S_DECLARE_AND_READ = """async (sToken) => {""" + _S_CLEAR_THE_MEMO + """
    listSteps[1].saDataCommands = ["python3 second.py " + sToken];
    listSteps[1].saSourceCodeDeps = [];
    return VaibifyApp.flistGetStepDependencies(1);
}"""


@pytest.fixture(autouse=True)
def fixtureDropClaimsBetweenTests(serverHub):
    """Give every claim back after each test (the hub is module-scoped)."""
    yield
    from vaibify.config.containerLock import fnReleaseContainerLock
    dictContainerOwners = serverHub.app.state.dictContainerOwners
    for _sName, recordOwner in list(dictContainerOwners.items()):
        fileHandle = getattr(recordOwner, "fileHandleLock", None)
        if fileHandle is not None:
            try:
                fnReleaseContainerLock(fileHandle)
            except OSError:
                pass
    dictContainerOwners.clear()
    serverHub.app.state.dictSessionOwner.clear()


@pytest.mark.falsification
def testASymbolicTokenDeclaresTheDependency(pageDashboard, serverHub):
    """Kills: reading only the positional {StepNN.x} token again."""
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    sStem = posixpath.splitext(posixpath.basename(S_HOST_STEP_OUTPUT))[0]
    listDeps = pageDashboard.evaluate(
        _S_DECLARE_AND_READ, "{step:make-numbers." + sStem + "}")
    assert listDeps == [0], listDeps
    assert pageDashboard.listPageErrors == []


def testAScannedDependencyIsReadByStepId(pageDashboard, serverHub):
    fnOpenTheSeededHostWorkflow(pageDashboard, serverHub)
    listDeps = pageDashboard.evaluate("""async () => {""" + _S_CLEAR_THE_MEMO
                                      + """
        listSteps[1].saSourceCodeDeps = ["make-numbers"];
        return VaibifyApp.flistGetStepDependencies(1);
    }""")
    assert listDeps == [0], listDeps
