"""Does the dashboard notice that vaibify.yml moved under a built image?

A researcher edited their configuration, restarted the container, and
met the same three warnings, because everything they edited is baked in
at build time and nothing said so. These tests pin the two halves of
the answer: which fields the fingerprint covers, and the three-state
rule that keeps an unlabelled image from being reported as drifted.
"""

import re

import pytest

from vaibify.config.configurationFingerprint import (
    S_CONFIGURATION_IMAGE_LABEL,
    T_BAKED_CONFIGURATION_FIELDS,
    fdictCompareConfigurationAgainstImage,
    flistDescribeConfigurationDrift,
    fsComputeConfigurationFingerprint,
)
from vaibify.config.projectConfig import ProjectConfig


def _fconfigBuildConfiguration(**dictOverrides):
    """Return a project configuration with the given fields overridden."""
    config = ProjectConfig(sProjectName="probe")
    for sField, jsonValue in dictOverrides.items():
        setattr(config, sField, jsonValue)
    return config


def testTheFingerprintIsStableAcrossEqualConfigurations():
    """Two configurations with the same baked fields hash the same."""
    assert fsComputeConfigurationFingerprint(
        _fconfigBuildConfiguration(listPythonPackages=["numpy", "scipy"]),
    ) == fsComputeConfigurationFingerprint(
        _fconfigBuildConfiguration(listPythonPackages=["numpy", "scipy"]),
    )


def testPackageOrderIsPartOfTheFingerprint():
    """Reordering an install list is a different build, not the same one."""
    assert fsComputeConfigurationFingerprint(
        _fconfigBuildConfiguration(listSystemPackages=["gcc", "make"]),
    ) != fsComputeConfigurationFingerprint(
        _fconfigBuildConfiguration(listSystemPackages=["make", "gcc"]),
    )


def testAFieldTheConfigurationDoesNotCarryIsSkippedNotDefaulted():
    """An older configuration object must not read as having empty fields."""

    class ConfigWithoutBinaries:
        sContainerUser = "researcher"
        sPythonVersion = "3.12"

    sPartial = fsComputeConfigurationFingerprint(ConfigWithoutBinaries())
    assert isinstance(sPartial, str) and len(sPartial) == 64


@pytest.mark.parametrize("sField", [
    "listRepositories", "listSystemPackages", "listPythonPackages",
    "sPythonVersion", "sBaseImage", "sContainerUser",
])
def testEveryBakedFieldMovesTheFingerprint(sField):
    """Each field the build reads must change the answer when it changes."""
    configBefore = _fconfigBuildConfiguration()
    jsonBefore = getattr(configBefore, sField)
    jsonAfter = (
        jsonBefore + [{"name": "extra"}] if isinstance(jsonBefore, list)
        else jsonBefore + "-changed"
    )
    assert fsComputeConfigurationFingerprint(configBefore) != (
        fsComputeConfigurationFingerprint(
            _fconfigBuildConfiguration(**{sField: jsonAfter}),
        )
    )


@pytest.mark.falsification
def testRuntimeOnlyFieldsNeverDemandARebuild():
    """Ports, mounts, secrets and limits apply at start, so they are excluded.

    The independent oracle is the Dockerfile's own build arguments and
    ``docker run``'s: a port publish, a bind mount, a secret mount, the
    network-isolation flag and the CPU/memory ceilings are all applied
    when the container STARTS. A fingerprint that covered them would
    send a researcher through an hour-long rebuild to publish a port —
    which is how a true warning becomes one people learn to ignore.

    Kills: adding a run-time field to
    ``configurationFingerprint.T_BAKED_CONFIGURATION_FIELDS``.
    """
    configBefore = _fconfigBuildConfiguration()
    sBefore = fsComputeConfigurationFingerprint(configBefore)
    for sField, jsonChanged in (
        ("listPorts", [{"iHostPort": 8888, "iContainerPort": 8888}]),
        ("listBindMounts", [{"sSource": "/tmp/x", "sTarget": "/x"}]),
        ("listSecrets", [{"sName": "token", "sMethod": "gh"}]),
        ("bNetworkIsolation", True),
        ("iCpuLimit", 4),
        ("fMemoryLimitGigabytes", 8.0),
    ):
        assert fsComputeConfigurationFingerprint(
            _fconfigBuildConfiguration(**{sField: jsonChanged}),
        ) == sBefore, (
            f"{sField} is applied by docker run, so changing it must not "
            "demand a rebuild"
        )


@pytest.mark.falsification
def testAnUnlabelledImageIsNeverReportedAsDrifted():
    """No label means nothing determined, and no surface may warn from it.

    Independent oracle: the three-state drift convention this codebase
    already states for ``environmentDrift`` — True is a difference,
    False is sameness, ``None`` is an absence of evidence. An image
    built before configuration labelling carries no label, and calling
    that drift would tell every researcher with an older image to spend
    an hour rebuilding on the strength of a missing string.

    Kills: treating an empty stamped fingerprint as a mismatch in
    ``configurationFingerprint.fdictCompareConfigurationAgainstImage``.
    """
    config = _fconfigBuildConfiguration()
    dictUnlabelled = fdictCompareConfigurationAgainstImage("", config)
    assert dictUnlabelled["bConfigurationChanged"] is None
    assert flistDescribeConfigurationDrift(dictUnlabelled) == []
    dictMatched = fdictCompareConfigurationAgainstImage(
        fsComputeConfigurationFingerprint(config), config,
    )
    assert dictMatched["bConfigurationChanged"] is False
    assert flistDescribeConfigurationDrift(dictMatched) == []
    dictDrifted = fdictCompareConfigurationAgainstImage("0" * 64, config)
    assert dictDrifted["bConfigurationChanged"] is True
    listLines = flistDescribeConfigurationDrift(dictDrifted)
    assert listLines, "a real drift must be described"
    assert any("Rebuild" in sLine for sLine in listLines), (
        "a drift warning must name its remedy"
    )


def testTheBuildStampsTheConfigurationLabel():
    """The base build carries the label the dashboard later reads."""
    from vaibify.docker import imageBuilder
    config = _fconfigBuildConfiguration()
    listArguments = imageBuilder._flistConfigurationLabelArguments(config)
    assert listArguments[0] == "--label"
    assert listArguments[1] == (
        f"{S_CONFIGURATION_IMAGE_LABEL}="
        + fsComputeConfigurationFingerprint(config)
    )


def testEveryDeclaredBakedFieldExistsOnTheConfiguration():
    """A field name that has been renamed away must fail loudly here."""
    config = ProjectConfig()
    listMissing = [
        sField for sField in T_BAKED_CONFIGURATION_FIELDS
        if not hasattr(config, sField)
    ]
    assert not listMissing, (
        "T_BAKED_CONFIGURATION_FIELDS names fields ProjectConfig does not "
        f"carry, so they silently stopped being hashed: {listMissing}"
    )


def _fsExtractFunctionBody(sSource, sFunctionName):
    """Return the text of one JavaScript function, brace-matched."""
    iStart = sSource.index("function " + sFunctionName)
    iBrace = sSource.index("{", iStart)
    iDepth = 0
    for iIndex in range(iBrace, len(sSource)):
        if sSource[iIndex] == "{":
            iDepth += 1
        elif sSource[iIndex] == "}":
            iDepth -= 1
            if iDepth == 0:
                return sSource[iStart:iIndex + 1]
    raise AssertionError(f"unbalanced braces in {sFunctionName}")


@pytest.mark.falsification
def testTheBannerRendersTheServersVerdictAndNothingElse():
    """The dashboard may not decide for itself that a container drifted.

    The independent oracle is this repository's own standing rule: a
    requirement row renders the gate's VERDICT and never re-derives it,
    because a mirrored predicate is a second authority on a question
    that has one. The server owns which fields are baked and what a
    missing label means; the banner owns only whether to show the
    sentences it was handed.

    Kills: rendering the drift banner from the presence of the payload
    key rather than from the lines in it — which paints a warning on
    every container whose configuration could not be judged.
    """
    import pathlib
    sScript = pathlib.Path(
        "vaibify/gui/static/scriptContainerManager.js",
    ).read_text(encoding="utf-8")
    sBody = _fsExtractFunctionBody(
        sScript, "_fnRenderConfigurationDriftBanner",
    )
    assert "listLines.length === 0" in sBody, (
        "an empty list is an absence of evidence and must hide the "
        "banner, not render an empty one"
    )
    # Comments may explain the server's rule; CODE may not restate it.
    sCode = re.sub(r"/\*.*?\*/", "", sBody, flags=re.S)
    for sForbidden in ("vaibify.yml has changed", "Rebuild the environment"):
        assert sForbidden not in sCode, (
            f"{sForbidden!r} belongs to the server's verdict; a copy "
            "here is a second authority on drift"
        )
    assert "listConfigurationDrift" in sScript, (
        "the banner must be fed from the readiness payload's own key"
    )


def testTheReadinessPayloadKeepsDriftOutOfTheStartWarnings():
    """A file edited since the start is not a warning the start produced."""
    import pathlib
    sRoutes = pathlib.Path(
        "vaibify/gui/routes/systemRoutes.py",
    ).read_text(encoding="utf-8")
    iAssignment = sRoutes.index('dictReadiness["listConfigurationDrift"]')
    assert 'saWarnings' not in sRoutes[iAssignment:iAssignment + 200], (
        "drift must ride its own key; the warnings list is headed "
        "\"from the most recent container start\" and this is not"
    )
