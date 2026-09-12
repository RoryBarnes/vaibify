"""A rebuild that moved the environment says so, and names the cause.

The comparison is three-state like every other envelope comparison in
this package, and the third state is the one that goes wrong quietly:
an undetermined answer rendered as a warning is a claim about an image
nobody compared. Each state has a test, and so does the distinction
that makes the warning worth reading -- "the recipe changed" versus
"the recipe did not change and the image did anyway", which is the only
signal a researcher gets that their Linux distribution moved underneath
them.

The build-path tests pin the ORDERING separately. The pin must be read
before the build, because fnRecordBaseImageDigestIfFloating rewrites
environment.json afterwards; a read on the far side compares the new
environment against itself and can never report drift.
"""

from unittest import mock

import pytest

from vaibify.reproducibility.environmentDrift import (
    S_DRIFT_OUTSIDE_THE_RECIPE,
    S_DRIFT_RECIPE_CHANGED,
    S_DRIFT_UNCHANGED,
    S_DRIFT_UNDETERMINED,
    fdictCompareRebuiltEnvironment,
    flistDescribeEnvironmentDrift,
)


__all__ = [
    "testAnUnchangedImageReportsNoDrift",
    "testAChangedImageWithAnUnchangedRecipeBlamesTheDistribution",
    "testAChangedImageWithAChangedRecipeBlamesTheRecipe",
    "testAMissingFingerprintIsNeverReadAsAHeldRecipe",
    "testAnAbsentPinIsUndeterminedNotChanged",
    "testAnUndeterminedComparisonProducesNoWarning",
    "testAnUnchangedEnvironmentProducesNoWarning",
    "testTheWarningNamesBothDigests",
    "testTheBuildWarnsWhenTheEnvironmentMoved",
    "testTheBuildIsSilentWithNoRecordedPin",
    "testThePinIsReadBeforeTheBuildRewritesIt",
]


S_RECORDED = "sha256:" + "aa" * 32
S_REBUILT = "sha256:" + "bb" * 32
S_RECIPE_ONE = "11" * 32
S_RECIPE_TWO = "22" * 32


def testAnUnchangedImageReportsNoDrift():
    dictAnswer = fdictCompareRebuiltEnvironment(
        S_RECORDED, S_RECIPE_ONE, S_RECORDED, S_RECIPE_ONE,
    )
    assert dictAnswer["bEnvironmentChanged"] is False
    assert dictAnswer["sCause"] == S_DRIFT_UNCHANGED


def testAChangedImageWithAnUnchangedRecipeBlamesTheDistribution():
    """The case a researcher cannot otherwise see."""
    dictAnswer = fdictCompareRebuiltEnvironment(
        S_RECORDED, S_RECIPE_ONE, S_REBUILT, S_RECIPE_ONE,
    )
    assert dictAnswer["bEnvironmentChanged"] is True
    assert dictAnswer["sCause"] == S_DRIFT_OUTSIDE_THE_RECIPE
    sMessage = "\n".join(flistDescribeEnvironmentDrift(dictAnswer))
    assert "did NOT change" in sMessage
    assert "distribution" in sMessage


def testAChangedImageWithAChangedRecipeBlamesTheRecipe():
    dictAnswer = fdictCompareRebuiltEnvironment(
        S_RECORDED, S_RECIPE_ONE, S_REBUILT, S_RECIPE_TWO,
    )
    assert dictAnswer["sCause"] == S_DRIFT_RECIPE_CHANGED
    sMessage = "\n".join(flistDescribeEnvironmentDrift(dictAnswer))
    assert "upgrade" in sMessage


@pytest.mark.parametrize("sPinnedRecipe,sBuiltRecipe", [
    ("", S_RECIPE_ONE),
    (S_RECIPE_ONE, ""),
    ("", ""),
])
def testAMissingFingerprintIsNeverReadAsAHeldRecipe(
    sPinnedRecipe, sBuiltRecipe,
):
    """An image predating the label is not evidence the recipe held.
    Reading '' == '' as agreement would blame the distribution for
    every upgrade from a pre-label image."""
    dictAnswer = fdictCompareRebuiltEnvironment(
        S_RECORDED, sPinnedRecipe, S_REBUILT, sBuiltRecipe,
    )
    assert dictAnswer["bEnvironmentChanged"] is True
    assert dictAnswer["sCause"] == S_DRIFT_UNDETERMINED


@pytest.mark.parametrize("sPinned,sBuilt", [
    ("", S_REBUILT),
    (S_RECORDED, ""),
    ("", ""),
])
def testAnAbsentPinIsUndeterminedNotChanged(sPinned, sBuilt):
    dictAnswer = fdictCompareRebuiltEnvironment(
        sPinned, S_RECIPE_ONE, sBuilt, S_RECIPE_ONE,
    )
    assert dictAnswer["bEnvironmentChanged"] is None
    assert dictAnswer["sCause"] == S_DRIFT_UNDETERMINED


def testAnUndeterminedComparisonProducesNoWarning():
    assert flistDescribeEnvironmentDrift(
        fdictCompareRebuiltEnvironment("", "", S_REBUILT, ""),
    ) == []


def testAnUnchangedEnvironmentProducesNoWarning():
    assert flistDescribeEnvironmentDrift(
        fdictCompareRebuiltEnvironment(
            S_RECORDED, S_RECIPE_ONE, S_RECORDED, S_RECIPE_ONE,
        ),
    ) == []


def testTheWarningNamesBothDigests():
    """A researcher must be able to tell which image is which."""
    sMessage = "\n".join(flistDescribeEnvironmentDrift(
        fdictCompareRebuiltEnvironment(
            S_RECORDED, S_RECIPE_ONE, S_REBUILT, S_RECIPE_TWO,
        ),
    ))
    assert S_RECORDED in sMessage
    assert S_REBUILT in sMessage
    assert "Re-run and re-verify" in sMessage


class _ConfigStub:
    sProjectName = "someproject"


def _fnDriveTheWarning(tPinnedBefore, sBuiltDigest, sBuiltRecipe):
    """Run the build-path warning against stubbed daemon reads."""
    from vaibify.cli import commandBuild

    listEchoed = []
    with mock.patch.object(
        commandBuild.click, "echo", listEchoed.append,
    ), mock.patch(
        "vaibify.reproducibility.environmentSnapshot"
        ".fdictCaptureBuiltImageIdentity",
        return_value={"sImageDigest": sBuiltDigest, "sImageId": ""},
    ), mock.patch(
        "vaibify.reproducibility.environmentSnapshot.fsReadImageRecipeLabel",
        return_value=sBuiltRecipe,
    ):
        commandBuild.fnWarnIfRebuildChangedEnvironment(
            _ConfigStub(), tPinnedBefore,
        )
    return "\n".join(str(sLine) for sLine in listEchoed)


def testTheBuildWarnsWhenTheEnvironmentMoved():
    sOutput = _fnDriveTheWarning(
        (S_RECORDED, S_RECIPE_ONE), S_REBUILT, S_RECIPE_ONE,
    )
    assert "The environment changed" in sOutput
    assert S_REBUILT in sOutput


def testTheBuildIsSilentWithNoRecordedPin():
    """A project with no envelope yet must not be warned at every build."""
    assert _fnDriveTheWarning(("", ""), S_REBUILT, S_RECIPE_ONE) == ""


def testThePinIsReadBeforeTheBuildRewritesIt():
    """fnRecordBaseImageDigestIfFloating rewrites environment.json after
    the build. Reading the pin on the far side of it compares the new
    environment against itself, and drift becomes unreportable."""
    from vaibify.cli import commandBuild

    listOrder = []
    with mock.patch.object(
        commandBuild, "_ftReadPinnedEnvironment",
        side_effect=lambda: (listOrder.append("read-pin"), ("", ""))[1],
    ), mock.patch.object(
        commandBuild, "fnRecordBaseImageDigestIfFloating",
        side_effect=lambda config: listOrder.append("rewrite-envelope"),
    ), mock.patch.object(
        commandBuild, "_ffnImportBuildOrExit",
        return_value=lambda *a, **kw: listOrder.append("build"),
    ), mock.patch.object(
        commandBuild, "fsStageBuildContext", return_value="/tmp/stage",
    ), mock.patch.object(
        commandBuild, "fnPrepareBuildContext",
    ), mock.patch.object(
        commandBuild, "fnDiscardBuildContext",
    ), mock.patch.object(
        commandBuild, "fnWarnIfBaseImageFloating",
    ), mock.patch.object(
        commandBuild, "fnRecordBuildArgHash",
    ), mock.patch.object(
        commandBuild, "fnWarnIfRebuildChangedEnvironment",
    ), mock.patch.object(
        commandBuild, "fnPruneDanglingImages",
    ), mock.patch.object(
        commandBuild, "_fbResolveNoCache", return_value=False,
    ):
        commandBuild.fnBuildFromConfig(_ConfigStub(), "/tmp/ctx", False)

    assert listOrder.index("read-pin") < listOrder.index("build")
    assert listOrder.index("read-pin") < listOrder.index("rewrite-envelope")
