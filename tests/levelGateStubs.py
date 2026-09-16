"""Satisfy every Level 3 conjunct, so one test can fail exactly one.

``fbAtLeastLevel3`` takes its workflow-scope conjuncts FROM
``_fdictL3WorkflowChecks`` rather than re-listing them, which is what
stops the scalar gate outranking its own rows. The consequence for
tests is that failing ONE criterion means satisfying every other, and
"every other" grows whenever a criterion is added.

Three modules kept their own list of that set and all three went stale
together the moment ``attestation-not-in-zenodo-archive`` joined the
dict -- each one then asserting attainment over a gate that had grown a
conjunct nobody had stubbed. So the list lives here, once.

The individual gate FUNCTIONS are stubbed, never
``_fdictL3WorkflowChecks`` itself. Patching the dict builder would make
every "deleting this conjunct is caught" test vacuous: with the dict
replaced, deleting an entry from the real one changes nothing a test
can see.
"""

__all__ = [
    "T_LEVEL3_CONJUNCT_GATES",
    "fnMakeEveryLevel3ConjunctPass",
]


# Every gate ``fbAtLeastLevel3`` reaches, by the name it is bound to in
# ``levelGates``. ``fbL3AttestationCurrent`` is imported into that
# module's namespace, so patching it there is what the gate sees.
T_LEVEL3_CONJUNCT_GATES = (
    "fbAtLeastLevel2",
    "fbL3ReadinessOK",
    "fbL3AttestationCurrent",
    "fbVerifyDockerfilePinned",
    "fbVerifyDependencyLock",
    "fbVerifyEnvironmentSnapshot",
    "fbVerifyReproduceScript",
    "fbVerifyReproduceScriptCurrent",
    "fbWorkflowDeclaresBinaries",
    "fbEnvelopeMatchesGithubMirror",
    "fbEnvelopeMatchesZenodoArchive",
    "fbAttestationIsPubliclyArchived",
    "fbImageArchiveDeposited",
    "fbNoArchiveIsKnownSandbox",
)


def fnMakeEveryLevel3ConjunctPass(monkeypatch, **dictOverrides):
    """Stub every L3 conjunct True, then apply the named overrides.

    ``dictOverrides`` maps a gate name to the boolean it should answer,
    so a test states only the criterion it is about. ``None`` means
    LEAVE THE REAL FUNCTION ALONE -- for a test whose fixture is what
    makes that one criterion fail, which is the only honest way to
    assert that the FIXTURE is what the gate refused on.

    Getting that wrong is not a style point. A test that leaves other
    conjuncts failing asserts a refusal it did not cause: the gate
    returns False either way, so deleting the criterion under test
    changes nothing the test can see, and the mutation SURVIVES while
    the test reads as a guard.
    """
    from vaibify.reproducibility import levelGates
    for sName in T_LEVEL3_CONJUNCT_GATES:
        bValue = dictOverrides.get(sName, True)
        if bValue is None:
            continue
        monkeypatch.setattr(
            levelGates, sName,
            lambda *args, bValue=bValue, **kwargs: bValue,
        )
    listUnknown = sorted(
        set(dictOverrides) - set(T_LEVEL3_CONJUNCT_GATES)
    )
    assert listUnknown == [], (
        "these overrides name gates the Level 3 conjunct list does not "
        f"carry, so they stub nothing: {listUnknown}"
    )
