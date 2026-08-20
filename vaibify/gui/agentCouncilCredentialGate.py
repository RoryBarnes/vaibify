"""Version-bound, machine-readable credential enablement (R10).

The council's runner backend reuses the researcher's own provider
subscription, which is the single highest-trust thing vaibify touches —
so the backend is DISABLED BY DEFAULT, and nothing in this repository
can enable it. What enables it is a machine-readable EVIDENCE RECORD
the maintainer writes after personally running the live credential
check on a paid account (design section 2.7 / 9.7; remediation R10):
one real runner, the copied access token only, a trivial headless turn,
the project login still valid afterwards, the token not rotated, the
staged files gone, across a failure and a crash-recovery. No test in
this repository can stand in for that run, and no green suite here may
be read as those properties holding.

The record is keyed to everything that could invalidate the evidence:
provider and backend, the CLI version the check ran, the project-image
identity it ran in, the credential schema and source, the host
platform, and the verification date. ANY mismatch — a missing record,
an unreadable one, a missing key, a different image — evaluates to
DISABLED with the mismatch named, never to a permissive default. The
image identity is the pin that does the runtime work: the START path
resolves the project image first and always passes it, and because the
CLI binary ships inside that image, pinning the image identity is what
holds the CLI version fixed between live checks — the recorded
``sCliVersion`` documents what was verified, while the image comparison
enforces it (a CLI probe would itself need a runner turn). Even
with a match, login PRESENCE is probed live at launch (the extraction
raises without a persisted login), and the first turn's failure
classification is the live usable-model probe: an unusable credential
lands as an authentication-classified failed turn, never a hang.

Enablement never weakens the disclosure: the residual
token-exfiltration risk (a prompt-injected model reading its own
copied token) is structural, and the UI keeps stating it after
verification — verified means "the plumbing was witnessed once", not
"proven secure".
"""

import json
import os
import sys

__all__ = [
    "LIST_EVIDENCE_REQUIRED_KEYS",
    "S_EXPECTED_CREDENTIAL_SCHEMA",
    "fdictEvaluateCredentialEnablement",
    "fsResolveCredentialEvidencePath",
]

LIST_EVIDENCE_REQUIRED_KEYS = [
    "sProvider",
    "sBackend",
    "sCliVersion",
    "sImageIdentity",
    "sCredentialSchema",
    "sCredentialSource",
    "sHostPlatform",
    "sVerificationDate",
]

# The narrowest authenticating field the extraction lane copies. A
# record naming any other schema was verified against a different
# handling path and enables nothing.
S_EXPECTED_CREDENTIAL_SCHEMA = "claudeAiOauth.accessToken"


def fsResolveCredentialEvidencePath():
    """Return the host app-data path the evidence record lives at.

    Beside the campaign store under the researcher's home — host
    app-data, never the repository: the record is a statement about
    THIS machine's verified setup, and committing one would enable the
    backend for machines the check never ran on.
    """
    return os.path.join(
        os.path.expanduser("~"), ".vaibify", "agentCouncils",
        "credentialEvidence.json")


def _fdictDisable(sReason):
    """Return the disabled evaluation with its reason named."""
    return {"bEnabled": False, "sReason": sReason, "dictRecord": None}


def fdictEvaluateCredentialEnablement(sProvider, sImageIdentity=None):
    """Evaluate the runner-backend enablement for one provider.

    Fails CLOSED on every path: no record, an unreadable record, a
    missing key, or any mismatch answers disabled with the reason
    named. ``sImageIdentity`` is compared when the caller knows it (the
    launch path resolves the project image first); a capabilities read
    that does not know it yet checks every other key.
    """
    sEvidencePath = fsResolveCredentialEvidencePath()
    if not os.path.isfile(sEvidencePath):
        return _fdictDisable(
            "the runner backend is disabled: no credential-verification "
            "evidence record exists on this machine. The maintainer "
            "records one only after personally running the live "
            "credential check on a paid account.")
    try:
        with open(sEvidencePath, encoding="utf-8") as fileEvidence:
            jsonRecord = json.load(fileEvidence)
    except (OSError, ValueError) as error:
        return _fdictDisable(
            "the credential-verification evidence record is unreadable "
            f"({type(error).__name__}); the runner backend stays "
            "disabled.")
    if not isinstance(jsonRecord, dict):
        return _fdictDisable(
            "the credential-verification evidence record is not a "
            "mapping; the runner backend stays disabled.")
    for sRequiredKey in LIST_EVIDENCE_REQUIRED_KEYS:
        if not jsonRecord.get(sRequiredKey):
            return _fdictDisable(
                "the credential-verification evidence record is missing "
                f"'{sRequiredKey}'; the runner backend stays disabled.")
    for sKey, sExpected, sWhat in (
            ("sProvider", sProvider, "provider"),
            ("sBackend", "runner", "backend"),
            ("sCredentialSchema", S_EXPECTED_CREDENTIAL_SCHEMA,
             "credential schema"),
            ("sHostPlatform", sys.platform, "host platform")):
        if jsonRecord[sKey] != sExpected:
            return _fdictDisable(
                f"the evidence record's {sWhat} is "
                f"{jsonRecord[sKey]!r}, not {sExpected!r}; the runner "
                "backend stays disabled.")
    if sImageIdentity is not None and (
            jsonRecord["sImageIdentity"] != sImageIdentity):
        return _fdictDisable(
            "the evidence was recorded for image "
            f"{jsonRecord['sImageIdentity']!r} but this project runs "
            f"{sImageIdentity!r}; the runner backend stays disabled "
            "until the live check is re-run there.")
    return {"bEnabled": True, "sReason": "", "dictRecord": jsonRecord}
