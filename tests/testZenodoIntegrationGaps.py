"""Coverage gap closers for the Zenodo first-time integration.

Targets specific missing-line branches in syncDispatcher, workflowManager,
and syncRoutes that the initial six-commit Zenodo series did not exercise.
All tests mock subprocess / requests / Docker; no live network calls.
"""

import json
from unittest.mock import MagicMock, patch

import pytest


# ----------------------------------------------------------------------
# syncDispatcher: small-branch coverage
# ----------------------------------------------------------------------


def test_fsZenodoTokenNameForInstance_rejects_invalid_instance():
    """Unknown instance strings must raise ValueError (line 90)."""
    from vaibify.gui.syncDispatcher import fsZenodoTokenNameForInstance
    with pytest.raises(ValueError, match="Invalid Zenodo instance"):
        fsZenodoTokenNameForInstance("staging")


def test_fsZenodoTokenNameForInstance_rejects_service_key():
    """Callers must use the UI instance name, not the service key."""
    from vaibify.gui.syncDispatcher import fsZenodoTokenNameForInstance
    with pytest.raises(ValueError):
        fsZenodoTokenNameForInstance("zenodo")


def test_flistBuildApiCreators_skips_empty_name_entries():
    """Creators with blank sName are dropped; others kept."""
    from vaibify.reproducibility.zenodoClient import (
        flistBuildApiCreators as _flistBuildApiCreators,
    )
    listApi = _flistBuildApiCreators([
        {"sName": ""},
        {"sName": "   "},
        {"sName": "Jane Doe"},
    ])
    assert listApi == [{"name": "Jane Doe"}]


def test_flistBuildApiCreators_falls_back_to_placeholder():
    """An all-empty creators list yields the Vaibify User placeholder."""
    from vaibify.reproducibility.zenodoClient import (
        flistBuildApiCreators as _flistBuildApiCreators,
    )
    listApi = _flistBuildApiCreators([{"sName": ""}, {}])
    assert listApi == [{"name": "Vaibify User"}]


def test_fnValidateApiMetadata_raises_when_title_missing():
    """Empty title must raise to prevent Zenodo 400s at publish time."""
    from vaibify.gui.syncDispatcher import _fnValidateApiMetadata
    with pytest.raises(ValueError, match="title"):
        _fnValidateApiMetadata(
            {"title": "", "creators": [{"name": "x"}]}
        )


def test_fnValidateApiMetadata_raises_when_creators_empty():
    """An empty creators list must raise before the container runs."""
    from vaibify.gui.syncDispatcher import _fnValidateApiMetadata
    with pytest.raises(ValueError, match="creator"):
        _fnValidateApiMetadata({"title": "t", "creators": []})


def test_fnValidateArchiveFilePaths_rejects_empty_string():
    """Empty path strings must raise even before null-byte check."""
    from vaibify.gui.syncDispatcher import _fnValidateArchiveFilePaths
    with pytest.raises(ValueError, match="non-empty"):
        _fnValidateArchiveFilePaths([""])


def test_fnValidateArchiveFilePaths_rejects_non_string():
    """A list element of the wrong type must raise, not silently pass."""
    from vaibify.gui.syncDispatcher import _fnValidateArchiveFilePaths
    with pytest.raises(ValueError, match="non-empty"):
        _fnValidateArchiveFilePaths([42])


def test_fdictBuildApiMetadata_adds_affiliation_when_present():
    """Affiliation must appear on the API creator dict when given."""
    from vaibify.gui.syncDispatcher import _fdictBuildApiMetadata
    dictApi = _fdictBuildApiMetadata({
        "sTitle": "T",
        "listCreators": [{
            "sName": "X", "sAffiliation": "UW",
        }],
    })
    assert dictApi["creators"][0]["affiliation"] == "UW"


def test_fdictBuildApiMetadata_adds_orcid_when_present():
    """ORCID must appear on the API creator dict when given."""
    from vaibify.gui.syncDispatcher import _fdictBuildApiMetadata
    dictApi = _fdictBuildApiMetadata({
        "sTitle": "T",
        "listCreators": [{
            "sName": "X", "sOrcid": "0000-0002-0000-0001",
        }],
    })
    assert dictApi["creators"][0]["orcid"] == "0000-0002-0000-0001"


def test_fdictBuildApiMetadata_description_falls_back_to_title():
    """A blank description is replaced with an Archived-by phrasing."""
    from vaibify.gui.syncDispatcher import _fdictBuildApiMetadata
    dictApi = _fdictBuildApiMetadata({
        "sTitle": "My Title",
        "sDescription": "   ",
        "listCreators": [{"sName": "X"}],
    })
    assert "My Title" in dictApi["description"]


def test_fdictBuildApiMetadata_drops_empty_keywords():
    """Keyword entries that are blank or non-string drop out."""
    from vaibify.gui.syncDispatcher import _fdictBuildApiMetadata
    dictApi = _fdictBuildApiMetadata({
        "sTitle": "T",
        "listCreators": [{"sName": "X"}],
        "listKeywords": ["", "  ", 42, "real"],
    })
    assert dictApi["keywords"] == ["real"]


def test_fdictBuildApiMetadata_omits_keywords_key_when_all_empty():
    """No keywords = no keywords key in the API payload."""
    from vaibify.gui.syncDispatcher import _fdictBuildApiMetadata
    dictApi = _fdictBuildApiMetadata({
        "sTitle": "T",
        "listCreators": [{"sName": "X"}],
        "listKeywords": ["", "  "],
    })
    assert "keywords" not in dictApi


def test_fdictBuildApiMetadata_related_url_produces_identifier_block():
    """A non-empty GitHub URL flows through to related_identifiers."""
    from vaibify.gui.syncDispatcher import _fdictBuildApiMetadata
    dictApi = _fdictBuildApiMetadata({
        "sTitle": "T",
        "listCreators": [{"sName": "X"}],
        "sRelatedGithubUrl": "https://github.com/user/repo",
    })
    listRelated = dictApi["related_identifiers"]
    assert listRelated[0]["identifier"] == (
        "https://github.com/user/repo"
    )
    assert listRelated[0]["relation"] == "isSupplementTo"


# ----------------------------------------------------------------------
# syncDispatcher: keyring health branches
# ----------------------------------------------------------------------


def _fMockDocker(iExitCode=0, sOutput=""):
    """Return a MagicMock that simulates docker exec results."""
    mockDocker = MagicMock()
    mockDocker.ftResultExecuteCommand.return_value = (
        iExitCode, sOutput,
    )
    return mockDocker


def test_fbKeyringBackendHealthy_returns_false_on_fail_backend():
    """The health probe reports False when the fail backend is active."""
    from vaibify.gui.syncDispatcher import _fbKeyringBackendHealthy
    mockDocker = _fMockDocker(
        0, "keyring.backends.fail Keyring",
    )
    assert not _fbKeyringBackendHealthy(mockDocker, "cid")


def test_fbKeyringBackendHealthy_returns_false_on_dotted_fail_keyring():
    """Dot-form 'fail.keyring' in the output also marks unhealthy."""
    from vaibify.gui.syncDispatcher import _fbKeyringBackendHealthy
    # The branch matches after .replace(" ", ".") collapses whitespace.
    mockDocker = _fMockDocker(
        0, "some_module fail.keyring_class",
    )
    assert not _fbKeyringBackendHealthy(mockDocker, "cid")


def test_fbKeyringBackendHealthy_returns_false_on_exec_failure():
    """Exit code != 0 flags the backend as unusable."""
    from vaibify.gui.syncDispatcher import _fbKeyringBackendHealthy
    mockDocker = _fMockDocker(1, "boom")
    assert not _fbKeyringBackendHealthy(mockDocker, "cid")


def test_fbKeyringBackendHealthy_returns_true_on_real_backend():
    """A healthy backend string reports True."""
    from vaibify.gui.syncDispatcher import _fbKeyringBackendHealthy
    mockDocker = _fMockDocker(
        0, "keyrings.alt.file PlaintextKeyring",
    )
    assert _fbKeyringBackendHealthy(mockDocker, "cid")


# ----------------------------------------------------------------------
# workflowManager: keyword normalization non-string branch
# ----------------------------------------------------------------------


def test_flistNormalizeKeywords_drops_non_string_entries():
    """Non-string items (ints, dicts) must be dropped, not raise."""
    from vaibify.gui.workflowManager import _flistNormalizeKeywords
    listOut = _flistNormalizeKeywords(["real", 42, None, {"k": "v"}, ""])
    assert listOut == ["real"]


def test_flistNormalizeKeywords_strips_each_entry():
    """Leading/trailing whitespace is stripped."""
    from vaibify.gui.workflowManager import _flistNormalizeKeywords
    listOut = _flistNormalizeKeywords(["  one  ", "\ttwo\n"])
    assert listOut == ["one", "two"]


def test_flistNormalizeKeywords_empty_list_returns_empty():
    """An empty input list returns an empty list (no defaults)."""
    from vaibify.gui.workflowManager import _flistNormalizeKeywords
    assert _flistNormalizeKeywords([]) == []


def test_fdictNormalizeZenodoMetadata_license_defaults_when_blank():
    """A missing or blank license normalises to the CC-BY-4.0 default."""
    from vaibify.gui.workflowManager import (
        _fdictNormalizeZenodoMetadata,
    )
    dictOut = _fdictNormalizeZenodoMetadata({
        "sTitle": "T",
        "listCreators": [{"sName": "X"}],
    })
    assert dictOut["sLicense"] == "CC-BY-4.0"


def test_flistNormalizeCreators_drops_empty_and_whitespace_names():
    """Creators with only whitespace names are dropped."""
    from vaibify.gui.workflowManager import _flistNormalizeCreators
    listOut = _flistNormalizeCreators([
        {"sName": "  "},
        {"sName": "Real Name", "sAffiliation": " UW ",
         "sOrcid": " 0000 "},
    ])
    assert len(listOut) == 1
    assert listOut[0]["sName"] == "Real Name"
    assert listOut[0]["sAffiliation"] == "UW"
    assert listOut[0]["sOrcid"] == "0000"


# ----------------------------------------------------------------------
# syncDispatcher: fbValidateZenodoToken error paths
# ----------------------------------------------------------------------


def test_fbValidateZenodoToken_rejects_production_ui_name():
    """The function takes the service key, not the UI instance name."""
    from vaibify.gui.syncDispatcher import fbValidateZenodoToken
    mockDocker = _fMockDocker()
    with pytest.raises(ValueError, match="Invalid Zenodo service"):
        fbValidateZenodoToken(mockDocker, "cid", "production")


# ----------------------------------------------------------------------
# syncDispatcher: archive path validation + script transport
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# syncRoutes: small-branch coverage via direct helper calls
# ----------------------------------------------------------------------


def test_fnPersistZenodoPublishRecord_handles_missing_fields():
    """Absent DOI / URL fields must leave the workflow dict untouched."""
    from vaibify.gui.routes.syncRoutes import (
        _fnPersistZenodoPublishRecord,
    )
    dictWf = {}
    _fnPersistZenodoPublishRecord(
        dictWf, {"iDepositId": 7}, "sandbox",
    )
    assert dictWf["sZenodoDepositionId"] == "7"
    assert "sZenodoLatestDoi" not in dictWf


def test_fdictParseZenodoResult_takes_last_marker_line():
    """When multiple marker lines exist the last one wins."""
    from vaibify.gui.routes.syncRoutes import _fdictParseZenodoResult
    sOut = (
        'ZENODO_RESULT={"iDepositId": 1}\n'
        'more stuff\n'
        'ZENODO_RESULT={"iDepositId": 2}\n'
    )
    assert _fdictParseZenodoResult(sOut)["iDepositId"] == 2


def test_fdictParseZenodoResult_handles_empty_output():
    """Blank output returns an empty dict rather than raising."""
    from vaibify.gui.routes.syncRoutes import _fdictParseZenodoResult
    assert _fdictParseZenodoResult("") == {}
    assert _fdictParseZenodoResult(None) == {}


def test_fsReadHostGitUserName_handles_timeout():
    """A git subprocess timeout falls back to the Vaibify User label."""
    from vaibify.gui.routes.syncRoutes import _fsReadHostGitUserName
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(
        cmd="git", timeout=5,
    )):
        assert _fsReadHostGitUserName() == "Vaibify User"


def test_fsReadHostGitUserName_handles_missing_git():
    """OSError (git not installed) falls back gracefully."""
    from vaibify.gui.routes.syncRoutes import _fsReadHostGitUserName
    with patch("subprocess.run", side_effect=OSError("no git")):
        assert _fsReadHostGitUserName() == "Vaibify User"


def test_fsReadHostGitUserName_strips_quotes_and_backslashes():
    """Sanitized shell-dangerous characters (for future contexts)."""
    from vaibify.gui.routes.syncRoutes import _fsReadHostGitUserName
    with patch("subprocess.run") as mockRun:
        mockResult = MagicMock()
        mockResult.stdout = "Rory'O\\Brien\n"
        mockRun.return_value = mockResult
        assert _fsReadHostGitUserName() == "RoryOBrien"


def test_fsReadHostGitUserName_empty_config_falls_back():
    """A git install with no user.name set also falls back."""
    from vaibify.gui.routes.syncRoutes import _fsReadHostGitUserName
    with patch("subprocess.run") as mockRun:
        mockResult = MagicMock()
        mockResult.stdout = "   \n"
        mockRun.return_value = mockResult
        assert _fsReadHostGitUserName() == "Vaibify User"


def test_fsResolveZenodoInstance_non_zenodo_service_returns_empty():
    """Overleaf / GitHub requests must not carry a Zenodo instance."""
    from vaibify.gui.routes.syncRoutes import _fsResolveZenodoInstance
    mockRequest = MagicMock()
    mockRequest.sService = "overleaf"
    assert _fsResolveZenodoInstance(mockRequest) == ""


def test_fsResolveZenodoInstance_default_is_sandbox():
    """A Zenodo request without sZenodoInstance defaults to sandbox."""
    from vaibify.gui.routes.syncRoutes import _fsResolveZenodoInstance
    mockRequest = MagicMock()
    mockRequest.sService = "zenodo"
    mockRequest.sZenodoInstance = None
    assert _fsResolveZenodoInstance(mockRequest) == "sandbox"


def test_fsResolveZenodoInstance_rejects_bad_value():
    """An unknown instance string raises HTTP 400."""
    from fastapi import HTTPException
    from vaibify.gui.routes.syncRoutes import _fsResolveZenodoInstance
    mockRequest = MagicMock()
    mockRequest.sService = "zenodo"
    mockRequest.sZenodoInstance = "staging"
    with pytest.raises(HTTPException) as excInfo:
        _fsResolveZenodoInstance(mockRequest)
    assert excInfo.value.status_code == 400


def test_fdictResolveZenodoMetadataForArchive_preserves_user_creators():
    """User-provided creators are kept verbatim, not overridden."""
    from vaibify.gui.routes.syncRoutes import (
        _fdictResolveZenodoMetadataForArchive,
    )
    dictWf = {
        "dictZenodoMetadata": {
            "sTitle": "Provided Title",
            "listCreators": [{"sName": "Real Author",
                              "sAffiliation": "UW",
                              "sOrcid": ""}],
            "sLicense": "MIT",
            "sDescription": "",
            "listKeywords": [],
            "sRelatedGithubUrl": "",
        },
        "sWorkflowName": "fallback-wf",
    }
    dictMeta = _fdictResolveZenodoMetadataForArchive(dictWf)
    assert dictMeta["sTitle"] == "Provided Title"
    assert dictMeta["listCreators"][0]["sName"] == "Real Author"


def test_fdictMetadataRequestToDict_default_license_applied():
    """An absent sLicense in the pydantic model resolves to CC-BY-4.0."""
    from vaibify.gui.routes.syncRoutes import (
        _fdictMetadataRequestToDict,
    )
    mockRequest = MagicMock()
    mockRequest.sTitle = "T"
    mockRequest.sDescription = None
    mockRequest.listCreators = []
    mockRequest.sLicense = None
    mockRequest.listKeywords = None
    mockRequest.sRelatedGithubUrl = None
    dictOut = _fdictMetadataRequestToDict(mockRequest)
    assert dictOut["sLicense"] == "CC-BY-4.0"
    assert dictOut["listKeywords"] == []
    assert dictOut["listCreators"] == []


def test_fdictMetadataRequestToDict_flattens_creator_models():
    """Pydantic ZenodoCreatorRequest objects are unwrapped into plain dicts."""
    from vaibify.gui.routes.syncRoutes import (
        _fdictMetadataRequestToDict,
    )
    mockCreator = MagicMock()
    mockCreator.sName = "Author"
    mockCreator.sAffiliation = "UW"
    mockCreator.sOrcid = ""
    mockRequest = MagicMock()
    mockRequest.sTitle = "T"
    mockRequest.sDescription = "D"
    mockRequest.listCreators = [mockCreator]
    mockRequest.sLicense = "MIT"
    mockRequest.listKeywords = ["one"]
    mockRequest.sRelatedGithubUrl = "https://github.com/a/b"
    dictOut = _fdictMetadataRequestToDict(mockRequest)
    assert dictOut["listCreators"][0] == {
        "sName": "Author", "sAffiliation": "UW", "sOrcid": "",
    }
    assert dictOut["sRelatedGithubUrl"] == "https://github.com/a/b"


# ----------------------------------------------------------------------
# workflowManager: Zenodo metadata end-to-end round trip
# ----------------------------------------------------------------------


def test_fnSetZenodoMetadata_round_trip_via_get():
    """Writing then reading yields the normalised stored value."""
    from vaibify.gui.workflowManager import (
        fnSetZenodoMetadata, fdictGetZenodoMetadata,
    )
    dictWf = {}
    fnSetZenodoMetadata(dictWf, {
        "sTitle": "  Spaces  ",
        "listCreators": [{"sName": "  Jane  "}],
        "sLicense": "MIT",
        "listKeywords": [" k1 ", "", "k2"],
        "sRelatedGithubUrl": " https://github.com/a/b ",
    })
    dictRead = fdictGetZenodoMetadata(dictWf)
    assert dictRead["sTitle"] == "Spaces"
    assert dictRead["listCreators"][0]["sName"] == "Jane"
    assert dictRead["listKeywords"] == ["k1", "k2"]
    assert dictRead["sRelatedGithubUrl"] == "https://github.com/a/b"


def test_fnSetZenodoMetadata_rejects_ftp_related_url():
    """FTP is not HTTP/HTTPS; the validator rejects it."""
    from vaibify.gui.workflowManager import fnSetZenodoMetadata
    with pytest.raises(ValueError, match="http"):
        fnSetZenodoMetadata({}, {
            "sTitle": "T",
            "listCreators": [{"sName": "X"}],
            "sLicense": "MIT",
            "sRelatedGithubUrl": "ftp://example.com/",
        })


def test_fdictInitializeZenodoMetadata_has_one_empty_creator_row():
    """The default template seeds an empty row so the form renders."""
    from vaibify.gui.workflowManager import (
        fdictInitializeZenodoMetadata,
    )
    dictDefault = fdictInitializeZenodoMetadata()
    assert dictDefault["listCreators"] == [
        {"sName": "", "sAffiliation": "", "sOrcid": ""},
    ]
    assert dictDefault["sLicense"] == "CC-BY-4.0"


# ----------------------------------------------------------------------
# /api/sync/{id}/track endpoint: per-file tracking opt-in (Zenodo badge)
# ----------------------------------------------------------------------


def test_set_tracking_endpoint_rejects_unknown_service(clientHttp):
    """Only Overleaf, Zenodo, and Github are valid tracking targets."""
    from tests.testSyncRoutesCoverage import (
        _fnConnectToContainer, S_CONTAINER_ID,
    )
    _fnConnectToContainer(clientHttp)
    responseHttp = clientHttp.post(
        f"/api/sync/{S_CONTAINER_ID}/track",
        json={
            "sPath": "/workspace/data.h5",
            "sService": "Dropbox",
            "bTrack": True,
        },
    )
    assert responseHttp.status_code == 400


def test_set_tracking_endpoint_accepts_zenodo_service(clientHttp):
    """Zenodo is a valid service for per-file tracking."""
    from tests.testSyncRoutesCoverage import (
        _fnConnectToContainer, S_CONTAINER_ID,
    )
    _fnConnectToContainer(clientHttp)
    with patch(
        "vaibify.gui.workflowManager.fnSetServiceTracking",
    ) as mockSet, patch(
        "vaibify.gui.workflowManager.fnSaveWorkflowToContainer",
    ):
        responseHttp = clientHttp.post(
            f"/api/sync/{S_CONTAINER_ID}/track",
            json={
                "sPath": "/workspace/out.pdf",
                "sService": "Zenodo",
                "bTrack": True,
            },
        )
    assert responseHttp.status_code == 200
    assert responseHttp.json() == {"bSuccess": True}
    assert mockSet.called


# The clientHttp fixture lives in testSyncRoutesCoverage.py; import here.
from tests.testSyncRoutesCoverage import clientHttp  # noqa: E402, F401
