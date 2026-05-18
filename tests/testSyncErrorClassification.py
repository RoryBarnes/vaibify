"""Tests for the broadened sync-error classifier patterns.

Plan D extended the auth, network, and conflict pattern lists in
``syncDispatcher.fdictClassifyError`` so that real-world failure
modes (missing keyring entry, DNS failure, Zenodo "this action is
not allowed", GitHub non-fast-forward) classify into actionable
buckets rather than falling through to ``unknown``.

The classifier is pure and operates only on lowercase substring
matches against the captured stderr/stdout from each push command,
so these tests construct realistic error strings and assert the
emitted ``sErrorType`` for each.
"""

from vaibify.gui.syncDispatcher import fdictClassifyError


class TestAuthPatterns:
    """Auth patterns: keyring missing or no token configured."""

    def test_fbKeyringSubstringIsAuth(self):
        dictResult = fdictClassifyError(
            1, "Could not load token from keyring backend",
        )
        assert dictResult["sErrorType"] == "auth"

    def test_fbNoTokenIsAuth(self):
        dictResult = fdictClassifyError(
            1, "Push failed: no token configured for service",
        )
        assert dictResult["sErrorType"] == "auth"

    def test_fbMissingCredentialsIsAuth(self):
        dictResult = fdictClassifyError(
            1, "Push failed: missing credentials for Zenodo",
        )
        assert dictResult["sErrorType"] == "auth"

    def test_fbNoKeyringErrorClassNameIsAuth(self):
        dictResult = fdictClassifyError(
            1, "raise NoKeyringError('no backend')",
        )
        assert dictResult["sErrorType"] == "auth"


class TestNetworkPatterns:
    """Network patterns: DNS failures and unreachable hosts."""

    def test_fbCouldNotResolveHostIsNetwork(self):
        dictResult = fdictClassifyError(
            1, "fatal: unable to access: Could not resolve host: "
            "github.com",
        )
        assert dictResult["sErrorType"] == "network"

    def test_fbNameResolutionIsNetwork(self):
        dictResult = fdictClassifyError(
            1, "Temporary failure in name resolution",
        )
        assert dictResult["sErrorType"] == "network"

    def test_fbDnsLowercaseIsNetwork(self):
        dictResult = fdictClassifyError(
            1, "DNS lookup failed for sandbox.zenodo.org",
        )
        assert dictResult["sErrorType"] == "network"

    def test_fbUnreachableIsNetwork(self):
        dictResult = fdictClassifyError(
            1, "Network is unreachable",
        )
        assert dictResult["sErrorType"] == "network"


class TestConflictPatterns:
    """Conflict patterns: published deposit, GitHub non-fast-forward."""

    def test_fbZenodoPublishedDepositionIsConflict(self):
        dictResult = fdictClassifyError(
            1,
            "HTTP 400: This action is not allowed for a "
            "published deposition.",
        )
        assert dictResult["sErrorType"] == "conflict"

    def test_fbGithubNonFastForwardIsConflict(self):
        dictResult = fdictClassifyError(
            1, "! [rejected]        main -> main (non-fast-forward)",
        )
        assert dictResult["sErrorType"] == "conflict"

    def test_fbBareRejectedIsNotConflict(self):
        """Hook rejections are auth/policy issues, not push conflicts.

        Git only emits the bracketed ``[rejected]`` form for non-fast-
        forward conflicts; bare ``rejected`` in stderr usually means
        a server-side hook denied the push for permission/policy
        reasons. Treating those as ``conflict`` would mislead the user
        into running ``git pull`` instead of fixing access controls.
        """
        dictResult = fdictClassifyError(
            1, "remote: error: rejected by repository hook",
        )
        assert dictResult["sErrorType"] != "conflict"


class TestAuthorIdentityPatterns:
    """git commit with no user.name / user.email surfaces a typed error."""

    def test_fbAuthorIdentityUnknownIsAuthorIdentity(self):
        dictResult = fdictClassifyError(
            128,
            "Author identity unknown\n*** Please tell me who you "
            "are.\nRun\n  git config --global user.email "
            "you@example.com\n",
        )
        assert dictResult["sErrorType"] == "authorIdentity"

    def test_fbAutoDetectEmailIsAuthorIdentity(self):
        dictResult = fdictClassifyError(
            128,
            "fatal: unable to auto-detect email address "
            "(got 'root@gj1132.(none)')",
        )
        assert dictResult["sErrorType"] == "authorIdentity"

    def test_fbEmptyIdentNameIsAuthorIdentity(self):
        dictResult = fdictClassifyError(
            128, "fatal: empty ident name (for <root@host>) not allowed",
        )
        assert dictResult["sErrorType"] == "authorIdentity"

    def test_fbAuthorIdentityWinsOverAuthFallback(self):
        """'Author identity' contains 'auth' but must not classify as auth."""
        dictResult = fdictClassifyError(
            128, "Author identity unknown",
        )
        assert dictResult["sErrorType"] == "authorIdentity"

    def test_fbAuthorIdentityWinsWhenAuthPatternAlsoPresent(self):
        """Identity bucket must run before the auth bucket.

        If git's output happens to mention both "Authentication" and
        "Author identity unknown" (e.g. a verbose remote helper),
        classifying as ``auth`` would hide the actionable remediation
        (set name + email) behind the wrong modal. Pin the precedence.
        """
        dictResult = fdictClassifyError(
            128,
            "Authentication succeeded\nfatal: Author identity "
            "unknown\nPlease tell me who you are.",
        )
        assert dictResult["sErrorType"] == "authorIdentity"


class TestUnknownFallback:
    """Synthetic random errors must still fall through to unknown."""

    def test_fbSyntheticRandomIsUnknown(self):
        dictResult = fdictClassifyError(
            7, "totally novel error string that matches nothing",
        )
        assert dictResult["sErrorType"] == "unknown"

    def test_fbEmptyOutputIsUnknown(self):
        dictResult = fdictClassifyError(1, "")
        assert dictResult["sErrorType"] == "unknown"
