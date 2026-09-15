"""``fbAtLeastLevel3`` had a second list of its own conjuncts, and it forgot one.

Two defects of one shape on one gate.

The scalar gate enumerated its conjuncts BY HAND beside the dict every
other surface reads, and ``fbAttestationIsPubliclyArchived`` was
registered in the dict and in the header tuple and never added here --
so a project whose Zenodo archive carried no covering attestation was
reported as Level 3 attained while the row beneath it blocked. That is
the header-outranks-its-rows defect, one level up from the display.

And it took no host-mode argument at all. A host project was denied
Level 3 only INCIDENTALLY, by failing the published-artifact conjuncts,
which reaches the right answer for the wrong reason: Level 3 is defined
by a pinned container image, and a host project has none, so no amount
of publishing could ever change the verdict.

The fix for the first was to delete the list rather than to parse it --
parsing ``fbAtLeastLevel3``'s source would be the same reflex that let
an ``ImportError`` ship behind a green source-text guard. The tests here
are therefore BEHAVIOURAL: they drive the gate.
"""

import pytest

from tests.levelGateStubs import fnMakeEveryLevel3ConjunctPass
from vaibify.reproducibility import levelGates


def _ffilesStubRepo():
    """A repo adapter the stubbed gate never actually reads."""
    from vaibify.reproducibility.repoFiles import HostRepoFiles
    return HostRepoFiles("/nonexistent-for-shape")


@pytest.mark.falsification
def test_an_archive_with_no_covering_attestation_denies_level_three(
    monkeypatch,
):
    """The conjunct the hand-written list forgot.

    Every other criterion is satisfied, so this can only be about the
    archived attestation -- which is the state that used to attain
    Level 3 while its own row was red.

    Kills: reinstating a hand-written conjunct list in
    ``fbAtLeastLevel3`` that omits this criterion, and dropping
    ``attestation-not-in-zenodo-archive`` from
    ``_fdictL3WorkflowChecks``.
    """
    fnMakeEveryLevel3ConjunctPass(
        monkeypatch, fbAttestationIsPubliclyArchived=False,
    )
    assert levelGates.fbAtLeastLevel3(
        {"listSteps": []}, _ffilesStubRepo(), False,
    ) is False


def test_the_same_project_attains_it_once_the_archive_carries_one(
    monkeypatch,
):
    """The complement, without which the test above passes against a gate
    that refuses everything."""
    fnMakeEveryLevel3ConjunctPass(monkeypatch)
    assert levelGates.fbAtLeastLevel3(
        {"listSteps": []}, _ffilesStubRepo(), False,
    ) is True


@pytest.mark.falsification
def test_a_host_project_is_denied_with_every_conjunct_satisfied(
    monkeypatch,
):
    """Denied BY CONSTRUCTION, not by failing something else.

    The state asserted here is the one that would otherwise attain the
    level: every conjunct green, host mode true. A gate that denied
    host projects only through the published-artifact conjuncts would
    return True here, which is why the stubs are total.

    Kills: dropping the ``bHostProject`` parameter, or defaulting it to
    False so a caller that forgot to ask is answered rather than told.
    """
    fnMakeEveryLevel3ConjunctPass(monkeypatch)
    assert levelGates.fbAtLeastLevel3(
        {"listSteps": []}, _ffilesStubRepo(), True,
    ) is False


@pytest.mark.falsification
def test_a_host_project_never_reports_level_three(monkeypatch):
    """The scalar LEVEL follows, because the level is what surfaces render.

    ``fiProofLevel`` threads the fact through; a defaulted parameter
    dropped at any hop would be indistinguishable from one that
    arrived False, and the dashboard would show a host project at
    Level 3 with every gate telling the truth.

    Kills: giving ``fiProofLevel``'s ``bHostProject`` a default.
    """
    for sName in ("fbAtLeastLevel1", "fbAtLeastLevel2"):
        monkeypatch.setattr(
            levelGates, sName, lambda *args, **kwargs: True,
        )
    fnMakeEveryLevel3ConjunctPass(monkeypatch)
    assert levelGates.fiProofLevel(
        {"listSteps": []}, _ffilesStubRepo(), bHostProject=True,
    ) == 2
    assert levelGates.fiProofLevel(
        {"listSteps": []}, _ffilesStubRepo(), bHostProject=False,
    ) == 3
    with pytest.raises(TypeError):
        levelGates.fiProofLevel({"listSteps": []}, _ffilesStubRepo())


@pytest.mark.falsification
def test_readiness_still_gates_what_the_checks_dict_does_not_carry(
    monkeypatch,
):
    """``fbL3ReadinessOK`` stays, and stays FIRST.

    The checks dict carries neither project-repo, manifest-complete nor
    determinism -- those are evaluated per step -- so replacing the
    gate's body with the dict alone would SILENTLY widen Level 3 to
    projects with an incomplete manifest. The overlap between the two
    (Dockerfile, lock, snapshot, reproduce-script) is harmless; the
    gap is not.

    Kills: deleting the ``fbL3ReadinessOK`` call now that the dict
    supplies the conjuncts.
    """
    setChecks = set(levelGates._fdictL3WorkflowChecks(
        {"listSteps": []}, _ffilesStubRepo(),
    ))
    assert "manifest-incomplete" not in setChecks, (
        "the checks dict has grown the manifest criterion; if it now "
        "carries every readiness question, this test is asserting "
        "nothing and the reason for keeping fbL3ReadinessOK has "
        "changed"
    )
    fnMakeEveryLevel3ConjunctPass(monkeypatch, fbL3ReadinessOK=False)
    assert levelGates.fbAtLeastLevel3(
        {"listSteps": []}, _ffilesStubRepo(), False,
    ) is False
