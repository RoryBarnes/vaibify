"""Clean Zenodo API client using Bearer token authentication.

Provides upload, download, draft management, and search operations
against Zenodo or the Zenodo sandbox. Tokens are retrieved via the
secretManager module so that credentials never appear in source code.

This module is also shipped into the vaibify workflow container at
``/usr/share/vaibify/zenodoClient.py`` so the container-side Zenodo
archive script can call the same API surface instead of re-
implementing every HTTP path. That deployment has two consequences:

1. Top-level imports must stay container-safe. ``keyring`` is always
   present; ``requests`` is present when a workflow uses this
   archive path; ``tqdm`` is optional and is therefore imported
   lazily inside ``_fnStreamUpload``.
2. The ``secretManager`` fallback for token acquisition only runs
   when ``sToken`` is ``None``. Container callers always pass the
   token explicitly (they read it from the container's keyring
   themselves), so the deferred ``vaibify.config.secretManager``
   import never fires inside the container.
"""

from pathlib import Path

import requests


class ZenodoError(Exception):
    """General Zenodo API error."""


class ZenodoAuthError(ZenodoError):
    """Authentication failed (401 or 403)."""


class ZenodoNotFoundError(ZenodoError):
    """Resource not found (404)."""


_SERVICES = {
    "zenodo": "https://zenodo.org",
    "sandbox": "https://sandbox.zenodo.org",
}

_CHUNK_SIZE = 1024 * 1024


class ZenodoClient:
    """Thin wrapper around the Zenodo REST API."""

    def __init__(self, sService="sandbox", sToken=None, sBaseUrl=None):
        _fnValidateService(sService)
        self._sService = sService
        self._sBaseUrl = sBaseUrl or f"{_SERVICES[sService]}/api"
        self._sToken = sToken

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fnUploadFile(self, iDepositId, sFilePath):
        """Upload a file to an existing deposit's bucket."""
        dictDeposit = self.fdictGetDeposit(iDepositId)
        sBucketUrl = _fsExtractBucketUrl(dictDeposit)
        _fnStreamUpload(self, sBucketUrl, sFilePath)

    def fnUploadToBucket(self, sBucketUrl, sFilePath):
        """Upload a file directly to a known bucket URL.

        Host callers use :meth:`fnUploadFile`, which refetches the
        deposit to discover the bucket. Container callers already have
        the bucket URL from the draft they just created, so this path
        skips the extra GET and the tqdm progress bar (tqdm is not
        guaranteed to be installed inside the container).
        """
        pathFile = Path(sFilePath)
        if not pathFile.is_file():
            raise FileNotFoundError(f"File not found: '{sFilePath}'")
        sUploadUrl = f"{sBucketUrl}/{pathFile.name}"
        dictHeaders = _fdictBuildAuthHeader(self._fsGetToken())
        dictHeaders["Content-Type"] = "application/octet-stream"
        with open(pathFile, "rb") as fileHandle:
            responseHttp = requests.put(
                sUploadUrl, headers=dictHeaders, data=fileHandle,
            )
        _fnCheckResponse(responseHttp)

    def fnDownloadFile(self, iRecordId, sFileName, sDestination):
        """Download a named file from a published record."""
        sUrl = f"{self._sBaseUrl}/records/{iRecordId}"
        dictRecord = self._fdictRequest("GET", sUrl)
        sFileUrl = _fsFindFileUrl(dictRecord, sFileName)
        _fnStreamDownload(self, sFileUrl, sDestination, sFileName)

    def fdictCreateDraft(self, dictMetadata=None):
        """Create a new deposit draft and return its metadata.

        ``dictMetadata`` is optional; when ``None`` the draft is
        created with the minimal placeholder metadata from
        ``_fdictEmptyMetadata``. The archive flow passes the full
        Zenodo-shape metadata here so the metadata and the draft are
        created in a single POST.
        """
        sUrl = f"{self._sBaseUrl}/deposit/depositions"
        dictPayload = {
            "metadata": dictMetadata or _fdictEmptyMetadata(),
        }
        return self._fdictRequest("POST", sUrl, json=dictPayload)

    def fnSetMetadata(self, iDepositId, dictMetadata):
        """Update deposit metadata before publishing."""
        sUrl = f"{self._sBaseUrl}/deposit/depositions/{iDepositId}"
        dictPayload = {"metadata": dictMetadata}
        self._fdictRequest("PUT", sUrl, json=dictPayload)

    def fnPublishDraft(self, iDepositId):
        """Publish an existing draft deposit."""
        sUrl = self._fsPublishUrl(iDepositId)
        self._fdictRequest("POST", sUrl)

    def fdictPublishDraft(self, iDepositId):
        """Publish a draft and return the published deposit dict.

        The archive flow needs the ``doi``, ``conceptdoi`` and
        ``links.html`` fields from the publish response; this is the
        dict-returning counterpart to :meth:`fnPublishDraft`.
        """
        return self._fdictRequest("POST", self._fsPublishUrl(iDepositId))

    def fnDeleteDraft(self, iDepositId):
        """Delete an unpublished draft deposit."""
        sUrl = f"{self._sBaseUrl}/deposit/depositions/{iDepositId}"
        self._fdictRequest("DELETE", sUrl)

    def fdictCopyDraft(self, iDepositId):
        """Create a new version draft from a published deposit.

        Returns the raw ``newversion`` action response, whose
        ``links.latest_draft`` points at the new draft. Call
        :meth:`fdictGetNewVersionDraft` for the complete draft dict in
        one hop.
        """
        sUrl = (
            f"{self._sBaseUrl}/deposit/depositions"
            f"/{iDepositId}/actions/newversion"
        )
        return self._fdictRequest("POST", sUrl)

    def fdictGetNewVersionDraft(self, iParentDepositId):
        """Create a newversion draft and return the draft dict itself.

        Combines the ``actions/newversion`` POST with the
        ``links.latest_draft`` GET so callers (notably the container-
        side archive script) get a draft dict with ``id`` and
        ``links.bucket`` in one call.
        """
        dictNewVersion = self.fdictCopyDraft(iParentDepositId)
        sDraftUrl = dictNewVersion["links"]["latest_draft"]
        return self._fdictRequest("GET", sDraftUrl)

    def fnClearDraftFiles(self, iDepositId):
        """Delete every existing file attached to a draft deposit.

        The newversion flow inherits the parent's file list; vaibify
        re-uploads a fresh set per version, so inherited files must be
        cleared before the new uploads to avoid duplicates.
        """
        dictDeposit = self.fdictGetDeposit(iDepositId)
        for dictFile in dictDeposit.get("files", []):
            sFileId = dictFile.get("id") or dictFile.get("file_id")
            if not sFileId:
                continue
            sUrl = (
                f"{self._sBaseUrl}/deposit/depositions"
                f"/{iDepositId}/files/{sFileId}"
            )
            self._fdictRequest("DELETE", sUrl)

    def fdictGetDeposit(self, iDepositId):
        """Retrieve metadata for a deposit."""
        sUrl = f"{self._sBaseUrl}/deposit/depositions/{iDepositId}"
        return self._fdictRequest("GET", sUrl)

    def flistSearchDeposits(self, sQuery):
        """Search deposits and return a list of result dicts."""
        sUrl = f"{self._sBaseUrl}/deposit/depositions"
        return self._fdictRequest("GET", sUrl, params={"q": sQuery})

    def _fsPublishUrl(self, iDepositId):
        """Return the publish-action URL for a given deposit id."""
        return (
            f"{self._sBaseUrl}/deposit/depositions"
            f"/{iDepositId}/actions/publish"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fdictRequest(self, sMethod, sUrl, **kwargs):
        """Send an authenticated request and return decoded JSON."""
        dictHeaders = _fdictBuildAuthHeader(self._fsGetToken())
        kwargs.setdefault("headers", {}).update(dictHeaders)
        responseHttp = requests.request(sMethod, sUrl, **kwargs)
        _fnCheckResponse(responseHttp)
        if responseHttp.status_code == 204:
            return {}
        return responseHttp.json()

    def _fsGetToken(self):
        """Lazy-load the Zenodo token via secretManager."""
        if self._sToken is None:
            self._sToken = _fsRetrieveToken(self._sService)
        return self._sToken


# ------------------------------------------------------------------
# Module-level helpers (keep class methods short)
# ------------------------------------------------------------------


def _fnValidateService(sService):
    """Raise ValueError for unknown service names."""
    if sService not in _SERVICES:
        raise ValueError(
            f"Unknown Zenodo service '{sService}'. "
            f"Valid options: {sorted(_SERVICES)}"
        )


def fsZenodoTokenName(sService):
    """Return the keyring slot name for a given Zenodo service.

    ``sService`` is the ZenodoClient service key (``"sandbox"`` or
    ``"zenodo"``); the keyring slot follows the instance naming the
    user sees in the UI (``sandbox`` / ``production``).
    """
    _fnValidateService(sService)
    if sService == "zenodo":
        return "zenodo_token_production"
    return "zenodo_token_sandbox"


def _fsRetrieveToken(sService="sandbox"):
    """Retrieve the Zenodo token for ``sService`` via secretManager.

    Reads the namespaced slot first (``zenodo_token_sandbox`` or
    ``zenodo_token_production``) and falls back to the legacy
    ``zenodo_token`` slot when the namespaced one is empty so users
    migrating from the pre-namespaced layout keep working.
    """
    from vaibify.config.secretManager import fbSecretExists, fsRetrieveSecret

    sNamespaced = fsZenodoTokenName(sService)
    if fbSecretExists(sNamespaced, "keyring"):
        return fsRetrieveSecret(sNamespaced, "keyring")
    return fsRetrieveSecret("zenodo_token", "keyring")


def _fdictBuildAuthHeader(sToken):
    """Return an Authorization header dict using Bearer scheme."""
    return {"Authorization": f"Bearer {sToken}"}


def _fdictEmptyMetadata():
    """Return the minimal metadata dict for a new deposit."""
    return {
        "title": "",
        "upload_type": "dataset",
        "description": "Uploaded by Vaibify",
        "creators": [{"name": "Vaibify"}],
    }


def _fsExtractBucketUrl(dictDeposit):
    """Extract the bucket URL from a deposit metadata dict."""
    try:
        return dictDeposit["links"]["bucket"]
    except KeyError:
        raise ZenodoError(
            "Deposit metadata missing 'links.bucket'. "
            "Is this an unpublished draft?"
        )


def _fsFindFileUrl(dictRecord, sFileName):
    """Find the download URL for a file within a record."""
    for dictFile in dictRecord.get("files", []):
        if dictFile.get("key") == sFileName:
            return dictFile["links"]["self"]
    raise ZenodoNotFoundError(
        f"File '{sFileName}' not found in record."
    )


def _fnStreamUpload(clientZenodo, sBucketUrl, sFilePath):
    """Stream-upload a file to a Zenodo bucket with progress bar."""
    from tqdm import tqdm
    pathFile = Path(sFilePath)
    if not pathFile.is_file():
        raise FileNotFoundError(f"File not found: '{sFilePath}'")
    iFileSize = pathFile.stat().st_size
    sFileName = pathFile.name
    sUrl = f"{sBucketUrl}/{sFileName}"
    dictHeaders = _fdictBuildAuthHeader(clientZenodo._fsGetToken())
    dictHeaders["Content-Type"] = "application/octet-stream"
    with open(pathFile, "rb") as fileHandle:
        barProgress = tqdm(
            total=iFileSize, unit="B",
            unit_scale=True, desc=sFileName,
        )
        responseHttp = requests.put(
            sUrl, headers=dictHeaders,
            data=_fiterReadChunks(fileHandle, barProgress),
        )
        barProgress.close()
    _fnCheckResponse(responseHttp)


def _fiterReadChunks(fileHandle, barProgress):
    """Yield file chunks and update the progress bar."""
    while True:
        baChunk = fileHandle.read(_CHUNK_SIZE)
        if not baChunk:
            break
        barProgress.update(len(baChunk))
        yield baChunk


def _fnStreamDownload(clientZenodo, sFileUrl, sDestination, sFileName):
    """Stream-download a file with progress bar."""
    pathDest = Path(sDestination)
    pathDest.mkdir(parents=True, exist_ok=True)
    pathOutput = pathDest / sFileName
    dictHeaders = _fdictBuildAuthHeader(clientZenodo._fsGetToken())
    responseHttp = requests.get(
        sFileUrl, headers=dictHeaders, stream=True,
    )
    _fnCheckResponse(responseHttp)
    iTotal = int(responseHttp.headers.get("content-length", 0))
    _fnWriteStreamToFile(responseHttp, pathOutput, sFileName, iTotal)


def _fnWriteStreamToFile(responseHttp, pathOutput, sFileName, iTotal):
    """Write streaming response content to disk with progress bar."""
    from tqdm import tqdm
    barProgress = tqdm(
        total=iTotal, unit="B",
        unit_scale=True, desc=sFileName,
    )
    with open(pathOutput, "wb") as fileHandle:
        for baChunk in responseHttp.iter_content(_CHUNK_SIZE):
            fileHandle.write(baChunk)
            barProgress.update(len(baChunk))
    barProgress.close()


def _fnCheckResponse(responseHttp):
    """Raise a typed exception for HTTP errors."""
    iStatus = responseHttp.status_code
    if 200 <= iStatus < 300:
        return
    sBody = responseHttp.text[:500]
    if iStatus in (401, 403):
        raise ZenodoAuthError(
            f"Zenodo authentication failed ({iStatus}): {sBody}"
        )
    if iStatus == 404:
        raise ZenodoNotFoundError(
            f"Zenodo resource not found ({iStatus}): {sBody}"
        )
    raise ZenodoError(
        f"Zenodo API error ({iStatus}): {sBody}"
    )
