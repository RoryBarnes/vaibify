"""The process that holds the Docker setup lock while a step runs.

Started by ``runtimeSetupRunner`` in its own session, with the setup
lock's descriptor inherited; it runs the step's invocations, relays
what they print, and ends with one result line. Because it waits for
every command it starts, and holds the lock until it exits, the lock
lasts as long as the step does: a hub killed mid-step leaves this
process, and so the lock, in place until the step ends. A hub that is
gone cannot read the relay, so a write to it that fails is dropped and
the step carries on.

Run as ``python -m vaibify.install.runtimeSetupSupervisor CHANNEL JSON``,
where JSON is the invocation list the plan built. Not run by hand.
"""

import json
import os
import sys

from vaibify.install.runtimeSetupRunner import (
    S_SUPERVISOR_RESULT_PREFIX,
    RuntimeSetupError,
    fnRunInvocations,
)


__all__ = ["main"]


def _fnRelayLine(sLine):
    """Print one line for the hub; once nobody is reading, print nowhere."""
    try:
        print(sLine, flush=True)
    except (BrokenPipeError, OSError):
        iDevNull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(iDevNull, sys.stdout.fileno())


def main():
    """Run the invocations in ``sys.argv`` and report how they ended."""
    sPrivilegeChannel, sInvocations = sys.argv[1], sys.argv[2]
    try:
        fnRunInvocations(json.loads(sInvocations), sPrivilegeChannel,
                         _fnRelayLine, False)
        dictResult = {"bSucceeded": True, "sMessage": ""}
    except RuntimeSetupError as error:
        dictResult = {"bSucceeded": False, "sMessage": str(error)}
    _fnRelayLine(S_SUPERVISOR_RESULT_PREFIX + json.dumps(dictResult))


if __name__ == "__main__":
    main()
