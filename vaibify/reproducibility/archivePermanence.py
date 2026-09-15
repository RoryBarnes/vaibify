"""Classify whether a Zenodo deposit is a permanent archive.

Zenodo's sandbox and its production instance are separate systems.
The sandbox mints test DOIs under DataCite's ``10.5072/`` prefix,
carries no preservation commitment, and may be cleaned at any time.
A deposit recorded there is therefore not an archive, however green
the row comparing its bytes against the envelope looks.

Three states, never a boolean. ``unknown`` is not a soft ``permanent``:
it is the answer when vaibify has been given no positive evidence
either way, it renders exactly as today, and it passes every gate.
A wrong ``sandbox`` costs a researcher a warning they did not need;
a wrong ``permanent`` costs them the warning this module exists to
show.

A leaf module: it imports the Zenodo DOI vocabulary and nothing from
:mod:`vaibify.gui`, because :mod:`vaibify.reproducibility.levelGates`
and the GUI payload builders both consume it.
"""

__all__ = [
    "S_PERMANENCE_PERMANENT",
    "S_PERMANENCE_SANDBOX",
    "S_PERMANENCE_UNKNOWN",
    "fsClassifyDeposit",
    "fsClassifyDepositRecord",
]

from vaibify.reproducibility import zenodoClient


S_PERMANENCE_PERMANENT = "permanent"
S_PERMANENCE_SANDBOX = "sandbox"
S_PERMANENCE_UNKNOWN = "unknown"

# The recorded service values vaibify itself writes, mapped to the
# claim each licenses. Any other value -- a typo, a service from a
# future release, a hand-edited envelope -- is not guessed around.
_DICT_SERVICE_PERMANENCE = {
    "sandbox": S_PERMANENCE_SANDBOX,
    "zenodo": S_PERMANENCE_PERMANENT,
}


def fsClassifyDeposit(sService, sDoi):
    """Return the permanence of a deposit: permanent, sandbox, unknown.

    Resolution order, and each step earns its place:

    1. A recorded service vaibify knows wins outright. It is what the
       deposit was actually made against, and it outranks anything
       inferred from the DOI string in both directions. A recorded
       service vaibify does NOT know abstains rather than falling
       through to the DOI: the record says where it lives and vaibify
       cannot read it, which is the definition of not knowing.
    2. With no service recorded at all -- records predating the field
       are legal -- a recognized sandbox DOI prefix means sandbox --
       :func:`zenodoClient.fsServiceForDoi` owns that prefix, and a
       second copy of it here would be a second authority.
    3. Otherwise a well-formed production Zenodo DOI means permanent.
       Nothing weaker does: ``fsServiceForDoi`` is total, so deferring
       to it wholesale would turn an empty or malformed DOI into the
       positive claim "permanently archived".
    4. Everything else abstains.
    """
    sRecorded = str(sService or "").strip()
    if sRecorded:
        return _DICT_SERVICE_PERMANENCE.get(sRecorded, S_PERMANENCE_UNKNOWN)
    sCleanDoi = str(sDoi or "").strip()
    if zenodoClient.fsServiceForDoi(sCleanDoi) == "sandbox":
        return S_PERMANENCE_SANDBOX
    if zenodoClient.fbIsWellFormedZenodoDoi(sCleanDoi):
        return S_PERMANENCE_PERMANENT
    return S_PERMANENCE_UNKNOWN


def fsClassifyDepositRecord(dictRecord):
    """Return the permanence of a deposit record, or unknown if absent.

    The two deposit records spell the SAME two fields differently,
    and reading only one lane's spelling is how a record classifies
    off its DOI prefix while its recorded instance is ignored:

    * the image deposit in ``environment.json`` names its instance
      ``sZenodoService`` and its DOI ``sVersionDoi``;
    * the project deposit in the sync sidecar names them ``sService``
      and ``sDoi`` (``DICT_REMOTE_PRODUCED_FIELDS["zenodo"]``).

    Both spellings are read here rather than normalized at the two
    call sites, so neither lane can be given a classifier that agrees
    with it by accident.
    """
    if not isinstance(dictRecord, dict):
        return S_PERMANENCE_UNKNOWN
    sDoi = dictRecord.get("sVersionDoi") or dictRecord.get("sDoi") or ""
    sService = (
        dictRecord.get("sZenodoService") or dictRecord.get("sService")
    )
    return fsClassifyDeposit(sService, sDoi)
