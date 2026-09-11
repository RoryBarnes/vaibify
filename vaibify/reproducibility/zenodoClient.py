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
   lazily inside ``_fnStreamUpload``. A vaibify-package import at top
   level breaks this rule outright, and one did: the redaction helpers
   arrived later as ``from vaibify.reproducibility.credentialRedactor
   import ...``, which raises ImportError at ``/usr/share/vaibify``
   where no vaibify package exists. It is now a flat fallback, and
   ``credentialRedactor`` is staged beside this file.
2. The ``secretManager`` fallback for token acquisition only runs
   when ``sToken`` is ``None``. Container callers always pass the
   token explicitly (they read it from the container's keyring
   themselves), so the deferred ``vaibify.config.secretManager``
   import never fires inside the container.
"""

import hashlib
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

try:
    from vaibify.reproducibility.credentialRedactor import (
        fsRedactCredentials,
        fsRedactUrlCredentials,
    )
except ImportError:  # staged flat at /usr/share/vaibify, no package
    from credentialRedactor import (
        fsRedactCredentials,
        fsRedactUrlCredentials,
    )


class ZenodoError(Exception):
    """General Zenodo API error."""


class ZenodoAuthError(ZenodoError):
    """Authentication failed (401 or 403)."""


class ZenodoNotFoundError(ZenodoError):
    """Resource not found (404)."""


class ZenodoRateLimitError(ZenodoError):
    """Rate limit exceeded (429)."""


class ZenodoRedirectRefusedError(ZenodoError):
    """A fetch would have left the allowed hosts, and was not sent.

    Raised BEFORE the request that would have crossed over: the host
    is checked on the URL a caller hands in and on every ``Location``
    a redirect names, so a redirect is never followed on trust.
    """


# The only place a Zenodo host is spelled. Every reader of a deposit
# record maps the record's service name through this table; a record
# that names any other service is refused, never fetched.
_SERVICES = {
    "zenodo": "https://zenodo.org",
    "sandbox": "https://sandbox.zenodo.org",
}

# The DOI resolver, for a deposit record the client cannot address.
# Followed BY HAND through the same origin check as every other fetch,
# so it may send a request to Zenodo and nowhere else.
S_DOI_RESOLVER_BASE = "https://doi.org/"

# DataCite's test prefix, which every sandbox DOI carries. The
# sandbox's DOIs do not contain the word "sandbox", so a classifier
# that looked for it sent every sandbox deposit to production Zenodo.
_S_SANDBOX_DOI_PREFIX = "10.5072/"

# How many redirects a hand-followed fetch will take before refusing.
# Zenodo answers its API and file links directly (measured against
# both instances on 2026-09-11); the loop exists for the doi.org
# resolver and for the day either instance starts redirecting.
_I_MAX_REDIRECT_HOPS = 5

_CHUNK_SIZE = 1024 * 1024
_HASH_CHUNK_SIZE = 64 * 1024
# A ceiling for reading a deposit file INTO MEMORY. The hashing
# path streams and needs no cap; the JSON path materializes, and
# a deposit key is caller-supplied, so an unbounded read would
# let one pull a dataset into the hub's address space.
_I_JSON_FETCH_BYTE_CAP = 4 * 1024 * 1024
_TUPLE_REQUEST_TIMEOUT_SECONDS = (10, 60)
_TUPLE_UPLOAD_TIMEOUT_SECONDS = (10, 600)


__all__ = [
    "fdictBuildApiMetadata",
    "flistBuildApiCreators",
    "ZenodoClient",
    "ZenodoError",
    "ZenodoAuthError",
    "ZenodoNotFoundError",
    "ZenodoRateLimitError",
    "ZenodoRedirectRefusedError",
    "S_DOI_RESOLVER_BASE",
    "fdictZenodoServiceTable",
    "flistAllowedZenodoOrigins",
    "fresponseGetWithinAllowlist",
    "fsOriginOfUrl",
    "fsResolveServiceBaseUrl",
    "fsServiceForDoi",
    "fsZenodoTokenName",
    "fdictFetchRemoteHashes",
    "fdictRevokeZenodoToken",
]


class ZenodoClient:
    """Thin wrapper around the Zenodo REST API."""

    def __init__(self, sService="sandbox", sToken=None, sBaseUrl=None):
        _fnValidateService(sService)
        self._sService = sService
        self._sBaseUrl = sBaseUrl or f"{_SERVICES[sService]}/api"
        self._sToken = sToken

    @property
    def sService(self):
        """The service key this client was built for."""
        return self._sService

    def flistAllowedOrigins(self):
        """Return the origins a GET from this client may reach.

        The table's hosts plus this client's own base URL, so a client
        built over an injected base (a loopback fixture, the container
        script's fixed API base) can reach it and nothing else.
        """
        listOrigins = flistAllowedZenodoOrigins()
        sOwnOrigin = fsOriginOfUrl(self._sBaseUrl)
        if sOwnOrigin not in listOrigins:
            listOrigins.append(sOwnOrigin)
        return listOrigins

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
                timeout=_TUPLE_UPLOAD_TIMEOUT_SECONDS,
            )
        _fnCheckResponse(responseHttp)

    def fnDownloadFile(self, iRecordId, sFileName, sDestination):
        """Download a named file from a published record."""
        sUrl = f"{self._sBaseUrl}/records/{iRecordId}"
        dictRecord = self._fdictRequest("GET", sUrl)
        sFileUrl = _fsFindFileUrl(dictRecord, sFileName)
        _fnStreamDownload(self, sFileUrl, sDestination, sFileName)

    def fjsonFetchRecordFile(self, sRecordId, sFileName):
        """Return one deposit file decoded as JSON, or ``None``.

        ``None`` means "this record does not serve a readable JSON
        file by that name", and covers all three ways that happens:
        the deposit has no such key, the download fails, or the bytes
        do not parse. A caller cannot act differently on those, and
        raising here would turn a routine absence -- an archive
        published before any rerun existed -- into a failed verify.

        A record-scoped error (auth, 404, rate limit) still propagates
        from :func:`_fdictGetRecordSafely`: a researcher who cannot
        read their own declared record needs to be told that, rather
        than have it reported as an absent file.

        A method rather than a module function because the client is
        then ``self``, and because :meth:`fnDownloadFile` beside it
        already owns the by-name file lookup this parallels.
        """
        dictRecord = _fdictGetRecordSafely(self, sRecordId)
        sFileUrl = _fsFindFileUrlOrNone(dictRecord, sFileName)
        if not sFileUrl:
            return None
        baContent = _fbaFetchBoundedContent(self, sFileUrl)
        if baContent is None:
            return None
        try:
            return json.loads(baContent.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None

    def fdictFetchPublishedRecord(self, sRecordId):
        """Return one PUBLISHED record's metadata dict.

        Published records are public, so this works without a token —
        which is what lets a researcher point at an existing
        environment archive from the host, where no Zenodo credential
        lives. A record-scoped failure (404, auth, rate limit) is
        re-raised with the id named rather than swallowed: a
        researcher who mistyped a DOI needs to be told that, not shown
        an empty answer that reads as "the deposit does not describe
        its image".
        """
        return _fdictGetRecordSafely(self, sRecordId)

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
        """Send an authenticated request and return decoded JSON.

        A GET is hand-followed through :func:`fresponseGetWithinAllowlist`
        so no redirect is taken before its target host is checked. Any
        other method is sent without following redirects at all: a
        mutating call Zenodo answers with a 3xx is an error to report,
        never a request to replay somewhere else.
        """
        dictHeaders = _fdictBuildAuthHeader(self._fsGetToken())
        kwargs.setdefault("headers", {}).update(dictHeaders)
        kwargs.setdefault("timeout", _TUPLE_REQUEST_TIMEOUT_SECONDS)
        if sMethod.upper() == "GET":
            responseHttp = fresponseGetWithinAllowlist(
                sUrl, self.flistAllowedOrigins(),
                dictHeaders=kwargs["headers"], tTimeout=kwargs["timeout"],
                dictParams=kwargs.get("params"),
            )
        else:
            kwargs.setdefault("allow_redirects", False)
            responseHttp = requests.request(sMethod, sUrl, **kwargs)
        _fnCheckResponse(responseHttp)
        if responseHttp.status_code == 204:
            return {}
        return responseHttp.json()

    def _fsGetToken(self):
        """Lazy-load the Zenodo token via secretManager, "" when absent.

        Absence is an answer, not an error. Published records are
        public, so the read paths (record fetch, file hashing) work
        without a token — which is exactly the host-side verify's
        situation: the researcher's token lives in the CONTAINER
        keyring, and demanding one from the host keyring made every
        hub-side verify die on a KeyError before any network call. A
        mutating call sent without a token gets Zenodo's own 401,
        surfaced as the actionable ``ZenodoAuthError``.
        """
        if self._sToken is None:
            self._sToken = _fsRetrieveToken(self._sService) or ""
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


def fsResolveServiceBaseUrl(sService):
    """Return the base URL of a validated service name; raise otherwise."""
    _fnValidateService(sService)
    return _SERVICES[sService]


def fdictZenodoServiceTable():
    """Return a copy of the service -> base-URL table.

    For the one other place a Zenodo host must be known -- the shell
    ``reproduce.sh`` renders -- which is generated from this table so
    the two lanes cannot disagree about where a deposit lives.
    """
    return dict(_SERVICES)


def fsServiceForDoi(sDoi):
    """Return the service a DOI's PREFIX names: sandbox or production.

    Used only for a deposit record written before the record carried
    its service. A sandbox DOI is ``10.5072/zenodo.<id>`` -- DataCite's
    test prefix -- and nothing in it says "sandbox"; classifying by the
    word sent every sandbox deposit to production Zenodo, where the
    record 404s and the caller fell through to composing a download
    URL from a DataCite error page.
    """
    if str(sDoi or "").strip().startswith(_S_SANDBOX_DOI_PREFIX):
        return "sandbox"
    return "zenodo"


def fsOriginOfUrl(sUrl):
    """Return ``scheme://netloc`` of a URL, lowercased, or ``""``.

    The netloc is compared whole -- port and any userinfo included --
    so ``zenodo.org:8443`` and ``zenodo.org@evil.example`` are both
    different origins from ``zenodo.org``, and refused.
    """
    tParts = urlsplit(str(sUrl or ""))
    if not tParts.scheme or not tParts.netloc:
        return ""
    return f"{tParts.scheme.lower()}://{tParts.netloc.lower()}"


def flistAllowedZenodoOrigins():
    """Return the origins of every service in the table."""
    return [fsOriginOfUrl(sBaseUrl) for sBaseUrl in _SERVICES.values()]


def fresponseGetWithinAllowlist(
    sUrl, listAllowedOrigins, dictHeaders=None, bStream=False,
    tTimeout=_TUPLE_REQUEST_TIMEOUT_SECONDS, dictParams=None,
):
    """GET one URL, following redirects BY HAND; return the response.

    Every hop's origin -- the URL handed in, then each ``Location`` --
    is checked against ``listAllowedOrigins`` BEFORE the request for
    it is sent. ``requests`` with ``allow_redirects=True`` (and ``curl
    -L``) has already contacted the redirected host by the time the
    final URL is readable, which is why the following is done here
    rather than delegated. A ``Location`` whose origin differs from the
    URL that answered it is fetched without the Authorization header,
    so a token is never carried to a host that did not receive the
    first request.

    Raises :class:`ZenodoRedirectRefusedError`, naming the refused
    host and never echoing the URL's path or query, when a hop leaves
    the allowlist or the hop limit is exceeded.
    """
    dictHeaders = dict(dictHeaders or {})
    for _iHop in range(_I_MAX_REDIRECT_HOPS + 1):
        _fnRefuseOutsideAllowlist(sUrl, listAllowedOrigins)
        responseHttp = requests.get(
            sUrl, headers=dictHeaders, stream=bStream, timeout=tTimeout,
            params=dictParams, allow_redirects=False,
        )
        if not _fbAnswersWithRedirect(responseHttp):
            return responseHttp
        sNextUrl = urljoin(sUrl, str(responseHttp.headers.get("Location")))
        responseHttp.close()
        if fsOriginOfUrl(sNextUrl) != fsOriginOfUrl(sUrl):
            dictHeaders.pop("Authorization", None)
        sUrl, dictParams = sNextUrl, None
    raise ZenodoRedirectRefusedError(
        f"refusing to follow more than {_I_MAX_REDIRECT_HOPS} redirects "
        f"from {fsOriginOfUrl(sUrl)}"
    )


def _fbAnswersWithRedirect(responseHttp):
    """Return True iff the response is a 3xx that names a ``Location``.

    Read off the status code and the header rather than the library's
    ``is_redirect`` so a stand-in response with neither reads as a
    final answer, never as a hop to follow.
    """
    iStatus = getattr(responseHttp, "status_code", None)
    if not isinstance(iStatus, int) or not 300 <= iStatus < 400:
        return False
    dictHeaders = getattr(responseHttp, "headers", None) or {}
    return bool(dictHeaders.get("Location"))


def _fnRefuseOutsideAllowlist(sUrl, listAllowedOrigins):
    """Raise unless ``sUrl``'s origin is one of the allowed ones."""
    sOrigin = fsOriginOfUrl(sUrl)
    if sOrigin and sOrigin in listAllowedOrigins:
        return
    raise ZenodoRedirectRefusedError(
        "refusing to fetch from "
        + (sOrigin or "a URL with no host")
        + ": it is not a Zenodo host this vaibify knows ("
        + ", ".join(listAllowedOrigins) + ")"
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
    """Retrieve the Zenodo token for ``sService``, or ``""`` when absent.

    Reads the namespaced slot first (``zenodo_token_sandbox`` or
    ``zenodo_token_production``) and falls back to the legacy
    ``zenodo_token`` slot when the namespaced one is empty so users
    migrating from the pre-namespaced layout keep working. A store
    holding neither returns ``""`` rather than raising: the caller may
    be a read of a public record, which needs no token at all.
    """
    from vaibify.config.secretManager import fbSecretExists, fsRetrieveSecret

    for sSlot in (fsZenodoTokenName(sService), "zenodo_token"):
        if fbSecretExists(sSlot, "keyring"):
            return fsRetrieveSecret(sSlot, "keyring")
    return ""


def _fdictBuildAuthHeader(sToken):
    """Return an Authorization header dict, empty when there is no token.

    Sending ``Bearer <empty>`` would turn a public, tokenless read
    into a 401; sending nothing lets Zenodo answer by resource — a
    published record serves, a mutation refuses with its own 401.
    """
    if not sToken:
        return {}
    return {"Authorization": f"Bearer {sToken}"}


def fdictBuildApiMetadata(dictMetadata, sUploadType="dataset"):
    """Translate a vaibify metadata dict into the Zenodo API shape.

    Vaibify carries metadata in its own Hungarian shape (``sTitle``,
    ``listCreators``) and Zenodo requires ``title``, ``creators`` and
    an upload type. Passing the vaibify shape straight to the API is
    not a partial success -- Zenodo rejects the draft with "Missing
    data for required field" naming three fields at once, which is
    what the environment-archive deposit did until 2026-09-09.

    It lives HERE, at the Zenodo boundary both deposit paths already
    import, because the mapping carries defaults a caller cannot see:
    a creators list that falls back to a placeholder rather than an
    empty array Zenodo refuses, and a licence default. Two copies of
    that would diverge silently, and the divergence would only ever
    surface as a rejected upload after a multi-hundred-megabyte
    save.
    """
    sTitle = (dictMetadata.get("sTitle") or "").strip() or (
        "Vaibify archive"
    )
    sDescription = (
        dictMetadata.get("sDescription") or ""
    ).strip() or f"Archived by Vaibify ({sTitle})"
    dictApi = {
        "title": sTitle,
        "upload_type": sUploadType,
        "description": sDescription,
        "creators": flistBuildApiCreators(
            dictMetadata.get("listCreators") or []
        ),
        "license": (
            dictMetadata.get("sLicense") or "CC-BY-4.0"
        ).strip(),
    }
    listKeywords = [
        sKeyword.strip()
        for sKeyword in (dictMetadata.get("listKeywords") or [])
        if isinstance(sKeyword, str) and sKeyword.strip()
    ]
    if listKeywords:
        dictApi["keywords"] = listKeywords
    sRelatedUrl = (dictMetadata.get("sRelatedGithubUrl") or "").strip()
    if sRelatedUrl:
        dictApi["related_identifiers"] = [{
            "identifier": sRelatedUrl,
            "relation": "isSupplementTo",
            "resource_type": "software",
        }]
    return dictApi


def flistBuildApiCreators(listCreators):
    """Build the Zenodo creators list; fall back to a placeholder.

    Zenodo refuses an empty creators array, so a project that has
    declared none still deposits rather than failing at the upload.
    """
    listApi = []
    for dictCreator in listCreators:
        sName = (dictCreator.get("sName") or "").strip()
        if not sName:
            continue
        listApi.append(_fdictBuildOneApiCreator(dictCreator, sName))
    return listApi or [{"name": "Vaibify User"}]


def _fdictBuildOneApiCreator(dictCreator, sName):
    """Build a single Zenodo-shaped creator dict from a vaibify creator."""
    dictApi = {"name": sName}
    sAffiliation = (dictCreator.get("sAffiliation") or "").strip()
    if sAffiliation:
        dictApi["affiliation"] = sAffiliation
    sOrcid = (dictCreator.get("sOrcid") or "").strip()
    if sOrcid:
        dictApi["orcid"] = sOrcid
    return dictApi


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


def _fdictBuildUploadHeaders(clientZenodo):
    """Return auth + content-type headers for a Zenodo bucket PUT."""
    dictHeaders = _fdictBuildAuthHeader(clientZenodo._fsGetToken())
    dictHeaders["Content-Type"] = "application/octet-stream"
    return dictHeaders


def _fnStreamUpload(clientZenodo, sBucketUrl, sFilePath):
    """Stream-upload a file to a Zenodo bucket with progress bar."""
    from tqdm import tqdm
    pathFile = Path(sFilePath)
    if not pathFile.is_file():
        raise FileNotFoundError(f"File not found: '{sFilePath}'")
    iFileSize = pathFile.stat().st_size
    sFileName = pathFile.name
    sUrl = f"{sBucketUrl}/{sFileName}"
    dictHeaders = _fdictBuildUploadHeaders(clientZenodo)
    with open(pathFile, "rb") as fileHandle:
        barProgress = tqdm(
            total=iFileSize, unit="B",
            unit_scale=True, desc=sFileName,
        )
        responseHttp = requests.put(
            sUrl, headers=dictHeaders,
            data=_fiterReadChunks(fileHandle, barProgress),
            timeout=_TUPLE_UPLOAD_TIMEOUT_SECONDS,
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
    responseHttp = fresponseGetWithinAllowlist(
        sFileUrl, clientZenodo.flistAllowedOrigins(),
        dictHeaders=_fdictBuildAuthHeader(clientZenodo._fsGetToken()),
        bStream=True, tTimeout=(10, 60),
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
    """Raise a typed exception for HTTP errors.

    The response body is scrubbed of token-bearing URLs before being
    embedded in any exception message; this prevents tokens echoed in
    Zenodo error payloads (e.g. on per-file fetches) from leaking
    into raised exceptions, logs, or user-visible toasts.
    """
    iStatus = responseHttp.status_code
    if 200 <= iStatus < 300:
        return
    sBody = _fsRedactToken(responseHttp.text[:500])
    if iStatus in (401, 403):
        raise ZenodoAuthError(
            f"Zenodo authentication failed ({iStatus}): {sBody}"
        )
    if iStatus == 404:
        raise ZenodoNotFoundError(
            f"Zenodo resource not found ({iStatus}): {sBody}"
        )
    if iStatus == 429:
        raise ZenodoRateLimitError(
            f"Zenodo rate limit exceeded ({iStatus}): {sBody}"
        )
    raise ZenodoError(
        f"Zenodo API error ({iStatus}): {sBody}"
    )


def fdictFetchRemoteHashes(
    sRecordId, listRelPaths=None, clientZenodo=None, sService="sandbox",
):
    """Fetch each file in a Zenodo deposit and return SHA-256 hex digests.

    Returns a dict mapping each deposit file's ``key`` (its path on
    Zenodo) to its SHA-256 hex digest. When ``listRelPaths`` is
    ``None`` every file in the record is hashed. When provided, only
    files whose key is in ``listRelPaths`` are hashed; requested keys
    that are absent from the deposit map to ``None``. The result
    iterates in the deposit's listing order, with any missing
    ``listRelPaths`` entries appended afterward in the order given.

    Streams each download in 64 KB chunks so multi-gigabyte files do
    not load into memory. Raises actionable errors for auth (401/403),
    record-not-found (404), and rate-limit (429) responses; tokens are
    redacted from any URL that appears in an error message.
    """
    clientResolved = clientZenodo or ZenodoClient(sService=sService)
    dictRecord = _fdictGetRecordSafely(clientResolved, sRecordId)
    listFiles = list(dictRecord.get("files", []))
    return _fdictHashSelectedFiles(clientResolved, listFiles, listRelPaths)


_RECORD_ERROR_TEMPLATES = (
    (
        ZenodoAuthError,
        "Zenodo authentication failed while fetching record '{id}'. "
        "Verify the stored Zenodo token. ({detail})",
    ),
    (
        ZenodoNotFoundError,
        "Zenodo record '{id}' not found. ({detail})",
    ),
    (
        ZenodoRateLimitError,
        "Zenodo rate limit hit while fetching record '{id}'; "
        "retry after a backoff. ({detail})",
    ),
)


def _fdictGetRecordSafely(clientZenodo, sRecordId):
    """GET a published record, surfacing record-scoped errors clearly."""
    sUrl = f"{clientZenodo._sBaseUrl}/records/{sRecordId}"
    try:
        return clientZenodo._fdictRequest("GET", sUrl)
    except ZenodoError as errorCaught:
        _fnReraiseRecordError(errorCaught, sRecordId)


def _fnReraiseRecordError(excOriginal, sRecordId):
    """Re-raise a record-scoped Zenodo error with a friendlier message."""
    sDetail = _fsRedactToken(str(excOriginal))
    for clsError, sTemplate in _RECORD_ERROR_TEMPLATES:
        if isinstance(excOriginal, clsError):
            raise clsError(
                sTemplate.format(id=sRecordId, detail=sDetail)
            ) from None
    raise excOriginal


def _fsFindFileUrlOrNone(dictRecord, sFileName):
    """Return the download URL for a deposit key, or "" when absent."""
    for dictFile in dictRecord.get("files", []) or []:
        if dictFile.get("key") == sFileName:
            dictLinks = dictFile.get("links") or {}
            return dictLinks.get("self") or dictLinks.get("download") or ""
    return ""


def _fbaFetchBoundedContent(clientZenodo, sFileUrl):
    """Stream up to the byte cap; ``None`` on any transport failure.

    A bounded read is the one download path where the caller must be
    able to see the ceiling, so the cap is a module constant beside
    the chunk sizes rather than a parameter.
    """
    try:
        responseHttp = fresponseGetWithinAllowlist(
            sFileUrl, clientZenodo.flistAllowedOrigins(),
            dictHeaders=_fdictBuildAuthHeader(clientZenodo._fsGetToken()),
            bStream=True, tTimeout=(10, 60),
        )
        _fnCheckResponse(responseHttp)
    except (requests.RequestException, ZenodoError):
        return None
    baBuffer = bytearray()
    for baChunk in responseHttp.iter_content(_HASH_CHUNK_SIZE):
        baBuffer.extend(baChunk)
        if len(baBuffer) > _I_JSON_FETCH_BYTE_CAP:
            return None
    return bytes(baBuffer)


def _fdictHashSelectedFiles(clientZenodo, listFiles, listRelPaths):
    """Hash the subset of listFiles selected by listRelPaths."""
    dictResult = {}
    setRequested = set(listRelPaths) if listRelPaths is not None else None
    for dictFile in listFiles:
        sKey = dictFile.get("key")
        if setRequested is not None and sKey not in setRequested:
            continue
        dictResult[sKey] = _fsHashRemoteFile(clientZenodo, dictFile)
    if listRelPaths is not None:
        _fnFillMissingRequestedPaths(dictResult, listRelPaths)
    return dictResult


def _fnFillMissingRequestedPaths(dictResult, listRelPaths):
    """Add ``None`` entries for requested paths not present in deposit."""
    for sRelPath in listRelPaths:
        if sRelPath not in dictResult:
            dictResult[sRelPath] = None


def _fsHashRemoteFile(clientZenodo, dictFile):
    """Stream-download one deposit file and return its SHA-256 digest."""
    sFileUrl = dictFile["links"]["self"]
    try:
        responseHttp = fresponseGetWithinAllowlist(
            sFileUrl, clientZenodo.flistAllowedOrigins(),
            dictHeaders=_fdictBuildAuthHeader(clientZenodo._fsGetToken()),
            bStream=True, tTimeout=(10, 60),
        )
    except requests.RequestException as errorCaught:
        raise ZenodoError(
            f"Network error fetching Zenodo file: "
            f"{_fsRedactToken(str(errorCaught))}"
        ) from None
    _fnCheckResponse(responseHttp)
    return _fsHashStreamingResponse(responseHttp)


def _fsHashStreamingResponse(responseHttp):
    """Consume an iter_content stream and return its SHA-256 hex digest."""
    from vaibify.reproducibility._hashing import fsHashChunkIteratorSha256
    return fsHashChunkIteratorSha256(
        responseHttp.iter_content(_HASH_CHUNK_SIZE),
    )


def _fsRedactToken(sMessage):
    """Strip credential-bearing URLs / tokens from sMessage.

    Preserves Zenodo's behaviour of redacting per-URL query parameters
    (``access_token=…`` / ``token=…``) so the surrounding error text
    survives intact, while also scrubbing any other credential shapes
    via :func:`fsRedactCredentials`.
    """
    if not sMessage:
        return ""
    listParts = [
        fsRedactUrlCredentials(sPart) for sPart in sMessage.split()
    ]
    return fsRedactCredentials(" ".join(listParts))


def fdictRevokeZenodoToken(sService="sandbox"):
    """Clear a Zenodo PAT from the local keyring.

    Zenodo does not expose a token-revocation endpoint, so the
    upstream side is logged as a no-op pointing to the web UI. The
    local keyring slot for the requested service is cleared via
    ``fnDeleteSecret``. Returns the same dict shape as the GitHub
    and Overleaf revokers so the CLI can render a uniform status.
    """
    sSlot = fsZenodoTokenName(sService)
    bLocal, sLocalMessage = _ftClearLocalZenodoCredential(sSlot)
    sWebPath = (
        "zenodo.org/account/settings/applications"
        if sService == "zenodo"
        else "sandbox.zenodo.org/account/settings/applications"
    )
    return {
        "bUpstreamRevoked": False,
        "bLocalCleared": bLocal,
        "sMessage": (
            f"Zenodo does not expose a revocation API; revoke at "
            f"{sWebPath}, then "
        ) + sLocalMessage,
    }


def _ftClearLocalZenodoCredential(sSlot):
    """Delete a single Zenodo keyring slot if it exists."""
    from vaibify.config.secretManager import fnDeleteSecret
    try:
        fnDeleteSecret(sSlot, "keyring")
    except Exception as errorDelete:
        return (
            False,
            f"local clear failed ({type(errorDelete).__name__}).",
        )
    return (True, f"local slot '{sSlot}' cleared.")
