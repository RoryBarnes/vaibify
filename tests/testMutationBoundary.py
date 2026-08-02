"""The mutation boundary refuses, and its exemption stays narrow.

Two controls, and neither is a naming convention. Python privacy and a
docstring enforce nothing; the boundary has to be a runtime refusal plus
a structural check, or it is advertisement.

**The runtime gate.** Arbitrary command execution is ALWAYS treated as
mutating, because the primitive cannot know whether the text it was
handed reads a file or deletes a workspace. Inside an enforced lane, an
exec without a live carrier admission raises. Previously only the
durable-task exec and the tar write consulted the gate, so every other
primitive was reachable directly from a route.

**The audited-read exemption.** A typed read is implemented with an
exec -- fetching a file means running a program in the container -- so
guarding the exec primitive would refuse reads too, and the reflex fix
would be to stop guarding it. The carve-out is therefore narrow by
construction: exactly one private method grants it, and every command
that travels through it must have been BUILT by its adapter from a path
or an identifier. A caller's string may never reach it, or the
exemption becomes the hole.
"""

import ast
import inspect
import pathlib

import pytest

from vaibify.config import mutationAdmission
from vaibify.docker.dockerConnection import DockerConnection


PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent

# The single method that grants the audited-read exemption.
S_EXEMPTION_METHOD = "_texecRunAuditedRead"


class _StubContainer:
    """A container stand-in that records whether it was ever exec'd."""

    def __init__(self):
        self.listExecuted = []
        self.id = "cid-1"

    def exec_run(self, **dictKwargs):
        self.listExecuted.append(dictKwargs)
        return (0, (b"", b""))


def _fconnectionWithStubContainer(stubContainer):
    """Return a DockerConnection whose container lookup is the stub."""
    connection = DockerConnection.__new__(DockerConnection)
    connection.fcontainerGetById = lambda sContainerId: stubContainer
    return connection


@pytest.fixture
def fnEnforcedLane():
    """Enter and leave an enforced lane with no admission in it."""
    tokenLane = mutationAdmission.ftokenMarkEnforcedLane()
    yield
    mutationAdmission.fnResetEnforcedLane(tokenLane)


@pytest.mark.falsification
def testAnUnadmittedExecIsRefusedBeforeItRuns(fnEnforcedLane):
    """The refusal must precede the exec, not report it afterwards.

    Asserted on the STUB, not on the exception: a gate placed after the
    call would raise the same error while the command had already run,
    and the container would carry the change the refusal claims to have
    prevented.

    Kills: removing the fnAssertContainerCommandAdmitted call from
    DockerConnection.texecRunInContainerStreamed.
    """
    stubContainer = _StubContainer()
    connection = _fconnectionWithStubContainer(stubContainer)
    with pytest.raises(mutationAdmission.MutationNotAdmittedError):
        connection.texecRunInContainerStreamed("cid-1", "rm -rf /workspace")
    assert stubContainer.listExecuted == [], (
        "the command ran and THEN the gate complained"
    )


def testAnUnadmittedExecIsPermittedOutsideAnEnforcedLane():
    """The unmarked remainder is deliberate, and is stated as such.

    CLI paths, background threads, and direct library use are not
    enforced lanes. Refusing them would break the host CLI, and
    pretending they are covered would be worse than either -- so the
    boundary says plainly where it does and does not apply.
    """
    stubContainer = _StubContainer()
    connection = _fconnectionWithStubContainer(stubContainer)
    connection.texecRunInContainerStreamed("cid-1", "echo hello")
    assert len(stubContainer.listExecuted) == 1


def testAnAuditedReadIsExemptButOnlyInsideItsAdapter(fnEnforcedLane):
    """The exemption covers the adapter's exec and nothing after it."""
    stubContainer = _StubContainer()
    connection = _fconnectionWithStubContainer(stubContainer)

    connection._texecRunAuditedRead("cid-1", "python3 -c 'print(1)'")
    assert len(stubContainer.listExecuted) == 1

    with pytest.raises(mutationAdmission.MutationNotAdmittedError):
        connection.texecRunInContainerStreamed("cid-1", "rm -rf /workspace")
    assert len(stubContainer.listExecuted) == 1, (
        "the audited-read exemption leaked past the adapter's own exec"
    )


def testTheExemptionIsGrantedInExactlyOnePlace():
    """One grant point, so the exempt set is one file's worth of reading.

    ``ftokenEnterAuditedRead`` scattered across adapters would make the
    exempt set a search rather than a list, and a search is what nobody
    repeats before shipping.
    """
    sSource = (
        PATH_REPOSITORY / "vaibify" / "docker" / "dockerConnection.py"
    ).read_text()
    assert sSource.count("ftokenEnterAuditedRead(") == 1, (
        "the audited-read exemption must be granted in exactly one "
        "method; scattering it makes the exempt set unreadable"
    )
    listGranting = [
        pathModule.name
        for pathModule in (PATH_REPOSITORY / "vaibify").rglob("*.py")
        if "__pycache__" not in pathModule.parts
        and "ftokenEnterAuditedRead" in pathModule.read_text()
        and pathModule.name not in ("mutationAdmission.py",)
    ]
    assert listGranting == ["dockerConnection.py"], (
        f"only the Docker gateway may grant the read exemption; "
        f"granted in: {listGranting}"
    )


def testEveryAuditedReadBuildsItsOwnCommand():
    """A caller's string may never travel through the exemption.

    This is the audit. An adapter that forwarded a command it was
    handed would turn the read carve-out into a general bypass of the
    mutation gate: any caller could ask for a "read" that deletes. Each
    caller of the exemption must construct its command from a path or
    an identifier, which shows up as the command argument being built
    in the adapter rather than being one of its parameters.
    """
    sSource = (
        PATH_REPOSITORY / "vaibify" / "docker" / "dockerConnection.py"
    ).read_text()
    treeAst = ast.parse(sSource)
    listViolations = []
    for nodeFunction in ast.walk(treeAst):
        if not isinstance(
            nodeFunction, (ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            continue
        setParameters = {
            nodeArgument.arg
            for nodeArgument in nodeFunction.args.args
        }
        for nodeCall in ast.walk(nodeFunction):
            if not isinstance(nodeCall, ast.Call):
                continue
            if getattr(nodeCall.func, "attr", "") != S_EXEMPTION_METHOD:
                continue
            if len(nodeCall.args) < 2:
                continue
            nodeCommand = nodeCall.args[1]
            if isinstance(nodeCommand, ast.Name) and (
                nodeCommand.id in setParameters
            ):
                listViolations.append(
                    f"{nodeFunction.name} forwards its own parameter "
                    f"{nodeCommand.id!r} through the read exemption"
                )
    assert listViolations == [], (
        f"the audited-read exemption must never carry a caller's "
        f"command: {listViolations}"
    )


def testTheGatewayIsTheOnlyModuleThatCallsExecRun():
    """No module outside the gateway reaches docker-py's exec directly.

    The gate lives in the gateway's primitives, so a module that called
    ``container.exec_run`` itself would be past every check by
    construction. The existing invariant suite forbids the SDK import
    outside the gateway; this is the narrower statement that the escape
    hatch inside it has not been re-opened elsewhere.
    """
    listOffenders = []
    for pathModule in (PATH_REPOSITORY / "vaibify").rglob("*.py"):
        if "__pycache__" in pathModule.parts:
            continue
        if pathModule.name == "dockerConnection.py":
            continue
        if "exec_run(" in pathModule.read_text():
            listOffenders.append(
                str(pathModule.relative_to(PATH_REPOSITORY))
            )
    assert listOffenders == [], (
        f"these modules call docker-py's exec_run directly, bypassing "
        f"the mutation gate entirely: {listOffenders}"
    )


def testTheCommandGateCoversTheDelegatingPrimitives():
    """``ftResultExecuteCommand`` is covered because it delegates.

    Recorded as a fact rather than left to be re-derived: the
    deprecated wrapper is guarded by the base primitive it calls, so
    the gate does not need its own copy -- and a future change that
    made it call docker-py directly would lose the gate silently. The
    same holds for ``fnWriteFile`` over ``fnWriteFileViaTar``.
    """
    for sWrapper, sBase in (
        ("ftResultExecuteCommand", "texecRunInContainerStreamed"),
        ("fnWriteFile", "fnWriteFileViaTar"),
    ):
        sSource = inspect.getsource(getattr(DockerConnection, sWrapper))
        assert f"self.{sBase}(" in sSource, (
            f"{sWrapper} no longer delegates to {sBase}, so it is "
            f"outside the gate that covers it"
        )
