"""What the attestation rerun asks of every command the runner runs.

The Level 3 rerun grades a run by its evidence, so every command it runs
needs an identity, every test command needs a JUnit report of its own,
and every exit status has to be accounted for. The rerun lane hands the
runner its evidence context (``{sEvidenceDirectory, listLedger}``) in
``dictVariables`` under :data:`S_RERUN_EVIDENCE_KEY`, the way the
environment overlay rides; the runner names each command through the
ONE identity builder every lane uses, and exports the report path only
for a test command. A run without the key builds today's command
strings, byte for byte.
"""

import posixpath

from .pipelineUtils import fsShellQuote


__all__ = [
    "S_RERUN_EVIDENCE_KEY",
    "TUPLE_TEST_INVOCATION_SECTIONS",
    "fsBuildScopedInvocationId",
    "fsJunitExportFor",
]


S_RERUN_EVIDENCE_KEY = "__dictRerunEvidence"
TUPLE_TEST_INVOCATION_SECTIONS = (
    "integrity", "qualitative", "quantitative", "legacy",
)


def fsBuildScopedInvocationId(sInvocationScope, iPosition):
    """Return an invocation ID through the one builder every lane uses.

    ``sInvocationScope`` is ``<step label>.<section>``.
    """
    from vaibify.reproducibility.rerunProcedure import fsBuildInvocationId
    sStepLabel, sSection = sInvocationScope.split(".", 1)
    return fsBuildInvocationId(sStepLabel, sSection, iPosition)


def fsJunitExportFor(dictRerunEvidence, sInvocationId):
    """Return the export pointing a test command's report at its own file.

    Empty for everything but a test command of the attestation rerun.
    The path is the rerun's private evidence directory plus the
    command's identity, never a name pytest or the step chose.
    """
    if not sInvocationId or sInvocationId.split(".")[1] not in (
            TUPLE_TEST_INVOCATION_SECTIONS):
        return ""
    sReport = posixpath.join(
        dictRerunEvidence["sEvidenceDirectory"], sInvocationId + ".xml")
    return f"export PYTEST_ADDOPTS={fsShellQuote('--junitxml=' + sReport)} && "
