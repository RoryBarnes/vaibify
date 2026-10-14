"""A loopback "Zenodo" file server that misbehaves on request.

The resumable download is tested against a REAL server on a REAL
socket, never a mock: the properties under test -- a ranged request
resuming where the bytes stopped, a stalled read woken by a socket
shutdown, a budget spent on a server that ignores Range -- live in the
interaction between two sockets, and a fake response object has no
socket to shut down.

One file is served under ``sFilePath`` (the query string is ignored,
as Zenodo's ``?download=1`` must be). Every request is recorded with
its lowercased headers, so a test can assert what was SENT: the
``Range`` of each attempt, ``Accept-Encoding: identity``, and that no
``Authorization`` header ever travelled. ``dictBehavior`` makes the
server:

* drop the connection after N body bytes (``listDropAfterBytes``, one
  entry consumed per request; ``None`` means serve the whole part);
* stall after N body bytes until ``eventRelease`` is set
  (``tStallAfterBytes``);
* ignore ``Range`` and answer 200 (``bIgnoreRange``);
* answer a 206 with a SHORTER part than asked (``iShortPartBytes``);
* answer canned statuses first (``listCannedAnswers`` of
  ``(iStatus, dictHeaders, baBody)``);
* lie about the whole file's size (``iAdvertisedTotal``);
* name a content type or encoding (``sContentType``,
  ``sContentEncoding``).

A tiny ``/api/records/<id>`` record and a ``/records/<id>`` page are
also answered, so the acquisition chain's record fetch and DOI follow
can be pointed at this server by injecting the Zenodo service table.
"""

import http.server
import json
import socket
import threading
from urllib.parse import urlsplit

import pytest


I_WRITE_PIECE_BYTES = 64 * 1024
S_DEFAULT_FILE_PATH = "/records/5150/files/environment-image.tar.gz"


class _FaultHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, sFormat, *aArguments):
        return

    def do_GET(self):
        serverFault = self.server
        sPath = urlsplit(self.path).path
        dictHeaders = {sKey.lower(): sValue for sKey, sValue in self.headers.items()}
        with serverFault.lockRequests:
            serverFault.listRequests.append({"sPath": sPath, "dictHeaders": dictHeaders})
        if sPath == serverFault.sFilePath:
            return self._fnAnswerFile(dictHeaders)
        if sPath.startswith("/api/records/"):
            return self._fnAnswerJson(serverFault.fdictRecordJson())
        if sPath.startswith("/records/"):
            return self._fnAnswerBytes(200, b"<html>record</html>", {"Content-Type": "text/html"})
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _fnAnswerJson(self, dictPayload):
        self._fnAnswerBytes(
            200, json.dumps(dictPayload).encode("utf-8"),
            {"Content-Type": "application/json"},
        )

    def _fnAnswerBytes(self, iStatus, baBody, dictHeaders):
        self.send_response(iStatus)
        for sKey, sValue in dictHeaders.items():
            self.send_header(sKey, sValue)
        self.send_header("Content-Length", str(len(baBody)))
        self.end_headers()
        self.wfile.write(baBody)

    def _fnAnswerFile(self, dictRequestHeaders):
        serverFault = self.server
        dictBehavior = serverFault.dictBehavior
        with serverFault.lockRequests:
            tCanned = (
                dictBehavior["listCannedAnswers"].pop(0)
                if dictBehavior["listCannedAnswers"] else None
            )
            iDropAfter = (
                dictBehavior["listDropAfterBytes"].pop(0)
                if dictBehavior["listDropAfterBytes"] else None
            )
        if tCanned is not None:
            iStatus, dictHeaders, baBody = tCanned
            return self._fnAnswerBytes(iStatus, baBody, dictHeaders)
        iStart, iEnd = self._ftRangeToServe(dictRequestHeaders)
        baPart = serverFault.baFile[iStart:iEnd + 1]
        dictHeaders = {"Content-Type": dictBehavior["sContentType"]}
        if dictBehavior["sContentEncoding"]:
            dictHeaders["Content-Encoding"] = dictBehavior["sContentEncoding"]
        if iStart == 0 and iEnd == len(serverFault.baFile) - 1 and (
            dictBehavior["bIgnoreRange"] or "range" not in dictRequestHeaders
        ):
            iStatus = 200
        else:
            iStatus = 206
            iTotal = dictBehavior["iAdvertisedTotal"] or len(serverFault.baFile)
            dictHeaders["Content-Range"] = f"bytes {iStart}-{iEnd}/{iTotal}"
        self.send_response(iStatus)
        for sKey, sValue in dictHeaders.items():
            self.send_header(sKey, sValue)
        self.send_header("Content-Length", str(len(baPart)))
        self.end_headers()
        self._fnWriteBodyWithFaults(baPart, iDropAfter)

    def _ftRangeToServe(self, dictRequestHeaders):
        serverFault = self.server
        iLast = len(serverFault.baFile) - 1
        sRange = dictRequestHeaders.get("range", "")
        if serverFault.dictBehavior["bIgnoreRange"] or not sRange.startswith("bytes="):
            return 0, iLast
        sStart = sRange[len("bytes="):].split("-", 1)[0]
        iStart = int(sStart or 0)
        iEnd = iLast
        iShort = serverFault.dictBehavior["iShortPartBytes"]
        if iShort:
            iEnd = min(iLast, iStart + iShort - 1)
        return iStart, iEnd

    def _fnWriteBodyWithFaults(self, baPart, iDropAfter):
        try:
            self._fnWriteBodyOrDrop(baPart, iDropAfter)
        except OSError:
            # The client went away first (a cancel, a timeout): nothing
            # to serve any more, and nothing for a test to hear about.
            self.close_connection = True

    def _fnWriteBodyOrDrop(self, baPart, iDropAfter):
        serverFault = self.server
        tStall = serverFault.dictBehavior["tStallAfterBytes"]
        iWritten = 0
        while iWritten < len(baPart):
            iPiece = min(I_WRITE_PIECE_BYTES, len(baPart) - iWritten)
            if iDropAfter is not None:
                iPiece = min(iPiece, max(0, iDropAfter - iWritten))
            if tStall is not None and iWritten < tStall[0]:
                iPiece = min(iPiece, tStall[0] - iWritten)
            if iPiece:
                self.wfile.write(baPart[iWritten:iWritten + iPiece])
                self.wfile.flush()
                iWritten += iPiece
            if iDropAfter is not None and iWritten >= iDropAfter:
                self._fnDropTheConnection()
                return
            if tStall is not None and iWritten == tStall[0]:
                if not serverFault.eventRelease.wait(tStall[1]):
                    self._fnDropTheConnection()
                    return
                tStall = None
        serverFault.iBytesServed += len(baPart)

    def _fnDropTheConnection(self):
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class FaultInjectingDepositServer:
    """One file on loopback, misbehaving as ``dictBehavior`` says."""

    def __init__(self, baFile, sFilePath=S_DEFAULT_FILE_PATH):
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FaultHandler)
        self._server.daemon_threads = True
        self._server.baFile = baFile
        self._server.sFilePath = sFilePath
        self._server.listRequests = []
        self._server.lockRequests = threading.Lock()
        self._server.eventRelease = threading.Event()
        self._server.iBytesServed = 0
        self._server.dictBehavior = {
            "listDropAfterBytes": [], "tStallAfterBytes": None,
            "bIgnoreRange": False, "iShortPartBytes": 0,
            "listCannedAnswers": [], "iAdvertisedTotal": None,
            "sContentType": "application/octet-stream", "sContentEncoding": "",
        }
        self._server.fdictRecordJson = self._fdictRecordJson
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *aExceptionInfo):
        self._server.eventRelease.set()
        self._server.shutdown()
        self._server.server_close()

    @property
    def sBase(self):
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def sFileUrl(self):
        return self.sBase + self._server.sFilePath + "?download=1"

    @property
    def dictBehavior(self):
        return self._server.dictBehavior

    @property
    def listRequests(self):
        with self._server.lockRequests:
            return list(self._server.listRequests)

    @property
    def listFileRequests(self):
        return [
            dictRequest for dictRequest in self.listRequests
            if dictRequest["sPath"] == self._server.sFilePath
        ]

    @property
    def eventRelease(self):
        return self._server.eventRelease

    @property
    def iBytesServed(self):
        return self._server.iBytesServed

    def fnServeBytes(self, baFile):
        """Replace the file served from now on."""
        self._server.baFile = baFile

    def _fdictRecordJson(self):
        sName = self._server.sFilePath.rsplit("/", 1)[-1]
        return {
            "id": 5150, "status": "published",
            "files": [{"key": sName, "links": {"self": self.sBase + self._server.sFilePath}}],
        }


@pytest.fixture
def fnPointTheZenodoTableAt(monkeypatch):
    """Return a function that makes both Zenodo services resolve to a server."""
    from vaibify.reproducibility import zenodoClient

    def fnPoint(serverFault):
        monkeypatch.setattr(zenodoClient, "_SERVICES", {
            "zenodo": serverFault.sBase, "sandbox": serverFault.sBase,
        })
    return fnPoint
