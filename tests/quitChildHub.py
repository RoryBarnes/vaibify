"""A real child hub process for the Quit tests.

Run as a script by ``tests/testHubQuitChildren.py``. It is a separate
process with its own admission gate, holding a real hub slot for its
port and serving the real host control socket through
``hostControlChannel`` -- so a parent's Quit reaches it exactly as it
reaches a spawned hub, over a peer-authenticated Unix socket. Only the
FastAPI layer is left out: Quit never speaks HTTP to a child.

Options seed the work a parent must see: ``--live-run`` registers a
durable task that never finishes, and ``--in-flight-seconds S`` a
lock-held supervisor that finishes after S seconds.
"""

import argparse
import asyncio
import sys
from types import SimpleNamespace


def _fargumentsParseCommandLine():
    parserArguments = argparse.ArgumentParser()
    parserArguments.add_argument("--port", type=int, required=True)
    parserArguments.add_argument("--session-dir", required=True)
    parserArguments.add_argument("--control-dir", required=True)
    parserArguments.add_argument("--live-run", action="store_true")
    parserArguments.add_argument("--in-flight-seconds", type=float, default=0)
    return parserArguments.parse_args()


def _fappBuildChildState(iPort):
    return SimpleNamespace(state=SimpleNamespace(
        iHubPort=iPort, listLifespanStartup=[], listLifespanShutdown=[],
        dictContainerOwners={}, dictMutationSupervisors={},
        dictDurableTaskRecords={}, listSpawnedChildren=[],
        dictTerminalExecutionRecords={},
    ))


def _fnSeedWork(app, argumentsParsed):
    from vaibify.gui import commitCarrier
    loopRunning = asyncio.get_running_loop()
    if argumentsParsed.live_run:
        app.state.dictDurableTaskRecords["childproj"] = (
            commitCarrier.DurableTaskRecord(
                sTaskId="child-task", sName="childproj", sContainerId="cid",
                iOwnerGeneration=1, taskAsync=loopRunning.create_future(),
                admission=None, sOperation="a pipeline run",
            )
        )
    if argumentsParsed.in_flight_seconds > 0:
        app.state.dictMutationSupervisors["child-sup"] = (
            commitCarrier.MutationSupervisor(
                sSupervisorId="child-sup", sName="childproj",
                sContainerId="cid", dictLaneTuple={},
                sOperationKind="helper", sTarget="slowWrite",
                taskSupervisor=asyncio.ensure_future(
                    asyncio.sleep(argumentsParsed.in_flight_seconds)),
            )
        )


async def _fnServeForever(argumentsParsed):
    from vaibify.gui import hostControlChannel
    app = _fappBuildChildState(argumentsParsed.port)
    _fnSeedWork(app, argumentsParsed)
    hostControlChannel.fnRegisterHostControlChannel(app, {})
    for fnStartup in app.state.listLifespanStartup:
        await fnStartup(app)
    sys.stdout.write("ready\n")
    sys.stdout.flush()
    await asyncio.Event().wait()


def main():
    argumentsParsed = _fargumentsParseCommandLine()
    from vaibify.config import sessionRegistry
    from vaibify.gui import hostControlChannel
    sessionRegistry._S_SESSION_DIRECTORY = argumentsParsed.session_dir
    hostControlChannel._S_CONTROL_DIRECTORY = argumentsParsed.control_dir
    fileSlot = sessionRegistry.ffileAcquireSessionSlot(
        "hub", argumentsParsed.port)
    try:
        asyncio.run(_fnServeForever(argumentsParsed))
    finally:
        sessionRegistry.fnReleaseSessionSlot(fileSlot)


if __name__ == "__main__":
    main()
