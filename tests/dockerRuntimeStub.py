"""Pin which Docker runtime a test is talking about.

Every runtime-dependent remediation vaibify prints -- what starts a
stopped daemon, what clears a stale Colima lock, what a denied socket
needs -- is decided from
``dockerContext.fdictClassifyDockerRuntime``. A test that does not pin
that answer is asserting against whatever runtime the machine running
it happens to have, which is the hazard
``fnStubTheDockerBinaryStatusProbes`` exists to keep out of this
suite: green on a developer's laptop with Colima, red on a CI runner
without it.

It bit in an inverted form too. The classifier reads the ACTIVE
DOCKER CONTEXT, and the suite's own state-isolation fixture redirects
``HOME`` -- so ``docker context show`` reads an empty Docker config
and answers ``default``. Tests that had quietly relied on the
developer's Colima context passed alone and failed in the full run,
which reads like flakiness and is not: it is the isolation fixture
doing exactly its job.
"""

from unittest.mock import patch

from vaibify.docker import dockerContext


def fnPinDockerRuntime(sRuntime, sColimaProfile="", sEndpoint=""):
    """Return a patch context fixing the classifier's whole answer."""
    return patch.object(
        dockerContext, "fdictClassifyDockerRuntime",
        return_value={
            "sRuntime": sRuntime,
            "sContextName": "",
            "sColimaProfile": sColimaProfile,
            "sEndpoint": sEndpoint,
            "bDaemonAnswered": True,
        },
    )
