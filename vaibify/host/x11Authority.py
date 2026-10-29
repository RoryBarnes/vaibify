"""Per-container X11 authorizations, done in a helper process the hub can lose.

The hub runs this module as ``python -I -m vaibify.host.x11Authority
<operation> ...`` with a timeout, and reads ONE JSON line back. Every
X call happens here and never in the hub's own process, because Xlib's
default error handler prints and exits, and its I/O-error handler cannot
return: Xlib ends the process when the server goes away mid-call. In a
subprocess such an exit ends the helper and the hub reports it; in the
hub it would end the hub.

Operations (every outcome is a ``sOutcome`` the hub maps to an explicit
dashboard state; anything else, a timeout or a nonzero exit, is
``error``):

- ``generate``: through the X SECURITY extension, generate an
  UNTRUSTED authorization with a timeout no idle spell reaches in
  practice (eleven days with no client using it; zero means "expire
  now" to Xorg) so it is revoked only explicitly, and write it as a
  wildcard-family MIT-MAGIC-COOKIE-1 entry to ``<directory>/Xauthority``
  by atomic rename, so a running container that mounts the directory
  sees the replacement. Answers ``generated`` with the authorization
  id, ``securityUnsupported`` when the server has no SECURITY
  extension, ``serverUnreachable`` or ``libraryMissing``.
- ``probe``: connect to the display AS THE CONTAINER WOULD, with the
  cookie in ``<directory>/Xauthority``. ``accepted``, ``refused`` (the
  server answered and turned the cookie down: the authorization is
  gone), or ``serverUnreachable``.
- ``revoke``: probe first, then revoke the recorded id over a trusted
  connection only when the probe was ``accepted``. After an X server
  restart ids are reused, so revoking a stale id blindly could revoke
  another container's live authorization. ``revoked``, ``gone`` (the
  probe was refused; nothing revoked) or ``serverUnreachable``.
- ``copy-server-cookie``: the fallback for a server without SECURITY:
  copy the server's own cookie for the display out of the researcher's
  Xauthority file into ``<directory>/Xauthority``. Such a copy grants
  full display access and is revocable only by restarting the X server;
  the hub says so wherever it reports it. ``copied`` or ``error``.

This module imports nothing from the vaibify package, so the same file
runs flat inside a Linux container for the live tests.
"""

import argparse
import ctypes
import ctypes.util
import json
import os
import struct
import sys
import tempfile

__all__ = [
    "I_SECURITY_NEVER_TIMEOUT_SECONDS",
    "S_COOKIE_FILE_NAME",
    "S_OUTCOME_ACCEPTED",
    "S_OUTCOME_COPIED",
    "S_OUTCOME_ERROR",
    "S_OUTCOME_GENERATED",
    "S_OUTCOME_GONE",
    "S_OUTCOME_LIBRARY_MISSING",
    "S_OUTCOME_REFUSED",
    "S_OUTCOME_REVOKED",
    "S_OUTCOME_SECURITY_UNSUPPORTED",
    "S_OUTCOME_SERVER_UNREACHABLE",
    "fbaRenderWildcardAuthority",
    "fbaFindServerCookie",
    "fnWriteCookieFileAtomically",
    "main",
]

S_COOKIE_FILE_NAME = "Xauthority"
S_COOKIE_NAME = b"MIT-MAGIC-COOKIE-1"
I_FAMILY_WILD = 0xFFFF

S_OUTCOME_GENERATED = "generated"
S_OUTCOME_REVOKED = "revoked"
S_OUTCOME_GONE = "gone"
S_OUTCOME_ACCEPTED = "accepted"
S_OUTCOME_REFUSED = "refused"
S_OUTCOME_COPIED = "copied"
S_OUTCOME_SECURITY_UNSUPPORTED = "securityUnsupported"
S_OUTCOME_SERVER_UNREACHABLE = "serverUnreachable"
S_OUTCOME_LIBRARY_MISSING = "libraryMissing"
S_OUTCOME_ERROR = "error"

# XSecurityGenerateAuthorization value masks and trust levels.
I_SECURITY_TIMEOUT_MASK = 1 << 0
I_SECURITY_TRUST_LEVEL_MASK = 1 << 1
I_SECURITY_CLIENT_UNTRUSTED = 1
# The timeout is the number of seconds an authorization survives while
# NO client uses it: the server's timer runs from the moment it is made
# and restarts whenever the last client using it disconnects. Xorg's
# implementation (Xext/security.c) treats zero as "expire now", and it
# adds the span in milliseconds to its 32-bit clock and compares with a
# SIGNED subtraction, so a span past 2**31 milliseconds (about 24.8
# days) reads as already expired, the expiry callback fires inside the
# call still creating the record, and Xvfb aborts on its own assertion
# (measured 2026-10-29, with both the protocol's full span and 46 days).
# An authorization meant to be revoked only explicitly therefore asks
# for eleven days of nobody using it; the hub's periodic probe is itself
# a client, so while the hub runs the timer never gets near that.
I_SECURITY_NEVER_TIMEOUT_SECONDS = 1_000_000

LIST_LIBRARY_DIRECTORIES = (
    "/opt/local/lib", "/opt/X11/lib", "/usr/lib/x86_64-linux-gnu",
    "/usr/lib/aarch64-linux-gnu", "/usr/lib64", "/usr/lib",
)


class _Xauth(ctypes.Structure):
    """libXau's ``Xauth``; the layout is assigned below, as ctypes allows."""


class _XSecurityAuthorizationAttributes(ctypes.Structure):
    """libXext's ``XSecurityAuthorizationAttributes``; layout assigned below."""


# ctypes lets a Structure's layout be assigned after the class exists;
# the attribute name is ctypes' own.
_Xauth._fields_ = [
    ("family", ctypes.c_ushort), ("address_length", ctypes.c_ushort),
    ("address", ctypes.c_char_p),
    ("number_length", ctypes.c_ushort), ("number", ctypes.c_char_p),
    ("name_length", ctypes.c_ushort), ("name", ctypes.c_char_p),
    ("data_length", ctypes.c_ushort), ("data", ctypes.POINTER(ctypes.c_char)),
]
_XSecurityAuthorizationAttributes._fields_ = [
    ("timeout", ctypes.c_uint), ("trust_level", ctypes.c_uint),
    ("group", ctypes.c_ulong), ("event_mask", ctypes.c_long),
]


def fnPrintOutcome(sOutcome, **dictFields):
    """Print the one JSON line the hub reads and flush it."""
    dictResult = {"sOutcome": sOutcome}
    dictResult.update(dictFields)
    sys.stdout.write(json.dumps(dictResult, sort_keys=True) + "\n")
    sys.stdout.flush()


def _fsFindLibrary(sName, sDirectoryOverride=""):
    """Return a path for libX11 or libXext, '' when none can be found."""
    listCandidates = []
    for sDirectory in ([sDirectoryOverride] if sDirectoryOverride else []) + list(
        LIST_LIBRARY_DIRECTORIES,
    ):
        for sSuffix in ("6.dylib", "so.6", "dylib", "so"):
            listCandidates.append(os.path.join(sDirectory, f"lib{sName}.{sSuffix}"))
    for sCandidate in listCandidates:
        if os.path.isfile(sCandidate):
            return sCandidate
    return ctypes.util.find_library(sName) or ""


class _XlibHandles:
    """The two libraries, their call signatures, the error handlers, and the operations.

    Every display is held as the integer address ``XOpenDisplay``
    returned (``c_void_p`` results arrive as Python integers); zero is
    "no connection".
    """

    def __init__(self, sDirectoryOverride=""):
        sX11 = _fsFindLibrary("X11", sDirectoryOverride)
        sXext = _fsFindLibrary("Xext", sDirectoryOverride)
        if not sX11 or not sXext:
            raise FileNotFoundError("libX11 or libXext is not installed")
        self.libX11 = ctypes.CDLL(sX11)
        self.libXext = ctypes.CDLL(sXext)
        self.listErrors = []
        self._fnDeclareSignatures()
        self._fnInstallErrorHandlers()

    def _fnDeclareSignatures(self):
        self.libX11.XOpenDisplay.restype = ctypes.c_void_p
        self.libX11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.libX11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.libX11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.libXext.XSecurityQueryExtension.restype = ctypes.c_int
        self.libXext.XSecurityQueryExtension.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
        ]
        self.libXext.XSecurityAllocXauth.restype = ctypes.c_void_p
        self.libXext.XSecurityGenerateAuthorization.restype = ctypes.c_void_p
        self.libXext.XSecurityGenerateAuthorization.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(_XSecurityAuthorizationAttributes),
            ctypes.POINTER(ctypes.c_ulong),
        ]
        self.libXext.XSecurityRevokeAuthorization.restype = ctypes.c_int
        self.libXext.XSecurityRevokeAuthorization.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong,
        ]
        self.libXext.XSecurityFreeXauth.argtypes = [ctypes.c_void_p]

    def _fnInstallErrorHandlers(self):
        """Keep Xlib from exiting silently; report, then end THIS process."""
        tErrorHandler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        tIoHandler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)

        def fiRecordError(iDisplay, iEvent):
            del iDisplay, iEvent
            self.listErrors.append("an X protocol error was reported")
            return 0

        def fiReportIoErrorAndExit(iDisplay):
            del iDisplay
            fnPrintOutcome(
                S_OUTCOME_SERVER_UNREACHABLE,
                sReason="the X server closed the connection mid-call",
            )
            os._exit(0)

        self._fnErrorHandler = tErrorHandler(fiRecordError)
        self._fnIoHandler = tIoHandler(fiReportIoErrorAndExit)
        self.libX11.XSetErrorHandler.argtypes = [tErrorHandler]
        self.libX11.XSetErrorHandler(self._fnErrorHandler)
        self.libX11.XSetIOErrorHandler.argtypes = [tIoHandler]
        self.libX11.XSetIOErrorHandler(self._fnIoHandler)

    def fiOpenDisplay(self, sDisplay):
        """Return the connection's address, 0 when the server refused or is absent."""
        return self.libX11.XOpenDisplay(sDisplay.encode("utf-8") if sDisplay else None) or 0

    def fbSecuritySupported(self, iDisplay):
        iMajor = ctypes.c_int(0)
        iMinor = ctypes.c_int(0)
        return bool(self.libXext.XSecurityQueryExtension(
            iDisplay, ctypes.byref(iMajor), ctypes.byref(iMinor),
        ))

    def ftGenerateUntrusted(self, iDisplay):
        """Return ``(baCookie, iAuthorizationId)`` for a new untrusted authorization."""
        iAuthIn = self.libXext.XSecurityAllocXauth()
        bufferName = ctypes.create_string_buffer(S_COOKIE_NAME)
        _Xauth.from_address(iAuthIn).name = ctypes.cast(bufferName, ctypes.c_char_p)
        _Xauth.from_address(iAuthIn).name_length = len(S_COOKIE_NAME)
        iAuthorizationId = ctypes.c_ulong(0)
        iAuthOut = self.libXext.XSecurityGenerateAuthorization(
            iDisplay, iAuthIn, I_SECURITY_TIMEOUT_MASK | I_SECURITY_TRUST_LEVEL_MASK,
            ctypes.byref(_XSecurityAuthorizationAttributes(
                timeout=I_SECURITY_NEVER_TIMEOUT_SECONDS,
                trust_level=I_SECURITY_CLIENT_UNTRUSTED, group=0, event_mask=0,
            )),
            ctypes.byref(iAuthorizationId),
        )
        self.libXext.XSecurityFreeXauth(iAuthIn)
        if not iAuthOut:
            raise RuntimeError("the X server refused to generate an authorization")
        baCookie = ctypes.string_at(
            _Xauth.from_address(iAuthOut).data, _Xauth.from_address(iAuthOut).data_length,
        )
        self.libXext.XSecurityFreeXauth(iAuthOut)
        return baCookie, int(iAuthorizationId.value)

    def fnRevoke(self, iDisplay, iAuthorizationId):
        self.libXext.XSecurityRevokeAuthorization(iDisplay, iAuthorizationId)
        self.libX11.XSync(iDisplay, 0)

    def fnRunGenerate(self, args):
        iDisplay = self.fiOpenDisplay(args.display)
        if not iDisplay:
            fnPrintOutcome(S_OUTCOME_SERVER_UNREACHABLE,
                           sReason="the trusted connection was refused")
            return
        if not self.fbSecuritySupported(iDisplay):
            fnPrintOutcome(S_OUTCOME_SECURITY_UNSUPPORTED)
            return
        baCookie, iAuthorizationId = self.ftGenerateUntrusted(iDisplay)
        fnWriteCookieFileAtomically(
            args.directory, fbaRenderWildcardAuthority(args.number, baCookie),
        )
        # The authorization belongs to the server, not to this connection,
        # so it outlives the helper (measured against Xvfb, 2026-10-29).
        # What does end it is a server RESET, which an X server performs
        # when its last client disconnects unless started with -noreset:
        # on a desktop some client always remains, and the hub's periodic
        # probe renews an authorization the server lost. A retained
        # close-down mode would also keep it, at the cost of one of the
        # server's client slots per generation, forever; it is not used.
        self.libX11.XCloseDisplay(iDisplay)
        fnPrintOutcome(S_OUTCOME_GENERATED, iAuthorizationId=iAuthorizationId)

    def fsProbe(self, args):
        """Connect as the container would; return accepted, refused or unreachable."""
        iTrusted = self.fiOpenDisplay(args.display)
        if not iTrusted:
            return S_OUTCOME_SERVER_UNREACHABLE
        self.libX11.XCloseDisplay(iTrusted)
        os.environ["XAUTHORITY"] = os.path.join(args.directory, S_COOKIE_FILE_NAME)
        iProbe = self.fiOpenDisplay(args.probe_display)
        if not iProbe:
            return S_OUTCOME_REFUSED
        self.libX11.XCloseDisplay(iProbe)
        return S_OUTCOME_ACCEPTED

    def fnRunRevoke(self, args):
        sProbe = self.fsProbe(args)
        if sProbe == S_OUTCOME_SERVER_UNREACHABLE:
            fnPrintOutcome(S_OUTCOME_SERVER_UNREACHABLE)
            return
        if sProbe == S_OUTCOME_REFUSED:
            fnPrintOutcome(S_OUTCOME_GONE,
                           sReason="the cookie is already refused; nothing revoked")
            return
        os.environ.pop("XAUTHORITY", None)
        if args.authority:
            os.environ["XAUTHORITY"] = args.authority
        iTrusted = self.fiOpenDisplay(args.display)
        if not iTrusted:
            fnPrintOutcome(S_OUTCOME_SERVER_UNREACHABLE)
            return
        self.fnRevoke(iTrusted, args.authorization_id)
        self.libX11.XCloseDisplay(iTrusted)
        fnPrintOutcome(S_OUTCOME_REVOKED, iAuthorizationId=args.authorization_id)

    def fnRunOperation(self, args):
        """Dispatch one of the three operations that need the libraries."""
        if args.operation == "generate":
            self.fnRunGenerate(args)
        elif args.operation == "probe":
            fnPrintOutcome(self.fsProbe(args))
        else:
            self.fnRunRevoke(args)


def fbaRenderWildcardAuthority(sDisplayNumber, baCookie):
    """Render one Xauthority entry any host name matches (family wild)."""
    def fbaField(baValue):
        return struct.pack(">H", len(baValue)) + baValue

    return (
        struct.pack(">H", I_FAMILY_WILD) + fbaField(b"")
        + fbaField(sDisplayNumber.encode("ascii")) + fbaField(S_COOKIE_NAME)
        + fbaField(baCookie)
    )


def fnWriteCookieFileAtomically(sDirectory, baContent):
    """Write ``<directory>/Xauthority`` mode 600 by rename inside the directory."""
    iDescriptor, sTemporary = tempfile.mkstemp(
        prefix=".Xauthority.", dir=sDirectory,
    )
    try:
        os.fchmod(iDescriptor, 0o600)
        os.write(iDescriptor, baContent)
        os.fsync(iDescriptor)
    finally:
        os.close(iDescriptor)
    os.replace(sTemporary, os.path.join(sDirectory, S_COOKIE_FILE_NAME))


def _fiterAuthorityEntries(baContent):
    """Yield ``(iFamily, baAddress, baNumber, baName, baData)`` from an Xauthority file."""
    iOffset = 0

    def fbaRead():
        nonlocal iOffset
        iLength = struct.unpack(">H", baContent[iOffset:iOffset + 2])[0]
        iOffset += 2
        baValue = baContent[iOffset:iOffset + iLength]
        iOffset += iLength
        return baValue

    while iOffset + 2 <= len(baContent):
        iFamily = struct.unpack(">H", baContent[iOffset:iOffset + 2])[0]
        iOffset += 2
        yield (iFamily, fbaRead(), fbaRead(), fbaRead(), fbaRead())


def fbaFindServerCookie(sAuthorityPath, sDisplayNumber):
    """Return the server's MIT-MAGIC-COOKIE-1 for a display number, or b''."""
    try:
        with open(sAuthorityPath, "rb") as fileAuthority:
            baContent = fileAuthority.read()
    except OSError:
        return b""
    for _iFamily, _baAddress, baNumber, baName, baData in _fiterAuthorityEntries(baContent):
        if baName == S_COOKIE_NAME and baNumber.decode("ascii", "replace") == sDisplayNumber:
            return baData
    return b""


def _fsDefaultAuthorityPath():
    return os.environ.get("XAUTHORITY") or os.path.join(
        os.path.expanduser("~"), ".Xauthority",
    )


def _fnRunCopyServerCookie(args):
    baCookie = fbaFindServerCookie(
        args.authority or _fsDefaultAuthorityPath(), args.number,
    )
    if not baCookie:
        fnPrintOutcome(
            S_OUTCOME_ERROR,
            sReason=f"no MIT-MAGIC-COOKIE-1 for display :{args.number} in the authority file",
        )
        return
    fnWriteCookieFileAtomically(
        args.directory, fbaRenderWildcardAuthority(args.number, baCookie),
    )
    fnPrintOutcome(S_OUTCOME_COPIED)


def _fparserBuild():
    parser = argparse.ArgumentParser(prog="x11Authority")
    parser.add_argument("operation", choices=(
        "generate", "probe", "revoke", "copy-server-cookie",
    ))
    parser.add_argument("--display", default=os.environ.get("DISPLAY", ""),
                        help="the display as this host reaches it (trusted)")
    parser.add_argument("--probe-display", default="",
                        help="the display as the container reaches it")
    parser.add_argument("--number", default="0",
                        help="the display number the cookie entry names")
    parser.add_argument("--directory", required=True,
                        help="the per-container directory holding Xauthority")
    parser.add_argument("--authorization-id", type=int, default=0)
    parser.add_argument("--authority", default="",
                        help="the researcher's own Xauthority file")
    parser.add_argument("--library-directory", default="")
    return parser


def main(listArgv=None):
    """Run one operation and print its outcome; never raise out."""
    args = _fparserBuild().parse_args(listArgv)
    if not args.probe_display:
        args.probe_display = args.display
    try:
        if args.operation == "copy-server-cookie":
            _fnRunCopyServerCookie(args)
            return
        fnRunOperation = _XlibHandles(args.library_directory).fnRunOperation
    except FileNotFoundError as error:
        fnPrintOutcome(S_OUTCOME_LIBRARY_MISSING, sReason=str(error))
        return
    except OSError as error:
        fnPrintOutcome(S_OUTCOME_ERROR, sReason=f"{type(error).__name__}: {error}")
        return
    try:
        fnRunOperation(args)
    except Exception as error:  # noqa: BLE001 -- the one JSON line is the contract
        fnPrintOutcome(S_OUTCOME_ERROR, sReason=f"{type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
