"""One question, asked the same way everywhere: can Docker answer now?

Before host mode, ``dictCtx["docker"]`` was a ``DockerConnection`` or
``None``, and fifteen-odd sites branched on ``is None`` to mean "the
daemon gave no answer this tick" — degrade a poll, defer a probe, 503 a
route. The connection router changed the object's shape: it is always
present (it must route host projects with or without a daemon), so a
bare ``is None`` would silently stop meaning anything and every
daemon-down branch would die.

This predicate is the migration target for those sites. It is
duck-typed on a marker method rather than an ``isinstance`` — the same
convention the operation journal's probe catalog uses — because the
sites live in ``vaibify/config`` and ``vaibify/gui`` both, and a type
import here would invert the package layering. A plain
``DockerConnection`` (or a test double, or the browser lane's fake)
carries no marker and reads as reachable by existing; a router answers
for its Docker leg.
"""

__all__ = ["fbDockerReachable"]


def fbDockerReachable(connectionDocker):
    """Return True when a Docker-lane call could reach a daemon now.

    ``None`` is the historical "no daemon" value and stays False. An
    object exposing ``fbDockerLegPresent`` (the connection router) is
    asked; anything else — a real ``DockerConnection``, the browser
    lane's fail-closed fake, a route test's double — is reachable by
    construction, exactly as the old ``is not None`` read.
    """
    if connectionDocker is None:
        return False
    fnProbeLeg = getattr(connectionDocker, "fbDockerLegPresent", None)
    if fnProbeLeg is None:
        return True
    return fnProbeLeg()
