#!/usr/bin/env python3
"""In-container CLI that forwards agent intent to the vaibify backend.

Reads a session env file and an action catalog written into the
container by the host, then dispatches one of those actions either as
an HTTP call (POST/PUT/DELETE/GET) or as a WebSocket message on the
pipeline socket. The authoritative catalog shape and shared constants
live in ``vaibify/gui/actionCatalog.py``; the constants below are
intentionally hard-coded copies because the host package is not
importable from inside the container.

Transport choice: stdlib only. HTTP uses ``urllib.request``; the
WebSocket path uses a small RFC 6455 client built on ``socket``,
because ``docker/requirements.txt`` does not pin ``websockets`` and
relying on a third-party package would make this CLI fragile.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import socket
import sys
import urllib.parse
import urllib.request
import urllib.error


S_SESSION_ENV_PATH = "/tmp/vaibify-session.env"
S_CATALOG_JSON_PATH = "/tmp/vaibify-action-catalog.json"
S_SESSION_HEADER_NAME = "X-Vaibify-Session"
S_EXPECTED_SCHEMA = "1.0"
F_CONNECT_TIMEOUT = 2.0
F_READ_TIMEOUT = 600.0
F_LABEL_LOOKUP_TIMEOUT = 10.0
RE_STEP_LABEL = re.compile(r"^[AIai]\d{1,3}$")


def fnFail(sMessage, iCode=3):
    sys.stderr.write(sMessage.rstrip() + "\n")
    sys.exit(iCode)


def fdictReadSession():
    """Parse /tmp/vaibify-session.env into a dict."""
    if not os.path.exists(S_SESSION_ENV_PATH):
        fnFail("vaibify session not initialized; have the researcher "
               "reconnect via the dashboard")
    dictEnv = {}
    with open(S_SESSION_ENV_PATH, "r", encoding="utf-8") as fileHandle:
        for sLine in fileHandle:
            sLine = sLine.strip()
            if not sLine or sLine.startswith("#") or "=" not in sLine:
                continue
            sKey, sValue = sLine.split("=", 1)
            dictEnv[sKey.strip()] = sValue.strip().strip('"').strip("'")
    listRequired = ["VAIBIFY_HOST_URL", "VAIBIFY_SESSION_TOKEN",
                    "VAIBIFY_CONTAINER_ID"]
    for sKey in listRequired:
        if not dictEnv.get(sKey):
            fnFail("vaibify session not initialized; have the researcher "
                   "reconnect via the dashboard")
    return dictEnv


def fdictReadCatalog():
    """Load the action catalog JSON, warning on schema drift."""
    if not os.path.exists(S_CATALOG_JSON_PATH):
        fnFail("vaibify action catalog missing at " + S_CATALOG_JSON_PATH)
    with open(S_CATALOG_JSON_PATH, "r", encoding="utf-8") as fileHandle:
        dictCatalog = json.load(fileHandle)
    sVersion = dictCatalog.get("sSchemaVersion", "")
    if sVersion != S_EXPECTED_SCHEMA:
        sys.stderr.write(
            "warning: catalog schema " + sVersion + " != expected "
            + S_EXPECTED_SCHEMA + "; continuing\n")
    return dictCatalog


def fdictFindAction(dictCatalog, sName):
    """Return the action entry for sName or None."""
    for dictEntry in dictCatalog.get("listActions", []):
        if dictEntry.get("sName") == sName:
            return dictEntry
    return None


def fnPrintList(dictCatalog):
    """Print the catalog as an aligned human-readable table."""
    listRows = [("NAME", "CATEGORY", "METHOD", "SAFE", "DESCRIPTION")]
    for dictEntry in dictCatalog.get("listActions", []):
        sFlag = " " if dictEntry.get("bAgentSafe") else "*"
        listRows.append((
            dictEntry.get("sName", "") + sFlag,
            dictEntry.get("sCategory", ""),
            dictEntry.get("sMethod", ""),
            "yes" if dictEntry.get("bAgentSafe") else "no",
            dictEntry.get("sDescription", "").split(". ")[0],
        ))
    listWidths = [max(len(tRow[i]) for tRow in listRows)
                  for i in range(len(listRows[0]))]
    for tRow in listRows:
        print("  ".join(tRow[i].ljust(listWidths[i])
                        for i in range(len(tRow))).rstrip())
    print("\n(rows marked * are user-only; agents must defer to the "
          "researcher.)")


def fnPrintDescribe(dictEntry):
    print(json.dumps(dictEntry, indent=2, sort_keys=True))


def ftParsePositionalArgs(listArgs):
    """Split CLI args into positional path values and a JSON body dict."""
    listPositional = []
    dictBody = {}
    for sArg in listArgs:
        if "=" in sArg and not sArg.startswith("{"):
            sKey, sValue = sArg.split("=", 1)
            dictBody[sKey] = _fnCoerceScalar(sValue)
        elif sArg.startswith("{"):
            dictBody.update(json.loads(sArg))
        else:
            listPositional.append(sArg)
    return listPositional, dictBody


def _fnCoerceScalar(sValue):
    """Coerce CLI string to int/float/bool/JSON where obvious."""
    if sValue.lower() in ("true", "false"):
        return sValue.lower() == "true"
    try:
        return int(sValue)
    except ValueError:
        pass
    try:
        return float(sValue)
    except ValueError:
        pass
    if sValue.startswith("[") or sValue.startswith("{"):
        try:
            return json.loads(sValue)
        except ValueError:
            return sValue
    return sValue


def flistPathPlaceholders(sPath):
    """Extract placeholder names from an sPath template, in order."""
    listNames = []
    i = 0
    while i < len(sPath):
        if sPath[i] == "{":
            iEnd = sPath.find("}", i)
            if iEnd < 0:
                break
            sToken = sPath[i + 1:iEnd].split(":")[0]
            listNames.append(sToken)
            i = iEnd + 1
        else:
            i += 1
    return listNames


def fsFillPath(sPath, dictValues):
    """Replace {name} and {name:path} placeholders from dictValues."""
    for sKey, sValue in dictValues.items():
        sPath = sPath.replace("{" + sKey + "}", urllib.parse.quote(
            str(sValue), safe="/"))
        sPath = sPath.replace("{" + sKey + ":path}", urllib.parse.quote(
            str(sValue), safe="/"))
    return sPath


def fbIsStepLabel(sArg):
    """Return True when sArg looks like a step label (A09, I01)."""
    return bool(RE_STEP_LABEL.match(sArg or ""))


def fiResolveLabelToIndex(sLabel, dictEnv):
    """Ask the backend to translate a step label to a 0-based index.

    Hits GET ``/api/steps/{id}/by-label/{sLabel}``; raises ``SystemExit``
    with a readable message on any non-200 response so the CLI fails
    fast with the server's own description of the mismatch.
    """
    sUrl = (
        dictEnv["VAIBIFY_HOST_URL"].rstrip("/")
        + "/api/steps/"
        + urllib.parse.quote(dictEnv["VAIBIFY_CONTAINER_ID"], safe="")
        + "/by-label/"
        + urllib.parse.quote(sLabel, safe="")
    )
    request = urllib.request.Request(
        sUrl,
        headers={S_SESSION_HEADER_NAME: dictEnv["VAIBIFY_SESSION_TOKEN"]},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=F_LABEL_LOOKUP_TIMEOUT,
        ) as resp:
            dictResult = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        fnFail(
            "label resolution failed for " + sLabel + ": "
            + error.read().decode("utf-8", errors="replace"),
            iCode=2,
        )
    except urllib.error.URLError as error:
        fnFail("label resolution failed for " + sLabel
               + ": " + str(error), iCode=2)
    return int(dictResult["iStepIndex"])


def fdictResolveHttpTarget(dictEntry, listArgs, dictEnv):
    """Build {sUrl, dictBody} for HTTP actions."""
    listPlaceholders = flistPathPlaceholders(dictEntry["sPath"])
    listPositional, dictBody = ftParsePositionalArgs(listArgs)
    dictValues = {"sContainerId": dictEnv["VAIBIFY_CONTAINER_ID"]}
    listNeeded = [s for s in listPlaceholders if s != "sContainerId"]
    if len(listPositional) < len(listNeeded):
        fnFail("action " + dictEntry["sName"] + " needs positional args: "
               + ", ".join(listNeeded), iCode=2)
    for sName, sValue in zip(listNeeded, listPositional):
        if sName == "iStepIndex" and fbIsStepLabel(sValue):
            sValue = str(fiResolveLabelToIndex(sValue, dictEnv))
        dictValues[sName] = sValue
    sPath = fsFillPath(dictEntry["sPath"], dictValues)
    sUrl = dictEnv["VAIBIFY_HOST_URL"].rstrip("/") + sPath
    return {"sUrl": sUrl, "dictBody": dictBody}


def _fnPopulateRunStep(dictPayload, listPositional):
    """Place a single run-step positional arg into indices or labels."""
    sArg = listPositional[0]
    if fbIsStepLabel(sArg):
        dictPayload["listStepLabels"] = [sArg]
        return
    try:
        dictPayload["listStepIndices"] = [int(sArg)]
    except ValueError:
        dictPayload["listStepLabels"] = [sArg]


def _fnPopulateRunSelected(dictPayload, listPositional):
    """Split run-selected-steps args between indices and labels."""
    listIndices = []
    listLabels = []
    for sArg in listPositional:
        if fbIsStepLabel(sArg):
            listLabels.append(sArg)
        else:
            try:
                listIndices.append(int(sArg))
            except ValueError:
                listLabels.append(sArg)
    if listIndices:
        dictPayload["listStepIndices"] = listIndices
    if listLabels:
        dictPayload["listStepLabels"] = listLabels


def _fnPopulateRunFromStep(dictPayload, listPositional):
    """Accept a label or integer for run-from-step's start point."""
    sArg = listPositional[0]
    if fbIsStepLabel(sArg):
        dictPayload["sStartStepLabel"] = sArg
    else:
        dictPayload["iStartStep"] = int(sArg)


def fdictResolveWsPayload(dictEntry, listArgs):
    """Build the WebSocket JSON message for a WS action."""
    listPositional, dictBody = ftParsePositionalArgs(listArgs)
    dictPayload = {"sAction": dictEntry["sPath"]}
    dictPayload.update(dictBody)
    if not listPositional:
        return dictPayload
    if dictEntry["sName"] == "run-step":
        _fnPopulateRunStep(dictPayload, listPositional)
    elif dictEntry["sName"] == "run-selected-steps":
        _fnPopulateRunSelected(dictPayload, listPositional)
    elif dictEntry["sName"] == "run-from-step":
        _fnPopulateRunFromStep(dictPayload, listPositional)
    return dictPayload


def fnSendHttp(dictTarget, sToken, sMethod, bJsonMode):
    """Perform the HTTP call and print the response."""
    dataBody = None
    dictHeaders = {S_SESSION_HEADER_NAME: sToken}
    if dictTarget["dictBody"]:
        dataBody = json.dumps(dictTarget["dictBody"]).encode("utf-8")
        dictHeaders["Content-Type"] = "application/json"
    request = urllib.request.Request(
        dictTarget["sUrl"], data=dataBody,
        headers=dictHeaders, method=sMethod)
    try:
        with urllib.request.urlopen(request, timeout=F_READ_TIMEOUT) as resp:
            _fnPrintHttpBody(resp.read(), bJsonMode)
            return 0
    except urllib.error.HTTPError as errHttp:
        return _fnHandleHttpError(errHttp, bJsonMode)
    except (urllib.error.URLError, socket.timeout, OSError):
        fnFail("vaibify host unreachable at " + dictTarget["sUrl"]
               + "; reconnect the container from the dashboard", iCode=4)


def _fnHandleHttpError(errHttp, bJsonMode):
    if errHttp.code == 401:
        fnFail("vaibify session token rejected; reconnect the "
               "container from the dashboard", iCode=4)
    try:
        dataBody = errHttp.read()
    except Exception:
        dataBody = b""
    _fnPrintHttpBody(dataBody, bJsonMode)
    if 400 <= errHttp.code < 500:
        return 1
    return 2


def _fnPrintHttpBody(dataBody, bJsonMode):
    sText = dataBody.decode("utf-8", errors="replace") if dataBody else ""
    if not sText:
        return
    try:
        objParsed = json.loads(sText)
    except ValueError:
        print(sText)
        return
    if bJsonMode:
        print(json.dumps(objParsed))
    else:
        print(json.dumps(objParsed, indent=2, sort_keys=True))


def ftWsEndpoint(dictEnv):
    """Return (sHost, iPort, sPath, bTls) for the pipeline WebSocket."""
    tParsed = urllib.parse.urlparse(dictEnv["VAIBIFY_HOST_URL"])
    bTls = tParsed.scheme == "https"
    iPort = tParsed.port or (443 if bTls else 80)
    sPath = ("/ws/pipeline/" + dictEnv["VAIBIFY_CONTAINER_ID"]
             + "?sToken=" + urllib.parse.quote(
                 dictEnv["VAIBIFY_SESSION_TOKEN"], safe=""))
    return tParsed.hostname, iPort, sPath, bTls


def fnWebsocketHandshake(sockConn, sHost, iPort, sPath):
    """Perform an RFC 6455 client handshake over an open socket."""
    sKey = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    sRequest = (
        "GET " + sPath + " HTTP/1.1\r\n"
        "Host: " + sHost + ":" + str(iPort) + "\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        "Sec-WebSocket-Key: " + sKey + "\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n")
    sockConn.sendall(sRequest.encode("ascii"))
    dataResponse = b""
    while b"\r\n\r\n" not in dataResponse:
        dataChunk = sockConn.recv(4096)
        if not dataChunk:
            fnFail("websocket handshake failed (empty response)", iCode=4)
        dataResponse += dataChunk
    if b" 101 " not in dataResponse.split(b"\r\n")[0]:
        if b" 401 " in dataResponse.split(b"\r\n")[0]:
            fnFail("vaibify session token rejected; reconnect "
                   "the container from the dashboard", iCode=4)
        fnFail("websocket handshake rejected: "
               + dataResponse.split(b"\r\n")[0].decode(
                   "ascii", errors="replace"), iCode=4)


def fnSendWsText(sockConn, sPayload):
    """Write one masked text frame to the open WebSocket."""
    dataPayload = sPayload.encode("utf-8")
    dataMask = secrets.token_bytes(4)
    dataMasked = bytes(b ^ dataMask[i % 4]
                       for i, b in enumerate(dataPayload))
    iLength = len(dataPayload)
    dataHeader = bytes([0x81])
    if iLength < 126:
        dataHeader += bytes([0x80 | iLength])
    elif iLength < 65536:
        dataHeader += bytes([0x80 | 126]) + iLength.to_bytes(2, "big")
    else:
        dataHeader += bytes([0x80 | 127]) + iLength.to_bytes(8, "big")
    sockConn.sendall(dataHeader + dataMask + dataMasked)


def _fnRecvExact(sockConn, iCount):
    dataBuffer = b""
    while len(dataBuffer) < iCount:
        dataChunk = sockConn.recv(iCount - len(dataBuffer))
        if not dataChunk:
            return b""
        dataBuffer += dataChunk
    return dataBuffer


def fsRecvWsFrame(sockConn):
    """Read one unmasked server text frame; returns '' on close."""
    dataHeader = _fnRecvExact(sockConn, 2)
    if len(dataHeader) < 2:
        return ""
    iOpcode = dataHeader[0] & 0x0F
    iLength = dataHeader[1] & 0x7F
    if iLength == 126:
        iLength = int.from_bytes(_fnRecvExact(sockConn, 2), "big")
    elif iLength == 127:
        iLength = int.from_bytes(_fnRecvExact(sockConn, 8), "big")
    dataPayload = _fnRecvExact(sockConn, iLength) if iLength else b""
    if iOpcode == 0x8:
        return ""
    if iOpcode == 0x9:
        return "__PING__"
    if iOpcode != 0x1:
        return "__SKIP__"
    return dataPayload.decode("utf-8", errors="replace")


def fnRunWebsocket(dictEnv, dictPayload, bJsonMode):
    """Open the pipeline socket, send one action, stream events."""
    sHost, iPort, sPath, bTls = ftWsEndpoint(dictEnv)
    if bTls:
        fnFail("vaibify-do does not support TLS in the in-container "
               "WebSocket path; use plain http host-bridge url", iCode=4)
    try:
        sockConn = socket.create_connection(
            (sHost, iPort), timeout=F_CONNECT_TIMEOUT)
    except (OSError, socket.timeout):
        fnFail("vaibify host unreachable at " + dictEnv["VAIBIFY_HOST_URL"]
               + "; reconnect the container from the dashboard", iCode=4)
    sockConn.settimeout(F_READ_TIMEOUT)
    fnWebsocketHandshake(sockConn, sHost, iPort, sPath)
    fnSendWsText(sockConn, json.dumps(dictPayload))
    return _fnStreamWsEvents(sockConn, bJsonMode)


def _fnStreamWsEvents(sockConn, bJsonMode):
    """Read events until 'completed' or error; return exit code."""
    while True:
        sFrame = fsRecvWsFrame(sockConn)
        if sFrame == "":
            return 1
        if sFrame in ("__PING__", "__SKIP__"):
            continue
        try:
            dictEvent = json.loads(sFrame)
        except ValueError:
            continue
        _fnPrintEvent(dictEvent, bJsonMode)
        sType = dictEvent.get("sType", "")
        if sType == "completed":
            return int(dictEvent.get("iExitCode", 0) or 0)
        if sType in ("error", "pipelineError"):
            return 1


def _fnPrintEvent(dictEvent, bJsonMode):
    if bJsonMode:
        print(json.dumps(dictEvent))
        sys.stdout.flush()
        return
    sType = dictEvent.get("sType", "event")
    listParts = ["[" + sType + "]"]
    for sKey in ("iStep", "sStepName", "sMessage", "iExitCode"):
        if sKey in dictEvent:
            listParts.append(sKey + "=" + str(dictEvent[sKey]))
    print(" ".join(listParts))
    sys.stdout.flush()


def fnCheckAgentSafety(dictEntry):
    """Refuse user-only actions with a one-line JSON refusal."""
    if dictEntry.get("bAgentSafe"):
        return
    dictRefusal = {
        "sRefusal": "user-only-action",
        "sName": dictEntry.get("sName", ""),
        "sDescription": dictEntry.get("sDescription", ""),
        "sHint": "Surface the request to the researcher and let them "
                 "click the UI button.",
    }
    print(json.dumps(dictRefusal))
    sys.exit(5)


def fnDryRun(dictEntry, listArgs, dictEnv):
    """Describe the call that would be made and exit 0."""
    if dictEntry["sMethod"] == "WS":
        dictPayload = fdictResolveWsPayload(dictEntry, listArgs)
        sHost, iPort, sPath, _ = ftWsEndpoint(dictEnv)
        print(json.dumps({
            "sTransport": "WS",
            "sUrl": "ws://" + sHost + ":" + str(iPort) + sPath,
            "dictPayload": dictPayload,
        }, indent=2))
    else:
        dictTarget = fdictResolveHttpTarget(dictEntry, listArgs, dictEnv)
        print(json.dumps({
            "sTransport": "HTTP",
            "sMethod": dictEntry["sMethod"],
            "sUrl": dictTarget["sUrl"],
            "dictBody": dictTarget["dictBody"],
        }, indent=2))
    sys.exit(0)


def fnDispatch(dictEntry, listArgs, dictEnv, bJsonMode):
    """Send the action and exit with the appropriate code."""
    if dictEntry["sMethod"] == "WS":
        dictPayload = fdictResolveWsPayload(dictEntry, listArgs)
        sys.exit(fnRunWebsocket(dictEnv, dictPayload, bJsonMode))
    dictTarget = fdictResolveHttpTarget(dictEntry, listArgs, dictEnv)
    sys.exit(fnSendHttp(dictTarget, dictEnv["VAIBIFY_SESSION_TOKEN"],
                        dictEntry["sMethod"], bJsonMode))


def fnParseArguments():
    """Build the argparse parser for vaibify-do."""
    parser = argparse.ArgumentParser(
        prog="vaibify-do",
        description="In-container bridge to vaibify UI actions.")
    parser.add_argument("--list", action="store_true",
                        help="Print the catalog and exit.")
    parser.add_argument("--describe", metavar="ACTION",
                        help="Print full catalog entry for ACTION.")
    parser.add_argument("--json", action="store_true",
                        help="Emit line-delimited JSON on stdout.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the call that would be made; do not send.")
    parser.add_argument("sAction", nargs="?",
                        help="Catalog action name.")
    parser.add_argument("listArgs", nargs=argparse.REMAINDER,
                        help="Positional path args and key=value fields.")
    return parser.parse_args()


def main():
    args = fnParseArguments()
    dictCatalog = fdictReadCatalog()
    if args.list:
        fnPrintList(dictCatalog)
        return
    if args.describe:
        dictEntry = fdictFindAction(dictCatalog, args.describe)
        if not dictEntry:
            fnFail("unknown action: " + args.describe, iCode=2)
        fnPrintDescribe(dictEntry)
        return
    if not args.sAction:
        fnFail("usage: vaibify-do [--list|--describe ACTION|ACTION args...]",
               iCode=2)
    dictEntry = fdictFindAction(dictCatalog, args.sAction)
    if not dictEntry:
        fnFail("unknown action: " + args.sAction
               + " (try 'vaibify-do --list')", iCode=2)
    dictEnv = fdictReadSession()
    if args.dry_run:
        fnDryRun(dictEntry, args.listArgs, dictEnv)
    fnCheckAgentSafety(dictEntry)
    fnDispatch(dictEntry, args.listArgs, dictEnv, args.json)


if __name__ == "__main__":
    main()
