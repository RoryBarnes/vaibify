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
would be to stop guarding it. The carve-out is therefore narrow BY
SIGNATURE: exactly one private method grants it, and it does not accept
a command. It takes the NAME of a declared operation and a path, and
builds the program from fixed module source text with the path embedded
as a Python literal.

That design replaced a source-shape audit which tried to prove no
caller-derived value reached an arbitrary-command parameter. Two
independent reviews defeated it in turn -- first with a keyword
argument, an alias, an f-string, a concatenation and a helper's return
value, then with two levels of assignment -- and the second round is
the lesson: enumerating bad shapes cannot win against a parameter that
accepts arbitrary text, because the next spelling is always one nobody
listed. Removing the parameter ends the argument. The bypass cases
those reviews contributed are gone from this file for the same reason:
none of them can be written any more.
"""

import ast
import inspect
import pathlib

import pytest

from vaibify.config import mutationAdmission
from vaibify.docker import dockerConnection as dockerConnectionModule
from vaibify.docker.dockerConnection import DockerConnection


PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent

# The single method that grants the audited-read exemption.
S_EXEMPTION_METHOD = "_ftRunTypedRead"


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
    DockerConnection.ftRunInContainerStreamed.
    """
    stubContainer = _StubContainer()
    connection = _fconnectionWithStubContainer(stubContainer)
    with pytest.raises(mutationAdmission.MutationNotAdmittedError):
        connection.ftRunInContainerStreamed("cid-1", "rm -rf /workspace")
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
    connection.ftRunInContainerStreamed("cid-1", "echo hello")
    assert len(stubContainer.listExecuted) == 1


def testAnAuditedReadIsExemptButOnlyInsideItsAdapter(fnEnforcedLane):
    """The exemption covers the adapter's exec and nothing after it."""
    stubContainer = _StubContainer()
    connection = _fconnectionWithStubContainer(stubContainer)

    connection._ftRunTypedRead(
        "cid-1", dockerConnectionModule.S_TYPED_READ_DIRECTORY, "/tmp",
    )
    assert len(stubContainer.listExecuted) == 1

    with pytest.raises(mutationAdmission.MutationNotAdmittedError):
        connection.ftRunInContainerStreamed("cid-1", "rm -rf /workspace")
    assert len(stubContainer.listExecuted) == 1, (
        "the audited-read exemption leaked past the adapter's own exec"
    )


@pytest.mark.falsification
def testAnExistenceProbeSurvivesAnEnforcedLane(fnEnforcedLane):
    """A repo-files existence check is a read and must be admitted as one.

    ``ContainerRepoFiles.fbIsFile`` assembled ``test -f <quoted path>``
    and ran it through the general exec primitive. The gate cannot tell
    that from ``rm -rf`` -- command text carries no such distinction --
    so under an enforced lane every existence check was REFUSED. The
    damage was not a visible error: the level gates that call it catch
    ``OSError``, and ``MutationNotAdmittedError`` is one, so a refused
    probe was read as "this file is not there" and the workflow's
    reproducibility badge silently dropped.

    Driven through ``ContainerRepoFiles`` rather than through the
    connection method directly, because the adapter is what had to
    change and a test of the gateway alone would pass with the adapter
    still shelling out.

    Kills: reverting ``ContainerRepoFiles.fbIsFile`` to
    ``self._ftExec("test -f " + fsShellQuotePosix(...))``.
    """
    from vaibify.reproducibility.repoFiles import ContainerRepoFiles

    stubContainer = _StubContainer()
    connection = _fconnectionWithStubContainer(stubContainer)
    filesRepo = ContainerRepoFiles(connection, "cid-1", "/workspace/repo")

    assert filesRepo.fbIsFile("MANIFEST.sha256") is False
    assert filesRepo.fbIsDir(".vaibify") is False
    assert len(stubContainer.listExecuted) == 2, (
        "the probes did not reach the container at all, so this proves "
        "nothing about how they were admitted"
    )

    with pytest.raises(mutationAdmission.MutationNotAdmittedError):
        connection.ftRunInContainerStreamed("cid-1", "rm -rf /workspace")
    assert len(stubContainer.listExecuted) == 2, (
        "the exemption leaked past the probes into general execution"
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


def testTheExemptionCannotCarryACommandAtAll():
    """The audit is now a property of the SIGNATURE, not of a scan.

    The previous audit read every call site and tried to prove no
    caller-derived value reached the exemption. It was defeated by two
    levels of assignment, and would have been defeated by the next
    spelling nobody thought of, because enumerating bad shapes is a
    losing game against a parameter that accepts arbitrary text.

    So the parameter is gone. The exemption takes an operation NAME and
    a PATH — or, since the batched existence probe, a flat sequence of
    paths; the command is fixed module source text with that value
    embedded as a Python literal. There is no shape of caller input
    that can become a command, which is a guarantee a reader can check
    by looking at one signature.

    The widening to a collection is asserted here rather than merely
    allowed, in both directions. The path parameter may be named for
    one path or for several, and NOTHING else; and it must still be
    impossible for it to be command text. A test that only checked for
    a three-parameter signature would have let ``sCommand`` back in
    under a different arity.
    """
    import inspect as inspectModule

    tSignature = inspectModule.signature(
        getattr(DockerConnection, S_EXEMPTION_METHOD),
    )
    listParameters = [
        sName for sName in tSignature.parameters if sName != "self"
    ]
    assert listParameters[:2] == ["sContainerId", "sOperation"], (
        f"the exemption must take a container and an operation NAME "
        f"before anything else; it takes {listParameters}"
    )
    assert listParameters[2:] in (["sPath"], ["objPaths"]), (
        f"the exemption's third parameter must be the path, or the "
        f"paths, and nothing else; it takes {listParameters}"
    )
    for sName in listParameters:
        assert "command" not in sName.lower(), (
            f"{sName} reintroduces caller-supplied command text"
        )


def testTheExemptionRefusesAnythingButPathStrings():
    """The widened parameter admits paths, and only paths.

    The batched probe embeds a LIST in the program's literal slot, and
    the slot is filled with ``repr``. ``repr`` of an arbitrary object is
    whatever that object's class chooses to print, so an object with a
    hostile ``__repr__`` -- or a nested list, or bytes -- would write
    its own text into the program. Only ``str`` has a ``repr`` that is a
    quoted string literal and nothing else.

    This is the half of the widening that a signature check cannot see,
    and it is strictly tighter than the single-path form it generalized:
    that one called ``repr`` on whatever it was handed.

    Kills: replacing ``_fsTypedReadPathLiteral``'s body with a bare
    ``return repr(objPaths)``.
    """
    from vaibify.docker.dockerConnection import _fsTypedReadPathLiteral

    assert _fsTypedReadPathLiteral("/workspace/a b.txt") == (
        repr("/workspace/a b.txt")
    )
    assert _fsTypedReadPathLiteral(["/a", "/b"]) == repr(["/a", "/b"])

    class _HostileRepr:
        def __repr__(self):
            return "__import__('os').system('touch /tmp/pwned')"

    for objRejected in (
        _HostileRepr(),
        [_HostileRepr()],
        [["/nested"]],
        b"/bytes",
        ["/ok", 7],
        None,
    ):
        with pytest.raises(TypeError):
            _fsTypedReadPathLiteral(objRejected)


def testEveryTypedReadNamesADeclaredOperation():
    """Every call site picks from the table; none invents a program.

    A constant operation name is what makes the exemption enumerable:
    the set of programs it can run is the table, and the set of callers
    is the ones naming a key in it. A computed name would put the
    program back in the caller's hands.
    """
    sSource = (
        PATH_REPOSITORY / "vaibify" / "docker" / "dockerConnection.py"
    ).read_text()
    treeAst = ast.parse(sSource)
    listViolations = []
    for nodeCall in ast.walk(treeAst):
        if not isinstance(nodeCall, ast.Call):
            continue
        if getattr(nodeCall.func, "attr", "") != S_EXEMPTION_METHOD:
            continue
        if len(nodeCall.args) < 3:
            listViolations.append("a call site omits the operation name")
            continue
        nodeOperation = nodeCall.args[1]
        if isinstance(nodeOperation, ast.Constant):
            sOperation = nodeOperation.value
        elif isinstance(nodeOperation, ast.Name):
            sOperation = _fsResolveModuleConstant(treeAst, nodeOperation.id)
        else:
            sOperation = None
        if sOperation not in dockerConnectionModule._DICT_TYPED_READ_PROGRAMS:
            listViolations.append(
                f"a call site passes the non-declared operation "
                f"{ast.dump(nodeOperation)[:60]}"
            )
    assert listViolations == [], listViolations


def _fsResolveModuleConstant(treeModule, sName):
    """Return a module-level string constant's value, or None."""
    for nodeAssign in ast.walk(treeModule):
        if not isinstance(nodeAssign, ast.Assign):
            continue
        if not isinstance(nodeAssign.value, ast.Constant):
            continue
        for nodeTarget in nodeAssign.targets:
            if isinstance(nodeTarget, ast.Name) and nodeTarget.id == sName:
                return nodeAssign.value.value
    return None


def testAnUndeclaredOperationRefusesInsteadOfRunning():
    """An unknown operation name raises; it does not fall through."""
    stubContainer = _StubContainer()
    connection = _fconnectionWithStubContainer(stubContainer)
    with pytest.raises(ValueError):
        connection._ftRunTypedRead("cid-1", "rmDashRf", "/workspace")
    assert stubContainer.listExecuted == []


def testTheTypedReadProgramsSubstituteOnlyARepresentedPath():
    """A path becomes a Python literal, never shell or program syntax.

    The one substitution into a fixed program. Driven with a path that
    is hostile in both languages: if it were concatenated raw, the
    program would break out; as a repr it is inert data.
    """
    sHostile = "/tmp/a'; import os; os.system('touch /tmp/pwned') #"
    for sOperation in dockerConnectionModule._DICT_TYPED_READ_PROGRAMS:
        sProgram = dockerConnectionModule._DICT_TYPED_READ_PROGRAMS[
            sOperation
        ].replace(
            dockerConnectionModule._S_TYPED_READ_PATH_SLOT,
            repr(sHostile),
        )
        # The whole hostile string survives inside ONE literal, and the
        # program parses to the statements the table declares -- no
        # os.system call appears among them.
        treeProgram = ast.parse(sProgram)
        listCalls = [
            node.func.attr for node in ast.walk(treeProgram)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        ]
        assert "system" not in listCalls, (
            f"{sOperation} let a hostile path become program syntax"
        )
        assert sHostile in [
            node.value for node in ast.walk(treeProgram)
            if isinstance(node, ast.Constant)
        ], f"{sOperation} did not carry the path as one literal"


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
        ("ftResultExecuteCommand", "ftRunInContainerStreamed"),
        ("fnWriteFile", "fnWriteFileViaTar"),
    ):
        sSource = inspect.getsource(getattr(DockerConnection, sWrapper))
        assert f"self.{sBase}(" in sSource, (
            f"{sWrapper} no longer delegates to {sBase}, so it is "
            f"outside the gate that covers it"
        )
