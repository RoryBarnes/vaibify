"""Probe matrix: which repository-local git mechanisms vaibify's host-side git runs.

Git can run programs that a repository itself configures: a filesystem
monitor, clean, smudge and process filters, text converters, external
diff drivers, pagers, hooks, credential helpers, ssh commands, URL
rewrites to the ``ext::`` transport, proxy commands and signing
programs. When the repository's ``.git`` directory or ``.gitattributes``
can come from a container or from a download, asking that repository a
question on the host can run somebody else's program as the researcher.

THIS FILE CHANGES NO BEHAVIOR. It documents the CURRENT behavior, so
every "runs" assertion below records a real exposure and the test of
that name is expected to FAIL (and be rewritten) the day the exposure
is closed. Each mechanism is a tiny shell script this suite writes that
appends to a marker file under a temporary directory and does nothing
else, so nothing outside ``tmp_path`` is ever touched.

Three layers:

1. ``testCall...`` -- each real vaibify host-side git call, against a
   repository carrying one mechanism at a time: which mechanisms run.
2. ``testNeutralizers...`` -- raw git under vaibify's real hardening
   list, with candidate per-invocation flags: which flags stop which
   mechanism, and whether the answer git gives changes.
3. Named single-fact tests for the headline findings.

Measured with git 2.50.1 on macOS; version-gated facts skip with a
reason on an older git. Baselines that do not fire skip rather than
pass, so a mechanism git stops honoring can never make a neutralizer
look effective.
"""

import os
import shutil
import subprocess
import sys

import pytest

from tests import hostGitProbeHarness as harness
from tests.hostGitProbeCalls import DICT_PROBE_CALLS
from vaibify.reproducibility.gitHardening import (
    LIST_GIT_CREDENTIAL_ISOLATION_CONFIG,
    LIST_GIT_HARDENING_CONFIG,
)

TUPLE_GIT_VERSION = harness.ftReadGitVersion()
B_GIT_HAS_ATTRIBUTE_SOURCE = TUPLE_GIT_VERSION >= (2, 40)
S_NEEDS_ATTRIBUTE_SOURCE = "GIT_ATTR_SOURCE needs git 2.40 or newer"

# Measured on ubuntu 20.04/22.04/24.04 and debian 12 (git 2.25 to 2.43) and
# on debian 13, ubuntu 25.04/25.10 and macOS (git 2.47 to 2.51): a quiet
# diff ("--quiet", "--cached --quiet") runs a text converter from 2.47 on,
# and did not before 2.44. Versions between are unmeasured.
B_QUIET_DIFF_RUNS_TEXTCONV = TUPLE_GIT_VERSION >= (2, 47)
B_QUIET_DIFF_TEXTCONV_UNMEASURED = (2, 44) <= TUPLE_GIT_VERSION < (2, 47)
S_QUIET_DIFF_UNMEASURED = (
    "whether a quiet diff runs a text converter is unmeasured for git "
    "2.44 to 2.46"
)
SET_CALLS_WITH_QUIET_DIFF_TEXTCONV = {
    "gitEvidenceFbManifestDiffersFromHeadWhenChanged",
    "syncDispatcherPublishSuffix",
}

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="host mode is POSIX only",
)


class ProbeWorld:
    """One isolated git universe: environment, scripts, markers, templates."""

    def __init__(self, sRoot, sHttpUrl):
        self.sRoot = sRoot
        sHome = os.path.join(sRoot, "home")
        os.makedirs(sHome)
        self.dictEnvironment = harness.fdictBuildIsolatedEnvironment(sHome)
        self.sMarkers = os.path.join(sRoot, "markers")
        self.sScripts = os.path.join(sRoot, "scripts")
        os.makedirs(self.sMarkers)
        os.makedirs(self.sScripts)
        self.dictPaths = {
            "sMarkers": self.sMarkers, "sScripts": self.sScripts,
            "sHttpUrl": sHttpUrl,
        }
        self.dictTemplates = {}
        self.iCellCounter = 0

    def fsTemplateFor(self, sMechanism):
        """Build (once) the committed repository carrying one mechanism."""
        if sMechanism not in self.dictTemplates:
            self.dictTemplates[sMechanism] = harness.fsBuildProbeRepository(
                os.path.join(self.sRoot, "template", sMechanism),
                sMechanism, self.dictPaths, self.dictEnvironment,
            )
        return self.dictTemplates[sMechanism]

    def fsFreshCell(self, sMechanism):
        """Copy a template into a new directory; return its path."""
        self.iCellCounter += 1
        sCell = os.path.join(
            self.sRoot, "cell%d" % self.iCellCounter, "repo",
        )
        os.makedirs(os.path.dirname(sCell))
        shutil.copytree(self.fsTemplateFor(sMechanism), sCell, symlinks=True)
        return sCell


@pytest.fixture(scope="module")
def worldProbe(tmp_path_factory):
    """Yield a ProbeWorld whose environment is also the process environment."""
    monkeypatch = pytest.MonkeyPatch()
    sRoot = str(tmp_path_factory.mktemp("hostGitProbe"))
    with harness.contextRejectingServer() as serverRejecting:
        worldNew = ProbeWorld(sRoot, serverRejecting.fsUrl())
        for sKey in [k for k in os.environ if k.startswith("GIT_")]:
            monkeypatch.delenv(sKey)
        for sKey, sValue in worldNew.dictEnvironment.items():
            monkeypatch.setenv(sKey, sValue)
        yield worldNew
    monkeypatch.undo()


def fsetRunCall(worldProbe, sCall, sMechanism):
    """Run one real vaibify call against one mechanism; return what ran."""
    sRepo = worldProbe.fsFreshCell(sMechanism)
    harness.fnMakeWorktreeStatDirty(sRepo)
    harness.fnClearMarkers(worldProbe.sMarkers)
    try:
        DICT_PROBE_CALLS[sCall](sRepo)
    except Exception:  # noqa: BLE001 -- a refusal is still a measurement
        pass
    return set(harness.flistReadTriggeredMechanisms(worldProbe.sMarkers))


# ----------------------------------------------------------------------
# Layer 1: which mechanisms each real vaibify call runs.
# ----------------------------------------------------------------------

SET_FILTER_AND_HOOK = {
    "filterClean", "filterCleanViaAttributesFile",
    "filterCleanViaInfoAttributes", "filterProcess",
    "hookPostIndexChange", "hooksPathConfig",
}
SET_FSMONITOR_FAMILY = {"fsmonitor", "includePath", "includeIfGitdir"}
SET_NETWORK = {"credentialHelper", "gitProxy", "insteadOfExt", "sshCommand"}
SET_STATUS_NO_FSMONITOR_FLAG = SET_FILTER_AND_HOOK | SET_FSMONITOR_FAMILY
SET_TYPED_READ = SET_FILTER_AND_HOOK
SET_COMMIT = SET_STATUS_NO_FSMONITOR_FLAG | {"gpgProgram", "hookPreCommit"}
SET_MERGE = (SET_COMMIT - {"hookPreCommit"}) | {"filterSmudge", "hookPostMerge"}
SET_PUBLISH = SET_COMMIT | SET_NETWORK | {"textconv"}

DICT_EXPECTED_TRIGGERS = {
    "gitStatusFdictGitStatusForWorkspace": SET_STATUS_NO_FSMONITOR_FLAG,
    "gitStatusFsReadOriginUrl": set(),
    "gitEvidenceFbManifestDiffersFromHeadWhenUnchanged":
        SET_STATUS_NO_FSMONITOR_FLAG,
    "gitEvidenceFbManifestDiffersFromHeadWhenChanged":
        (SET_STATUS_NO_FSMONITOR_FLAG - {"hookPostIndexChange",
                                         "hooksPathConfig"}) | {"textconv"},
    "gitEvidenceFbRepositoryCarriesForeignManifest": set(),
    "reproductionSourceRefuseUnlessClean": SET_STATUS_NO_FSMONITOR_FLAG,
    "reproductionSourceRepositoryRoot": set(),
    "reproductionSourceShowCommitted": set(),
    "reproductionSourceLocalClone": SET_STATUS_NO_FSMONITOR_FLAG,
    "typedReadGitRepoStatus": SET_TYPED_READ,
    "typedReadGitTrackedIdentities": SET_TYPED_READ,
    "typedReadGitUntrackedInventory": set(),
    "typedReadGitWorktreeIdentities": SET_TYPED_READ,
    "commandBuildRemoteAndBranch": set(),
    "containerGitStatusViaShell": SET_STATUS_NO_FSMONITOR_FLAG,
    "containerGitAddViaShell": SET_STATUS_NO_FSMONITOR_FLAG,
    "containerGitCommitViaShell": SET_COMMIT,
    "containerGitFetchViaShell": SET_NETWORK,
    "containerGitMergePreviewViaShell": set(),
    "containerGitMergeUpstreamViaShell": SET_MERGE,
    "containerGitRemoteHeadsViaShell": set(),
    "syncDispatcherPublishSuffix": SET_PUBLISH,
}


def fsetExpectedTriggersFor(sCall):
    """Return the recorded mechanisms for a call under the local git."""
    setExpected = set(DICT_EXPECTED_TRIGGERS[sCall])
    if (sCall in SET_CALLS_WITH_QUIET_DIFF_TEXTCONV
            and not B_QUIET_DIFF_RUNS_TEXTCONV):
        setExpected.discard("textconv")
    return setExpected


def test_everyProbeCallHasARecordedExpectation():
    assert set(DICT_PROBE_CALLS) == set(DICT_EXPECTED_TRIGGERS)


@pytest.mark.parametrize("sCall", sorted(DICT_PROBE_CALLS))
def testCallTriggersExactlyTheRecordedMechanisms(worldProbe, sCall):
    if (sCall in SET_CALLS_WITH_QUIET_DIFF_TEXTCONV
            and B_QUIET_DIFF_TEXTCONV_UNMEASURED):
        pytest.skip(S_QUIET_DIFF_UNMEASURED)
    dictObserved = {
        sMechanism: fsetRunCall(worldProbe, sCall, sMechanism)
        for sMechanism in harness.T_ALL_MECHANISMS
    }
    setObserved = set()
    for sMechanism, setRan in dictObserved.items():
        if setRan:
            assert setRan == {sMechanism}, (
                f"{sCall}: a repository carrying only {sMechanism} ran "
                f"{sorted(setRan)}"
            )
            setObserved.add(sMechanism)
    assert setObserved == fsetExpectedTriggersFor(sCall), (
        f"{sCall}: ran {sorted(setObserved)}; recorded "
        f"{sorted(fsetExpectedTriggersFor(sCall))}"
    )


# ----------------------------------------------------------------------
# Layer 2: which per-invocation flags stop which mechanism.
# ----------------------------------------------------------------------

DICT_NEUTRALIZERS = {
    "fsmonitorFalse": {"listGlobal": ["-c", "core.fsmonitor=false"]},
    "hooksPathNull": {"listGlobal": ["-c", "core.hooksPath=/dev/null"]},
    "fsmonitorAndHooksPathNull": {
        "listGlobal": ["-c", "core.fsmonitor=false",
                       "-c", "core.hooksPath=/dev/null"],
    },
    "noPager": {"listGlobal": ["--no-pager"]},
    "attributesFileNull": {
        "listGlobal": ["-c", "core.attributesFile=/dev/null"],
    },
    "attributeSourceEmptyTree": {
        "dictEnvironment": {"GIT_ATTR_SOURCE": harness.S_EMPTY_TREE_SHA},
        "bNeedsAttributeSource": True,
    },
    "attributeSourceAndAttributesFileNull": {
        "listGlobal": ["-c", "core.attributesFile=/dev/null"],
        "dictEnvironment": {"GIT_ATTR_SOURCE": harness.S_EMPTY_TREE_SHA},
        "bNeedsAttributeSource": True,
    },
    "filterOverriddenByName": {
        "listGlobal": [
            "-c", "filter.probe.clean=", "-c", "filter.probe.smudge=",
            "-c", "filter.probe.process=",
            "-c", "filter.probe.required=false",
        ],
    },
    "noTextconv": {
        "listSubcommand": ["--no-textconv"], "setSubcommands": {"diff", "log"},
    },
    "noExtDiff": {
        "listSubcommand": ["--no-ext-diff"], "setSubcommands": {"diff", "log"},
    },
    "noVerify": {
        "listSubcommand": ["--no-verify"],
        "setSubcommands": {"commit", "merge"},
    },
    "gpgSignOff": {"listGlobal": ["-c", "commit.gpgSign=false"]},
    "credentialHelperReset": {"listGlobal": ["-c", "credential.helper="]},
    "protocolExtNever": {"listGlobal": ["-c", "protocol.ext.allow=never"]},
    "sshCommandOverridden": {"listGlobal": ["-c", "core.sshCommand=false"]},
    "sshCommandEnvironment": {
        "dictEnvironment": {"GIT_SSH_COMMAND": "false"},
    },
    "gitProxyEnvironment": {
        "dictEnvironment": {"GIT_PROXY_COMMAND": "false"},
    },
    "gitProxyConfigEmpty": {"listGlobal": ["-c", "core.gitProxy="]},
    "optionalLocksOff": {"dictEnvironment": {"GIT_OPTIONAL_LOCKS": "0"}},
}

# (mechanism, argv, preparation); the row identifier is "mechanism:argv".
LIST_NEUTRALIZER_ROWS = [
    ("fsmonitor", ["status", "--porcelain"], "none"),
    ("includePath", ["status", "--porcelain"], "none"),
    ("includeIfGitdir", ["status", "--porcelain"], "none"),
    ("filterClean", ["status", "--porcelain"], "none"),
    ("filterCleanViaInfoAttributes", ["status", "--porcelain"], "none"),
    ("filterCleanViaAttributesFile", ["status", "--porcelain"], "none"),
    ("filterProcess", ["status", "--porcelain"], "none"),
    ("filterClean", ["add", "--", "data.txt"], "modify"),
    ("filterSmudge", ["checkout", "--", "data.txt"], "modify"),
    ("textconv", ["diff", "HEAD"], "modify"),
    ("textconv", ["diff", "--quiet", "HEAD", "--", "MANIFEST.sha256"],
     "modifyManifest"),
    ("textconv", ["diff", "--cached", "--quiet"], "stage"),
    ("textconv", ["log", "-p", "-1"], "none"),
    ("externalDiff", ["diff", "HEAD"], "modify"),
    ("hookPostIndexChange", ["status", "--porcelain"], "none"),
    ("hooksPathConfig", ["status", "--porcelain"], "none"),
    ("hookPreCommit", ["commit", "-m", "probe"], "stage"),
    ("hooksPathConfig", ["commit", "-m", "probe"], "stage"),
    ("gpgProgram", ["commit", "-m", "probe"], "stage"),
    ("hookPostMerge", ["merge", "--no-ff", "--no-edit", "@{upstream}"],
     "none"),
    ("insteadOfExt", ["fetch", "--no-tags", "origin"], "none"),
    ("sshCommand", ["fetch", "--no-tags", "origin"], "none"),
    ("credentialHelper", ["fetch", "--no-tags", "origin"], "none"),
    ("gitProxy", ["fetch", "--no-tags", "origin"], "none"),
]

# The neutralizers that stop each row, measured with git 2.50.1.
SET_STOPS_FSMONITOR = {"fsmonitorFalse", "fsmonitorAndHooksPathNull"}
SET_STOPS_HOOKS = {"hooksPathNull", "fsmonitorAndHooksPathNull"}
SET_STOPS_INDEX_HOOKS = SET_STOPS_HOOKS | {"optionalLocksOff"}

SET_STOPS_ATTRIBUTE_ROUTES = {
    "attributeSourceEmptyTree", "attributeSourceAndAttributesFileNull",
}
SET_STOPS_TEXTCONV = SET_STOPS_ATTRIBUTE_ROUTES | {"noTextconv"}

DICT_EXPECTED_NEUTRALIZERS = {
    "fsmonitor:status --porcelain": SET_STOPS_FSMONITOR,
    "includePath:status --porcelain": SET_STOPS_FSMONITOR,
    "includeIfGitdir:status --porcelain": SET_STOPS_FSMONITOR,
    "filterClean:status --porcelain":
        SET_STOPS_ATTRIBUTE_ROUTES | {"filterOverriddenByName"},
    "filterCleanViaInfoAttributes:status --porcelain":
        {"filterOverriddenByName"},
    "filterCleanViaAttributesFile:status --porcelain":
        {"attributeSourceAndAttributesFileNull", "attributesFileNull",
         "filterOverriddenByName"},
    "filterProcess:status --porcelain":
        SET_STOPS_ATTRIBUTE_ROUTES | {"filterOverriddenByName"},
    "filterClean:add -- data.txt":
        SET_STOPS_ATTRIBUTE_ROUTES | {"filterOverriddenByName"},
    "filterSmudge:checkout -- data.txt":
        SET_STOPS_ATTRIBUTE_ROUTES | {"filterOverriddenByName"},
    "textconv:diff HEAD": SET_STOPS_TEXTCONV,
    "textconv:diff --quiet HEAD -- MANIFEST.sha256":
        SET_STOPS_TEXTCONV | {"noExtDiff"},
    "textconv:diff --cached --quiet":
        SET_STOPS_TEXTCONV | {"noExtDiff"},
    "textconv:log -p -1": SET_STOPS_TEXTCONV,
    "externalDiff:diff HEAD": {"noExtDiff"},
    "hookPostIndexChange:status --porcelain": SET_STOPS_INDEX_HOOKS,
    "hooksPathConfig:status --porcelain": SET_STOPS_INDEX_HOOKS,
    "hookPreCommit:commit -m probe": SET_STOPS_HOOKS | {"noVerify"},
    "hooksPathConfig:commit -m probe": SET_STOPS_HOOKS,
    "gpgProgram:commit -m probe": {"gpgSignOff"},
    "hookPostMerge:merge --no-ff --no-edit @{upstream}": SET_STOPS_HOOKS,
    "insteadOfExt:fetch --no-tags origin": {"protocolExtNever"},
    "sshCommand:fetch --no-tags origin":
        {"sshCommandEnvironment", "sshCommandOverridden"},
    "credentialHelper:fetch --no-tags origin": {"credentialHelperReset"},
    "gitProxy:fetch --no-tags origin": {"gitProxyEnvironment"},
}


def fsRowIdentifier(tRow):
    """Return the stable identifier of one neutralizer row."""
    return tRow[0] + ":" + " ".join(tRow[1])


def fnPrepareRow(worldProbe, sRepo, sPreparation):
    """Bring a copied repository to the state a row's command needs."""
    if sPreparation == "modify":
        harness.fnModifyTrackedFile(sRepo)
    elif sPreparation == "modifyManifest":
        with open(os.path.join(sRepo, harness.S_MANIFEST_FILE), "w") as f:
            f.write("changed\n")
    elif sPreparation == "stage":
        harness.fnModifyTrackedFile(sRepo)
        subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "add", "-A"],
            cwd=sRepo, capture_output=True,
        )
    harness.fnMakeWorktreeStatDirty(sRepo)


def ftRunRow(worldProbe, tRow, dictNeutralizer):
    """Run one row under vaibify's hardening plus one neutralizer."""
    sMechanism, listArgv, sPreparation = tRow
    sRepo = worldProbe.fsFreshCell(sMechanism)
    fnPrepareRow(worldProbe, sRepo, sPreparation)
    harness.fnClearMarkers(worldProbe.sMarkers)
    listSubFlags = []
    if listArgv[0] in dictNeutralizer.get("setSubcommands", {listArgv[0]}):
        listSubFlags = dictNeutralizer.get("listSubcommand", [])
    dictEnvironment = dict(worldProbe.dictEnvironment)
    dictEnvironment.update(dictNeutralizer.get("dictEnvironment", {}))
    processGit = subprocess.run(
        ["git", *dictNeutralizer.get("listGlobal", []),
         *LIST_GIT_HARDENING_CONFIG, listArgv[0], *listSubFlags,
         *listArgv[1:]],
        cwd=sRepo, env=dictEnvironment, capture_output=True, text=True,
        timeout=60,
    )
    bRan = bool(harness.flistReadTriggeredMechanisms(worldProbe.sMarkers))
    return bRan, processGit.returncode


def fsetFindNeutralizers(worldProbe, tRow):
    """Return the names of the neutralizers that stop a row's mechanism."""
    bBaselineRan, iBaselineCode = ftRunRow(worldProbe, tRow, {})
    if not bBaselineRan:
        pytest.skip("the mechanism does not run for this command here")
    setStopping = set()
    for sName, dictNeutralizer in DICT_NEUTRALIZERS.items():
        if (dictNeutralizer.get("bNeedsAttributeSource")
                and not B_GIT_HAS_ATTRIBUTE_SOURCE):
            continue
        dictSubcommandScope = dictNeutralizer.get("setSubcommands")
        if dictSubcommandScope and tRow[1][0] not in dictSubcommandScope:
            continue
        bRan, iCode = ftRunRow(worldProbe, tRow, dictNeutralizer)
        if not bRan and iCode in (iBaselineCode, 0):
            setStopping.add(sName)
    return setStopping


def test_everyNeutralizerRowHasARecordedExpectation():
    setRows = {fsRowIdentifier(tRow) for tRow in LIST_NEUTRALIZER_ROWS}
    assert setRows == set(DICT_EXPECTED_NEUTRALIZERS)


@pytest.mark.parametrize(
    "tRow", LIST_NEUTRALIZER_ROWS, ids=fsRowIdentifier,
)
def testNeutralizersStopExactlyTheRecordedSet(worldProbe, tRow):
    setExpected = set(DICT_EXPECTED_NEUTRALIZERS[fsRowIdentifier(tRow)])
    if B_QUIET_DIFF_TEXTCONV_UNMEASURED and "--quiet" in tRow[1]:
        pytest.skip(S_QUIET_DIFF_UNMEASURED)
    if not B_GIT_HAS_ATTRIBUTE_SOURCE:
        setExpected -= {
            sName for sName, dictNeutralizer in DICT_NEUTRALIZERS.items()
            if dictNeutralizer.get("bNeedsAttributeSource")
        }
    assert fsetFindNeutralizers(worldProbe, tRow) == setExpected


# ----------------------------------------------------------------------
# Layer 3: named single-fact tests. A name that says "runs" records a
# real exposure; the test is expected to be rewritten when it is closed.
# ----------------------------------------------------------------------


def testHarnessDetectsAMechanismThatRuns(worldProbe):
    sRepo = worldProbe.fsFreshCell("fsmonitor")
    harness.fnClearMarkers(worldProbe.sMarkers)
    subprocess.run(["git", "status"], cwd=sRepo, capture_output=True)
    assert harness.flistReadTriggeredMechanisms(worldProbe.sMarkers) == [
        "fsmonitor",
    ]


def testHarnessReportsSilenceWhenTheMechanismIsDisabled(worldProbe):
    sRepo = worldProbe.fsFreshCell("fsmonitor")
    harness.fnClearMarkers(worldProbe.sMarkers)
    subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "status"],
        cwd=sRepo, capture_output=True,
    )
    assert harness.flistReadTriggeredMechanisms(worldProbe.sMarkers) == []


def testGitVersionIsRecordedInTheReport(record_property):
    record_property("gitVersion", harness.fsDescribeGitVersion())
    assert TUPLE_GIT_VERSION >= (2, 20)


def testFsmonitorRunsDuringHostStatus(worldProbe):
    setRan = fsetRunCall(
        worldProbe, "gitStatusFdictGitStatusForWorkspace", "fsmonitor",
    )
    assert setRan == {"fsmonitor"}


def testIncludedFsmonitorRunsDuringHostStatus(worldProbe):
    for sMechanism in ("includePath", "includeIfGitdir"):
        setRan = fsetRunCall(
            worldProbe, "gitStatusFdictGitStatusForWorkspace", sMechanism,
        )
        assert setRan == {sMechanism}


def testCleanFilterRunsDuringHostStatus(worldProbe):
    setRan = fsetRunCall(
        worldProbe, "gitStatusFdictGitStatusForWorkspace", "filterClean",
    )
    assert setRan == {"filterClean"}


def testCleanFilterRunsDuringTheManifestEvidenceQuestion(worldProbe):
    setRan = fsetRunCall(
        worldProbe, "gitEvidenceFbManifestDiffersFromHeadWhenChanged", "filterClean",
    )
    assert setRan == {"filterClean"}


def testCleanFilterRunsDuringTypedReadStatusDespiteFsmonitorFlag(worldProbe):
    assert fsetRunCall(worldProbe, "typedReadGitRepoStatus", "fsmonitor") == (
        set()
    )
    assert fsetRunCall(worldProbe, "typedReadGitRepoStatus", "filterClean") == (
        {"filterClean"}
    )


def testPostIndexChangeHookRunsDuringTypedReadStatus(worldProbe):
    setRan = fsetRunCall(
        worldProbe, "typedReadGitRepoStatus", "hookPostIndexChange",
    )
    assert setRan == {"hookPostIndexChange"}


def testCleanFilterStillRunsWithFsmonitorAndHooksPathDisabled(worldProbe):
    tRow = ("filterClean", ["status", "--porcelain"], "none")
    dictAuditFlags = DICT_NEUTRALIZERS["fsmonitorAndHooksPathNull"]
    bRan, _iCode = ftRunRow(worldProbe, tRow, dictAuditFlags)
    assert bRan


def testInfoAttributesFilterEscapesTheAttributeSourceFlag(worldProbe):
    if not B_GIT_HAS_ATTRIBUTE_SOURCE:
        pytest.skip(S_NEEDS_ATTRIBUTE_SOURCE)
    tRow = ("filterCleanViaInfoAttributes", ["status", "--porcelain"], "none")
    dictCombined = DICT_NEUTRALIZERS["attributeSourceAndAttributesFileNull"]
    bRan, _iCode = ftRunRow(worldProbe, tRow, dictCombined)
    assert bRan


def testAttributeSourceStopsAFilterSelectedByWorktreeAttributes(worldProbe):
    if not B_GIT_HAS_ATTRIBUTE_SOURCE:
        pytest.skip(S_NEEDS_ATTRIBUTE_SOURCE)
    tRow = ("filterClean", ["status", "--porcelain"], "none")
    bRan, _iCode = ftRunRow(
        worldProbe, tRow, DICT_NEUTRALIZERS["attributeSourceEmptyTree"],
    )
    assert not bRan


def testExtTransportRunsOnlyBecauseVaibifyAllowsProtocolsForTheUser(
    worldProbe,
):
    listFetch = ["fetch", "--no-tags", "origin"]
    sRepo = worldProbe.fsFreshCell("insteadOfExt")
    harness.fnClearMarkers(worldProbe.sMarkers)
    processBare = subprocess.run(
        ["git", *listFetch], cwd=sRepo, capture_output=True, text=True,
    )
    assert harness.flistReadTriggeredMechanisms(worldProbe.sMarkers) == []
    assert "not allowed" in processBare.stderr
    subprocess.run(
        ["git", *LIST_GIT_HARDENING_CONFIG, *listFetch],
        cwd=sRepo, capture_output=True, text=True,
    )
    assert harness.flistReadTriggeredMechanisms(worldProbe.sMarkers) == [
        "insteadOfExt",
    ]


def testCredentialHelperRunsDuringContainerGitFetch(worldProbe):
    assert fsetRunCall(
        worldProbe, "containerGitFetchViaShell", "credentialHelper",
    ) == {"credentialHelper"}


def testCredentialIsolationListStopsARepositoryCredentialHelper(worldProbe):
    sRepo = worldProbe.fsFreshCell("credentialHelper")
    harness.fnClearMarkers(worldProbe.sMarkers)
    subprocess.run(
        ["git", *LIST_GIT_CREDENTIAL_ISOLATION_CONFIG,
         *LIST_GIT_HARDENING_CONFIG, "fetch", "--no-tags", "origin"],
        cwd=sRepo, capture_output=True, text=True,
    )
    assert harness.flistReadTriggeredMechanisms(worldProbe.sMarkers) == []


def testSshCommandAndGitProxyRunDuringContainerGitFetch(worldProbe):
    for sMechanism in ("sshCommand", "gitProxy"):
        assert fsetRunCall(
            worldProbe, "containerGitFetchViaShell", sMechanism,
        ) == {sMechanism}


def testPreCommitHookRunsDuringContainerGitCommit(worldProbe):
    assert fsetRunCall(
        worldProbe, "containerGitCommitViaShell", "hookPreCommit",
    ) == {"hookPreCommit"}


def testSigningProgramRunsDuringContainerGitCommit(worldProbe):
    assert fsetRunCall(
        worldProbe, "containerGitCommitViaShell", "gpgProgram",
    ) == {"gpgProgram"}


def testSmudgeFilterRunsDuringContainerGitMerge(worldProbe):
    assert fsetRunCall(
        worldProbe, "containerGitMergeUpstreamViaShell", "filterSmudge",
    ) == {"filterSmudge"}


def testTextconvRunsWhenTheManifestDiffersFromHead(worldProbe):
    if not B_QUIET_DIFF_RUNS_TEXTCONV:
        pytest.skip("a quiet diff runs a text converter only from git 2.47")
    assert fsetRunCall(
        worldProbe, "gitEvidenceFbManifestDiffersFromHeadWhenChanged", "textconv",
    ) == {"textconv"}


def testPagerNeverRunsBecauseNoVaibifyCallHasATerminal(worldProbe):
    for sCall in ("containerGitCommitViaShell", "containerGitStatusViaShell",
                  "syncDispatcherPublishSuffix"):
        assert fsetRunCall(worldProbe, sCall, "pager") == set()


def testExternalDiffDoesNotRunForAnyVaibifyCall(worldProbe):
    for sCall in sorted(DICT_PROBE_CALLS):
        assert fsetRunCall(worldProbe, sCall, "externalDiff") == set()


def testFailingProcessFilterNeverMakesTheManifestQuestionLie(worldProbe):
    """The manifest is unchanged, so the only honest answers are 'no' or 'unknown'.

    Git 2.50 and older fail the command (exit 128), which vaibify turns
    into an undetermined refusal; git 2.55 skips the broken filter and
    reports no difference. Either is truthful here; a 'yes' would not be.
    """
    from vaibify.cli.commandReproduce import _ffnBuildHostGitRunner
    from vaibify.reproducibility import gitEvidence
    sRepo = worldProbe.fsFreshCell("filterProcess")
    harness.fnMakeWorktreeStatDirty(sRepo)
    try:
        bDiffers = gitEvidence.fbManifestDiffersFromHead(
            _ffnBuildHostGitRunner(sRepo)
        )
    except gitEvidence.RecordKindUndeterminedError:
        return
    assert bDiffers is False


@pytest.mark.parametrize(
    "sMechanism", ["fsmonitor", "filterClean", "hookPostIndexChange"],
)
def testLocalCloneStagingCarriesNeitherSourceConfigNorHooks(
    worldProbe, sMechanism,
):
    from vaibify.gui import gitStatus
    from vaibify.reproducibility import reproductionSource
    sRepo = worldProbe.fsFreshCell(sMechanism)
    harness.fnRunSetupGit(["remote", "remove", "origin"], sRepo,
                          worldProbe.dictEnvironment)
    sStaging = sRepo + "Staging"
    os.makedirs(sStaging)
    dictStaged = reproductionSource._fdictMaterializeLocalClone(
        sRepo, sStaging,
    )
    sClone = os.path.join(sStaging, os.listdir(sStaging)[0])
    assert dictStaged["sResolvedCommit"]
    harness.fnClearMarkers(worldProbe.sMarkers)
    harness.fnMakeWorktreeStatDirty(sClone)
    gitStatus.fdictGitStatusForWorkspace(sClone)
    assert harness.flistReadTriggeredMechanisms(worldProbe.sMarkers) == []
    with open(os.path.join(sClone, ".git", "config")) as fileConfig:
        sCloneConfig = fileConfig.read()
    assert "fsmonitor" not in sCloneConfig and "filter" not in sCloneConfig


def testDisablingACleanFilterChangesTheStatusAnswer(worldProbe, tmp_path):
    """A status computed with the filter off disagrees with the real one.

    This is why 'suppress the filters and carry on' cannot be the design:
    the suppressed answer is confidently wrong, not merely incomplete.
    """
    sRepo = str(tmp_path / "normalised")
    os.makedirs(sRepo)
    dictEnvironment = worldProbe.dictEnvironment
    for listArguments in (
        ["init", "-q", "-b", "main"],
        ["config", "filter.probe.clean", "tr a-z A-Z"],
    ):
        harness.fnRunSetupGit(listArguments, sRepo, dictEnvironment)
    harness.fnAppendText(os.path.join(sRepo, ".gitattributes"),
                         "data.txt filter=probe\n")
    with open(os.path.join(sRepo, "data.txt"), "w") as fileTracked:
        fileTracked.write("alpha\n")
    for listArguments in (["add", "-A"], ["commit", "-q", "-m", "initial"]):
        harness.fnRunSetupGit(listArguments, sRepo, dictEnvironment)
    sTracked = os.path.join(sRepo, "data.txt")
    listStatus = ["git", *LIST_GIT_HARDENING_CONFIG, "status", "--porcelain"]
    os.utime(sTracked, (1_000_000_000, 1_000_000_000))
    sHonest = subprocess.run(
        listStatus, cwd=sRepo, env=dictEnvironment,
        capture_output=True, text=True,
    ).stdout
    os.utime(sTracked, (1_100_000_000, 1_100_000_000))
    sSuppressed = subprocess.run(
        ["git", "-c", "filter.probe.clean=", *listStatus[1:]], cwd=sRepo,
        env=dictEnvironment, capture_output=True, text=True,
    ).stdout
    assert sHonest == ""
    assert "data.txt" in sSuppressed
