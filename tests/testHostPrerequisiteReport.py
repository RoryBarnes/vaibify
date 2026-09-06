"""``vaibify doctor`` asks about the host-side prerequisites in advance.

Three of them reached a researcher one crash at a time in a single
session (2026-09-05), each surfacing late, in a different place, in a
different vocabulary: a Docker context pointing at a stopped runtime
reported as "install Docker" on a machine with Docker running; an
absent host ``gh`` reported as a container that would not start, hours
after a wizard toggle; and a missing council credential record reported
as a greyed button with a paragraph attached. They share no mechanism
and one shape -- something the MACHINE must provide, that the
repository cannot carry, that nothing checks until the moment it is
needed.

Two properties are load-bearing and easy to undo. The report must not
GATE: none of these is a fault, and a doctor that failed on an unusual
Docker context would put a red line in front of everyone running
rootless Docker. And it must never offer to SATISFY the council's
evidence record, whose whole purpose is that only the maintainer can
write one -- an offer there would defeat the gate rather than report
it.
"""

from unittest.mock import patch

from vaibify.cli import commandDoctor
from vaibify.cli import preflightChecks


def _fdictResultsByName(listResults):
    """Return the non-None results keyed by check name."""
    return {
        result.sName: result
        for result in listResults if result is not None
    }


def test_the_endpoint_is_reported_beside_the_context():
    """The context NAME does not say where the context points.

    A researcher whose context named a stopped runtime saw a healthy
    "Active Docker context: X" line and a daemon failure, and nothing
    connected the two. The endpoint is the fact that does.
    """
    with patch(
        "vaibify.docker.dockerContext.fsReadActiveContextEndpoint",
        return_value="unix:///run/probe/docker.sock",
    ), patch.dict("os.environ", {}, clear=False) as dictEnvironment:
        dictEnvironment.pop("DOCKER_HOST", None)
        resultEndpoint = preflightChecks.fpreflightDockerEndpoint()
    assert "unix:///run/probe/docker.sock" in resultEndpoint.sMessage
    assert resultEndpoint.sLevel == "info"


def test_an_explicit_docker_host_wins_and_says_so():
    """The two sources must be distinguishable in the report.

    "unix:///x" from the environment and "unix:///x" from the context
    are different situations with different fixes, and a line that
    prints only the value tells the researcher neither.
    """
    from vaibify.docker.dockerContext import fsResolveDockerEndpoint

    with patch.dict(
        "os.environ", {"DOCKER_HOST": "tcp://192.0.2.1:2376"},
    ), patch(
        "vaibify.docker.dockerContext.fsReadActiveContextEndpoint",
        return_value="unix:///run/context/docker.sock",
    ):
        sEndpoint = fsResolveDockerEndpoint()
    assert "tcp://192.0.2.1:2376" in sEndpoint
    assert "DOCKER_HOST" in sEndpoint
    assert "unix:///run/context/docker.sock" not in sEndpoint


def test_the_connection_and_the_report_read_the_same_endpoint():
    """One authority, or the report cannot be evidence about the connection.

    ``_fnEnsureDockerHost`` used to run ``docker context inspect``
    itself. A second copy in the reporting path could answer
    differently from the one the connection used, which would make the
    report worse than silence: it would name an endpoint nothing tried.
    """
    from vaibify.docker import dockerConnection

    with patch.dict("os.environ", {}, clear=False) as dictEnvironment:
        dictEnvironment.pop("DOCKER_HOST", None)
        with patch(
            "vaibify.docker.dockerContext.fsReadActiveContextEndpoint",
            return_value="unix:///run/probe/docker.sock",
        ):
            dockerConnection._fnEnsureDockerHost()
            sRecorded = dictEnvironment.get("DOCKER_HOST")
    assert sRecorded == "unix:///run/probe/docker.sock"


def test_the_council_evidence_is_reported_and_never_graded_a_failure():
    """A machine without the record is not a broken machine.

    The evidence record is written by the maintainer alone, after a
    live credential check on a paid account. Every other researcher's
    machine legitimately lacks it, so grading it would make the report
    fail for almost everyone who runs it -- and a report that always
    fails is a report nobody reads.
    """
    with patch(
        "vaibify.gui.agentCouncilCredentialGate."
        "fdictEvaluateCredentialEnablement",
        return_value={
            "bEnabled": False,
            "sReason": "no credential-verification evidence record exists",
            "dictRecord": None,
        },
    ):
        resultCouncil = preflightChecks.fpreflightCouncilCredentialEvidence()
    assert resultCouncil.sLevel == "info"
    assert "evidence record" in resultCouncil.sMessage
    assert not resultCouncil.sRemediation


def test_a_raising_credential_gate_does_not_take_the_report_down():
    """The gate refuses an unknown provider by raising.

    The council's providers are not the API-key providers, and the two
    sets have already been confused once. A readiness report is the
    wrong place to learn that by traceback, so the refusal is reported
    like any other reason.
    """
    with patch(
        "vaibify.gui.agentCouncilCredentialGate."
        "fdictEvaluateCredentialEnablement",
        side_effect=ValueError("provider 'x' has no reviewed adapter"),
    ):
        resultCouncil = preflightChecks.fpreflightCouncilCredentialEvidence()
    assert resultCouncil.sLevel == "info"
    assert "no reviewed adapter" in resultCouncil.sMessage


def test_doctor_reports_all_three_prerequisites_in_one_pass():
    """The point of the feature is that they arrive TOGETHER.

    Each of the three was individually discoverable; what cost the
    researcher an afternoon is that each was discovered separately, at
    the moment it was needed. A test that checked them one at a time
    would pass against exactly the state being fixed.
    """
    class _ConfigStub:
        sProjectName = "probeProject"
        listSecrets = [{"name": "gh_token", "method": "gh_auth"}]

    with patch.object(
        commandDoctor, "_fdictHostProjectOrNone", return_value=None,
    ), patch.object(
        commandDoctor, "_flistBuildOnlyChecks", return_value=[],
    ), patch(
        "vaibify.config.secretManager.fbSecretExists", return_value=False,
    ), patch(
        "vaibify.docker.dockerContext.fsReadActiveContextEndpoint",
        return_value="unix:///run/probe/docker.sock",
    ), patch(
        "vaibify.gui.agentCouncilCredentialGate."
        "fdictEvaluateCredentialEnablement",
        return_value={"bEnabled": False, "sReason": "no record"},
    ), patch(
        "vaibify.cli.commandStart._fpreflightImage", return_value=None,
    ), patch(
        "vaibify.cli.commandStart._flistPreflightPorts", return_value=[],
    ), patch(
        "vaibify.cli.commandStart._fpreflightContainerName",
        return_value=None,
    ), patch(
        "vaibify.cli.commandStart._flistPreflightBindMounts",
        return_value=[],
    ), patch(
        "vaibify.cli.commandStart._flistPreflightBindMountFormats",
        return_value=[],
    ), patch(
        "vaibify.cli.commandStart._flistPreflightColimaSharedRoots",
        return_value=[],
    ), patch.object(
        preflightChecks, "fpreflightDaemon",
        return_value=preflightChecks.PreflightResult(
            sName="docker-daemon", sLevel="ok", sMessage="reachable",
        ),
    ), patch.object(
        commandDoctor, "fpreflightDaemon",
        return_value=preflightChecks.PreflightResult(
            sName="docker-daemon", sLevel="ok", sMessage="reachable",
        ),
    ):
        listResults = commandDoctor.flistRunDoctorChecks(
            _ConfigStub(), False, False,
        )
    dictByName = _fdictResultsByName(listResults)
    assert "docker-endpoint" in dictByName
    assert "council-credentials" in dictByName
    assert "secret:gh_token" in dictByName
    assert dictByName["secret:gh_token"].sLevel == "warn"


def test_doctor_does_not_fail_on_any_of_the_three():
    """It REPORTS; it does not block.

    A fail-level result exits ``vaibify doctor`` non-zero. None of
    these three is a fault, and turning one into one would make the
    command unusable as the "is this machine ready?" surface it is.
    """
    with patch(
        "vaibify.docker.dockerContext.fsReadActiveContextEndpoint",
        return_value="",
    ), patch(
        "vaibify.gui.agentCouncilCredentialGate."
        "fdictEvaluateCredentialEnablement",
        return_value={"bEnabled": False, "sReason": "no record"},
    ), patch(
        "vaibify.config.secretManager.fbSecretExists", return_value=False,
    ):
        from vaibify.cli.commandStart import flistPreflightSecrets

        class _ConfigStub:
            sProjectName = "probeProject"
            listSecrets = [
                {"name": "gh_token", "method": "gh_auth"},
                {"name": "apiKey", "method": "keyring"},
            ]

        listResults = [
            preflightChecks.fpreflightDockerEndpoint(),
            preflightChecks.fpreflightCouncilCredentialEvidence(),
        ] + flistPreflightSecrets(_ConfigStub())
    assert [r for r in listResults if r.sLevel == "fail"] == []
