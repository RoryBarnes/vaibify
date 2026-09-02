"""An isolated tier failure must be distinguishable from a no-op.

``fdictGenerateReproducibilityEnvelope`` isolates each tier's failure so
a partial envelope beats no envelope. That is right, but it used to
return None and log the reason -- and the log is the one place the
caller could not look. An in-container agent reads
``get-host-log-tail``, whose agent lane withholds raw log lines and
returns only incidents tagged with the container id; the tier warnings
carried no such tag, so they reached the ring not at all.

The observable result was a readiness flag that stayed false, which
reads exactly like "this gate was already false and regeneration is
not what fixes it" -- and that is the wrong conclusion a session
actually drew, spending a long detour on it.
"""

from vaibify.gui import hostIncidents
from vaibify.reproducibility import dataArchiver
from vaibify.reproducibility.repoFiles import HostRepoFiles


def test_a_skipped_tier_reports_bwritten_false_with_a_reason(tmp_path):
    """A repo with no dependency declaration names what it lacked."""
    dictTiers = dataArchiver.fdictGenerateReproducibilityEnvelope(
        HostRepoFiles(str(tmp_path)), {"listSteps": []},
    )
    dictLock = dictTiers["requirements.lock"]
    assert dictLock["bWritten"] is False
    assert "No dependency input found" in dictLock["sSkipReason"]


def test_a_tier_that_ran_is_not_reported_as_skipped(tmp_path):
    """The manifest tier writes here, so its record must say so.

    Without this leg the test above passes for a generator that
    reports every tier as failed.
    """
    dictTiers = dataArchiver.fdictGenerateReproducibilityEnvelope(
        HostRepoFiles(str(tmp_path)), {"listSteps": []},
    )
    assert dictTiers["MANIFEST.sha256"]["bWritten"] is True
    assert dictTiers["MANIFEST.sha256"]["sSkipReason"] == ""


def test_the_environment_tier_says_why_it_skipped_without_a_container(
    tmp_path,
):
    """Tier 3 is skipped by design when no container is named.

    It returned None silently before, which is the same shape as a
    tier that ran -- so the caller could not report the difference.
    """
    dictTiers = dataArchiver.fdictGenerateReproducibilityEnvelope(
        HostRepoFiles(str(tmp_path)), {"listSteps": []},
    )
    dictEnv = dictTiers[".vaibify/environment.json"]
    assert dictEnv["bWritten"] is False
    assert "no container supplied" in dictEnv["sSkipReason"]


def test_a_tier_failure_reaches_the_per_container_incident_ring(tmp_path):
    """The warning must carry sContainerId or the agent never sees it.

    ``HostIncidentHandler`` drops every record without that attribute,
    and the agent lane of get-host-log-tail returns incidents ONLY.
    Asserting that something was logged would pass against the old
    untagged call; the tag is the whole property.
    """
    hostIncidents.fnResetHostIncidents()
    handlerIncident = hostIncidents.HostIncidentHandler()
    # The module's OWN logger object, not getLogger(__name__): it logs
    # to the "vaibify" logger, and a handler attached to the module's
    # dotted name would sit on a child that never receives the record.
    loggerArchiver = dataArchiver.logger
    loggerArchiver.addHandler(handlerIncident)
    try:
        dataArchiver.fdictGenerateReproducibilityEnvelope(
            HostRepoFiles(str(tmp_path)), {"listSteps": []},
            sContainerName="cid-under-test",
        )
    finally:
        loggerArchiver.removeHandler(handlerIncident)
    listIncidents = hostIncidents.flistIncidentsForContainer(
        "cid-under-test",
    )
    assert listIncidents, "tier failure never reached the incident ring"
    assert any(
        "requirements.lock" in dictIncident["sMessage"]
        for dictIncident in listIncidents
    )


def test_incidents_are_not_recorded_against_a_different_container(
    tmp_path,
):
    """The tag must be THIS container's, not merely present.

    A ring keyed by the wrong id is the name-vs-id failure this repo
    has already shipped once: green fixtures, and every real lookup
    empty.
    """
    hostIncidents.fnResetHostIncidents()
    handlerIncident = hostIncidents.HostIncidentHandler()
    # The module's OWN logger object, not getLogger(__name__): it logs
    # to the "vaibify" logger, and a handler attached to the module's
    # dotted name would sit on a child that never receives the record.
    loggerArchiver = dataArchiver.logger
    loggerArchiver.addHandler(handlerIncident)
    try:
        dataArchiver.fdictGenerateReproducibilityEnvelope(
            HostRepoFiles(str(tmp_path)), {"listSteps": []},
            sContainerName="cid-under-test",
        )
    finally:
        loggerArchiver.removeHandler(handlerIncident)
    assert hostIncidents.flistIncidentsForContainer("other-cid") == []
