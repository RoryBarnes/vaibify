"""One label on every container a live test lane creates, and a sweep.

A live lane creates throwaway containers and removes them in a fixture
teardown. A lane that is KILLED — a CI cancel, a ``^C``, a harness
timeout, a hub that dies with the suite — never reaches its teardown,
and the container stays. Ten of them accumulated on one researcher's
daemon that way, with names (``host-only``, ``acceptance``,
``fresh-image``) that read like somebody's research environment rather
than like litter, so nobody dared delete them. That is the real cost:
not the disk, but a researcher who can no longer tell their own
containers from the suite's.

Two rules close it, and they only work together:

- **Every container a test lane creates carries
  :data:`S_LIVE_LANE_LABEL`.** A label is the one piece of evidence a
  later sweep can act on without guessing from a name.
  ``tests/testLiveLanesLabelWhatTheyCreate.py`` fails if a lane creates
  a container without it.
- **The sweep destroys only what carries that label.** Never a name
  pattern, never an age, never "everything the suite might have made".
  A researcher's container cannot carry this label, because nothing but
  a test writes it.

The sweep runs at the START of a live session as well as the end: the
end is exactly what a killed run does not reach, so reclaiming at the
start is what actually collects the previous run's leftovers.
"""

S_LIVE_LANE_LABEL = "vaibify-live-test-lane"
_S_LABEL_VALUE = "1"


def flistLabelArguments():
    """Return the ``--label`` argv pair for a ``docker run`` command line."""
    return ["--label", f"{S_LIVE_LANE_LABEL}={_S_LABEL_VALUE}"]


def fdictLabels(dictExtra=None):
    """Return the ``labels=`` mapping for a Docker SDK create or run."""
    dictLabels = {S_LIVE_LANE_LABEL: _S_LABEL_VALUE}
    dictLabels.update(dictExtra or {})
    return dictLabels


def flistSweepLiveLaneContainers():
    """Destroy every container carrying the live-lane label; return names.

    Best-effort by construction. A daemon that cannot be reached, or a
    container that vanished between the list and the remove, must not
    fail a suite — the sweep is hygiene, and a hygiene step that can
    red a lane is a step people disable.
    """
    listRemoved = []
    try:
        import docker
        clientDocker = docker.from_env()
        listContainers = clientDocker.containers.list(
            all=True, filters={"label": S_LIVE_LANE_LABEL},
        )
    except Exception:
        return listRemoved
    for containerFound in listContainers:
        try:
            containerFound.remove(force=True)
            listRemoved.append(containerFound.name)
        except Exception:
            continue
    return listRemoved
