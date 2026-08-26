"""A file that is not there gets no badge that claims it is in sync.

``git status --porcelain`` names only files it has something to say
about. The GitHub badge read that silence as ``synced`` -- "in sync
with remote" -- which is right for a tracked, clean file and a lie for
a file that was never committed and does not exist. A researcher saw
the green claim on the same row as the panel's own red "missing"
marker; the row contradicted itself.

Rory's ruling (2026-08-13): a missing file's remote badge is ``none``.

Three claims are pinned here, and the third is the one that makes the
other two reach production:

1. a missing file gets ``none`` from every column;
2. a PRESENT tracked file still gets its real state -- the symmetric
   half, without which "return none always" would satisfy claim 1;
3. the badges ROUTE asks the filesystem which files are missing,
   rather than inferring it from silence or from an empty hash.
"""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from tests.carrierStandDown import fnStandCarrierDown
from vaibify.gui import badgeState
from vaibify.gui.routes import gitRoutes

S_RESOURCE_ID = "cid-badge-demo"
S_REPO = "/workspace/DemoRepo"
S_PRESENT = "Plot/figure.pdf"
S_MISSING = "Plot/ghost.pdf"


def _fdictGitStatusWithNeitherMentioned():
    """Porcelain that mentions neither file -- the defect's precondition.

    Clean-and-tracked and never-existed produce the identical record,
    which is exactly why existence has to be asked separately.
    """
    return {"bIsRepo": True, "dictFileStates": {}}


class TestTheRuleItself:

    @pytest.mark.falsification
    def testAMissingFileClaimsNothingOnAnyRemote(self):
        """Kills: reading "porcelain did not mention it" as ``synced``."""
        dictBadges = badgeState.fdictBadgeStateFromHashes(
            [S_MISSING], _fdictGitStatusWithNeitherMentioned(),
            {S_MISSING: {
                "bOverleaf": True,
                "sOverleafLastPushedDigest": "a" * 40,
                "bZenodo": True,
                "sZenodoLastPushedDigest": "a" * 40,
                "sZenodoLastPushedEndpoint": "sandbox",
            }},
            {}, {S_MISSING},
            sZenodoService="sandbox",
            dictArxivStatus={"sLastVerified": "2026-08-13", "listDiverged": []},
            bArxivConfigured=True,
        )[S_MISSING]

        # sGitState joined the dict on 2026-08-25, when sGithub became
        # agreement with the published copy and the local git answer
        # moved to its own key. A missing file claims nothing there
        # either. Exact equality is kept deliberately: a new key must
        # not be addable without deciding what a missing file says on
        # it.
        assert dictBadges == {
            "sGithub": badgeState.S_BADGE_NONE,
            "sOverleaf": badgeState.S_BADGE_NONE,
            "sZenodo": badgeState.S_BADGE_NONE,
            "sArxiv": badgeState.S_BADGE_NONE,
            "sGitState": badgeState.S_BADGE_NONE,
        }

    @pytest.mark.falsification
    def testAPresentTrackedFileStillReportsItsRealState(self):
        """Kills: answering ``none`` for everything.

        The symmetric half of the rule. Without it, "no file has any
        state" satisfies the test above perfectly and silently blanks
        every badge in the dashboard.
        """
        dictBadges = badgeState.fdictBadgeStateFromHashes(
            [S_PRESENT], _fdictGitStatusWithNeitherMentioned(),
            {S_PRESENT: {
                "bZenodo": True,
                "sZenodoLastPushedDigest": "c" * 40,
                "sZenodoLastPushedEndpoint": "sandbox",
            }},
            {S_PRESENT: "c" * 40}, set(),
            sZenodoService="sandbox",
        )[S_PRESENT]

        # sGitState carries the local git answer this exercises.
        # sGithub is asserted as UNKNOWN rather than dropped: no
        # GitHub verify was supplied here, and "nobody looked" must
        # stay distinguishable from the NONE the missing-file rule
        # produces, or this pair stops discriminating.
        assert dictBadges["sGitState"] == badgeState.S_BADGE_SYNCED
        assert dictBadges["sGithub"] == badgeState.S_BADGE_UNKNOWN
        assert dictBadges["sZenodo"] == badgeState.S_BADGE_SYNCED


class ConnectionWithOneFileOnDisk:
    """A leg where exactly one of the two tracked files exists.

    Fail-closed: it answers the four probes the badge refresh makes and
    raises on anything else, so a route that starts asking a new
    question has to say so here rather than receive a plausible
    default. The existence answer is deliberately NOT derivable from
    the hash map -- ``S_MISSING`` is absent from both, and a double
    that conflated them could not tell the fix from the defect.
    """

    def __init__(self):
        self.listExistenceProbes = []

    def ftResultExecuteCommand(self, sContainerId, sCommand, **dictKeywords):
        del sContainerId, dictKeywords
        if "rev-parse --is-inside-work-tree" in sCommand:
            return (0, "true\n")
        if "status --porcelain=v2" in sCommand:
            return (0, "# branch.head main\n# branch.ab +0 -0\n")
        if "rev-parse HEAD" in sCommand:
            return (0, "b" * 40 + "\n")
        if "remote get-url origin" in sCommand:
            return (1, "")
        if "python3 -c" in sCommand and "glob" in sCommand:
            return (0, f'["{S_PRESENT}", "{S_MISSING}"]\n')
        if "python3 -c" in sCommand and "hashlib" in sCommand:
            return (0, '{"' + S_PRESENT + '": "' + "c" * 40 + '"}\n')
        raise AssertionError(f"unmodelled command: {sCommand!r}")

    def flistContainerPathsExist(self, sContainerId, listPaths):
        del sContainerId
        self.listExistenceProbes.extend(listPaths)
        return [
            sPath == f"{S_REPO}/{S_PRESENT}" for sPath in listPaths
        ]

    def fbaFetchFile(self, sContainerId, sPath, iMaxBytes=None):
        del sContainerId, sPath, iMaxBytes
        raise FileNotFoundError("no cached sync status")


@pytest.fixture
def fixtureCarrierStoodDown(monkeypatch):
    """Stand the carrier down; this module is about the payload."""
    fnStandCarrierDown(monkeypatch, gitRoutes)


def _ftBuildBadgeClient():
    """Return ``(client, connection)`` serving the badges route."""
    connection = ConnectionWithOneFileOnDisk()
    app = FastAPI()
    dictCtx = {
        "require": lambda *aArgs: None,
        "docker": connection,
        "workflows": {S_RESOURCE_ID: {
            "sPlotDirectory": "Plot",
            "listSteps": [],
            "sProjectRepoPath": S_REPO,
            "dictSyncStatus": {},
        }},
    }
    gitRoutes.fnRegisterAll(app, dictCtx)
    return TestClient(app), connection


@pytest.mark.falsification
def testTheBadgeRouteAsksWhichTrackedFilesAreOnDisk(
    fixtureCarrierStoodDown,
):
    """Kills: the route answering "nothing is missing" without looking.

    The rule in ``badgeState`` is unreachable unless somebody supplies
    the missing set, and the honest supplier is a probe of the
    filesystem. A route that passed an empty set would leave the
    dashboard exactly as it was while every unit test above stayed
    green -- which is the shape this repo has shipped a fatal bug in
    before.
    """
    client, connection = _ftBuildBadgeClient()

    response = client.get(f"/api/git/{S_RESOURCE_ID}/badges")

    assert response.status_code == 200, response.text
    dictBadges = response.json()["dictBadges"]
    assert dictBadges[S_MISSING]["sGithub"] == badgeState.S_BADGE_NONE
    assert dictBadges[S_PRESENT]["sGitState"] == (
        badgeState.S_BADGE_SYNCED
    )
    assert connection.listExistenceProbes == [
        f"{S_REPO}/{S_PRESENT}", f"{S_REPO}/{S_MISSING}",
    ]
