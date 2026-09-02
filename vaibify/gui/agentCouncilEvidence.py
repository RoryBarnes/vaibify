"""Evidence discipline for a settled turn's confirmed claims.

Phase 1 of the Agent Council (design/agentCouncil.md section 7.4). This
mixin turns the ``confirmed`` evidence claims a turn produced into
retained ledger entries, or reverts them to ``asserted`` — the
verified-versus-asserted honesty of principle 2.1 applied at the point a
claim is recorded. It is separated from the orchestration engine because
evidence provenance policy (baseline-versus-modified-state, the change
manifest, credential redaction) changes for different reasons than the
protocol progression that produced the turn.

A confirmed claim survives only in one of two honest forms: baseline —
the supporting command is re-run server-side through the injected
baseline-evidence executor in a fresh sandbox, so the ledger's state
identity IS the snapshot hash and the runner's possibly-mutated copy is
never trusted; or modified-state — the runner's own experiment, carrying
a reconstructable change manifest. A read-only council confirms nothing.
Anything the ledger refuses to retain (unprovenanced, oversize,
credential-bearing, or an incomplete manifest) reverts the claim rather
than shipping a ``confirmed`` label with no basis.

The mixin reads ``self.dictCampaign`` and calls the engine's
``_fnEmitEvent`` plus the injected ``fdictRecordEvidence`` and
``fdictExecuteBaselineEvidence`` callbacks — all resolved on the
concrete engine through the method-resolution order.
"""

import copy

from .agentCouncilCampaign import (
    S_CLAIM_ASSERTED,
    S_CLAIM_CONFIRMED,
    S_EXECUTION_READ_ONLY,
)

__all__ = ["EvidenceDisciplineMixin"]


class EvidenceDisciplineMixin:
    """Settle a turn's confirmed evidence claims against the ledger."""

    def _fnProcessEvidenceClaims(self, dictTurnRecord):
        bReadOnly = (self.dictCampaign["dictSettings"][
            "sExecutionPermission"] == S_EXECUTION_READ_ONLY)
        for dictClaim in dictTurnRecord["dictResult"].get(
                "listEvidence", []):
            if not isinstance(dictClaim, dict):
                continue
            if dictClaim.get("sStatus") != S_CLAIM_CONFIRMED:
                continue
            if bReadOnly:
                self._fnRevertClaim(dictClaim, "readOnlyCouncil")
            elif dictClaim.get("sStateForm") == "baseline":
                self._fnRecordBaselineClaim(dictClaim)
            elif dictClaim.get("sStateForm") == "modifiedState":
                self._fnRecordModifiedStateClaim(dictClaim)
            else:
                self._fnRevertClaim(dictClaim, "unprovenancedConfirmedClaim")

    def _fsMintClaimIdentifier(self):
        self.dictCampaign["iClaimCounter"] += 1
        return f"claim-{self.dictCampaign['iClaimCounter']}"

    def _fnRecordBaselineClaim(self, dictClaim):
        """Baseline confirmation is server-driven (section 9.6): the
        engine runs the supporting command through the baseline-evidence
        executor seam, never trusting a runner's possibly-mutated copy."""
        try:
            dictExecution = self.fdictExecuteBaselineEvidence(
                {"sCommandText": dictClaim.get("sCommandText", "")})
        except Exception as error:
            self._fnRevertClaim(dictClaim, f"baselineExecutorFailed: {error}")
            return
        dictEntry = {
            "sClaimIdentifier": self._fsMintClaimIdentifier(),
            "sAttemptBinding": self._fsDescribeCurrentAttemptBinding(),
            "sCommandText": dictClaim.get("sCommandText", ""),
            "sStateForm": "baseline",
            "sSnapshotHash": dictExecution.get("sSnapshotHash", ""),
            "sExecutionImageIdentity":
                dictExecution.get("sExecutionImageIdentity", ""),
            "iExitCode": dictExecution.get("iExitCode"),
            "sOutputDigest": dictExecution.get("sOutputDigest", ""),
        }
        self._fnSettleClaimAgainstLedger(dictClaim, dictEntry)

    def _fnRecordModifiedStateClaim(self, dictClaim):
        dictEntry = {
            "sClaimIdentifier": self._fsMintClaimIdentifier(),
            "sAttemptBinding": self._fsDescribeCurrentAttemptBinding(),
            "sCommandText": dictClaim.get("sCommandText", ""),
            "sStateForm": "modifiedState",
            "sSnapshotHash": dictClaim.get("sSnapshotHash", ""),
            "sExecutionImageIdentity":
                dictClaim.get("sExecutionImageIdentity", ""),
            "iExitCode": dictClaim.get("iExitCode"),
            "sOutputDigest": dictClaim.get("sOutputDigest", ""),
            "dictChangeManifest": copy.deepcopy(
                dictClaim.get("dictChangeManifest")),
        }
        self._fnSettleClaimAgainstLedger(dictClaim, dictEntry)

    def _fnSettleClaimAgainstLedger(self, dictClaim, dictEntry):
        dictAnswer = self.fdictRecordEvidence(dictEntry)
        if dictAnswer["bRecorded"]:
            dictClaim["sEvidenceEntryIdentifier"] = (
                dictAnswer["sEntryIdentifier"])
            dictClaim["sLedgerStateForm"] = dictEntry["sStateForm"]
            return
        self._fnRevertClaim(
            dictClaim,
            f"evidenceLedgerRefused: {dictAnswer['sRefusalReason']}")

    def _fnRevertClaim(self, dictClaim, sReason):
        dictClaim["sStatus"] = S_CLAIM_ASSERTED
        dictClaim["sReversionReason"] = sReason
        self._fnEmitEvent("confirmedClaimReverted", {"sReason": sReason})
