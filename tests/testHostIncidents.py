"""Tests for vaibify.gui.hostIncidents ring buffer + logging handler."""

import logging

import pytest

from vaibify.gui import hostIncidents


@pytest.fixture(autouse=True)
def fnClearRingBetweenTests():
    """Reset the module-level ring before and after every test."""
    hostIncidents.fnResetHostIncidents()
    yield
    hostIncidents.fnResetHostIncidents()


def test_fnRecordHostIncident_appends_entry():
    hostIncidents.fnRecordHostIncident(
        "ctr-a", {"sIso": "2026-06-16T00:00:00Z", "sMessage": "boom"},
    )
    listIncidents = hostIncidents.flistIncidentsForContainer("ctr-a")
    assert len(listIncidents) == 1
    assert listIncidents[0]["sMessage"] == "boom"


def test_fnRecordHostIncident_empty_container_id_is_noop():
    hostIncidents.fnRecordHostIncident("", {"sMessage": "stray"})
    assert hostIncidents.flistIncidentsForContainer("") == []


def test_fnRecordHostIncident_empty_dict_is_noop():
    hostIncidents.fnRecordHostIncident("ctr-a", {})
    assert hostIncidents.flistIncidentsForContainer("ctr-a") == []


def test_flistIncidentsForContainer_returns_empty_for_unknown():
    assert hostIncidents.flistIncidentsForContainer("ctr-x") == []


def test_fdictLatestIncidentForContainer_returns_None_when_empty():
    assert hostIncidents.fdictLatestIncidentForContainer("ctr-x") is None


def test_fdictLatestIncidentForContainer_returns_last_entry():
    hostIncidents.fnRecordHostIncident("ctr-a", {"sMessage": "first"})
    hostIncidents.fnRecordHostIncident("ctr-a", {"sMessage": "second"})
    dictLatest = hostIncidents.fdictLatestIncidentForContainer("ctr-a")
    assert dictLatest["sMessage"] == "second"


def test_ring_bounded_to_max_entries():
    """A noisy container cannot grow memory without bound."""
    iMax = hostIncidents.I_MAX_INCIDENTS_PER_CONTAINER
    for i in range(iMax + 10):
        hostIncidents.fnRecordHostIncident(
            "ctr-a", {"sIso": f"i-{i}", "sMessage": f"event-{i}"},
        )
    listIncidents = hostIncidents.flistIncidentsForContainer("ctr-a")
    assert len(listIncidents) == iMax
    assert listIncidents[-1]["sMessage"] == f"event-{iMax + 9}"


def test_per_container_buckets_are_independent():
    hostIncidents.fnRecordHostIncident("ctr-a", {"sMessage": "a-event"})
    hostIncidents.fnRecordHostIncident("ctr-b", {"sMessage": "b-event"})
    assert (
        hostIncidents.fdictLatestIncidentForContainer("ctr-a")["sMessage"]
        == "a-event"
    )
    assert (
        hostIncidents.fdictLatestIncidentForContainer("ctr-b")["sMessage"]
        == "b-event"
    )


def test_HostIncidentHandler_captures_tagged_record():
    """A LogRecord carrying sContainerId lands in the ring."""
    loggerLocal = logging.getLogger("vaibify.test.host-incident-tagged")
    loggerLocal.setLevel(logging.INFO)
    handlerIncident = hostIncidents.HostIncidentHandler()
    loggerLocal.addHandler(handlerIncident)
    try:
        loggerLocal.error(
            "runner died at step %d", 7,
            extra={"sContainerId": "ctr-tagged"},
        )
    finally:
        loggerLocal.removeHandler(handlerIncident)
    dictLatest = hostIncidents.fdictLatestIncidentForContainer(
        "ctr-tagged",
    )
    assert dictLatest is not None
    assert dictLatest["sLevel"] == "ERROR"
    assert "runner died at step 7" in dictLatest["sMessage"]


def test_HostIncidentHandler_captures_exception_repr():
    """When exc_info is supplied the repr is stored in the incident."""
    loggerLocal = logging.getLogger("vaibify.test.host-incident-exc")
    loggerLocal.setLevel(logging.INFO)
    handlerIncident = hostIncidents.HostIncidentHandler()
    loggerLocal.addHandler(handlerIncident)
    try:
        try:
            raise RuntimeError("simulated host crash")
        except RuntimeError:
            loggerLocal.exception(
                "pipeline died",
                extra={"sContainerId": "ctr-exc"},
            )
    finally:
        loggerLocal.removeHandler(handlerIncident)
    dictLatest = hostIncidents.fdictLatestIncidentForContainer("ctr-exc")
    assert "RuntimeError" in dictLatest["sExceptionRepr"]
    assert "simulated host crash" in dictLatest["sExceptionRepr"]


def test_HostIncidentHandler_ignores_untagged_record():
    """Records without sContainerId do not pollute any bucket."""
    loggerLocal = logging.getLogger("vaibify.test.host-incident-untag")
    loggerLocal.setLevel(logging.INFO)
    handlerIncident = hostIncidents.HostIncidentHandler()
    loggerLocal.addHandler(handlerIncident)
    try:
        loggerLocal.warning("untagged warning, should be dropped")
    finally:
        loggerLocal.removeHandler(handlerIncident)
    # No buckets should exist.
    for sCid in ("", "any", "untagged"):
        assert hostIncidents.flistIncidentsForContainer(sCid) == []


def test_fnEvictHostIncidentsForContainer_drops_bucket():
    """Sweep-driven eviction removes the named container's deque."""
    hostIncidents.fnRecordHostIncident("doomed", {"sMessage": "gone"})
    hostIncidents.fnRecordHostIncident("keeper", {"sMessage": "stay"})
    hostIncidents.fnEvictHostIncidentsForContainer("doomed")
    assert hostIncidents.flistIncidentsForContainer("doomed") == []
    assert (
        hostIncidents.flistIncidentsForContainer("keeper")[0]["sMessage"]
        == "stay"
    )


def test_fnEvictHostIncidentsForContainer_unknown_id_is_noop():
    """Evicting an absent bucket does not raise or leak state."""
    hostIncidents.fnEvictHostIncidentsForContainer("never-existed")
    hostIncidents.fnEvictHostIncidentsForContainer("")
    assert hostIncidents.flistIncidentsForContainer("never-existed") == []
