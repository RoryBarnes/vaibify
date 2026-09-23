"""Kill-confirmed guards on adoption: each proven to fail when its guard breaks.

Adoption writes into a researcher's own repository and is callable by
an agent, so the guards that matter are the ones whose silent removal
would destroy work or quietly repoint a project: the file it must never
overwrite, the directory it must never create, the history it must
never rewrite, the enclosing repository it must never nest inside, and
the remedy every refusal must carry.

Each test below was proven to FAIL under the mutation named on its
``Kills:`` line, and that mutation is recorded in
``tests/falsificationRegistry.py`` so the kill can be re-confirmed as
the module changes. Sensitivity is not correctness: these tests also
rest on the git semantics measured inside a real container and recorded
in ``projectAdoption``'s module docstring, and on the live drive in the
adoption block of ``testContainerAcceptance.py``.
"""

import json

import pytest
from fastapi import HTTPException

from vaibify.gui import projectAdoption

from tests.testProjectAdoption import (
    FakeConnection,
    S_CONTAINER,
    S_DIRECTORY,
    S_PROJECT_NAME,
    S_PROJECT_PATH,
    S_REPOSITORY,
    S_ROOT,
    fixtureStubTheSidecarAndTheSearch,  # noqa: F401 -- autouse fixture
)


S_FILE_NAME = "batchAnalysis.json"


def _fdictAdopt(connectionFake):
    """Adopt with this file's canonical arguments."""
    return projectAdoption.fdictAdoptDirectoryAsProject(
        connectionFake, S_CONTAINER, S_DIRECTORY, S_PROJECT_NAME,
        S_FILE_NAME,
    )


@pytest.mark.falsification
def testAnExistingProjectFileIsNeverOverwritten():
    """A retry must not discard the steps added since the first adoption.

    An agent that cannot see host-side state retries, and adoption is
    advertised as idempotent -- so the second call arrives at a project
    file that now holds seven steps. Rewriting the blank template over
    it would destroy an hour of work and report success.

    Kills: removing the "already present" early return from
    ``_fbWriteProjectFileIfAbsent``, so every adoption writes the blank
    template.
    """
    connectionFake = FakeConnection(
        setDirectories={S_REPOSITORY},
        dictRepositoryRoots={S_REPOSITORY: S_REPOSITORY},
        setReposWithCommits={S_REPOSITORY},
        setFiles={S_PROJECT_PATH},
    )
    dictReport = _fdictAdopt(connectionFake)
    assert S_PROJECT_PATH not in connectionFake.dictWritten, (
        "adoption overwrote an existing project file"
    )
    assert dictReport["bProjectIsNew"] is False


@pytest.mark.falsification
def testAMissingDirectoryIsRefusedRatherThanCreated():
    """A mistyped directory name must not produce an empty project.

    If adoption created what it could not find, ``yeilds`` would become
    a second project beside ``yields`` and both would look legitimate
    in the Project field. The researcher would have no way to tell
    which one their agent had been working in.

    Kills: replacing the missing-directory refusal with a ``mkdir -p``
    of the target, so adoption creates the directory it was asked to
    adopt.
    """
    connectionFake = FakeConnection()
    with pytest.raises(HTTPException) as recordRaised:
        _fdictAdopt(connectionFake)
    assert recordRaised.value.status_code == 404
    assert recordRaised.value.detail["sRefusal"] == (
        "adoption-directory-missing"
    )
    assert not connectionFake.dictWritten
    assert not any(
        "mkdir" in sCommand for sCommand in connectionFake.saCommands
    ), "adoption created the directory it was asked to adopt"


@pytest.mark.falsification
def testAdoptingASubdirectoryOfARepositoryIsRefused():
    """Nesting would repoint the project root without saying so.

    A project's repository is detected from its ``project.json``
    upwards, so adopting ``outer/inner`` makes ``outer`` the project
    repository: every declared path then resolves from a root the
    researcher never named, and the failure surfaces much later as
    missing outputs.

    Kills: dropping the enclosing-repository check from
    ``_fbCreateRepositoryIfAbsent``, so ``git init`` runs inside
    another work tree.
    """
    connectionFake = FakeConnection(
        setDirectories={S_REPOSITORY},
        dictRepositoryRoots={S_REPOSITORY: S_ROOT + "/outerRepo"},
    )
    with pytest.raises(HTTPException) as recordRaised:
        _fdictAdopt(connectionFake)
    assert recordRaised.value.detail["sRefusal"] == (
        "adoption-directory-inside-another-repository"
    )
    assert not any(
        " init" in sCommand for sCommand in connectionFake.saCommands
    ), "a repository was initialized inside another one"


@pytest.mark.falsification
def testARepositoryThatAlreadyHasCommitsGainsNoNewOne():
    """Adoption must add nothing to a history the researcher already has.

    The empty initial commit exists only so a repository with NO
    commits can report ``SOURCE_DATE_EPOCH``. Firing it unconditionally
    would drop a vaibify-authored commit into the middle of the
    researcher's own history, on every adoption and every retry.

    Kills: removing the resolved-HEAD early return from
    ``_fbCreateInitialCommitIfUnborn``, so every adoption commits.
    """
    connectionFake = FakeConnection(
        setDirectories={S_REPOSITORY},
        dictRepositoryRoots={S_REPOSITORY: S_REPOSITORY},
        setReposWithCommits={S_REPOSITORY},
    )
    dictReport = _fdictAdopt(connectionFake)
    assert projectAdoption.S_STAGE_CREATED_INITIAL_COMMIT in (
        dictReport["saStagesAlreadySatisfied"]
    )
    assert not any(
        " commit " in sCommand
        for sCommand in connectionFake.saCommands
    ), "adoption committed into an existing history"


@pytest.mark.falsification
def testAdoptionIssuesNoHistoryRewritingCommand():
    """No stage may rewrite commit ids, on any path through adoption.

    Grafting an empty root commit beneath existing history was the
    repair an in-container agent performed by hand, and it buys
    nothing: measured in a real container, every git operation vaibify
    performs already works on a root commit that carries content. On a
    repository with a remote, that graft is a rewrite nobody asked for.

    Kills: making ``_fbCreateInitialCommitIfUnborn`` graft an empty
    root beneath existing history with ``rebase --onto``.
    """
    connectionFake = FakeConnection(
        setDirectories={S_REPOSITORY},
        dictRepositoryRoots={S_REPOSITORY: S_REPOSITORY},
        setReposWithCommits={S_REPOSITORY},
    )
    _fdictAdopt(connectionFake)
    sIssued = " ".join(connectionFake.saCommands)
    for sForbidden in [
        "rebase", "--orphan", "filter-branch", "commit-tree",
        "update-ref", "reset --hard", "push",
    ]:
        assert sForbidden not in sIssued, (
            f"adoption issued a history-rewriting command: {sForbidden}"
        )


@pytest.mark.falsification
def testEveryAdoptionRefusalCarriesARemedy():
    """A refusal an agent cannot act on is one it works around.

    The in-container agent sees no dashboard, no Repos panel and no
    Project field. Told only "no project is open", it spent an hour
    reimplementing the backend in the shell. Every refusal here
    therefore names the next action, and the response shape is what
    makes that checkable.

    Kills: dropping ``sRemedy`` from the refusal body built by
    ``fnRefuseAdoption``.
    """
    with pytest.raises(HTTPException) as recordRaised:
        projectAdoption.fsValidateProjectDirectoryName("one/two")
    dictDetail = recordRaised.value.detail
    assert set(dictDetail) == {"sMessage", "sRemedy", "sRefusal"}
    assert dictDetail["sRemedy"].strip(), "the remedy is empty"


@pytest.mark.falsification
def testTheWrittenProjectFileCarriesItsOwnName():
    """A project with no recorded name displays its file name instead.

    Every hand-authored project observed in the field displayed its own
    file name in the Project field, because the template they were
    copied from omitted ``sWorkflowName`` -- so the omission propagated
    from one project to the next. Adoption is the lane that stops it.

    Kills: removing ``sWorkflowName`` from the body
    ``_fdictBlankProjectContent`` returns.
    """
    connectionFake = FakeConnection(setDirectories={S_REPOSITORY})
    _fdictAdopt(connectionFake)
    dictWritten = json.loads(
        connectionFake.dictWritten[S_PROJECT_PATH].decode("utf-8"),
    )
    assert dictWritten.get("sWorkflowName") == S_PROJECT_NAME


@pytest.mark.falsification
def testARetryIsNotMistakenForADuplicateName(
    fixtureStubTheSidecarAndTheSearch,  # noqa: F811
):
    """The name check must exempt the project file being adopted.

    Without the exemption, the second call refuses with
    ``adoption-project-name-taken`` against the project it created
    itself -- and to the caller that is indistinguishable from a real
    collision with somebody else's project, which is the shape that
    turns a safe retry into an apparent conflict.

    Kills: removing the same-path exemption from
    ``_fnRequireProjectNameFree``.
    """
    fixtureStubTheSidecarAndTheSearch["listProjects"] = [
        {"sName": S_PROJECT_NAME, "sPath": S_PROJECT_PATH},
    ]
    dictReport = _fdictAdopt(FakeConnection(
        setDirectories={S_REPOSITORY},
        dictRepositoryRoots={S_REPOSITORY: S_REPOSITORY},
        setReposWithCommits={S_REPOSITORY},
        setFiles={S_PROJECT_PATH},
    ))
    assert dictReport["bProjectIsNew"] is False


@pytest.mark.falsification
def testAnOmittedFileNameStillWritesARealProjectFile(
    fixtureStubTheSidecarAndTheSearch,  # noqa: F811
):
    """An absent file name must be resolved inside adoption, not outside it.

    Found by hand, not by this suite. The file-name rule lived in the
    route, so calling the module directly with ``sFileName=""`` joined
    a path ending in ``/``; the "is it already there" probe then
    matched the PROJECTS DIRECTORY, and adoption answered
    ``wrote-project-file`` already-satisfied with overall success for a
    project that did not exist. A green response for work not done is
    the one failure mode this repository treats as unacceptable, so the
    resolution moved into the single entry point.

    Kills: making ``fdictAdoptDirectoryAsProject`` use its
    ``sFileName`` argument verbatim instead of resolving it.
    """
    connectionFake = FakeConnection(setDirectories={S_REPOSITORY})
    dictReport = projectAdoption.fdictAdoptDirectoryAsProject(
        connectionFake, S_CONTAINER, S_DIRECTORY, S_PROJECT_NAME, "",
    )
    assert dictReport["sProjectPath"].endswith(".json"), (
        f"resolved to a directory-shaped path: "
        f"{dictReport['sProjectPath']}"
    )
    assert dictReport["bProjectIsNew"] is True
    assert projectAdoption.S_STAGE_WROTE_PROJECT_FILE in (
        dictReport["saStagesPerformed"]
    )
    baWritten = connectionFake.dictWritten[dictReport["sProjectPath"]]
    assert json.loads(baWritten.decode("utf-8"))["sWorkflowName"] == (
        S_PROJECT_NAME
    )
