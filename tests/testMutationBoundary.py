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
    """No caller-derived value may travel through the exemption.

    This is the audit, and the first version of it was weaker than this
    docstring: it caught only a positional argument that was directly a
    parameter NAME, so a keyword argument, a local alias, an f-string,
    a concatenation, or a helper's return value all slipped through. A
    check that is narrower than the guarantee it states is the shape
    this repository treats as the serious failure -- prose promising
    what nothing enforces.

    So the rule is inverted, from "reject the shapes I thought of" to
    "accept only the shape I can verify": the command argument must be
    a local name bound, in the same function, to an expression that
    contains no parameter of that function. Anything else -- including
    a spelling nobody has thought of yet -- fails and must be justified
    by making the construction explicit.
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
        listViolations.extend(
            _flistExemptionViolations(nodeFunction),
        )
    assert listViolations == [], (
        f"the audited-read exemption must carry only an adapter-built "
        f"command: {listViolations}"
    )


def _flistExemptionViolations(nodeFunction):
    """Return the ways one function misuses the read exemption."""
    setParameters = {
        nodeArgument.arg for nodeArgument in
        nodeFunction.args.args + nodeFunction.args.kwonlyargs
    }
    dictLocalBindings = _fdictLocalStringBindings(nodeFunction)
    listViolations = []
    for nodeCall in ast.walk(nodeFunction):
        if not isinstance(nodeCall, ast.Call):
            continue
        if getattr(nodeCall.func, "attr", "") != S_EXEMPTION_METHOD:
            continue
        nodeCommand = _fnodeCommandArgument(nodeCall)
        if nodeCommand is None:
            listViolations.append(
                f"{nodeFunction.name}: no command argument found"
            )
            continue
        listViolations.extend(_flistCommandViolations(
            nodeFunction.name, nodeCommand, setParameters,
            dictLocalBindings,
        ))
    return listViolations


def _fnodeCommandArgument(nodeCall):
    """Return the command expression, positional or keyword."""
    for nodeKeyword in nodeCall.keywords:
        if nodeKeyword.arg == "sCommand":
            return nodeKeyword.value
    if len(nodeCall.args) >= 2:
        return nodeCall.args[1]
    return None


def _fdictLocalStringBindings(nodeFunction):
    """Map local names to the expressions assigned to them."""
    dictBindings = {}
    for nodeAssign in ast.walk(nodeFunction):
        if not isinstance(nodeAssign, ast.Assign):
            continue
        for nodeTarget in nodeAssign.targets:
            if isinstance(nodeTarget, ast.Name):
                dictBindings.setdefault(nodeTarget.id, []).append(
                    nodeAssign.value,
                )
    return dictBindings


def _flistCommandViolations(
    sFunctionName, nodeCommand, setParameters, dictLocalBindings,
):
    """Return why one command expression is not adapter-built."""
    if isinstance(nodeCommand, ast.Constant):
        return []
    if not isinstance(nodeCommand, ast.Name):
        # A call, an f-string, a concatenation: accepted only when it
        # names no parameter anywhere inside it.
        listNamed = _flistParameterNamesWithin(nodeCommand, setParameters)
        return [
            f"{sFunctionName}: the command expression carries the "
            f"caller's {sorted(listNamed)}"
        ] if listNamed else []
    listBindings = dictLocalBindings.get(nodeCommand.id)
    if not listBindings:
        return [
            f"{sFunctionName}: the command {nodeCommand.id!r} is not "
            f"built in this function"
        ]
    listNamed = set()
    for nodeBinding in listBindings:
        listNamed |= _flistParameterNamesWithin(nodeBinding, setParameters)
    return [
        f"{sFunctionName}: the command {nodeCommand.id!r} is built from "
        f"the caller's {sorted(listNamed)}"
    ] if listNamed else []


def _flistParameterNamesWithin(nodeExpression, setParameters):
    """Return the function parameters an expression reads."""
    return {
        node.id for node in ast.walk(nodeExpression)
        if isinstance(node, ast.Name) and node.id in setParameters
    }


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


# The bypass shapes an external review named as slipping past the first
# version of the audit. Kept as cases rather than prose: a check that
# claims to catch a class must be shown catching it.
_LIST_EXEMPTION_BYPASS_SHAPES = [
    ("a keyword argument", '''
def fbaLeak(self, sContainerId, sCallerCommand):
    self._texecRunAuditedRead(sContainerId, sCommand=sCallerCommand)
'''),
    ("a local alias", '''
def fbaLeak(self, sContainerId, sCallerCommand):
    sAlias = sCallerCommand
    self._texecRunAuditedRead(sContainerId, sAlias)
'''),
    ("an f-string", '''
def fbaLeak(self, sContainerId, sCallerCommand):
    sBuilt = f"cat {sCallerCommand}"
    self._texecRunAuditedRead(sContainerId, sBuilt)
'''),
    ("a concatenation", '''
def fbaLeak(self, sContainerId, sCallerCommand):
    self._texecRunAuditedRead(sContainerId, "cat " + sCallerCommand)
'''),
    ("a helper's return value", '''
def fbaLeak(self, sContainerId, sCallerCommand):
    sBuilt = fsBuildIt(sCallerCommand)
    self._texecRunAuditedRead(sContainerId, sBuilt)
'''),
]


@pytest.mark.parametrize(
    "sShapeName,sSource", _LIST_EXEMPTION_BYPASS_SHAPES,
    ids=[sName for sName, _ in _LIST_EXEMPTION_BYPASS_SHAPES],
)
def testTheAuditCatchesEveryKnownBypassShape(sShapeName, sSource):
    """Each way a caller's command could reach the exemption is refused."""
    import textwrap

    nodeFunction = ast.parse(textwrap.dedent(sSource)).body[0]
    listViolations = _flistExemptionViolations(nodeFunction)
    assert listViolations, (
        f"{sShapeName} carried the caller's command through the "
        f"audited-read exemption unnoticed"
    )


def testTheAuditStillAcceptsAnAdapterBuiltCommand():
    """The negative control: a real adapter's shape must pass.

    Without this, a check that rejected everything would satisfy the
    five cases above and quietly forbid the reads the exemption exists
    to permit.
    """
    import textwrap

    nodeFunction = ast.parse(textwrap.dedent('''
def fbaSafe(self, sContainerId, sFilePath):
    sProgram = "import os,sys; sys.stdout.write(" + repr("x") + ")"
    sCommand = "python3 -c " + sProgram
    self._texecRunAuditedRead(sContainerId, sCommand)
''')).body[0]
    assert _flistExemptionViolations(nodeFunction) == []
