"""Which of a project's configured secrets this host can resolve.

A secret is configured in the project, and satisfied by the MACHINE:
``gh_auth`` needs the GitHub CLI installed and logged in, ``keyring``
needs the OS keyring plus an entry, ``docker_secret`` needs a file
under ``/run/secrets``. None of the three travels with the repository,
so a project that starts on one machine can be unstartable on the next
one — and until 2026-09-05 the way a researcher found out was a
container that refused to start hours after they ticked a wizard
toggle, with a ``RuntimeError`` naming neither the secret nor the
remedy.

This module answers the question once, for every method, so no caller
grows a ``gh``-shaped special case: ``gh`` is one instance of one
defect. It NEVER materializes a secret to answer —
``secretManager.fbSecretExists`` probes without writing a token
anywhere, and a preflight that wrote one to a temp file to prove it
exists would have widened the attack surface to answer a question.

Nothing here carries a secret's VALUE, and nothing here should start
to: the descriptions are written for logs, CLI reports and the
dashboard, all of which are places a credential must never appear.
"""

__all__ = [
    "flistFindUnresolvableSecrets",
    "fsDescribeUnresolvableSecret",
]


# Per method: why the host cannot answer, and what the researcher does
# about it. Three methods have three remedies, so "a secret is
# unavailable" names none of them — which is the whole complaint.
_DICT_METHOD_DIAGNOSIS = {
    "gh_auth": (
        "the GitHub CLI is not installed on this host, or it is not "
        "logged in",
        "Install the GitHub CLI and run `gh auth login` on this "
        "machine (not inside the container).",
    ),
    "keyring": (
        "the OS keyring holds no entry for it, or the 'keyring' "
        "package is not installed",
        "Store the value in this host's keyring under the service "
        "'vaibify', or install the 'keyring' package.",
    ),
    "docker_secret": (
        "no file exists at /run/secrets/<name> on this host",
        "Provide that file, or change the project's secret method.",
    ),
}

# The one secret name with a cost the researcher can act on: the
# entrypoint reads the GitHub token from /run/secrets/gh_token, and
# without it the container is public-repos-only. Every other name
# belongs to the researcher's own workflow, so the cost is stated in
# terms of what the container will not have.
_S_GITHUB_TOKEN_SECRET_NAME = "gh_token"


def flistFindUnresolvableSecrets(listSecrets):
    """Return one record per configured secret this host cannot resolve.

    Each record carries ``sName``, ``sMethod``, ``sReason``,
    ``sRemedy`` and ``sCost``. An unknown method is reported rather
    than raised: a project.json naming a method vaibify does not
    support is exactly the sort of thing a readiness report exists to
    surface, and raising here would take the whole report down with it.
    """
    listUnresolvable = []
    for dictSecret in listSecrets or []:
        sName = (dictSecret or {}).get("name", "")
        sMethod = (dictSecret or {}).get("method", "")
        if _fbSecretResolves(sName, sMethod):
            continue
        listUnresolvable.append(_fdictDescribeSecret(sName, sMethod))
    return listUnresolvable


def _fbSecretResolves(sName, sMethod):
    """Return True iff this host can answer for the named secret.

    ``fbSecretExists`` already catches everything its probes can throw,
    but it validates its arguments first and raises on a method or name
    the project should never have held. That is a configuration fault,
    not an unresolvable secret, and it belongs in the report beside the
    others rather than as a traceback out of a readiness check.
    """
    from .secretManager import fbSecretExists
    try:
        return bool(fbSecretExists(sName, sMethod))
    except ValueError:
        return False


def _fdictDescribeSecret(sName, sMethod):
    """Return the reason/remedy/cost record for one unresolvable secret."""
    tDiagnosis = _DICT_METHOD_DIAGNOSIS.get(sMethod)
    if tDiagnosis is None:
        sReason = f"'{sMethod}' is not a secret method vaibify supports"
        sRemedy = (
            "Set the secret's method to one of "
            + ", ".join(sorted(_DICT_METHOD_DIAGNOSIS))
            + " in the project configuration."
        )
    else:
        sReason, sRemedy = tDiagnosis
    return {
        "sName": sName,
        "sMethod": sMethod,
        "sReason": sReason.replace("<name>", sName or "<name>"),
        "sRemedy": sRemedy,
        "sCost": _fsDescribeCost(sName),
    }


def _fsDescribeCost(sName):
    """Return what the container loses by starting without this secret."""
    if sName == _S_GITHUB_TOKEN_SECRET_NAME:
        return (
            "the container starts, but git operations against GitHub "
            "run unauthenticated: pushes are rejected and private "
            "repositories cannot be cloned"
        )
    return (
        "the container starts, but nothing will be mounted at "
        f"/run/secrets/{sName}, so any step that reads it fails"
    )


def fsDescribeUnresolvableSecret(dictRecord):
    """Return one human-readable line for a record, remedy included.

    One renderer, because the CLI preflight, the doctor report and the
    hub log must not each write their own wording for the same fact.
    """
    return (
        f"secret '{dictRecord.get('sName', '')}' "
        f"({dictRecord.get('sMethod', '')}) cannot be resolved on this "
        f"host: {dictRecord.get('sReason', '')} — "
        f"{dictRecord.get('sCost', '')}. {dictRecord.get('sRemedy', '')}"
    )
