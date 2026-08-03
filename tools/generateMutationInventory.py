#!/usr/bin/env python3
"""Enumerate every container-mutation call site in the shipped package.

This is the mandatory checkpoint before any migration onto a single
mutation carrier (design: the alpha-hardening wave-2 inventory). A
207-site surgery without a per-site record is how a large refactor ends
with helpers that agree with each other and miss production paths, so
the inventory comes first and the migration is judged against it.

THE DIVISION OF LABOUR IS THE POINT. The machine proves COMPLETENESS:
it walks the AST, finds every call to a declared primitive, and emits
exactly one row per call site with a stable key and a content
fingerprint. It does NOT judge semantics -- what the operation means,
who owns its lock, what metadata a busy refusal should name, whether it
outlives its request. Those are the reviewer's, and a generator that
guessed them would launder a guess into a record. Every such field ships
as ``UNCLASSIFIED`` until somebody classifies it.

The scope is deliberately wider than the mutating primitives: reads are
listed too, and marked as reads. A boundary that only enumerates what it
already believes to be dangerous cannot discover that it was wrong about
one.

Usage::

    python tools/generateMutationInventory.py            # print JSON
    python tools/generateMutationInventory.py --write    # update the file
    python tools/generateMutationInventory.py --check    # drift only

``tests/testMutationInventory.py`` runs the same scan in CI and fails on
an added, removed, duplicated, or edited call site, so the record cannot
quietly fall behind the code it describes.
"""

import argparse
import ast
import hashlib
import json
import pathlib
import sys

PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_PACKAGE = PATH_REPOSITORY / "vaibify"
PATH_INVENTORY = PATH_REPOSITORY / "tests" / "mutationInventory.json"

S_UNCLASSIFIED = "UNCLASSIFIED"

# Access kinds. The primitive alone determines this one, which is why it
# is machine-filled: what a caller can do THROUGH a primitive is a
# property of the primitive, not of the call site.
S_ACCESS_ARBITRARY_COMMAND = "arbitrary-command"
S_ACCESS_ROOT_SHELL = "arbitrary-root-shell"
S_ACCESS_ARCHIVE_WRITE = "archive-write"
S_ACCESS_EXEC_CREATE = "exec-create"
S_ACCESS_EXEC_STATE = "exec-state"
S_ACCESS_SIGNAL = "signal"
S_ACCESS_LIFECYCLE = "container-lifecycle"
S_ACCESS_TYPED_READ = "typed-read"
# A Docker CLI invocation assembled outside both gateways. It reaches
# the daemon without passing any primitive at all, so a scan that knew
# only about primitives would report a boundary it had not looked at.
S_ACCESS_DIRECT_DOCKER_CLI = "direct-docker-cli"
# A docker-py SDK call assembled outside both gateways. It reaches the
# daemon through the library rather than the CLI, so a scan that looked
# only for `subprocess.run(["docker", ...])` reported on a boundary it
# had not looked at -- `vaibify destroy` removes a volume and an image
# this way, and neither produced a row.
S_ACCESS_DIRECT_DOCKER_SDK = "direct-docker-sdk"

# HOW the primitive is reached. A bound method PASSED as a callback --
# ``asyncio.to_thread(connection.ftResultExecuteCommand, ...)`` -- is a
# call site in every sense that matters and was invisible to the first
# scanner, because the AST node it produces is an attribute load, not a
# Call. 21 mutation-capable sites were missing, including the
# clean-outputs deletion this whole boundary was justified by. An alias
# (``fnWriter = connection.fnWriteFile``) is the same shape.
S_REFERENCE_DIRECT_CALL = "direct-call"
S_REFERENCE_PASSED_CALLABLE = "passed-callable"
S_REFERENCE_DIRECT_DOCKER_CLI = "direct-docker-cli"
S_REFERENCE_DIRECT_DOCKER_SDK = "direct-docker-sdk"

# The two kinds of site the scan cannot resolve. They are declared
# separately because they are answered differently: an opaque command
# is resolved by naming it literally or routing it through a gateway, a
# untraceable SDK root by constructing the client where it is used. A
# single undifferentiated list misdescribed twelve of them.
S_BLIND_SPOT_OPAQUE_COMMAND = "opaque-subprocess-command"
S_BLIND_SPOT_UNTRACEABLE_SDK_ROOT = "untraceable-docker-sdk-root"

# Docker CLI subcommands that change something, as opposed to reporting
# it. ``info``, ``inspect``, ``ps``, and ``context`` are reads.
SET_MUTATING_DOCKER_SUBCOMMANDS = frozenset({
    "run", "start", "stop", "rm", "kill", "restart", "exec", "cp",
    "pull", "push", "build", "buildx", "create", "commit", "rename",
    "update", "pause", "unpause", "prune", "load", "import", "tag",
})

# Every primitive that reaches a container or the Docker daemon, and the
# access each one grants. Adding a primitive to the codebase without
# adding it here is caught by
# testMutationInventory::testEveryDockerPrimitiveIsInTheScopeList.
DICT_PRIMITIVE_ACCESS = {
    # --- vaibify/docker/dockerConnection.py: mutation-capable ---
    "texecRunInContainerStreamed": S_ACCESS_ARBITRARY_COMMAND,
    "texecRunInContainerStreamedWithChunks": S_ACCESS_ARBITRARY_COMMAND,
    "ftResultExecuteCommand": S_ACCESS_ARBITRARY_COMMAND,
    "fnWriteFile": S_ACCESS_ARCHIVE_WRITE,
    "fnWriteFileViaTar": S_ACCESS_ARCHIVE_WRITE,
    "fsExecCreate": S_ACCESS_EXEC_CREATE,
    "fsocketExecStart": S_ACCESS_EXEC_CREATE,
    "fnExecResize": S_ACCESS_EXEC_STATE,
    "ftupleRunRootShellProbe": S_ACCESS_ROOT_SHELL,
    "fdictProbeProcessGroupMembers": S_ACCESS_ARBITRARY_COMMAND,
    "fnSignalProcessGroupMembers": S_ACCESS_SIGNAL,
    # --- vaibify/docker/dockerConnection.py: read / cache ---
    "flistGetRunningContainers": S_ACCESS_TYPED_READ,
    "fcontainerGetById": S_ACCESS_TYPED_READ,
    "fbaFetchFile": S_ACCESS_TYPED_READ,
    "fnIterStreamFile": S_ACCESS_TYPED_READ,
    "fdictInspectExec": S_ACCESS_TYPED_READ,
    "fnEvictAbsentContainers": S_ACCESS_TYPED_READ,
    # The audited adapter behind `vaibify ls`: the caller supplies a
    # PATH, never a command, so it is a typed read even though it uses
    # the raw executor internally.
    "flistDirectoryEntries": S_ACCESS_TYPED_READ,
    # --- vaibify/docker/containerManager.py: lifecycle ---
    "fnStartContainer": S_ACCESS_LIFECYCLE,
    "fsStartContainerDetached": S_ACCESS_LIFECYCLE,
    "fsCreateContainerForReservation": S_ACCESS_LIFECYCLE,
    "fnStartCreatedContainer": S_ACCESS_LIFECYCLE,
    "fnStopContainer": S_ACCESS_LIFECYCLE,
    "fnRemoveStopped": S_ACCESS_LIFECYCLE,
    "fdictSettleReservationContainers": S_ACCESS_LIFECYCLE,
    "fdictTerminateDockerProcess": S_ACCESS_SIGNAL,
    "fbStopContainerProvenSettled": S_ACCESS_LIFECYCLE,
    # --- vaibify/docker/containerManager.py: probes ---
    "fbContainerIsRunning": S_ACCESS_TYPED_READ,
    "fdictGetContainerStatus": S_ACCESS_TYPED_READ,
    "fdictProbeContainerPresence": S_ACCESS_TYPED_READ,
    "fdictFindContainersForReservation": S_ACCESS_TYPED_READ,
    "fbContainerIsNetworkIsolated": S_ACCESS_TYPED_READ,
}

# The docker-py collections a call chain must pass through to count,
# and the leaf methods on them that CHANGE something. `list`, `get`,
# and `inspect` are reads and are recorded as such.
SET_DOCKER_SDK_COLLECTIONS = frozenset({
    "containers", "images", "volumes", "networks", "api",
})
SET_MUTATING_SDK_METHODS = frozenset({
    "remove", "prune", "create", "run", "start", "stop", "kill",
    "restart", "pause", "unpause", "pull", "push", "build", "commit",
    "tag", "put_archive", "exec_create", "exec_start", "exec_resize",
    "rename", "update",
})

SET_MUTATION_CAPABLE_ACCESS = frozenset({
    S_ACCESS_ARBITRARY_COMMAND,
    S_ACCESS_ROOT_SHELL,
    S_ACCESS_ARCHIVE_WRITE,
    S_ACCESS_EXEC_CREATE,
    S_ACCESS_EXEC_STATE,
    S_ACCESS_SIGNAL,
    S_ACCESS_LIFECYCLE,
    S_ACCESS_DIRECT_DOCKER_SDK,
})

# The modules that DEFINE the primitives. Their own definitions and
# internal helpers are the boundary itself, not callers of it.
SET_GATEWAY_MODULES = frozenset({
    "docker/dockerConnection.py",
    "docker/containerManager.py",
})

# The reviewer's fields: everything that needs judgement rather than
# parsing. They are emitted UNCLASSIFIED so the record never implies a
# review that did not happen.
#
# Three of them are LISTS, and that is not tidiness. A shared helper is
# reachable from an HTTP route and from a durable pipeline worker, and
# forcing one value would make the reviewer pick a lie. Where one call
# site genuinely has several execution contexts, it says so; where a
# single value is wrong, the alternative is to classify at the logical
# entry point instead and record that here.
TUPLE_REVIEWER_FIELDS = (
    "sLogicalOperation",
    "listExecutionLanes",
    "listLockOwners",
    "sOperationMetadata",
    "listCarrierModes",
    "sJournalKind",
    "sLifetime",
    "sCarrierDisposition",
    "sExclusionRationale",
)

# The list-valued reviewer fields and their permitted members. Free text
# in a classification is how a record stops being comparable: "the
# route" and "route wrapper" would be two different answers to one
# question, and neither could be checked.
DICT_REVIEWER_ENUMS = {
    "listExecutionLanes": frozenset({
        "http", "websocket", "background", "startup", "shutdown",
        "host-cli", "host-control-socket",
    }),
    "listLockOwners": frozenset({
        "route-wrapper", "carrier-supervisor", "lifecycle-transaction",
        "none",
    }),
    "listCarrierModes": frozenset({
        "synchronous", "lock-held", "durable", "none",
    }),
}

DICT_REVIEWER_SCALAR_ENUMS = {
    "sJournalKind": frozenset({
        "start", "exec", "terminal", "helper", "file-write", "none",
    }),
    "sLifetime": frozenset({"request", "outlives-request"}),
    # EXCLUDED demands a rationale. A boundary whose exceptions are
    # unexplained is a boundary nobody can audit, and "it was already
    # like that" is how the exceptions grow.
    "sCarrierDisposition": frozenset({"migrate", "excluded"}),
}

# Free-text fields: they must say SOMETHING, and the empty string is not
# a classification. Replacing UNCLASSIFIED with "" would otherwise
# satisfy a ratchet that only looked for the sentinel.
TUPLE_REVIEWER_PROSE_FIELDS = (
    "sLogicalOperation",
    "sOperationMetadata",
)


class _VisitorCallSites(ast.NodeVisitor):
    """Collect every reference that can reach a container.

    Three shapes, because the daemon can be reached three ways and a
    scan that knows only the first proves nothing about the other two:

    * a direct call, ``connection.fnWriteFile(...)``;
    * a bound method PASSED somewhere -- ``asyncio.to_thread(
      connection.ftResultExecuteCommand, ...)``, or an alias assignment
      -- which produces an attribute LOAD, not a Call, and was
      therefore invisible to the first version of this scanner; and
    * a Docker CLI invocation assembled from scratch, which passes no
      primitive at all.
    """

    def __init__(self, sRelativePath):
        self.sRelativePath = sRelativePath
        self.listRows = []
        self.listUnresolvedSubprocessSites = []
        self.listUnresolvedSdkSites = []
        self._listFunctionStack = []
        self._setCalledFunctionNodes = set()
        self._dictLocalCommandLists = {}
        self._dictSdkBoundNames = set()
        self._setDockerClientNames = set()

    def fnCollect(self, treeModule):
        """Walk a parsed module, direct calls resolved first."""
        for nodeCall in ast.walk(treeModule):
            if isinstance(nodeCall, ast.Call):
                self._setCalledFunctionNodes.add(id(nodeCall.func))
        self._dictLocalCommandLists = _fdictCollectCommandLists(treeModule)
        self._setDockerClientNames = _fsetCollectDockerClientNames(
            treeModule,
        )
        self._dictSdkBoundNames = _fsetCollectSdkBoundNames(
            treeModule, self._setDockerClientNames,
        )
        self.visit(treeModule)

    def visit_FunctionDef(self, nodeFunction):
        self._listFunctionStack.append(nodeFunction.name)
        self.generic_visit(nodeFunction)
        self._listFunctionStack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, nodeCall):
        sPrimitive = _fsCalledName(nodeCall)
        if sPrimitive in DICT_PRIMITIVE_ACCESS:
            self.listRows.append(self._fdictBuildRow(
                nodeCall, sPrimitive,
                DICT_PRIMITIVE_ACCESS[sPrimitive],
                S_REFERENCE_DIRECT_CALL,
            ))
        else:
            self._fnRecordDirectDockerInvocation(nodeCall)
            self._fnRecordDirectDockerSdkCall(nodeCall)
        self.generic_visit(nodeCall)

    def visit_Attribute(self, nodeAttribute):
        self._fnRecordPassedCallable(nodeAttribute, nodeAttribute.attr)
        self.generic_visit(nodeAttribute)

    def visit_Name(self, nodeName):
        if isinstance(nodeName.ctx, ast.Load):
            self._fnRecordPassedCallable(nodeName, nodeName.id)
        self.generic_visit(nodeName)

    def _fnRecordPassedCallable(self, nodeReference, sPrimitive):
        """Record a primitive referenced without being called here."""
        if sPrimitive not in DICT_PRIMITIVE_ACCESS:
            return
        if id(nodeReference) in self._setCalledFunctionNodes:
            return
        self.listRows.append(self._fdictBuildRow(
            nodeReference, sPrimitive,
            DICT_PRIMITIVE_ACCESS[sPrimitive],
            S_REFERENCE_PASSED_CALLABLE,
        ))

    def _fnRecordDirectDockerInvocation(self, nodeCall):
        """Record a ``docker ...`` subprocess assembled outside a gateway."""
        if _fbIsSubprocessLaunch(nodeCall) and _fbArgumentIsOpaque(
            nodeCall, self._dictLocalCommandLists,
        ):
            # DECLARED, not ignored. The scan cannot see what a command
            # built somewhere else contains, and a boundary that
            # silently drops what it cannot read reports coverage it
            # does not have.
            self.listUnresolvedSubprocessSites.append(
                self._fdictBuildBlindSpot(
                    nodeCall, S_BLIND_SPOT_OPAQUE_COMMAND,
                ),
            )
            return
        sSubcommand = _fsDockerSubcommandForCall(
            nodeCall, self._dictLocalCommandLists,
        )
        if sSubcommand is None:
            return
        self.listRows.append(self._fdictBuildRow(
            nodeCall, f"docker {sSubcommand}".strip(),
            S_ACCESS_DIRECT_DOCKER_CLI,
            S_REFERENCE_DIRECT_DOCKER_CLI,
            bMutationCapable=(
                sSubcommand in SET_MUTATING_DOCKER_SUBCOMMANDS
            ),
        ))

    def _fnRecordDirectDockerSdkCall(self, nodeCall):
        """Record a docker-py call assembled outside a gateway."""
        sMethod = _fsDockerSdkMethodForCall(
            nodeCall, self._dictSdkBoundNames, self._setDockerClientNames,
        )
        if sMethod is None:
            if _fbLooksLikeAnUnrootedSdkCall(nodeCall):
                # DECLARED, not dropped. The chain passes through a
                # docker-py collection but its root is a client this
                # scan cannot trace -- typically one received as a
                # function PARAMETER, which would need interprocedural
                # analysis to resolve. Guessing from the name is what
                # this scan just stopped doing; saying nothing would
                # lose coverage to a precision fix.
                self.listUnresolvedSdkSites.append(
                    self._fdictBuildBlindSpot(
                        nodeCall, S_BLIND_SPOT_UNTRACEABLE_SDK_ROOT,
                    ),
                )
            return
        self.listRows.append(self._fdictBuildRow(
            nodeCall, f"sdk {sMethod}", S_ACCESS_DIRECT_DOCKER_SDK,
            S_REFERENCE_DIRECT_DOCKER_SDK,
            bMutationCapable=sMethod.split(".")[-1] in (
                SET_MUTATING_SDK_METHODS
            ),
        ))

    def _fdictBuildBlindSpot(self, nodeCall, sBlindSpotKind):
        """Return one declared-unreadable site, fingerprinted like a row.

        A blind spot carries the same identity a row does because it is
        subject to the same drift question. Without a fingerprint the
        record could only be counted, so a checked-in site's file,
        function, or kind could be rewritten by hand and every check
        stayed green -- and a manual disposition bound to such a site
        would survive the site changing underneath it.
        """
        return {
            "sBlindSpotKind": sBlindSpotKind,
            "sFile": self.sRelativePath,
            "sFunction": (
                self._listFunctionStack[-1]
                if self._listFunctionStack else "<module>"
            ),
            "iLine": nodeCall.lineno,
            "iOrdinal": 0,
            "sFingerprint": _fsFingerprintNode(nodeCall),
        }

    def _fdictBuildRow(
        self, nodeReference, sPrimitive, sAccess, sReferenceKind,
        bMutationCapable=None,
    ):
        """Return one row: machine-derived identity, unclassified meaning."""
        dictRow = {
            "sFile": self.sRelativePath,
            "sFunction": (
                self._listFunctionStack[-1]
                if self._listFunctionStack else "<module>"
            ),
            "sPrimitive": sPrimitive,
            "sReferenceKind": sReferenceKind,
            "iOrdinal": 0,
            "sFingerprint": _fsFingerprintNode(nodeReference),
            "sAccess": sAccess,
            "bMutationCapable": (
                sAccess in SET_MUTATION_CAPABLE_ACCESS
                if bMutationCapable is None else bMutationCapable
            ),
            "bInsideGateway": (
                self.sRelativePath in SET_GATEWAY_MODULES
            ),
        }
        for sField in TUPLE_REVIEWER_FIELDS:
            dictRow[sField] = (
                [S_UNCLASSIFIED] if sField in DICT_REVIEWER_ENUMS
                else S_UNCLASSIFIED
            )
        return dictRow


def _flistAttributeChain(nodeExpression):
    """Return the dotted name a call's function expression spells.

    Walks THROUGH intervening calls, so
    ``client.volumes.get(name).remove()`` yields
    ``client volumes get remove`` rather than stopping at ``remove``.
    A chain that stopped at the first call recorded the read and missed
    the delete -- the exact shape a review used to defeat the first
    version of this scan.
    """
    listParts = []
    nodeCurrent = nodeExpression
    while True:
        if isinstance(nodeCurrent, ast.Attribute):
            listParts.append(nodeCurrent.attr)
            nodeCurrent = nodeCurrent.value
        elif isinstance(nodeCurrent, ast.Call):
            nodeCurrent = nodeCurrent.func
        else:
            break
    if isinstance(nodeCurrent, ast.Name):
        listParts.append(nodeCurrent.id)
    elif isinstance(nodeCurrent, ast.Subscript):
        # ``dictCtx["docker"].containers.list(...)`` -- the client is
        # fetched from a mapping, so the root is the KEY. Dropping this
        # shape lost a real Docker read from the record.
        sKey = _fsConstantSubscriptKey(nodeCurrent)
        if sKey:
            listParts.append(sKey)
    return list(reversed(listParts))


def _fsConstantSubscriptKey(nodeSubscript):
    """Return a subscript's literal string key, or ''."""
    nodeSlice = nodeSubscript.slice
    # Python 3.9 wraps a simple slice in ast.Index. Unwrap ONLY that --
    # ast.Constant also has a ``.value``, so a blanket getattr returns
    # the string itself and the isinstance below then fails.
    if nodeSlice.__class__.__name__ == "Index":
        nodeSlice = nodeSlice.value
    if isinstance(nodeSlice, ast.Constant) and isinstance(
        nodeSlice.value, str,
    ):
        return nodeSlice.value
    return ""


def _fsetCollectSdkBoundNames(treeModule, setDockerClientNames):
    """Return local names bound to an object fetched from the SDK.

    ``volume = dockerClient.volumes.get(name)`` then
    ``volume.remove(force=True)``: the mutation is a method on the
    RETURNED object, so a chain-only scan sees the read and misses the
    delete. One step of propagation catches the shape this package
    actually uses -- ``vaibify destroy`` removes a volume exactly so.
    """
    setBound = set()
    for nodeAssign in ast.walk(treeModule):
        if not isinstance(nodeAssign, ast.Assign):
            continue
        if not isinstance(nodeAssign.value, ast.Call):
            continue
        listChain = _flistAttributeChain(nodeAssign.value.func)
        if not listChain or listChain[0] not in setDockerClientNames:
            continue
        if not any(
            sPart in SET_DOCKER_SDK_COLLECTIONS for sPart in listChain
        ):
            continue
        for nodeTarget in nodeAssign.targets:
            if isinstance(nodeTarget, ast.Name):
                setBound.add(nodeTarget.id)
    return setBound


def _fsDockerSdkMethodForCall(
    nodeCall, setSdkBoundNames, setDockerClientNames,
):
    """Return the docker-py method a call invokes, or None.

    A collection name alone is not evidence: ``photoLibrary.images
    .remove(image)`` reads exactly like ``dockerClient.images.remove``
    to a scan that matches on ``images``. The chain must ALSO be rooted
    in something that is a Docker client -- a name this module
    recognises, or a local bound from one -- so an unrelated library
    with a colliding collection name does not enter the record and
    dilute it.
    """
    listChain = _flistAttributeChain(nodeCall.func)
    if len(listChain) < 2:
        return None
    if not _fbChainIsRootedInADockerClient(
        listChain, setSdkBoundNames | setDockerClientNames,
    ):
        return None
    if any(sPart in SET_DOCKER_SDK_COLLECTIONS for sPart in listChain):
        return ".".join(listChain[-2:])
    if listChain[0] in setSdkBoundNames and listChain[-1] in (
        SET_MUTATING_SDK_METHODS
    ):
        return ".".join(listChain[-2:])
    return None


def _fbChainIsRootedInADockerClient(listChain, setDockerClientNames):
    """Return True when a call chain starts at a Docker client.

    ORIGIN, not spelling. An earlier version accepted any root whose
    name contained "docker" or "client", which both missed
    ``d = docker.from_env()`` and falsely claimed an unrelated
    ``client.images.remove(...)``. A record whose coverage turns on
    what somebody named a local is a record a rename can silently
    empty.
    """
    return listChain[0] in setDockerClientNames


# The constructors that hand back a Docker client. A name is a client
# because it was assigned from one of these, or from a subscript keyed
# "docker" (the route context's own slot) -- never because of how it is
# spelled.
_TUPLE_DOCKER_CLIENT_CONSTRUCTORS = (
    "from_env", "DockerClient", "APIClient",
)
_S_DOCKER_CONTEXT_KEY = "docker"


def _fsetCollectDockerClientNames(treeModule):
    """Return local names holding a Docker client, by ORIGIN.

    Two origins, both syntactic and both checkable: an assignment from
    a known constructor (``docker.from_env()``, ``DockerClient(...)``,
    ``APIClient(...)``), and the gateway's own ``self._clientDocker``.
    Anything else is not treated as a client, so an unrelated library's
    ``client`` never enters the record.
    """
    setNames = {"_clientDocker", _S_DOCKER_CONTEXT_KEY}
    for nodeAssign in ast.walk(treeModule):
        if not isinstance(nodeAssign, ast.Assign):
            continue
        if not isinstance(nodeAssign.value, ast.Call):
            continue
        sCalled = _fsCalledName(nodeAssign.value)
        if sCalled not in _TUPLE_DOCKER_CLIENT_CONSTRUCTORS:
            continue
        for nodeTarget in nodeAssign.targets:
            if isinstance(nodeTarget, ast.Name):
                setNames.add(nodeTarget.id)
    return setNames


def _fbLooksLikeAnUnrootedSdkCall(nodeCall):
    """Return True for a docker-py-shaped chain with an untraceable root."""
    listChain = _flistAttributeChain(nodeCall.func)
    if len(listChain) < 2:
        return False
    return any(
        sPart in SET_DOCKER_SDK_COLLECTIONS for sPart in listChain[:-1]
    )


def _fbIsSubprocessLaunch(nodeCall):
    """Return True for a subprocess-launching call of any spelling."""
    return _fsCalledName(nodeCall) in (
        "run", "Popen", "call", "check_call", "check_output",
    )


def _fdictCollectCommandLists(treeModule):
    """Map local names to the list literals they are assigned.

    A one-step, same-module constant propagation, which is what most of
    this package's subprocess calls actually look like:
    ``saCommand = ["docker", "rm", ...]`` then ``subprocess.run(
    saCommand, ...)``. Without it, ten real Docker invocations --
    ``rm``, ``tag``, ``exec``, ``stats`` -- were invisible purely
    because the argv had a name.

    A name assigned more than once resolves to nothing: two candidate
    values is not a resolution, and guessing one would be worse than
    declaring the site unresolved.
    """
    dictAssigned = {}
    setReassigned = set()
    for nodeAssign in ast.walk(treeModule):
        if not isinstance(nodeAssign, ast.Assign):
            continue
        if not isinstance(nodeAssign.value, (ast.List, ast.Tuple)):
            continue
        for nodeTarget in nodeAssign.targets:
            if not isinstance(nodeTarget, ast.Name):
                continue
            if nodeTarget.id in dictAssigned:
                setReassigned.add(nodeTarget.id)
            dictAssigned[nodeTarget.id] = nodeAssign.value
    return {
        sName: nodeValue for sName, nodeValue in dictAssigned.items()
        if sName not in setReassigned
    }


def _fnodeResolveArgumentVector(nodeCall, dictLocalCommandLists):
    """Return the argv sequence node a subprocess call runs, or None."""
    if not nodeCall.args:
        return None
    nodeArgument = nodeCall.args[0]
    if isinstance(nodeArgument, (ast.List, ast.Tuple)):
        return nodeArgument
    if isinstance(nodeArgument, ast.Name):
        return dictLocalCommandLists.get(nodeArgument.id)
    return None


def _fbArgumentIsOpaque(nodeCall, dictLocalCommandLists):
    """Return True when the scan cannot read what a subprocess runs."""
    if not nodeCall.args:
        return False
    nodeVector = _fnodeResolveArgumentVector(
        nodeCall, dictLocalCommandLists,
    )
    if nodeVector is None:
        return not isinstance(nodeCall.args[0], ast.Constant)
    return bool(nodeVector.elts) and not isinstance(
        nodeVector.elts[0], ast.Constant,
    )


def _fsDockerSubcommandForCall(nodeCall, dictLocalCommandLists):
    """Return the docker subcommand a subprocess call runs, or None."""
    if not _fbIsSubprocessLaunch(nodeCall):
        return None
    nodeVector = _fnodeResolveArgumentVector(
        nodeCall, dictLocalCommandLists,
    )
    if nodeVector is None:
        return None
    listLiterals = [
        nodeElement.value for nodeElement in nodeVector.elts
        if isinstance(nodeElement, ast.Constant)
    ]
    if not listLiterals or listLiterals[0] != "docker":
        return None
    return listLiterals[1] if len(listLiterals) > 1 else ""


def _fsCalledName(nodeCall):
    """Return the called name for a Name or Attribute call, else ''."""
    nodeFunction = nodeCall.func
    if isinstance(nodeFunction, ast.Name):
        return nodeFunction.id
    if isinstance(nodeFunction, ast.Attribute):
        return nodeFunction.attr
    return ""


def _fsFingerprintNode(nodeReference):
    """Return a stable digest of a reference's own source expression.

    Unparsed from the AST rather than sliced out of the file, so
    reformatting and comment changes do not move it while a change to
    the reference ITSELF -- a new argument, a different target -- does.
    A row whose fingerprint moved has to be re-read; that is what the
    fingerprint is for.
    """
    try:
        sSource = ast.unparse(nodeReference)
    except AttributeError:  # pragma: no cover - Python < 3.9
        sSource = ast.dump(nodeReference)
    return hashlib.sha256(sSource.encode("utf-8")).hexdigest()[:16]


def flistScanPackage():
    """Return every primitive call site in the package, ordinal-numbered."""
    return _flistNumberOrdinals(_tScanPackage()[0])


def flistUnresolvedSites():
    """Return the call sites the scan could not resolve.

    Two kinds, carried in one list but each stamped with its
    ``sBlindSpotKind``: a subprocess whose command is built where the
    scan cannot follow, and a docker-py chain whose client this scan
    cannot trace to a constructor. They were briefly merged under a
    subprocess-only name, which misdescribed twelve of them -- a record
    that misnames what it cannot read is no better than one that drops
    it.

    The scan's DECLARED blind spot: a command assembled somewhere the
    scan cannot follow might be a Docker invocation, and nothing here
    can tell. Reporting it is the whole point -- a boundary that
    silently drops what it cannot read claims a completeness it does
    not have, which is the failure this record exists to prevent.
    """
    return _flistNumberBlindSpotOrdinals(_tScanPackage()[1])


def _flistNumberBlindSpotOrdinals(listSites):
    """Give repeated (file, function, kind) triples a stable ordinal."""
    dictCounts = {}
    for dictSite in sorted(
        listSites,
        key=lambda site: (
            site["sFile"], site["sFunction"], site["sBlindSpotKind"],
            site["sFingerprint"],
        ),
    ):
        tKey = (
            dictSite["sFile"], dictSite["sFunction"],
            dictSite["sBlindSpotKind"],
        )
        dictSite["iOrdinal"] = dictCounts.get(tKey, 0)
        dictCounts[tKey] = dictSite["iOrdinal"] + 1
    return sorted(listSites, key=fsBlindSpotKey)


def fsBlindSpotKey(dictSite):
    """Return the stable identity of one declared-unreadable site."""
    return "|".join((
        dictSite["sFile"], dictSite["sFunction"],
        dictSite["sBlindSpotKind"], str(dictSite["iOrdinal"]),
    ))


def _tScanPackage():
    """Return ``(listRows, listUnresolved)`` for the whole package."""
    listRows = []
    listUnresolved = []
    for pathModule in sorted(PATH_PACKAGE.rglob("*.py")):
        if "__pycache__" in pathModule.parts:
            continue
        sRelativePath = str(pathModule.relative_to(PATH_PACKAGE))
        visitor = _VisitorCallSites(sRelativePath)
        visitor.fnCollect(ast.parse(pathModule.read_text(encoding="utf-8")))
        listRows.extend(visitor.listRows)
        listUnresolved.extend(visitor.listUnresolvedSubprocessSites)
        listUnresolved.extend(visitor.listUnresolvedSdkSites)
    return (listRows, listUnresolved)


def _flistNumberOrdinals(listRows):
    """Disambiguate repeated (file, function, primitive) triples.

    One function may call the same primitive several times, and each
    call is its own row with its own classification. The ordinal makes
    the key unique without depending on a line number, which every
    unrelated edit above it would change.
    """
    dictCounts = {}
    for dictRow in sorted(
        listRows,
        key=lambda row: (
            row["sFile"], row["sFunction"], row["sPrimitive"],
            row["sReferenceKind"], row["sFingerprint"],
        ),
    ):
        tKey = (
            dictRow["sFile"], dictRow["sFunction"], dictRow["sPrimitive"],
            dictRow["sReferenceKind"],
        )
        dictRow["iOrdinal"] = dictCounts.get(tKey, 0)
        dictCounts[tKey] = dictRow["iOrdinal"] + 1
    return sorted(listRows, key=fsRowKey)


def fsRowKey(dictRow):
    """Return the stable identity of one call site."""
    return "|".join((
        dictRow["sFile"], dictRow["sFunction"], dictRow["sPrimitive"],
        dictRow["sReferenceKind"], str(dictRow["iOrdinal"]),
    ))


# The fields the SCANNER owns. A checked-in row may not disagree with a
# fresh scan about any of them: they are derived, so an edited one is
# either a mistake or an attempt to reclassify a site by hand, and both
# have to surface. The first version compared only keys and
# fingerprints, so bMutationCapable could be flipped by hand and the
# drift check stayed green.
TUPLE_MACHINE_DERIVED_FIELDS = (
    "sFingerprint", "sAccess", "bMutationCapable", "bInsideGateway",
)


def fdictLoadInventory():
    """Return the checked-in inventory, or an empty one."""
    if not PATH_INVENTORY.is_file():
        return {"listRows": []}
    return json.loads(PATH_INVENTORY.read_text(encoding="utf-8"))


def fdictCompareAgainstSource(dictInventory, listScanned):
    """Return the drift between the checked-in record and a fresh scan."""
    dictRecorded = {fsRowKey(row): row for row in dictInventory["listRows"]}
    dictScanned = {fsRowKey(row): row for row in listScanned}
    listDuplicated = _flistDuplicatedKeys(dictInventory["listRows"])
    listEdited = []
    listAltered = []
    for sKey in sorted(set(dictRecorded) & set(dictScanned)):
        if dictRecorded[sKey]["sFingerprint"] != (
            dictScanned[sKey]["sFingerprint"]
        ):
            listEdited.append(sKey)
            continue
        listDisagreeing = [
            sField for sField in TUPLE_MACHINE_DERIVED_FIELDS
            if dictRecorded[sKey].get(sField) != dictScanned[sKey][sField]
        ]
        if listDisagreeing:
            listAltered.append(f"{sKey} ({', '.join(listDisagreeing)})")
    return {
        "listAdded": sorted(set(dictScanned) - set(dictRecorded)),
        "listRemoved": sorted(set(dictRecorded) - set(dictScanned)),
        "listDuplicated": listDuplicated,
        "listEdited": listEdited,
        "listAltered": listAltered,
        "listCountMismatch": (
            [] if dictInventory.get("iRowCount") == len(listScanned)
            else [
                f"recorded {dictInventory.get('iRowCount')}, "
                f"scanned {len(listScanned)}"
            ]
        ),
        "listBlindSpotDrift": _flistBlindSpotDrift(dictInventory),
    }


def _flistBlindSpotDrift(dictInventory):
    """Return how the recorded blind spot differs from a fresh scan.

    The blind spot used to be compared by COUNT alone, so a recorded
    site's file, function, or kind could be rewritten by hand and the
    check stayed green as long as the total held. That matters more
    than it sounds: a manual disposition is bound to a site, and a
    disposition attached to a site that has silently moved is a claim
    about code nobody looked at.
    """
    dictRecorded = {
        fsBlindSpotKey(site): site
        for site in dictInventory.get("listUnresolvedSites", [])
    }
    dictScanned = {
        fsBlindSpotKey(site): site for site in flistUnresolvedSites()
    }
    listDrift = [
        f"added {sKey}" for sKey in sorted(set(dictScanned) - set(dictRecorded))
    ] + [
        f"removed {sKey}"
        for sKey in sorted(set(dictRecorded) - set(dictScanned))
    ]
    for sKey in sorted(set(dictRecorded) & set(dictScanned)):
        if dictRecorded[sKey]["sFingerprint"] != (
            dictScanned[sKey]["sFingerprint"]
        ):
            listDrift.append(f"edited {sKey}")
    return listDrift


def _flistDuplicatedKeys(listRows):
    """Return keys appearing more than once in the checked-in record."""
    dictSeen = {}
    for dictRow in listRows:
        sKey = fsRowKey(dictRow)
        dictSeen[sKey] = dictSeen.get(sKey, 0) + 1
    return sorted(sKey for sKey, iCount in dictSeen.items() if iCount > 1)


def flistUnclassifiedKeys(dictInventory):
    """Return the keys still awaiting a reviewer's semantic judgement."""
    return sorted(
        fsRowKey(dictRow) for dictRow in dictInventory["listRows"]
        if _fbRowIsUnclassified(dictRow)
    )


def _fbRowIsUnclassified(dictRow):
    """Return True while any reviewer field is still the sentinel."""
    for sField in TUPLE_REVIEWER_FIELDS:
        objValue = dictRow.get(sField)
        if isinstance(objValue, list):
            if S_UNCLASSIFIED in objValue or not objValue:
                return True
        elif objValue == S_UNCLASSIFIED:
            return True
    return False


def flistSchemaViolations(dictInventory):
    """Return every classified value that is not a permitted one.

    The ratchet counts rows that still hold the sentinel; this is the
    other half, and without it the ratchet is trivially defeated by
    replacing UNCLASSIFIED with an empty string or with prose nobody can
    compare. A row is checked only once it claims to be classified --
    an untouched row is honest about being untouched.
    """
    listViolations = []
    for dictRow in dictInventory["listRows"]:
        if _fbRowIsUnclassified(dictRow):
            continue
        listViolations.extend(_flistRowSchemaViolations(dictRow))
    return listViolations


def _flistRowSchemaViolations(dictRow):
    """Return one classified row's schema violations."""
    sKey = fsRowKey(dictRow)
    listViolations = []
    for sField, setPermitted in DICT_REVIEWER_ENUMS.items():
        listValues = dictRow.get(sField) or []
        if not isinstance(listValues, list) or not listValues:
            listViolations.append(f"{sKey}: {sField} must be a non-empty list")
            continue
        for sValue in listValues:
            if sValue not in setPermitted:
                listViolations.append(
                    f"{sKey}: {sField} has {sValue!r}, not one of "
                    f"{sorted(setPermitted)}"
                )
    for sField, setPermitted in DICT_REVIEWER_SCALAR_ENUMS.items():
        if dictRow.get(sField) not in setPermitted:
            listViolations.append(
                f"{sKey}: {sField} has {dictRow.get(sField)!r}, not one "
                f"of {sorted(setPermitted)}"
            )
    for sField in TUPLE_REVIEWER_PROSE_FIELDS:
        if not str(dictRow.get(sField) or "").strip():
            listViolations.append(f"{sKey}: {sField} is empty")
    if dictRow.get("sCarrierDisposition") == "excluded" and not str(
        dictRow.get("sExclusionRationale") or "",
    ).strip():
        listViolations.append(
            f"{sKey}: excluded from the carrier with no rationale"
        )
    return listViolations


def _fdictBuildInventory(listScanned, dictExisting):
    """Merge a fresh scan with any classifications already recorded."""
    dictRecorded = {fsRowKey(row): row for row in dictExisting["listRows"]}
    listMerged = []
    for dictRow in listScanned:
        dictPrevious = dictRecorded.get(fsRowKey(dictRow))
        if dictPrevious is not None and dictPrevious.get(
            "sFingerprint",
        ) == dictRow["sFingerprint"]:
            for sField in TUPLE_REVIEWER_FIELDS:
                dictRow[sField] = dictPrevious.get(sField, S_UNCLASSIFIED)
        listMerged.append(dictRow)
    listUnresolved = flistUnresolvedSites()
    return {
        "iUnresolvedSiteCount": len(listUnresolved),
        "listUnresolvedSites": listUnresolved,
        "sPurpose": (
            "Every container-mutation call site in vaibify/, one row "
            "each. Machine-derived identity; reviewer-classified "
            "meaning. See tools/generateMutationInventory.py."
        ),
        "iRowCount": len(listMerged),
        "listRows": listMerged,
    }


def main():
    """Print, write, or check the inventory; return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true",
        help="Rewrite tests/mutationInventory.json from a fresh scan.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Report drift against the checked-in inventory.",
    )
    args = parser.parse_args()

    listScanned = flistScanPackage()
    if args.check:
        dictDrift = fdictCompareAgainstSource(
            fdictLoadInventory(), listScanned,
        )
        print(json.dumps(dictDrift, indent=2))
        return 1 if any(dictDrift.values()) else 0
    dictInventory = _fdictBuildInventory(listScanned, fdictLoadInventory())
    if args.write:
        PATH_INVENTORY.write_text(
            json.dumps(dictInventory, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        iUnclassified = len(flistUnclassifiedKeys(dictInventory))
        print(
            f"Wrote {dictInventory['iRowCount']} rows to "
            f"{PATH_INVENTORY.relative_to(PATH_REPOSITORY)}; "
            f"{iUnclassified} still need a reviewer."
        )
        return 0
    print(json.dumps(dictInventory, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
