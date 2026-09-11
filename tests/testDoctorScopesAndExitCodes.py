"""What `vaibify doctor` reports when it could not assess something.

A check that could not run must never be counted as a pass, and a
scope the researcher explicitly ASKED about must not exit 0 having
assessed none of it. This repository has shipped the opposite twice --
a CI step guarded by ``docker info || exit 0``, and falsification legs
that timed out for weeks -- and in both cases the lane was green for
having run nothing.

Exit 2 is deliberately scoped to what was REQUESTED. A researcher
running plain `vaibify doctor` on a laptop with no container running
should not get a non-zero exit for a scope they did not ask about;
one running `vaibify doctor --container` asked exactly that question
and got no answer.
"""

from unittest.mock import patch

import pytest

from vaibify.cli.commandDoctor import (
    I_EXIT_EVERYTHING_ASSESSED, I_EXIT_SCOPE_UNASSESSED,
    I_EXIT_SOMETHING_FAILED, _ftRequestedScopes, fiResolveDoctorExitCode,
    flistRunDoctorChecks,
)
from vaibify.cli.preflightResult import (
    S_LEVEL_FAIL, S_LEVEL_NOT_CHECKED, S_LEVEL_OK, S_SCOPE_CONTAINER,
    S_SCOPE_HOST, S_SCOPE_PROJECT, PreflightResult,
)


def _fresult(sLevel, sScope=S_SCOPE_HOST, sName="probe"):
    """Return one result at a level and scope."""
    return PreflightResult(
        sName=sName, sLevel=sLevel, sMessage="", sScope=sScope,
    )


def test_everything_assessed_and_passing_exits_zero():
    """The ordinary answer."""
    assert fiResolveDoctorExitCode(
        [_fresult(S_LEVEL_OK)], (S_SCOPE_HOST,),
    ) == I_EXIT_EVERYTHING_ASSESSED


def test_a_failure_exits_one():
    """A failure is a stronger statement than an absence."""
    assert fiResolveDoctorExitCode(
        [_fresult(S_LEVEL_FAIL)], (S_SCOPE_HOST,),
    ) == I_EXIT_SOMETHING_FAILED


def test_a_failure_outranks_an_unassessed_check():
    """A caller branching on non-zero gets the same answer either way."""
    assert fiResolveDoctorExitCode(
        [
            _fresult(S_LEVEL_FAIL),
            _fresult(S_LEVEL_NOT_CHECKED, S_SCOPE_CONTAINER),
        ],
        (S_SCOPE_CONTAINER,),
    ) == I_EXIT_SOMETHING_FAILED


@pytest.mark.falsification
def test_one_unassessed_check_among_passes_still_exits_two():
    """One unassessed probe among passes is still an unanswered scope.

    Kills: In commandDoctor.fiResolveDoctorExitCode, require ALL
    results to be unassessed rather than any, so a single timed-out
    probe surrounded by successes still exits 0 -- a skipped-green
    lane wearing a diagnostic's clothes.
    """
    assert fiResolveDoctorExitCode(
        [
            _fresult(S_LEVEL_OK, S_SCOPE_CONTAINER, "a"),
            _fresult(S_LEVEL_NOT_CHECKED, S_SCOPE_CONTAINER, "b"),
            _fresult(S_LEVEL_OK, S_SCOPE_CONTAINER, "c"),
        ],
        (S_SCOPE_CONTAINER,),
    ) == I_EXIT_SCOPE_UNASSESSED


def test_an_unrequested_scope_does_not_turn_a_run_non_zero():
    """Plain `vaibify doctor` asked nothing about the container."""
    assert fiResolveDoctorExitCode(
        [
            _fresult(S_LEVEL_OK),
            _fresult(S_LEVEL_NOT_CHECKED, S_SCOPE_CONTAINER),
        ],
        (),
    ) == I_EXIT_EVERYTHING_ASSESSED


def test_requesting_the_container_scope_requests_the_project_scope_too():
    """They are one question from the chair: "what is wrong with this"."""
    tScopes = _ftRequestedScopes(False, False, True)
    assert S_SCOPE_CONTAINER in tScopes
    assert S_SCOPE_PROJECT in tScopes


def test_an_unassessed_check_is_never_counted_as_ok():
    """The tally keeps them apart; so does the report's grouping."""
    from vaibify.cli.commandDoctor import _ftCountLevels
    iOk, iWarn, iFail, iNotChecked = _ftCountLevels([
        _fresult(S_LEVEL_OK), _fresult(S_LEVEL_NOT_CHECKED),
    ])
    assert (iOk, iWarn, iFail, iNotChecked) == (1, 0, 0, 1)


class _ConfigStub:
    sProjectName = "doctorScopeProbe"
    listPorts = []
    listBindMounts = []
    listSecrets = []
    listRepositories = []
    features = None
    sWorkspaceRoot = "/workspace"


def test_an_absent_container_makes_every_container_check_unassessed():
    """None of them may be silently dropped, which reads as nothing wrong."""
    with patch(
        "vaibify.cli.commandDoctor._flistSharedChecks", return_value=[],
    ), patch(
        "vaibify.cli.commandDoctor._fdictHostProjectOrNone",
        return_value=None,
    ), patch(
        "vaibify.docker.containerManager.fdictGetContainerStatus",
        return_value={
            "bExists": False, "bRunning": False, "sStatus": "not found",
        },
    ):
        listResults = flistRunDoctorChecks(
            _ConfigStub(), False, False, True, False,
        )
    listContainerScope = [
        r for r in listResults if r.sScope == S_SCOPE_CONTAINER
    ]
    assert listContainerScope
    assert all(
        r.sLevel == S_LEVEL_NOT_CHECKED for r in listContainerScope
    )
    assert fiResolveDoctorExitCode(
        listResults, _ftRequestedScopes(False, False, True),
    ) == I_EXIT_SCOPE_UNASSESSED
