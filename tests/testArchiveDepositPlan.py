"""Where an environment deposit goes, and which way the row recommends.

The row used to offer one button while the route read the project's
Zenodo setting, so a project whose earlier deposit was permanent but
whose setting said sandbox would silently start an unrelated sandbox
record. Every test here that crosses into the route makes the project
setting and the chosen destination DISTINCT, because with the two
equal a route still reading the setting passes.

The expected values come from Zenodo's own rule -- a new version can
only be drafted on the instance that holds the record -- and from the
researcher's ruling that a permanent record is continued and anything
else is recommended a new permanent record, never from the module
under test.
"""

import json
import os
import subprocess

import pytest

from vaibify.gui import archiveProgress
from vaibify.gui.pipelineServer import fdictBuildImageArchiveDetail
from vaibify.gui.routes import environmentArchiveRoutes
from vaibify.reproducibility import (
    archiveDepositPlan, imageArchive, imageDeposit, zenodoClient,
)


_S_DIGEST = "registry.example/project@sha256:" + "a" * 64
_S_PERMANENT_VERSION_DOI = "10.5281/zenodo.8100002"
_S_PERMANENT_CONCEPT_DOI = "10.5281/zenodo.8100001"
_S_SANDBOX_VERSION_DOI = "10.5072/zenodo.4200002"


def _fdictContainerWithLineage(sVersionDoi, sConceptDoi, sService):
    """Return a container block whose image changed after a deposit."""
    return {
        "sImageDigest": _S_DIGEST,
        "sArchitecture": "arm64",
        "dictImageArchiveLineage": {
            "sVersionDoi": sVersionDoi,
            "sConceptDoi": sConceptDoi,
            "sZenodoService": sService,
        },
    }


def _fdictPermanentLineage():
    return _fdictContainerWithLineage(
        _S_PERMANENT_VERSION_DOI, _S_PERMANENT_CONCEPT_DOI, "zenodo",
    )


def _flistChoiceNames(dictPlan):
    return [dictOffered["sChoice"] for dictOffered in dictPlan["listChoices"]]


# ----------------------------------------------------------------------
# The plan the row renders.
# ----------------------------------------------------------------------


def test_a_project_that_never_deposited_is_recommended_a_permanent_record():
    dictPlan = archiveDepositPlan.fdictBuildDepositPlan(
        {"sImageDigest": _S_DIGEST}, bCovered=False,
    )
    assert dictPlan["dictPreviousDeposit"] is None
    assert _flistChoiceNames(dictPlan) == [
        "new-record-permanent", "new-record-sandbox",
    ]
    assert dictPlan["sRecommendedChoice"] == "new-record-permanent"


@pytest.mark.falsification
def test_a_permanent_earlier_deposit_is_continued_as_a_new_version():
    """One project's environments stay one citable record.

    Kills: recommending a new permanent record over a permanent record
    that could be continued -- the duplicate the row exists to prevent.
    """
    dictPlan = archiveDepositPlan.fdictBuildDepositPlan(
        _fdictPermanentLineage(), bCovered=False,
    )
    assert dictPlan["sRecommendedChoice"] == "new-version"
    assert dictPlan["listChoices"][0] == {
        "sChoice": "new-version", "sZenodoService": "zenodo",
    }
    dictPrevious = dictPlan["dictPreviousDeposit"]
    assert dictPrevious["sVersionDoi"] == _S_PERMANENT_VERSION_DOI
    assert dictPrevious["sConceptDoi"] == _S_PERMANENT_CONCEPT_DOI
    assert dictPrevious["sPermanence"] == "permanent"


@pytest.mark.falsification
def test_a_sandbox_earlier_deposit_is_recommended_a_new_permanent_record():
    """A sandbox record can be continued, but only on the sandbox.

    Level 3 needs a permanent archive and Zenodo cannot version a
    sandbox record onto zenodo.org, so the continuation is OFFERED
    (on the sandbox) and a new permanent record is RECOMMENDED.

    Kills: recommending a new version whatever Zenodo the earlier
    deposit is on.
    """
    dictPlan = archiveDepositPlan.fdictBuildDepositPlan(
        _fdictContainerWithLineage(_S_SANDBOX_VERSION_DOI, "", "sandbox"),
        bCovered=False,
    )
    assert dictPlan["listChoices"][0] == {
        "sChoice": "new-version", "sZenodoService": "sandbox",
    }
    assert dictPlan["sRecommendedChoice"] == "new-record-permanent"


def test_an_earlier_deposit_with_no_recorded_service_is_read_by_its_doi():
    """Records predating the field name their Zenodo by DOI prefix."""
    dictPrevious = archiveDepositPlan.fdictDescribePreviousDeposit(
        _fdictContainerWithLineage(_S_SANDBOX_VERSION_DOI, "", ""),
    )
    assert dictPrevious["sZenodoService"] == "sandbox"
    assert dictPrevious["bCanContinue"] is True


def test_an_earlier_deposit_on_an_unknown_zenodo_offers_no_new_version():
    """A record naming a service vaibify cannot reach is not continued."""
    dictPlan = archiveDepositPlan.fdictBuildDepositPlan(
        _fdictContainerWithLineage(_S_PERMANENT_VERSION_DOI, "", "elsewhere"),
        bCovered=False,
    )
    assert dictPlan["dictPreviousDeposit"]["sPermanence"] == "unknown"
    assert "new-version" not in _flistChoiceNames(dictPlan)
    assert dictPlan["sRecommendedChoice"] == "new-record-permanent"


def test_nothing_is_recommended_when_a_deposit_already_covers_the_image():
    dictPlan = archiveDepositPlan.fdictBuildDepositPlan(
        _fdictPermanentLineage(), bCovered=True,
    )
    assert dictPlan["sRecommendedChoice"] == ""


# ----------------------------------------------------------------------
# Resolving the researcher's choice -- the route's half.
# ----------------------------------------------------------------------


@pytest.mark.falsification
def test_a_new_record_starts_fresh_even_on_the_zenodo_holding_the_last():
    """"Start a separate record" must not quietly continue the old one.

    The route used to pass the earlier record as the parent of EVERY
    deposit, so a new record on the same Zenodo became a new version.

    Kills: passing the lineage as the parent for every choice.
    """
    dictContainer = _fdictPermanentLineage()
    assert archiveDepositPlan.ftResolveDepositChoice(
        dictContainer, "new-record-permanent",
    ) == ("zenodo", {})
    sService, dictParent = archiveDepositPlan.ftResolveDepositChoice(
        dictContainer, "new-version",
    )
    assert sService == "zenodo"
    assert dictParent["sVersionDoi"] == _S_PERMANENT_VERSION_DOI


def test_a_new_version_is_refused_when_there_is_nothing_to_continue():
    with pytest.raises(ValueError, match="new-record-permanent"):
        archiveDepositPlan.ftResolveDepositChoice(
            {"sImageDigest": _S_DIGEST}, "new-version",
        )


@pytest.mark.falsification
def test_the_chosen_destination_reaches_the_upload(monkeypatch, tmp_path):
    """The Zenodo the row names is the Zenodo the bytes go to.

    The project publishes to the sandbox; the researcher chose a new
    version of their permanent record. Driven from the request body
    through the route's resolver into the synchronous deposit, with
    only the save-and-upload itself replaced.

    Kills: the deposit still building its client from the project's
    Zenodo setting.
    """
    dictCaptured = {}

    def fdictFakeDeposit(clientZenodo, *listArgs, **dictKwargs):
        dictCaptured["sService"] = clientZenodo.sService
        dictCaptured["dictParent"] = dictKwargs["dictParentArchive"]
        return {}

    monkeypatch.setattr(
        imageDeposit, "fdictDepositImageArchive", fdictFakeDeposit,
    )
    monkeypatch.setattr(
        imageDeposit, "fsResolveDepositScratchDirectory",
        lambda: str(tmp_path / "scratch"),
    )
    monkeypatch.setattr(
        environmentArchiveRoutes, "_fnRefuseAgentsInTheEnvironment",
        lambda sContainerId, dictContainer: None,
    )
    dictContainer = _fdictPermanentLineage()
    dictDestination = environmentArchiveRoutes._fdictResolveDepositDestination(
        dictContainer, {"sChoice": "new-version"},
    )
    environmentArchiveRoutes._fdictDepositSynchronously(
        "cid", {"sZenodoService": "sandbox", "sWorkflowName": "w"},
        dictContainer, dictDestination, "token", None,
    )
    assert dictCaptured["sService"] == "zenodo"
    assert dictCaptured["dictParent"]["sVersionDoi"] == (
        _S_PERMANENT_VERSION_DOI
    )


@pytest.mark.falsification
def test_a_referenced_doi_is_looked_up_on_the_zenodo_it_names(monkeypatch):
    """A permanent DOI does not exist on the sandbox.

    Kills: looking the reference up on the project's Zenodo setting.
    """
    listServices = []

    class _RecordingClient:
        def __init__(self, sService="sandbox", sToken=None, sBaseUrl=None):
            listServices.append(sService)

        def fdictFetchPublishedRecord(self, sRecordId):
            raise zenodoClient.ZenodoError("stop after the lookup")

    monkeypatch.setattr(zenodoClient, "ZenodoClient", _RecordingClient)
    for sDoi in (_S_PERMANENT_VERSION_DOI, _S_SANDBOX_VERSION_DOI):
        with pytest.raises(Exception):
            environmentArchiveRoutes._fdictVerifyReferencedDeposit(
                sDoi, {"sImageDigest": _S_DIGEST, "sArchitecture": "arm64"},
            )
    assert listServices == ["zenodo", "sandbox"]


# ----------------------------------------------------------------------
# The poll payload and the row's own sentence.
# ----------------------------------------------------------------------


@pytest.fixture
def sProjectRepo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    os.makedirs(os.path.join(str(tmp_path), ".vaibify"), exist_ok=True)
    return str(tmp_path)


def _fnWriteContainerBlock(sProjectRepo, dictContainer):
    with open(
        os.path.join(sProjectRepo, ".vaibify", "environment.json"), "w",
    ) as fileOut:
        json.dump({"dictContainer": dictContainer}, fileOut)


@pytest.mark.falsification
def test_the_row_payload_ships_the_plan(sProjectRepo):
    """The row renders the plan; it never decides the destination.

    Kills: dropping ``dictDepositPlan`` from the archive payload.
    """
    _fnWriteContainerBlock(sProjectRepo, _fdictPermanentLineage())
    archiveProgress.fnForgetDeposit("cid-plan", sProjectRepo)
    dictDetail = fdictBuildImageArchiveDetail({}, sProjectRepo, "cid-plan")
    dictPlan = dictDetail["dictDepositPlan"]
    assert dictPlan["sRecommendedChoice"] == "new-version"
    assert dictPlan["dictPreviousDeposit"]["sVersionDoi"] == (
        _S_PERMANENT_VERSION_DOI
    )


def test_the_missing_deposit_sentence_names_the_earlier_one():
    """"No archive has been deposited" hid that this project had one."""
    listReasons = imageArchive.flistDescribeArchiveMismatch(
        {"dictContainer": _fdictPermanentLineage()},
    )
    assert len(listReasons) == 1
    assert _S_PERMANENT_VERSION_DOI in listReasons[0]
    assert "earlier deposit" in listReasons[0]
