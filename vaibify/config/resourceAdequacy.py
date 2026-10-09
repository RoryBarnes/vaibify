"""Whether a project's memory limit leaves room for the AI agents it runs.

An AI agent in a container, with the subagents and background jobs it
starts, routinely needs several GB, and a container that reaches its
memory limit has a process killed by the kernel -- often the agent
itself. A project that enables an agent overlay and caps memory below
``F_AGENT_MEMORY_ADVISORY_GIGABYTES`` is ADVISED of that, never refused:
5 GB is a starting point, not a requirement, and the researcher may know
their workload needs less.

One authority, four surfaces, on the terms of ``secretAvailability``:
``vaibify doctor``, the ``vaibify start`` pre-flight, the dashboard's
readiness answer, and the settings-save response all call
:func:`flistDescribeResourceAdvisories`, so none of them can hold a
threshold or a sentence of its own. The memory figure is the one
``docker run`` is given (``resourceLimits.fiResolveMemoryBytes``), and an
unlimited container is never advised.
"""

from vaibify.config.resourceLimits import (
    I_BYTES_PER_GIGABYTE,
    fiResolveMemoryBytes,
    fsFormatBytes,
)

__all__ = [
    "F_AGENT_MEMORY_ADVISORY_GIGABYTES",
    "fbConfigEnablesAnAgent",
    "flistDescribeResourceAdvisories",
]


F_AGENT_MEMORY_ADVISORY_GIGABYTES = 5.0


def fbConfigEnablesAnAgent(config):
    """Return True when the config enables any AI agent overlay.

    The agent overlays are the image builder's own list, so an agent
    added there is covered here without a second list to forget.
    """
    from vaibify.docker.imageBuilder import (
        T_AGENT_OVERLAY_NAMES,
        fsFeatureFieldForOverlay,
    )
    featuresConfig = getattr(config, "features", None)
    return any(
        bool(getattr(featuresConfig, fsFeatureFieldForOverlay(sOverlay), False))
        for sOverlay in T_AGENT_OVERLAY_NAMES
    )


def flistDescribeResourceAdvisories(config):
    """Return the advisory sentences for this config; [] when it has none."""
    iMemoryBytes = fiResolveMemoryBytes(config)
    if iMemoryBytes is None or not fbConfigEnablesAnAgent(config):
        return []
    if iMemoryBytes >= F_AGENT_MEMORY_ADVISORY_GIGABYTES * I_BYTES_PER_GIGABYTE:
        return []
    return [
        f"This project runs an AI agent with a {fsFormatBytes(iMemoryBytes)} "
        "memory limit. Agents and the jobs they start often need several "
        f"GB; {F_AGENT_MEMORY_ADVISORY_GIGABYTES:g} GB is a starting point, "
        "not a requirement. Raise or remove the limit in Settings."
    ]
