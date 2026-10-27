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
a reconstructable change manifest, which vaibify replays itself: the
same executor seeds a fresh sandbox from the snapshot, applies the
manifest there and runs the command, and the ledger records what that
sandbox observed. A participant's own report of an exit code is never
a confirmation, and a manifest vaibify cannot apply reverts the claim.
A read-only council confirms nothing. Anything the ledger refuses to
retain (unprovenanced, oversize, credential-bearing, or an incomplete
manifest) reverts the claim rather than shipping a ``confirmed`` label
with no basis.

A web source is never confirmed and never ledgered. It stays in the
turn record, links and query included, and is scanned for credential
shapes the way a ledgered command is: a member misled by a planted
instruction could put a login token into a search query.

The mixin reads ``self.dictCampaign`` and calls the engine's
``_fnEmitEvent`` plus the injected ``fdictRecordEvidence`` and
``fdictExecuteBaselineEvidence`` callbacks — all resolved on the
concrete engine through the method-resolution order.
"""

import asyncio

from . import agentCouncilManifest
from . import agentCouncilStore
from .agentCouncilCampaign import (
    S_CLAIM_ASSERTED,
    S_CLAIM_CONFIRMED,
    S_EXECUTION_READ_ONLY,
)

__all__ = ["EvidenceDisciplineMixin"]


class EvidenceDisciplineMixin:
    """Settle a turn's confirmed evidence claims against the ledger."""

    async def _fnProcessEvidenceClaims(self, dictTurnRecord):
        bReadOnly = (self.dictCampaign["dictSettings"][
            "sExecutionPermission"] == S_EXECUTION_READ_ONLY)
        for dictClaim in dictTurnRecord["dictResult"].get(
                "listEvidence", []):
            if not isinstance(dictClaim, dict):
                continue
            if dictClaim.get("sStateForm") == "webSource":
                self._fnSettleWebSourceClaim(dictClaim)
                continue
            if dictClaim.get("sStatus") != S_CLAIM_CONFIRMED:
                continue
            if bReadOnly:
                self._fnRevertClaim(dictClaim, "readOnlyCouncil")
            elif dictClaim.get("sStateForm") == "baseline":
                await self._fnRecordBaselineClaim(dictClaim)
            elif dictClaim.get("sStateForm") == "modifiedState":
                await self._fnRecordModifiedStateClaim(dictClaim)
            else:
                self._fnRevertClaim(dictClaim, "unprovenancedConfirmedClaim")

    def _fnSettleWebSourceClaim(self, dictClaim):
        """Keep a web source in the record, credential spans removed.

        A confirmed web source cannot pass validation, so the first
        branch only matters if that ever stops holding; it keeps the
        engine from becoming the place a web page is called confirmed.
        """
        if dictClaim.get("sStatus") == S_CLAIM_CONFIRMED:
            self._fnRevertClaim(dictClaim, "webSourceNeverConfirmed")
        listTexts = [str(sLink) for sLink in
                     dictClaim.get("listSourceLinks") or []]
        listTexts.append(str(dictClaim.get("sSearchQuery", "")))
        if not any(agentCouncilStore.fbDetectCredentialText(sText)
                   for sText in listTexts):
            return
        dictClaim["listSourceLinks"] = [
            agentCouncilStore.fsRedactCredentialSpans(str(sLink))
            for sLink in dictClaim.get("listSourceLinks") or []]
        dictClaim["sSearchQuery"] = agentCouncilStore.fsRedactCredentialSpans(
            str(dictClaim.get("sSearchQuery", "")))
        self._fnRevertClaim(dictClaim, "credentialInWebSource")

    def _fsMintClaimIdentifier(self):
        self.dictCampaign["iClaimCounter"] += 1
        return f"claim-{self.dictCampaign['iClaimCounter']}"

    async def _fnRecordBaselineClaim(self, dictClaim):
        """Baseline confirmation is server-driven (section 9.6): the
        engine runs the supporting command through the baseline-evidence
        executor seam, never trusting a runner's possibly-mutated copy.
        The command is model-supplied and runs in a container, so the
        executor is awaited on a worker thread under the campaign's
        turn wall clock; on the event loop it froze every route."""
        try:
            dictExecution = await asyncio.to_thread(
                self.fdictExecuteBaselineEvidence,
                {"sCommandText": dictClaim.get("sCommandText", "")})
        except Exception as error:
            self._fnRevertClaim(dictClaim, f"baselineExecutorFailed: {error}")
            return
        self._fnSettleClaimAgainstLedger(dictClaim, self._fdictComposeObservedEntry(
            dictClaim, "baseline", dictExecution))

    async def _fnRecordModifiedStateClaim(self, dictClaim):
        """Replay a modified-state claim before the ledger may record it.

        The same executor and the same off-loop, wall-clock-bounded call
        as a baseline claim; the request adds the manifest, which the
        executor applies to its fresh copy of the snapshot before it
        runs the command. Any failure to admit, apply or run reverts the
        claim with the reason.
        """
        dictManifest = dictClaim.get("dictChangeManifest")
        try:
            dictExecution = await asyncio.to_thread(
                self.fdictExecuteBaselineEvidence,
                {"sCommandText": dictClaim.get("sCommandText", ""),
                 "dictChangeManifest": dictManifest})
        except Exception as error:
            self._fnRevertClaim(
                dictClaim, f"modifiedStateReplayFailed: {error}")
            return
        dictEntry = self._fdictComposeObservedEntry(
            dictClaim, "modifiedState", dictExecution)
        dictEntry["dictChangeManifest"] = (
            agentCouncilManifest.fdictTranslateManifestForLedger(dictManifest))
        self._fnSettleClaimAgainstLedger(dictClaim, dictEntry)

    def _fdictComposeObservedEntry(self, dictClaim, sStateForm,
                                   dictExecution):
        """Build a ledger entry whose provenance is what the sandbox saw.

        Shared by both confirmed forms because these four fields are the
        whole point of a replay: they come from the executor, never from
        the claim, and two copies of this mapping could drift apart.
        """
        return {
            "sClaimIdentifier": self._fsMintClaimIdentifier(),
            "sAttemptBinding": self._fsDescribeCurrentAttemptBinding(),
            "sCommandText": dictClaim.get("sCommandText", ""),
            "sStateForm": sStateForm,
            "sSnapshotHash": dictExecution.get("sSnapshotHash", ""),
            "sExecutionImageIdentity":
                dictExecution.get("sExecutionImageIdentity", ""),
            "iExitCode": dictExecution.get("iExitCode"),
            "sOutputDigest": dictExecution.get("sOutputDigest", ""),
        }

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
