"""A configured secret this host cannot resolve is reported, not fatal.

Three failure modes, one shape. ``gh_auth`` needs the GitHub CLI
installed and logged in, ``keyring`` needs the package plus an entry,
``docker_secret`` needs a file under ``/run/secrets`` — and none of the
three travels with the repository. Every one of them used to raise
inside ``fnMountSecrets``, which runs BEFORE ``docker run`` is
composed, so no container was created and there was nothing to inspect
afterwards. A researcher moving a project to a second machine ticked a
wizard toggle and, hours later, met a ``RuntimeError`` naming neither
the secret nor the remedy, and reasonably concluded the container was
dead (2026-09-05).

The ruling was to correct the CHECK rather than the Features page,
whose promise — "the container will still work but git push will fail"
— becomes true. Degrading is only defensible because the researcher is
TOLD: a silent degrade trades an early clear refusal for a late
obscure one (a step failing on an unauthenticated push, minutes in) and
would be strictly worse than the refusal it replaces. So these tests
assert both halves — the start proceeds, AND the notice names the
secret, its method, its cost and its remedy.

Nothing here may materialize a secret. ``fbSecretExists`` is written
not to, and a preflight that wrote a token to a temp file to prove it
exists would have widened the attack surface to answer a question.
"""

from unittest.mock import patch

from vaibify.config.secretAvailability import (
    flistFindUnresolvableSecrets,
    fsDescribeUnresolvableSecret,
)


_LIST_ALL_THREE_METHODS = [
    {"name": "gh_token", "method": "gh_auth"},
    {"name": "apiKey", "method": "keyring"},
    {"name": "dbPassword", "method": "docker_secret"},
]


def _fnStubProbe(fbAnswer):
    """Patch the availability probe with a caller-supplied answer."""
    return patch(
        "vaibify.config.secretManager.fbSecretExists",
        side_effect=fbAnswer,
    )


def test_every_method_gets_its_own_remedy():
    """"A secret is unavailable" names none of the three fixes.

    The remedy for a missing ``gh`` login is not the remedy for a
    missing keyring entry, and a report that collapses them sends the
    researcher looking in the wrong place. This asserts the remedies
    are DISTINCT rather than merely present, because a single shared
    string would satisfy "a remedy exists" while helping nobody.
    """
    with _fnStubProbe(lambda sName, sMethod: False):
        listRecords = flistFindUnresolvableSecrets(_LIST_ALL_THREE_METHODS)
    assert len(listRecords) == 3
    setRemedies = {dictRecord["sRemedy"] for dictRecord in listRecords}
    assert len(setRemedies) == 3
    for dictRecord in listRecords:
        assert dictRecord["sReason"]
        assert dictRecord["sCost"]


def test_a_resolvable_secret_is_not_reported():
    with _fnStubProbe(lambda sName, sMethod: True):
        assert flistFindUnresolvableSecrets(_LIST_ALL_THREE_METHODS) == []


def test_only_the_unresolvable_ones_are_reported():
    """The report is per SECRET, not per project.

    A project with a working keyring entry and no ``gh`` login has one
    problem, and telling it about two would train it to ignore the
    notice.
    """
    with _fnStubProbe(lambda sName, sMethod: sMethod != "gh_auth"):
        listRecords = flistFindUnresolvableSecrets(_LIST_ALL_THREE_METHODS)
    assert [dictRecord["sName"] for dictRecord in listRecords] == [
        "gh_token",
    ]


def test_the_github_token_cost_names_what_stops_working():
    """The cost is the actionable half, and it differs by secret.

    The entrypoint reads the GitHub token from
    ``/run/secrets/gh_token``; without it the container is
    public-repos-only. A researcher who reads only the cost line has to
    be able to decide whether to proceed.
    """
    with _fnStubProbe(lambda sName, sMethod: False):
        listRecords = flistFindUnresolvableSecrets([
            {"name": "gh_token", "method": "gh_auth"},
            {"name": "somethingElse", "method": "gh_auth"},
        ])
    assert "push" in listRecords[0]["sCost"]
    assert "/run/secrets/somethingElse" in listRecords[1]["sCost"]


def test_an_unsupported_method_is_reported_not_raised():
    """A bad method belongs in the report, beside the other answers.

    ``fbSecretExists`` validates its arguments and raises ``ValueError``
    on a method it does not know. Letting that out of a readiness check
    takes the whole report down over one malformed entry — the failure
    mode this module exists to remove, one level up.
    """
    listRecords = flistFindUnresolvableSecrets([
        {"name": "token", "method": "carrierPigeon"},
    ])
    assert len(listRecords) == 1
    assert "carrierPigeon" in listRecords[0]["sReason"]


def test_the_rendered_line_carries_the_remedy():
    with _fnStubProbe(lambda sName, sMethod: False):
        listRecords = flistFindUnresolvableSecrets([
            {"name": "gh_token", "method": "gh_auth"},
        ])
    sLine = fsDescribeUnresolvableSecret(listRecords[0])
    assert "gh_token" in sLine
    assert "gh auth login" in sLine


# ------------------------------------------------------------------
# The start no longer refuses (ruled 2026-09-05)
# ------------------------------------------------------------------


def _fconfigWithSecrets(listSecrets):
    """Return the smallest object the mount path reads."""
    class _ConfigStub:
        sProjectName = "probeProject"

    configStub = _ConfigStub()
    configStub.listSecrets = listSecrets
    return configStub


def test_an_unresolvable_secret_no_longer_stops_the_start():
    """The container starts, minus that one mount.

    The mount helper is given a side effect that RAISES, which is what
    the real one does for all three methods. Reaching it at all is the
    regression: the probe is supposed to have removed that secret from
    the loop before anything tries to retrieve it.
    """
    from vaibify.docker.containerManager import flistMountSecrets

    def _fnRefuseToMount(sName, sMethod):
        raise RuntimeError("gh auth token failed")

    saRunArgs = []
    with _fnStubProbe(lambda sName, sMethod: False), patch(
        "vaibify.config.secretManager.fsMountSecret",
        side_effect=_fnRefuseToMount,
    ):
        listUnresolvable = flistMountSecrets(
            _fconfigWithSecrets([{"name": "gh_token", "method": "gh_auth"}]),
            saRunArgs, [],
        )
    assert saRunArgs == []
    assert [d["sName"] for d in listUnresolvable] == ["gh_token"]


def test_the_resolvable_secrets_are_still_mounted():
    """One unresolvable secret must not cost the project the others.

    Driven with two secrets whose names differ from each other and from
    their methods, so a filter keyed on the wrong field cannot pass.
    """
    from vaibify.docker.containerManager import flistMountSecrets

    saRunArgs = []
    listCleanup = []
    with _fnStubProbe(
        lambda sName, sMethod: sName == "apiKey",
    ), patch(
        "vaibify.config.secretManager.fsMountSecret",
        return_value="/tmp/probeSecretFile",
    ):
        listUnresolvable = flistMountSecrets(
            _fconfigWithSecrets([
                {"name": "gh_token", "method": "gh_auth"},
                {"name": "apiKey", "method": "keyring"},
            ]),
            saRunArgs, listCleanup,
        )
    assert any("/run/secrets/apiKey" in sArg for sArg in saRunArgs)
    assert not any("/run/secrets/gh_token" in sArg for sArg in saRunArgs)
    assert listCleanup == ["/tmp/probeSecretFile"]
    assert [d["sName"] for d in listUnresolvable] == ["gh_token"]


def test_the_start_says_which_secret_it_proceeded_without(caplog):
    """A degrade nobody is told about is worse than the old refusal."""
    import logging

    from vaibify.docker.containerManager import (
        fnAnnounceUnresolvableSecrets,
    )
    with _fnStubProbe(lambda sName, sMethod: False):
        listRecords = flistFindUnresolvableSecrets([
            {"name": "gh_token", "method": "gh_auth"},
        ])
    with caplog.at_level(logging.WARNING, logger="vaibify"):
        fnAnnounceUnresolvableSecrets(
            _fconfigWithSecrets([]), listRecords,
        )
    sLogged = caplog.text
    assert "gh_token" in sLogged
    assert "gh auth login" in sLogged


# ------------------------------------------------------------------
# The CLI preflight (the early warning)
# ------------------------------------------------------------------


def test_the_cli_preflight_warns_rather_than_failing():
    """Failing here would contradict the start a few lines later.

    ``_fnEnforcePreflightOrExit`` exits on any fail-level result, so a
    fail here would reinstate the refusal the ruling removed — in a
    different file, where nobody would look for it.
    """
    from vaibify.cli.commandStart import flistPreflightSecrets

    with _fnStubProbe(lambda sName, sMethod: False):
        listResults = flistPreflightSecrets(
            _fconfigWithSecrets([{"name": "gh_token", "method": "gh_auth"}]),
        )
    assert len(listResults) == 1
    assert listResults[0].sLevel == "warn"
    assert "gh_token" in listResults[0].sMessage
    assert "gh auth login" in listResults[0].sRemediation


def test_the_cli_preflight_is_silent_when_everything_resolves():
    from vaibify.cli.commandStart import flistPreflightSecrets

    with _fnStubProbe(lambda sName, sMethod: True):
        assert flistPreflightSecrets(
            _fconfigWithSecrets(_LIST_ALL_THREE_METHODS),
        ) == []


def test_the_start_preflight_includes_the_secret_checks():
    """The check has to be WIRED, not merely written.

    ``flistPreflightSecrets`` passing its own tests says nothing about
    whether ``vaibify start`` calls it, and an unwired check is exactly
    the shape of the defect being fixed.
    """
    from vaibify.cli import commandStart

    with patch.object(
        commandStart, "fpreflightDaemon",
        return_value=commandStart.PreflightResult(
            sName="docker-daemon", sLevel="ok", sMessage="",
        ),
    ), patch.object(
        commandStart, "flistPreflightSecrets", return_value=["sentinel"],
    ), patch.object(
        commandStart, "_fpreflightImage", return_value=None,
    ), patch.object(
        commandStart, "_flistPreflightPorts", return_value=[],
    ), patch.object(
        commandStart, "_fpreflightContainerName", return_value=None,
    ), patch.object(
        commandStart, "_flistPreflightBindMounts", return_value=[],
    ), patch.object(
        commandStart, "_flistPreflightBindMountFormats", return_value=[],
    ), patch.object(
        commandStart, "_flistPreflightColimaSharedRoots", return_value=[],
    ), patch.object(
        commandStart, "fpreflightColimaVersion", return_value=None,
    ):
        listResults = commandStart.flistRunStartPreflight(
            _fconfigWithSecrets([]),
        )
    assert "sentinel" in listResults


# ------------------------------------------------------------------
# The dashboard half (the persistent notice)
# ------------------------------------------------------------------


def test_the_readiness_payload_carries_the_secret_warnings():
    """The banner the dashboard already renders is where this belongs.

    ``saWarnings`` is rendered persistently as "from the most recent
    container start", which is exactly what an unresolvable secret is.
    A toast would disappear, and the whole premise of degrading rather
    than refusing is that the researcher keeps being told.
    """
    from vaibify.gui.routes import systemRoutes

    with patch.object(
        systemRoutes, "_fdictProbeContainerReadiness",
        return_value={
            "bReady": True, "sStatus": "ok", "sReason": "",
            "saWarnings": ["an entrypoint warning"], "iWarningCount": 1,
        },
    ), patch.object(
        systemRoutes, "_flistDescribeUnresolvableSecrets",
        return_value=["secret 'gh_token' cannot be resolved"],
    ):
        dictReadiness = systemRoutes._fdictReadinessWithSecretWarnings(
            None, "containerIdNotAName",
        )
    assert dictReadiness["saWarnings"] == [
        "an entrypoint warning",
        "secret 'gh_token' cannot be resolved",
    ]
    assert dictReadiness["iWarningCount"] == 2


def test_a_still_booting_container_is_not_probed_for_secrets():
    """Sixty polls must not become sixty subprocesses.

    The frontend polls readiness every two seconds for up to two
    minutes while a container boots. ``gh auth token`` is a subprocess;
    running it on each poll would turn a supplementary notice into a
    load problem, so it is computed on the settled answer only.
    """
    from vaibify.gui.routes import systemRoutes

    with patch.object(
        systemRoutes, "_fdictProbeContainerReadiness",
        return_value={
            "bReady": False, "sStatus": "booting", "sReason": "",
            "saWarnings": [], "iWarningCount": 0,
        },
    ), patch.object(
        systemRoutes, "_flistDescribeUnresolvableSecrets",
    ) as mockDescribe:
        systemRoutes._fdictReadinessWithSecretWarnings(
            None, "containerIdNotAName",
        )
    assert mockDescribe.call_count == 0


def test_a_failed_readiness_answer_still_gets_the_warnings():
    """A failed start is when the researcher most needs to know.

    ``bReady`` is False on a failed or stalled container, so gating on
    it alone would withhold the notice from precisely the case where an
    unresolvable secret is a plausible cause.
    """
    from vaibify.gui.routes import systemRoutes

    with patch.object(
        systemRoutes, "_fdictProbeContainerReadiness",
        return_value={
            "bReady": False, "sStatus": "failed", "sReason": "boom",
            "saWarnings": [], "iWarningCount": 0,
        },
    ), patch.object(
        systemRoutes, "_flistDescribeUnresolvableSecrets",
        return_value=["secret 'gh_token' cannot be resolved"],
    ):
        dictReadiness = systemRoutes._fdictReadinessWithSecretWarnings(
            None, "containerIdNotAName",
        )
    assert dictReadiness["iWarningCount"] == 1


def test_the_secret_lookup_resolves_the_container_id_to_a_name():
    """The registry is keyed by NAME; the route is handed an ID.

    This repository has already shipped an owner map written by name
    and read by id under a fully green suite, so the two are driven
    DISTINCT here: a lookup that passed the id straight through finds
    no project and silently reports no warnings, which reads exactly
    like a healthy host.
    """
    from vaibify.gui.routes import systemRoutes

    dictSeen = {}

    def _fdictRecordLookup(sKey):
        dictSeen["sKey"] = sKey
        return None

    with patch(
        "vaibify.gui.pipelineServer.fsContainerNameForId",
        return_value="probeProject",
    ), patch(
        "vaibify.config.registryManager.fdictGetProject",
        side_effect=_fdictRecordLookup,
    ):
        systemRoutes._flistDescribeUnresolvableSecrets(
            None, "0123456789abcdef",
        )
    assert dictSeen["sKey"] == "probeProject"
