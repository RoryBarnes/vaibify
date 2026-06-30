"""Machine-applicable record of the mutation each falsification test kills.

A falsification test (pytest mark ``falsification``; see AGENTS.md
"Epistemics") is kill-confirmed: it was proven to FAIL when a specific
source mutation is applied. This registry stores that mutation in an
*applicable* form -- (source file, exact ``old`` text -> ``new`` text) --
so the kill can be RE-confirmed automatically as the code evolves, via
``tools/reconfirmFalsification.py``. A count of falsification tests means
little; "every one still kills its mutant" is the guarantee, and this
registry plus that harness is how it is kept honest.

INDEPENDENT-ORACLE RULE (load-bearing -- do not weaken): kill-confirmation
proves a test is SENSITIVE to change, NOT that its asserted value is
CORRECT. A falsification test is trustworthy only when its expected value
is derived INDEPENDENTLY of the code under test (a specification, an
analytic result, a conservation law, a published benchmark) AND it is
kill-confirmed; neither condition alone suffices. The danger zone is a
test written against freshly-authored, unverified code, whose oracle then
freezes the bug. (Mathews & Nagappan 2024; Konstantinou et al. 2024 --
see the vaibify-falsification-notes synthesis.)

Each entry:
- ``nodeid``: the pytest node id of the falsification test.
- ``source``: the source file the mutation is applied to.
- ``old``: the EXACT text to replace; must occur exactly once in ``source``.
- ``new``: the replacement (``old != new``); realizes the break the test
  is meant to catch.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Falsification:
    """One falsification test and the source mutation it is proven to kill."""

    nodeid: str
    source: str
    old: str
    new: str


# Hand-verified seed entries. The build appends the remainder, each
# confirmed by tools/reconfirmFalsification.py to actually kill its test.
LIST_FALSIFICATIONS = [
    Falsification(
        nodeid=(
            "tests/testL3AttestationMutationCoverage.py"
            "::test_empty_digest_attestation_not_current_without_manifest"
        ),
        source="vaibify/reproducibility/l3Attestation.py",
        old="if not sRecorded:",
        new="if False:",
    ),
    Falsification(
        nodeid=(
            "tests/testPathValidation.py"
            "::testRejectsRootEmbeddedAsInteriorSubstring"
        ),
        source="vaibify/gui/pipelineServer.py",
        old='if not sNormalized.startswith(sRoot + "/") and sNormalized != sRoot:',
        new='if not ((sRoot + "/") in sNormalized) and sNormalized != sRoot:',
    ),
]
