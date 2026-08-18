"""What to tell a client about WHERE it is.

Three locations exist once a browser can be somewhere else: the
computer the researcher is sitting at, the machine running the backend,
and the environment the commands run in. Until remote access, the first
two were always the same machine and the distinction did not pay for
itself -- "here" was unambiguous and nothing needed to say it.

The domain kept naming this and the code had no home for it, so this is
that home. It exists as its own module rather than as three more
functions in ``pipelineServer`` because that module is already the
largest in the package, and because these answer a question none of
its other 2900 lines ask: not "what shall I do" but "where am I".

Nothing here decides anything. Every function answers a question, and
the handshake carries the answers to a client that cannot work them out
for itself -- which is the point. A frontend that re-derived which
locations coincide would be a second implementation of a fact the
server already knows, and the absence of ANY such check is what let a
host-mode "pull to host" ship as a self-copy presented as a transfer.
"""

__all__ = [
    "fbConnectionIsRemote",
    "fdictExecutionTopology",
    "fsExecutionHostname",
]


def fsExecutionHostname():
    """Return the name of the machine this hub actually runs on.

    Nothing in vaibify computed a hostname before, because until a
    browser could be on a DIFFERENT machine there was nothing to say.
    Every sentence the dashboard writes about "this machine" needs a
    subject, and through a tunnel it is not the one the reader is at.
    """
    import socket
    try:
        return socket.gethostname()
    except Exception:
        return ""


def fbConnectionIsRemote(dictBrowserSessions, sBrowserSessionId):
    """Return True when this browser reached the hub over a tunnel.

    Answered from the session record, never from the request. Through
    the tunnel a remote browser IS an ordinary loopback client -- which
    is exactly the property that keeps the Host, Origin, credential and
    lease checks unweakened -- so the fact has to be CARRIED rather
    than detected, and it is carried on the capability that minted the
    session.
    """
    if not sBrowserSessionId or not dictBrowserSessions:
        return False
    from . import browserSession
    return browserSession.fbSessionIsRemote(
        dictBrowserSessions, sBrowserSessionId,
    )


def fdictExecutionTopology(sProjectMode):
    """Return which of the three locations coincide, and how work runs.

    Structured, not a boolean: there is more than one way for two of
    three places to be the same, and a bare flag could not grow a
    scheduled placement later without becoming a lie.

    ``bSameFilesystem`` is the one that matters today. In host mode the
    execution host and the execution environment are one filesystem, so
    "copy this to the host" copies a file from a directory to the home
    directory of the same machine and calls it a transfer.

    Takes the mode rather than the resource id so this module depends on
    nothing that depends on it; the caller already knows the mode.
    """
    return {
        "bSameFilesystem": sProjectMode == "host",
        "sExecutionPlacement": "direct",
    }
