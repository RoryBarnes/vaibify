"""A loopback stand-in for the GitHub and Zenodo APIs, on a real socket.

What a READER is served is decided by status codes, headers and bodies
crossing a socket, so the reader-access observers are tested against a
real server rather than a stubbed response object. Each answer is
shaped from the anonymous requests observed live on 2026-10-30:

* ``api.github.com/repos/<public>`` -- 200, ``"private": false``,
  ``"full_name"`` the repository, ``X-RateLimit-Limit: 60``;
* ``api.github.com/repos/<private>`` -- 404 to a request without a login;
* ``zenodo.org/api/records/<open>`` -- 200, ``metadata.access_right``
  ``"open"``, its files listed;
* a restricted record -- 200, ``access_right: "restricted"``, no files;
* an embargoed record -- ``access_right: "embargoed"`` with
  ``metadata.embargo_date``.

Every request is recorded with its lowercased headers, so a test can
assert what was SENT -- that no ``Authorization`` travelled, and how
many requests a path received.
"""

import http.server
import json
import threading
from urllib.parse import urlsplit

import pytest


__all__ = [
    "RemoteAccessServer",
    "fbaJson",
    "fdictGithubRepository",
    "fdictZenodoRecord",
    "fixtureRemoteAccessServer",
]


def fbaJson(jsonValue):
    """Encode a JSON body the way both APIs send one."""
    return json.dumps(jsonValue).encode("utf-8")


def fdictGithubRepository(sFullName, bPrivate=False):
    """Return the fields of a repository answer the probe reads."""
    return {
        "full_name": sFullName, "private": bPrivate,
        "visibility": "private" if bPrivate else "public",
    }


def fdictZenodoRecord(sBaseUrl, sRecordId, sAccessRight, dictFiles=None,
                      sEmbargoDate=""):
    """Return a legacy-serializer record; a non-open record lists no files."""
    dictMetadata = {"access_right": sAccessRight}
    if sEmbargoDate:
        dictMetadata["embargo_date"] = sEmbargoDate
    listFiles = []
    if sAccessRight == "open":
        listFiles = [
            {"key": sKey, "links": {
                "self": f"{sBaseUrl}/api/records/{sRecordId}/files/{sKey}"}}
            for sKey in (dictFiles or {})
        ]
    return {"id": int(sRecordId), "status": "published",
            "metadata": dictMetadata, "files": listFiles}


class _RoutingHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, sFormat, *listArguments):
        return

    def do_GET(self):
        serverAccess = self.server
        sPath = urlsplit(self.path).path
        dictHeaders = {
            sKey.lower(): sValue for sKey, sValue in self.headers.items()
        }
        with serverAccess.lockRequests:
            serverAccess.listRequests.append(
                {"sPath": sPath, "dictHeaders": dictHeaders})
            tAnswer = serverAccess.dictRoutes.get(sPath)
        if tAnswer is None:
            tAnswer = (599, {}, b"no route for this path")
        iStatus, dictAnswerHeaders, baBody = tAnswer
        self.send_response(iStatus)
        for sKey, sValue in dictAnswerHeaders.items():
            self.send_header(sKey, sValue)
        self.send_header("Content-Length", str(len(baBody)))
        self.end_headers()
        self.wfile.write(baBody)


class RemoteAccessServer(http.server.ThreadingHTTPServer):
    """Answers each path from ``dictRoutes``; unrouted paths get a 599."""

    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _RoutingHandler)
        self.dictRoutes = {}
        self.listRequests = []
        self.lockRequests = threading.Lock()
        self.sBaseUrl = f"http://127.0.0.1:{self.server_address[1]}"

    def fnServe(self, sPath, iStatus, baBody=b"", dictHeaders=None):
        """Answer GETs of sPath with this status, body and headers."""
        dictAnswerHeaders = {"Content-Type": "application/json"}
        dictAnswerHeaders.update(dictHeaders or {})
        with self.lockRequests:
            self.dictRoutes[sPath] = (iStatus, dictAnswerHeaders, baBody)

    def fnServeZenodoRecord(self, sRecordId, sAccessRight, dictFiles=None,
                            sEmbargoDate=""):
        """Serve one record and, when it is open, each of its files."""
        dictRecord = fdictZenodoRecord(
            self.sBaseUrl, sRecordId, sAccessRight, dictFiles, sEmbargoDate)
        self.fnServe(f"/api/records/{sRecordId}", 200, fbaJson(dictRecord))
        if sAccessRight != "open":
            return
        for sKey, baContent in (dictFiles or {}).items():
            self.fnServe(
                f"/api/records/{sRecordId}/files/{sKey}", 200, baContent,
                {"Content-Type": "application/octet-stream"},
            )

    def flistRequestsTo(self, sPath):
        """Return every recorded request for one path, in arrival order."""
        with self.lockRequests:
            return [d for d in self.listRequests if d["sPath"] == sPath]


@pytest.fixture
def fixtureRemoteAccessServer():
    """Run one routing server for the test and shut it down after."""
    serverAccess = RemoteAccessServer()
    # A short poll so shutdown returns at once, not after half a second.
    threadServe = threading.Thread(
        target=serverAccess.serve_forever, kwargs={"poll_interval": 0.01},
        daemon=True)
    threadServe.start()
    try:
        yield serverAccess
    finally:
        serverAccess.shutdown()
        serverAccess.server_close()
