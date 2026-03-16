"""Clean Zenodo API client using Bearer token authentication.

Provides upload, download, draft management, and search operations
against Zenodo or the Zenodo sandbox. Tokens are retrieved via the
secretManager module so that credentials never appear in source code.
"""

from pathlib import Path

import requests
from tqdm import tqdm


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

    def __init__(self, sService="sandbox"):
        _fnValidateService(sService)
        self._sBaseUrl = f"{_SERVICES[sService]}/api"
        self._sToken = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fnUploadFile(self, iDepositId, sFilePath):
        """Upload a file to an existing deposit's bucket."""
        dictDeposit = self.fdictGetDeposit(iDepositId)
        sBucketUrl = _fsExtractBucketUrl(dictDeposit)
        _fnStreamUpload(self, sBucketUrl, sFilePath)

    def fnDownloadFile(self, iRecordId, sFileName, sDestination):
        """Download a named file from a published record."""
        sUrl = f"{self._sBaseUrl}/records/{iRecordId}"
        dictRecord = self._fdictRequest("GET", sUrl)
        sFileUrl = _fsFindFileUrl(dictRecord, sFileName)
        _fnStreamDownload(self, sFileUrl, sDestination, sFileName)

    def fdictCreateDraft(self):
        """Create a new empty deposit draft and return its metadata."""
        sUrl = f"{self._sBaseUrl}/deposit/depositions"
        dictPayload = {"metadata": _fdictEmptyMetadata()}
        return self._fdictRequest("POST", sUrl, json=dictPayload)

    def fnPublishDraft(self, iDepositId):
        """Publish an existing draft deposit."""
        sUrl = (
            f"{self._sBaseUrl}/deposit/depositions"
            f"/{iDepositId}/actions/publish"
        )
        self._fdictRequest("POST", sUrl)

    def fnDeleteDraft(self, iDepositId):
        """Delete an unpublished draft deposit."""
        sUrl = f"{self._sBaseUrl}/deposit/depositions/{iDepositId}"
        self._fdictRequest("DELETE", sUrl)

    def fdictCopyDraft(self, iDepositId):
        """Create a new version draft from a published deposit."""
        sUrl = (
            f"{self._sBaseUrl}/deposit/depositions"
            f"/{iDepositId}/actions/newversion"
        )
        return self._fdictRequest("POST", sUrl)

    def fdictGetDeposit(self, iDepositId):
        """Retrieve metadata for a deposit."""
        sUrl = f"{self._sBaseUrl}/deposit/depositions/{iDepositId}"
        return self._fdictRequest("GET", sUrl)

    def flistSearchDeposits(self, sQuery):
        """Search deposits and return a list of result dicts."""
        sUrl = f"{self._sBaseUrl}/deposit/depositions"
        return self._fdictRequest("GET", sUrl, params={"q": sQuery})

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
            self._sToken = _fsRetrieveToken()
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


def _fsRetrieveToken():
    """Retrieve the Zenodo token through secretManager."""
    from vaibify.config.secretManager import fsRetrieveSecret

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
