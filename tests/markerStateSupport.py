"""Build the category states a marker implies, for tests of the appliers.

The dashboard no longer turns a marker's per-category counts into
verification axes in the route layer: ``testMarkerContract`` reads the
marker into category states (the one derivation every reader shares)
and the route layer only applies them. A test of the appliers needs
states in hand; this builds them from the shape those tests always
had -- a dict of per-category pass/fail counts -- through the REAL
derivation, never a hand-written state.
"""

from vaibify.gui import testMarkerContract


def fdictStatesFromCounts(dictCategories, bCurrent=True):
    """Return the states for a single-run marker that recorded these counts.

    ``bCurrent`` False gives the run no UTC stamp, which is the format
    the dashboard has never trusted: every category it speaks for then
    reads untested.
    """
    dictMarker = {
        "fTimestamp": 1_700_000_000.0,
        "sRunAtUtc": "2026-01-01T00:00:00Z" if bCurrent else "",
        "iExitStatus": 0, "dictCategories": dictCategories,
    }
    return testMarkerContract.fdictCategoryStatesFromMarker(
        dictMarker, [], {}, {}, 0)


def fdictEntryFromCounts(dictCategories, bStale=False):
    """Return a ``dictTestMarkers`` entry as the poll builds it."""
    return {
        "bStale": bStale,
        "dictMarker": {"sRunId": "legacy", "fTimestamp": 1_700_000_000.0},
        "dictCategoryStates": fdictStatesFromCounts(
            dictCategories, bCurrent=not bStale),
    }
