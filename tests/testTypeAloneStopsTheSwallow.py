"""A refusal is not an I/O error, and the TYPE is what says so.

WHY THIS FILE IS SEPARATE FROM THE GUARD TESTS
----------------------------------------------

``8dcb07c`` fixed twelve sites that swallowed a carrier refusal, by
calling :func:`fnReRaiseControlPlaneRefusal` first in each handler.
That worked, and it was the wrong shape to stop at: 85 ``except
OSError``/``except PermissionError`` clauses exist under ``vaibify/``,
and the twelve were only the ones on a path somebody had walked. Every
future handler would have had to remember.

The durable fix is the type. ``MutationNotAdmittedError`` and
``CommitRefusedError`` used to subclass ``PermissionError``, hence
``OSError``, so an ``except OSError`` caught them by construction. They
now descend from :class:`ControlPlaneRefusalError`, which descends from
``Exception`` — so an ``except OSError`` does not see them at all.

These tests assert that property THROUGH A BARE ``except OSError``, with
no guard call anywhere in the path, so they cannot pass on the strength
of the twelve hand-guards. The kill-confirm reparents the classes back
to ``PermissionError``: if these tests still passed after that, their
premise would be wrong and the guards would be the thing doing the work.
"""

import pytest

from vaibify.config.mutationAdmission import (
    ControlPlaneRefusalError,
    MutationNotAdmittedError,
)
from vaibify.gui.commitCarrier import CommitRefusedError


LIST_REFUSAL_CLASSES = [MutationNotAdmittedError, CommitRefusedError]
LIST_REFUSAL_IDS = ["MutationNotAdmittedError", "CommitRefusedError"]


def _fnSwallowLikeAConservativeReader(fnRaise):
    """Answer None on I/O failure, exactly as the level gates do.

    A deliberately faithful copy of the shape this bug lived in, and
    deliberately NOT a call into the real gate: the real one now calls
    ``fnReRaiseControlPlaneRefusal`` first, so driving it would prove
    the guard works and say nothing about the type. There is no guard
    here. Whatever this returns, the type alone decided.
    """
    try:
        return fnRaise()
    except OSError:
        return None


@pytest.mark.falsification
@pytest.mark.parametrize(
    "classRefusal", LIST_REFUSAL_CLASSES, ids=LIST_REFUSAL_IDS,
)
def testARefusalIsNotCaughtByABareExceptOsError(classRefusal):
    """A refusal must pass straight through an I/O handler.

    This is the whole guarantee in one line of control flow. Before the
    reparenting the call below returned ``None`` — the refusal was
    absorbed and the caller went on to report "unverified" — and after
    it the refusal comes back out.

    Kills: reparenting the class onto ``PermissionError``.
    """
    def fnRaise():
        raise classRefusal("refused")

    with pytest.raises(classRefusal):
        _fnSwallowLikeAConservativeReader(fnRaise)


@pytest.mark.parametrize(
    "classRefusal", LIST_REFUSAL_CLASSES, ids=LIST_REFUSAL_IDS,
)
def testARefusalIsNotAnOsError(classRefusal):
    """State the ancestry directly, so a reader need not run anything.

    ``PermissionError`` is checked as well as ``OSError`` because it is
    the spelling both classes actually used, and because a handler
    written as ``except PermissionError`` is the one whose author most
    believed they were narrowing.
    """
    assert not issubclass(classRefusal, OSError)
    assert not issubclass(classRefusal, PermissionError)
    assert issubclass(classRefusal, ControlPlaneRefusalError)


def testARefusalEscapingARouteIsLoggedAndNotJustA500(caplog):
    """The last-resort handler must not make a refusal quiet again.

    Reparenting onto ``Exception`` keeps a refusal inside the reach of
    ``pipelineServer``'s ``@app.exception_handler(Exception)``. That is
    fine only if the handler is LOUD: a refusal that becomes an
    unlogged generic 500 is barely better than one that became
    ``None``, which is the failure this whole line of work exists to
    remove.

    So the assertion is on the LOG RECORD, not the status code. The
    client body stays sanitized on purpose -- internal paths and
    credentials must not leak -- which is exactly why the diagnosis has
    to reach the log instead.
    """
    import logging

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from vaibify.gui.pipelineServer import (
        _fnRegisterLastResortExceptionHandler,
    )

    app = FastAPI()

    @app.get("/refuses")
    async def fnRefuses():
        raise MutationNotAdmittedError("refused with no carrier")

    _fnRegisterLastResortExceptionHandler(app)
    client = TestClient(app, raise_server_exceptions=False)

    with caplog.at_level(logging.ERROR, logger="vaibify"):
        response = client.get("/refuses")

    assert response.status_code == 500
    listErrors = [
        record for record in caplog.records
        if record.levelno >= logging.ERROR
    ]
    assert listErrors, (
        "a refusal escaped a route and produced a 500 with NO log "
        "record; the researcher sees a sanitized body and nobody can "
        "tell a refused mutation from any other server error"
    )
    assert any(
        record.exc_info is not None for record in listErrors
    ), (
        "the refusal was logged without a traceback, so the log names "
        "no primitive and no route -- which is the diagnosis"
    )


def testOneBaseCoversEveryRefusalTheCodebaseDefines():
    """No refusal class may sit outside the base the guard checks.

    The predicate re-raises on :class:`ControlPlaneRefusalError`. A
    third refusal class added later off plain ``Exception`` would be
    invisible to it and to every reader who checked the base — which is
    exactly how ``CommitRefusedError`` came to be missed by the guard
    that existed to catch it.
    """
    import inspect

    from vaibify.config import mutationAdmission
    from vaibify.gui import commitCarrier

    listRefusalClasses = []
    for moduleUnderTest in (mutationAdmission, commitCarrier):
        for _sName, objMember in inspect.getmembers(
            moduleUnderTest, inspect.isclass,
        ):
            if objMember.__module__ != moduleUnderTest.__name__:
                continue
            if "Refus" in objMember.__name__ or (
                "NotAdmitted" in objMember.__name__
            ):
                listRefusalClasses.append(objMember)

    assert listRefusalClasses, "found no refusal classes to check at all"
    listOutside = [
        objClass.__name__ for objClass in listRefusalClasses
        if not issubclass(objClass, ControlPlaneRefusalError)
    ]
    assert listOutside == [], (
        f"these refusal classes do not descend from "
        f"ControlPlaneRefusalError, so fnReRaiseControlPlaneRefusal "
        f"will not re-raise them and every conservative handler will "
        f"absorb them: {listOutside}"
    )
