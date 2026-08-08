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

COMPLETENESS RESTS ON THE CAPABILITY, NOT ON DECODING A COMMAND. Four
earlier versions each taught this scanner one more piece of Python's
name resolution, and each round a review found another shape that
vanished from the record. That is not bad luck: it is what happens when
completeness depends on decoding an expression somebody else writes. The
demonstration on shipped code is ``gui/director.py``, withdrawn in
6e55cbf. Its ``fnExecuteCommand`` ran ``subprocess.Popen(sCommand,
shell=True)`` over an arbitrary string -- the most permissive command
authority anywhere under ``vaibify/gui/`` -- and produced ZERO inventory
rows, appearing only as one blind-spot entry.

So there are three records, and they carry different weight:

* **Acquisitions** -- an import or an attribute-acquisition of a member
  in the closed DANGEROUS vocabulary below. A module is process-capable
  or Docker-capable by ORIGIN, whatever it later does with the capability
  and whether or not the scan can read it. This record is
  completeness-critical and fails closed: every acquisition needs a
  reviewed disposition.
* **Use sites** (``listRows``) -- decoded calls and commands. Best-effort
  metadata. A launch whose argv the scan cannot read is still a row, with
  the command marked UNKNOWN and treated as mutation-capable, because
  unknown means mutating.
* **Dispositions** -- the reviewer's judgement on an acquisition. Human,
  recorded against a fingerprint, and honest about being a judgement.

THE SCANNER NEVER DECIDES REACHABILITY. It inventories acquisitions
across the whole package and requires a disposition for each. Whether an
authority is CLI-only, unreachable, or separately authorized is
established by an independently reviewed disposition, never by a
scanner's verdict -- a verdict would pretend to be a proof.

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

# A launch whose argv the scan cannot read. It is a ROW, not merely a
# declared blind spot: the capability is exercised here whatever the
# command turns out to be, and R4 says unknown counts as
# mutation-capable. The withdrawn director's `subprocess.Popen(sCommand,
# shell=True)` is the shape this exists for -- under the previous design
# it produced no row at all.
S_ACCESS_UNKNOWN_COMMAND = "unknown-command"
S_REFERENCE_UNKNOWN_COMMAND = "unknown-command"
S_PRIMITIVE_UNKNOWN_COMMAND = "process UNKNOWN"

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
    "ftRunInContainerStreamed": S_ACCESS_ARBITRARY_COMMAND,
    "ftRunInContainerStreamedWithChunks": S_ACCESS_ARBITRARY_COMMAND,
    "ftResultExecuteCommand": S_ACCESS_ARBITRARY_COMMAND,
    "fnWriteFile": S_ACCESS_ARCHIVE_WRITE,
    "fnWriteFileViaTar": S_ACCESS_ARCHIVE_WRITE,
    "fsExecCreate": S_ACCESS_EXEC_CREATE,
    "fsocketExecStart": S_ACCESS_EXEC_CREATE,
    "fnExecResize": S_ACCESS_EXEC_STATE,
    "ftRunRootShellProbe": S_ACCESS_ROOT_SHELL,
    "fdictProbeProcessGroupMembers": S_ACCESS_ARBITRARY_COMMAND,
    "fnSignalProcessGroupMembers": S_ACCESS_SIGNAL,
    # --- vaibify/docker/dockerConnection.py: read / cache ---
    "flistGetRunningContainers": S_ACCESS_TYPED_READ,
    "fcontainerGetById": S_ACCESS_TYPED_READ,
    "fbaFetchFile": S_ACCESS_TYPED_READ,
    "fiterStreamFile": S_ACCESS_TYPED_READ,
    "fdictInspectExec": S_ACCESS_TYPED_READ,
    "fnEvictAbsentContainers": S_ACCESS_TYPED_READ,
    # The audited adapter behind `vaibify ls`: the caller supplies a
    # PATH, never a command, so it is a typed read even though it uses
    # the raw executor internally.
    "flistDirectoryEntries": S_ACCESS_TYPED_READ,
    # The audited adapter behind the dashboard's disk tile, on the same
    # terms: a PATH and a declared operation name, never a command. It
    # replaced `docker exec -u <user> <id> df -PB1 /` assembled in a GUI
    # module, which the boundary had to read as an arbitrary command
    # because a primitive cannot tell a df from an rm -rf.
    "fdictReadFilesystemUsage": S_ACCESS_TYPED_READ,
    # The two existence probes, on the same terms again. They replaced
    # `test -f`/`test -d` assembled by ContainerRepoFiles and run
    # through the general exec primitive -- which the boundary had to
    # read as an arbitrary command, so under an enforced lane a level
    # gate's existence check was REFUSED, and the gate (catching
    # OSError) reported the workflow unverified rather than raising.
    "fbContainerPathIsFile": S_ACCESS_TYPED_READ,
    "fbContainerPathIsDirectory": S_ACCESS_TYPED_READ,
    # The BATCHED existence probe, on the same terms. It replaced a
    # shell heredoc in fileRoutes that interpolated up to a thousand
    # caller-derived paths raw between the heredoc marker and its
    # terminator -- so a path carrying that terminator on a line of its
    # own ended the heredoc and made the rest shell, and the whole
    # thing went through the general exec primitive besides.
    "flistContainerPathsExist": S_ACCESS_TYPED_READ,
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
    S_ACCESS_UNKNOWN_COMMAND,
})


# ---------------------------------------------------------------------
# The closed DANGEROUS vocabulary, and how a capability is acquired.
# ---------------------------------------------------------------------
#
# Kept NARROW so that acquisition stays sound. Importing ``os`` is not
# an acquisition and ``from os import system`` is: 33 modules in this
# package import ``os`` and 28 import ``asyncio``, so a module-level
# reading of either would classify most of the package as dangerous and
# tell a reviewer nothing.

S_CAPABILITY_PROCESS_LAUNCH = "process-launch"
S_CAPABILITY_DOCKER_CLIENT = "docker-client"
S_CAPABILITY_REFLECTION = "reflection"
S_CAPABILITY_UNIX_SOCKET = "unix-socket"

# Modules whose IMPORT is the acquisition, because every reason to import
# one is the capability itself. ``import docker.errors`` counts even
# though the member is an exception class: the statement binds the name
# ``docker``, so ``docker.from_env()`` works immediately afterwards.
DICT_CAPABILITY_MODULES = {
    "subprocess": S_CAPABILITY_PROCESS_LAUNCH,
    "multiprocessing": S_CAPABILITY_PROCESS_LAUNCH,
    "pty": S_CAPABILITY_PROCESS_LAUNCH,
    "docker": S_CAPABILITY_DOCKER_CLIENT,
    "importlib": S_CAPABILITY_REFLECTION,
}

# Members of ORDINARY modules that are a capability on their own. The
# import of the module is not the acquisition; naming the member is.
DICT_CAPABILITY_MEMBERS = {
    ("asyncio", "create_subprocess_exec"): S_CAPABILITY_PROCESS_LAUNCH,
    ("asyncio", "create_subprocess_shell"): S_CAPABILITY_PROCESS_LAUNCH,
    ("concurrent.futures", "ProcessPoolExecutor"): (
        S_CAPABILITY_PROCESS_LAUNCH
    ),
    ("sys", "modules"): S_CAPABILITY_REFLECTION,
    ("builtins", "__import__"): S_CAPABILITY_REFLECTION,
    ("socket", "AF_UNIX"): S_CAPABILITY_UNIX_SOCKET,
}

# ``os``'s process-creating surface. A PREFIX rule rather than a list of
# spellings, and the difference matters: enumerating spellings loses to
# whatever the next one is, but ``os`` is the standard library's own
# closed namespace, where every ``exec*`` and every ``spawn*`` replaces
# or forks the process and nothing else begins with either word.
SET_OS_PROCESS_MEMBERS = frozenset({
    "system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp",
})
TUPLE_OS_PROCESS_MEMBER_PREFIXES = ("exec", "spawn")

# Reflection obtained without importing anything. Listing only
# ``importlib`` / ``__import__`` / ``getattr`` would leave
# ``sys.modules["subprocess"].run(...)`` and an ``eval``-obtained handle
# outside the record entirely.
SET_REFLECTION_BUILTINS = frozenset({"eval", "exec", "__import__"})
S_REFLECTION_DYNAMIC_ATTRIBUTE = "getattr"

# The ordinary modules a dangerous MEMBER hangs off, by their real dotted
# names. A member is looked up under the module it actually came from,
# never under whatever local name the file gave it -- ``import os as
# operatingSystem`` then ``operatingSystem.system(...)`` is os.system,
# and a lookup keyed on the spelling would not see it at all. That is
# the same classification-by-spelling defect the launcher and the Docker
# client each already had; leaving it here would be fixing the instance
# rather than the class.
SET_MEMBER_CAPABILITY_MODULE_ROOTS = frozenset(
    {"os"} | {sModule.split(".")[0] for sModule, _ in DICT_CAPABILITY_MEMBERS}
)

# The subprocess module's launchers. Only meaningful once the call is
# ROOTED in a name bound to the subprocess module, or in a name bound
# directly to one of these -- matching on the bare spelling recorded
# ``asyncio.run`` and ``uvicorn.run`` as subprocess launches, which put
# eight sites in the blind spot that were never process launches at all.
SET_SUBPROCESS_LAUNCHERS = frozenset({
    "run", "Popen", "call", "check_call", "check_output",
})

S_ACQUISITION_IMPORT = "import"
S_ACQUISITION_IMPORT_FROM = "import-from"
S_ACQUISITION_ATTRIBUTE = "attribute"
S_ACQUISITION_REFLECTION_CALL = "reflection-call"

# The reviewer's judgement on one acquisition. ``guarded`` is the default
# reading of a raw effect -- it says the capability is expected to be
# exercised behind the mutation boundary. ``forbidden`` says this module
# may not hold it at all. ``separate-authority`` says it is outside the
# carrier's scope and owes a rationale.
SET_ACQUISITION_DISPOSITIONS = frozenset({
    "guarded", "forbidden", "separate-authority",
})
TUPLE_ACQUISITION_REVIEWER_FIELDS = (
    "sDisposition", "sDispositionRationale",
)

# The modules that DEFINE the primitives. Their own definitions and
# internal helpers are the boundary itself, not callers of it. The
# host gateway joined 2026-08-08: it is the single permitted subprocess
# launcher under vaibify/host/ (testHostSubprocessConfinement pins
# that), every launch it makes is gated, journaled, and
# admission-checked, and its acquisition carries a reviewed
# disposition in the inventory.
SET_GATEWAY_MODULES = frozenset({
    "docker/dockerConnection.py",
    "docker/containerManager.py",
    "host/hostConnection.py",
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
        "start", "exec", "terminal", "helper", "file-write",
        "host-exec", "none",
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


_DICT_EMPTY_BINDINGS = {
    "dictCommandLists": {},
    "setClientNames": frozenset(),
    "setSdkBoundNames": frozenset(),
    "setProcessModuleNames": frozenset(),
    "setLauncherNames": frozenset(),
    "setDockerModuleNames": frozenset(),
    "dictModuleAliases": {},
}


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

    def __init__(self, sRelativePath, sModuleSource=""):
        self.sRelativePath = sRelativePath
        # The fingerprints are sliced from this, never regenerated
        # from the tree -- see _fsFingerprintNode.
        self.sModuleSource = sModuleSource
        self.tupleSourceLines = ftupleSourceLines(sModuleSource)
        self.listRows = []
        self.listAcquisitions = []
        self.listUnresolvedSubprocessSites = []
        self.listUnresolvedSdkSites = []
        self._listFunctionStack = []
        self._listScopeStack = []
        self._setCalledFunctionNodes = set()
        self._dictScopeModel = {}

    def fnCollect(self, treeModule):
        """Walk a parsed module, direct calls resolved first."""
        for nodeCall in ast.walk(treeModule):
            if isinstance(nodeCall, ast.Call):
                self._setCalledFunctionNodes.add(id(nodeCall.func))
        self._dictScopeModel = fdictBuildScopeModel(treeModule)
        self.visit(treeModule)

    def visit_FunctionDef(self, nodeFunction):
        self._listFunctionStack.append(nodeFunction.name)
        self._listScopeStack.append(nodeFunction)
        self.generic_visit(nodeFunction)
        self._listScopeStack.pop()
        self._listFunctionStack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef
    # A class body is a scope in the model, so the visitor has to enter
    # it as one too. Without this the bindings consulted inside a class
    # body were the enclosing function's, which is not what Python does
    # and is not what the model says.
    visit_ClassDef = visit_FunctionDef

    def _fdictBindingsHere(self):
        """Return the bindings visible at the current position.

        The scope model has already resolved inheritance and shadowing,
        so this is a lookup, not a walk up the stack -- which is the
        point of having one model instead of three collectors that each
        guessed at scoping in their own way.
        """
        keyScope = (
            id(self._listScopeStack[-1]) if self._listScopeStack else None
        )
        return self._dictScopeModel.get(keyScope, _DICT_EMPTY_BINDINGS)

    @property
    def _setDockerClientNames(self):
        return self._fdictBindingsHere()["setClientNames"]

    @property
    def _dictSdkBoundNames(self):
        return self._fdictBindingsHere()["setSdkBoundNames"]

    def _fdictCommandListsInScope(self):
        """Return the list literals visible at the current position."""
        return self._fdictBindingsHere()["dictCommandLists"]

    def _fbIsProcessLaunchCall(self, nodeCall):
        """Return True for a call that launches a process, by ORIGIN.

        Rooted in a name this scope binds to the subprocess module, or in
        a name bound directly to one of its launchers -- never in the
        spelling of the method. Matching ``run`` wherever it appeared put
        eight ``asyncio.run`` and ``uvicorn.run`` calls into the recorded
        blind spot, which is the same name-based classification the
        Docker-client origin rule was written to end, one primitive over.
        """
        dictBindings = self._fdictBindingsHere()
        nodeFunction = nodeCall.func
        if isinstance(nodeFunction, ast.Name):
            return nodeFunction.id in dictBindings["setLauncherNames"]
        if not isinstance(nodeFunction, ast.Attribute):
            return False
        if nodeFunction.attr not in SET_SUBPROCESS_LAUNCHERS:
            return False
        listChain = _flistAttributeChain(nodeFunction)
        return bool(listChain) and listChain[0] in (
            dictBindings["setProcessModuleNames"]
        )

    def visit_Call(self, nodeCall):
        self._fnRecordReflectionCall(nodeCall)
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

    def visit_Import(self, nodeImport):
        """Record an import that acquires a whole-module capability."""
        for nodeAlias in nodeImport.names:
            sCapability = DICT_CAPABILITY_MODULES.get(
                nodeAlias.name.split(".")[0],
            )
            if sCapability is not None:
                self._fnRecordAcquisition(
                    nodeImport, nodeAlias.name.split(".")[0], sCapability,
                    S_ACQUISITION_IMPORT,
                )
        self.generic_visit(nodeImport)

    def visit_ImportFrom(self, nodeImport):
        """Record a from-import that acquires a declared capability.

        A RELATIVE import can never be one. ``from ..docker.dockerContext
        import ...`` reaches this package's own ``docker`` subpackage, not
        the Docker SDK, and reading the two as the same thing would put
        five vaibify modules in the record for importing themselves.
        """
        if nodeImport.level:
            self.generic_visit(nodeImport)
            return
        sModule = nodeImport.module or ""
        sModuleCapability = DICT_CAPABILITY_MODULES.get(sModule.split(".")[0])
        for nodeAlias in nodeImport.names:
            sCapability = sModuleCapability or _fsCapabilityForMember(
                sModule, nodeAlias.name,
            )
            if sCapability is not None:
                self._fnRecordAcquisition(
                    nodeImport, f"{sModule}.{nodeAlias.name}", sCapability,
                    S_ACQUISITION_IMPORT_FROM,
                )
        self.generic_visit(nodeImport)

    def visit_Attribute(self, nodeAttribute):
        self._fnRecordPassedCallable(nodeAttribute, nodeAttribute.attr)
        self._fnRecordMemberAcquisition(nodeAttribute)
        self.generic_visit(nodeAttribute)

    def _fnRecordMemberAcquisition(self, nodeAttribute):
        """Record an attribute load that names a dangerous member."""
        sModule, sMember = _ftSplitAttributeIntoModuleAndMember(nodeAttribute)
        sModule = _fsResolveModuleAlias(
            sModule, self._fdictBindingsHere()["dictModuleAliases"],
        )
        sCapability = _fsCapabilityForMember(sModule, sMember)
        if sCapability is not None:
            self._fnRecordAcquisition(
                nodeAttribute, f"{sModule}.{sMember}", sCapability,
                S_ACQUISITION_ATTRIBUTE,
            )

    def _fnRecordReflectionCall(self, nodeCall):
        """Record ``eval``, ``exec``, ``__import__``, or a dynamic getattr.

        A dynamic ``getattr`` is one whose attribute name is not a
        literal. ``getattr(objThing, "sName", None)`` reaches a member the
        reader can see; ``getattr(module, sName)`` reaches whichever
        member the caller asks for, which is reflection.
        """
        if not isinstance(nodeCall.func, ast.Name):
            return
        sCalled = nodeCall.func.id
        if sCalled in SET_REFLECTION_BUILTINS:
            self._fnRecordAcquisition(
                nodeCall, sCalled, S_CAPABILITY_REFLECTION,
                S_ACQUISITION_REFLECTION_CALL,
            )
        elif sCalled == S_REFLECTION_DYNAMIC_ATTRIBUTE and _fbIsDynamicLookup(
            nodeCall,
        ):
            self._fnRecordAcquisition(
                nodeCall, "getattr(dynamic)", S_CAPABILITY_REFLECTION,
                S_ACQUISITION_REFLECTION_CALL,
            )

    def _fnRecordAcquisition(
        self, nodeAcquisition, sCapabilityName, sCapability, sAcquisitionKind,
    ):
        """Append one acquisition, identified and awaiting a disposition."""
        nodeScope = (
            self._listScopeStack[-1] if self._listScopeStack else None
        )
        dictAcquisition = {
            "sFile": self.sRelativePath,
            "sScope": (
                self._listFunctionStack[-1]
                if self._listFunctionStack else "<module>"
            ),
            "sCapability": sCapability,
            "sCapabilityName": sCapabilityName,
            "sAcquisitionKind": sAcquisitionKind,
            "iOrdinal": 0,
            "iLine": nodeAcquisition.lineno,
            "sFingerprint": _fsFingerprintNode(
                nodeAcquisition, self.tupleSourceLines,
            ),
            "sScopeFingerprint": _fsFingerprintNode(
                nodeScope if nodeScope is not None else nodeAcquisition,
                self.tupleSourceLines,
            ),
        }
        for sField in TUPLE_ACQUISITION_REVIEWER_FIELDS:
            dictAcquisition[sField] = S_UNCLASSIFIED
        self.listAcquisitions.append(dictAcquisition)

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
        if not self._fbIsProcessLaunchCall(nodeCall):
            return
        if _fbArgumentIsOpaque(nodeCall, self._fdictCommandListsInScope()):
            # A ROW and a declared blind spot, which answer different
            # questions. The row says the capability is exercised here
            # whatever the command turns out to be -- completeness, the
            # thing keyed on decoding used to lose. The blind spot says
            # the scan could not read the command -- honesty about the
            # metadata, and the input to Phase 2b's per-site disposition.
            self.listRows.append(self._fdictBuildRow(
                nodeCall, S_PRIMITIVE_UNKNOWN_COMMAND,
                S_ACCESS_UNKNOWN_COMMAND, S_REFERENCE_UNKNOWN_COMMAND,
            ))
            self.listUnresolvedSubprocessSites.append(
                self._fdictBuildBlindSpot(
                    nodeCall, S_BLIND_SPOT_OPAQUE_COMMAND,
                ),
            )
            return
        sSubcommand = _fsDockerSubcommandForCall(
            nodeCall, self._fdictCommandListsInScope(),
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
            self._fdictBindingsHere()["setDockerModuleNames"],
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
        """Return one declared-unreadable site, with two fingerprints.

        A blind spot carries the same identity a row does because it is
        subject to the same drift question. Without a fingerprint the
        record could only be counted, so a checked-in site's file,
        function, or kind could be rewritten by hand and every check
        stayed green.

        TWO fingerprints, because they answer different questions and a
        review found the single one insufficient. ``sFingerprint``
        hashes the call expression and answers "is this the same
        site" -- but for an opaque command the expression is
        ``subprocess.run(listCommand)``, which is IDENTICAL before and
        after the builder that fills ``listCommand`` is swapped from
        git to ``docker rm``. A manual disposition bound to that alone
        would survive the very change that invalidates it.

        ``sScopeFingerprint`` hashes the whole enclosing function, so
        any edit to how the command is built inside it invalidates the
        disposition. Its LIMIT, stated rather than papered over: a
        builder defined in another function is still outside it. The
        answer for those is to resolve the site mechanically, or for
        the disposition to NAME the supporting symbols it relied on so
        they can be fingerprinted too.
        """
        nodeScope = (
            self._listScopeStack[-1] if self._listScopeStack else None
        )
        return {
            "sBlindSpotKind": sBlindSpotKind,
            "sFile": self.sRelativePath,
            "sFunction": (
                self._listFunctionStack[-1]
                if self._listFunctionStack else "<module>"
            ),
            "iLine": nodeCall.lineno,
            "iOrdinal": 0,
            "sFingerprint": _fsFingerprintNode(
                nodeCall, self.tupleSourceLines,
            ),
            "sScopeFingerprint": _fsFingerprintNode(
                nodeScope if nodeScope is not None else nodeCall,
                self.tupleSourceLines,
            ),
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
            "sFingerprint": _fsFingerprintNode(
                nodeReference, self.tupleSourceLines,
            ),
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


def _fsetSdkBoundNamesInScope(listOwn, setDockerClientNames):
    """Return names this scope binds to an object fetched from the SDK.

    ``volume = dockerClient.volumes.get(name)`` then
    ``volume.remove(force=True)``: the mutation is a method on the
    RETURNED object, so a chain-only scan sees the read and misses the
    delete. One step of propagation catches the shape this package
    actually uses -- ``vaibify destroy`` removes a volume exactly so.
    """
    setBound = set()
    for nodeAssign in listOwn:
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
    nodeCall, setSdkBoundNames, setDockerClientNames, setDockerModuleNames,
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
        setDockerModuleNames,
    ):
        return None
    if any(sPart in SET_DOCKER_SDK_COLLECTIONS for sPart in listChain):
        return ".".join(listChain[-2:])
    if listChain[0] in setSdkBoundNames and listChain[-1] in (
        SET_MUTATING_SDK_METHODS
    ):
        return ".".join(listChain[-2:])
    return None


def _fbChainIsRootedInADockerClient(
    listChain, setDockerClientNames, setDockerModuleNames,
):
    """Return True when a call chain starts at a Docker client.

    ORIGIN, not spelling. An earlier version accepted any root whose
    name contained "docker" or "client", which both missed
    ``d = docker.from_env()`` and falsely claimed an unrelated
    ``client.images.remove(...)``. A record whose coverage turns on
    what somebody named a local is a record a rename can silently
    empty.

    The second clause is for a client that is never given a name:
    ``docker.APIClient().containers.remove(...)`` constructs one inline,
    so a rule that only reads assignments has nothing to match on. The
    low-level ``APIClient`` is the one that most needs recording -- it
    reaches the daemon's HTTP API directly.
    """
    if listChain[0] in setDockerClientNames:
        return True
    return listChain[0] in setDockerModuleNames and any(
        sPart in _TUPLE_DOCKER_CLIENT_CONSTRUCTORS
        for sPart in listChain[1:]
    )


# The constructors that hand back a Docker client. A name is a client
# because it was assigned from one of these, or from a subscript keyed
# "docker" (the route context's own slot) -- never because of how it is
# spelled.
_TUPLE_DOCKER_CLIENT_CONSTRUCTORS = (
    "from_env", "DockerClient", "APIClient",
)
_S_DOCKER_CONTEXT_KEY = "docker"


# Roots that are a Docker client wherever they appear, because they are
# not lexical bindings at all: the gateway's own attribute, and the
# context mapping's key.
_TUPLE_SEED_DOCKER_CLIENT_NAMES = ("_clientDocker", _S_DOCKER_CONTEXT_KEY)

# The two module names seeded at module scope, so that a fragment
# examined on its own -- a synthetic probe, or a snippet quoted in a
# test -- resolves the same way the real module does. A name may still be
# SHADOWED out of either set by a scope that rebinds it, which is the
# whole reason these are seeds rather than a global allow-list.
_TUPLE_SEED_PROCESS_MODULE_NAMES = ("subprocess",)
_TUPLE_SEED_DOCKER_MODULE_NAMES = ("docker",)


def _fsetClientNamesInScope(listOwn, setDockerModuleNames):
    """Return names this scope binds to a Docker client, by ORIGIN.

    The origin is an assignment from a known constructor
    (``docker.from_env()``, ``docker.DockerClient(...)``,
    ``docker.APIClient(...)``) rooted in the Docker SDK package. The root
    matters as much as the constructor name: ``boto3.from_env()`` spells
    a constructor this list holds, and a check that read only the
    trailing name recorded an unrelated cloud client as a Docker one.
    """
    setNames = set()
    for nodeAssign in listOwn:
        if not isinstance(nodeAssign, ast.Assign):
            continue
        if not isinstance(nodeAssign.value, ast.Call):
            continue
        if not _fbIsDockerClientConstructor(
            nodeAssign.value, setDockerModuleNames,
        ):
            continue
        for nodeTarget in nodeAssign.targets:
            if isinstance(nodeTarget, ast.Name):
                setNames.add(nodeTarget.id)
    return setNames


def _fbIsDockerClientConstructor(nodeCall, setDockerModuleNames):
    """Return True for a call that hands back a Docker client."""
    listChain = _flistAttributeChain(nodeCall.func)
    if len(listChain) < 2:
        return False
    if listChain[-1] not in _TUPLE_DOCKER_CLIENT_CONSTRUCTORS:
        return False
    return listChain[0] in setDockerModuleNames


def _fbLooksLikeAnUnrootedSdkCall(nodeCall):
    """Return True for a docker-py-shaped chain with an untraceable root."""
    listChain = _flistAttributeChain(nodeCall.func)
    if len(listChain) < 2:
        return False
    return any(
        sPart in SET_DOCKER_SDK_COLLECTIONS for sPart in listChain[:-1]
    )


def _ftSplitAttributeIntoModuleAndMember(nodeAttribute):
    """Return ``(dotted module, member)`` for a pure attribute chain.

    ``concurrent.futures.ProcessPoolExecutor`` yields
    ``("concurrent.futures", "ProcessPoolExecutor")``. A chain
    interrupted by a call or a subscript yields ``("", "")`` -- the
    prefix is then an expression, not a module path, and reading it as
    one would classify by spelling.
    """
    listParts = []
    nodeCurrent = nodeAttribute
    while isinstance(nodeCurrent, ast.Attribute):
        listParts.append(nodeCurrent.attr)
        nodeCurrent = nodeCurrent.value
    if not isinstance(nodeCurrent, ast.Name):
        return ("", "")
    listParts.append(nodeCurrent.id)
    listParts.reverse()
    return (".".join(listParts[:-1]), listParts[-1])


def _fsResolveModuleAlias(sModule, dictModuleAliases):
    """Return a dotted module path with its local alias resolved away."""
    if not sModule:
        return sModule
    listParts = sModule.split(".")
    listParts[0] = dictModuleAliases.get(listParts[0], listParts[0])
    return ".".join(listParts)


def _fdictModuleAliasesInScope(listOwn):
    """Return this scope's local names for the member-capability modules."""
    dictAliases = {}
    for nodeChild in listOwn:
        if not isinstance(nodeChild, ast.Import):
            continue
        for nodeAlias in nodeChild.names:
            sRoot = nodeAlias.name.split(".")[0]
            if sRoot not in SET_MEMBER_CAPABILITY_MODULE_ROOTS:
                continue
            # Without an alias, ``import concurrent.futures`` binds only
            # ``concurrent``; with one, the whole dotted path is bound
            # to the new name.
            if nodeAlias.asname:
                dictAliases[nodeAlias.asname] = nodeAlias.name
            else:
                dictAliases[sRoot] = sRoot
    return dictAliases


def _fsCapabilityForMember(sModule, sMember):
    """Return the capability a module member grants, or None."""
    sCapability = DICT_CAPABILITY_MEMBERS.get((sModule, sMember))
    if sCapability is not None:
        return sCapability
    if sModule != "os":
        return None
    if sMember in SET_OS_PROCESS_MEMBERS or sMember.startswith(
        TUPLE_OS_PROCESS_MEMBER_PREFIXES,
    ):
        return S_CAPABILITY_PROCESS_LAUNCH
    return None


def _fbIsDynamicLookup(nodeCall):
    """Return True when a ``getattr`` names its attribute at runtime."""
    return len(nodeCall.args) >= 2 and not isinstance(
        nodeCall.args[1], ast.Constant,
    )


def _fsetProcessModuleNamesInScope(listOwn):
    """Return names this scope binds to a process-launching module."""
    setNames = set()
    for nodeChild in listOwn:
        if not isinstance(nodeChild, ast.Import):
            continue
        for nodeAlias in nodeChild.names:
            sRoot = nodeAlias.name.split(".")[0]
            if DICT_CAPABILITY_MODULES.get(sRoot) == (
                S_CAPABILITY_PROCESS_LAUNCH
            ):
                setNames.add(nodeAlias.asname or sRoot)
    return setNames


def _fsetDockerModuleNamesInScope(listOwn):
    """Return names this scope binds to the Docker SDK package."""
    setNames = set()
    for nodeChild in listOwn:
        if not isinstance(nodeChild, ast.Import):
            continue
        for nodeAlias in nodeChild.names:
            sRoot = nodeAlias.name.split(".")[0]
            if DICT_CAPABILITY_MODULES.get(sRoot) == (
                S_CAPABILITY_DOCKER_CLIENT
            ):
                setNames.add(nodeAlias.asname or sRoot)
    return setNames


def _fsetLauncherNamesInScope(listOwn, setProcessModuleNames):
    """Return names this scope binds directly to a subprocess launcher.

    Two origins, and both are real here: ``from subprocess import run as
    fnLaunch`` and ``fnLaunch = subprocess.run``. Either way the launch
    is later spelled ``fnLaunch(...)``, with no ``subprocess`` in sight.
    """
    setNames = set()
    for nodeChild in listOwn:
        if isinstance(nodeChild, ast.ImportFrom) and not nodeChild.level:
            if (nodeChild.module or "") == "subprocess":
                for nodeAlias in nodeChild.names:
                    if nodeAlias.name in SET_SUBPROCESS_LAUNCHERS:
                        setNames.add(nodeAlias.asname or nodeAlias.name)
            continue
        if not isinstance(nodeChild, ast.Assign):
            continue
        if not isinstance(nodeChild.value, ast.Attribute):
            continue
        listChain = _flistAttributeChain(nodeChild.value)
        if len(listChain) < 2 or listChain[0] not in setProcessModuleNames:
            continue
        if listChain[-1] not in SET_SUBPROCESS_LAUNCHERS:
            continue
        for nodeTarget in nodeChild.targets:
            if isinstance(nodeTarget, ast.Name):
                setNames.add(nodeTarget.id)
    return setNames


def _fdictCollectCommandLists(treeModule):
    """Map each scope's local names to the list literals assigned there.

    A one-step constant propagation, which is what most of this
    package's subprocess calls actually look like:
    ``saCommand = ["docker", "rm", ...]`` then ``subprocess.run(
    saCommand, ...)``. Without it, ten real Docker invocations --
    ``rm``, ``tag``, ``exec``, ``stats`` -- were invisible purely
    because the argv had a name.

    Collected PER FUNCTION SCOPE, not per module. Module-wide
    collection treated two functions that each build their own
    ``listCommand`` as a reassignment and resolved neither, which is
    how ``resourceMonitor``'s ``docker stats`` and ``docker exec ... df``
    -- one of them mutation-capable, both in a GUI module -- came to sit
    in the blind spot rather than in the record. There was never an
    ambiguity to be cautious about: they are separate locals in
    separate scopes.

    Within one scope the caution still holds. A name assigned more than
    once resolves to nothing: two candidate values is not a resolution,
    and guessing one would be worse than declaring the site unresolved.

    Returns ``{scope key: {name: node}}``, where the scope key is the
    id of the enclosing function node, or ``None`` at module level.
    """
    return {
        keyScope: dictBindings["dictCommandLists"]
        for keyScope, dictBindings in fdictBuildScopeModel(treeModule).items()
    }


# A scope's body belongs to IT, not to its parent. Comprehensions have
# their own scope in Python 3 and cannot contain assignments that matter
# here, so they are not listed.
_T_SCOPE_BOUNDARY_NODES = (
    ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
)


def _tSplitScopeNodes(nodeScope):
    """Return ``(own nodes, directly nested scopes)`` for one scope.

    ``ast.walk`` cannot express this. Skipping a nested ``FunctionDef``
    inside a walk loop does NOT prune its subtree -- walk keeps yielding
    the descendants regardless -- so a collector written that way still
    reads the nested function's assignments as if they were the
    parent's. That defect made a real call site vanish from the record
    entirely: with an outer parameter shadowed by an inner local list,
    the scan borrowed the inner value, judged the outer command
    readable, and emitted neither a row nor a blind spot.
    """
    listOwn = []
    listNested = []
    listPending = list(ast.iter_child_nodes(nodeScope))
    while listPending:
        nodeChild = listPending.pop()
        if isinstance(nodeChild, _T_SCOPE_BOUNDARY_NODES):
            listNested.append(nodeChild)
            continue
        listOwn.append(nodeChild)
        listPending.extend(ast.iter_child_nodes(nodeChild))
    return (listOwn, listNested)


def fdictBuildScopeModel(treeModule):
    """Map every scope to the bindings VISIBLE inside it.

    One model, three consumers -- the command lists, the Docker client
    names, and the SDK-bound object names -- because they were three
    copies of the same mistake: each collected across the whole module,
    so any function's local spelling leaked into every other's. That is
    how ``def unrelated(d)`` came to be recorded as a Docker client
    because a different function had written ``d = docker.from_env()``,
    which is exactly the name-based classification the origin rule was
    written to end.

    Bindings are inherited from enclosing scopes (a closure really does
    see its parent's locals) EXCEPT through a class body, whose names
    are not visible in its methods. A name REBOUND in a scope --
    including as a parameter -- shadows the outer one and resolves to
    nothing unless this scope also gives it a readable value. Shadowing
    to nothing is the honest answer: it produces a blind spot, which is
    declared, rather than a confident wrong reading.
    """
    dictByScope = {}
    dictSeedBindings = {
        "dictCommandLists": {},
        "setClientNames": set(_TUPLE_SEED_DOCKER_CLIENT_NAMES),
        "setSdkBoundNames": set(),
        "setProcessModuleNames": set(_TUPLE_SEED_PROCESS_MODULE_NAMES),
        "setLauncherNames": set(),
        "setDockerModuleNames": set(_TUPLE_SEED_DOCKER_MODULE_NAMES),
        "dictModuleAliases": {
            sRoot: sRoot for sRoot in SET_MEMBER_CAPABILITY_MODULE_ROOTS
        },
    }
    listPending = [(None, treeModule, dictSeedBindings)]
    while listPending:
        keyScope, nodeScope, dictInherited = listPending.pop()
        listOwn, listNested = _tSplitScopeNodes(nodeScope)
        dictVisible = _fdictBindingsVisibleInScope(
            nodeScope, listOwn, dictInherited,
        )
        dictByScope[keyScope] = dictVisible
        # A class body's names are invisible to its methods, so its
        # children inherit what the CLASS inherited, not what it bound.
        dictForChildren = (
            dictInherited if isinstance(nodeScope, ast.ClassDef)
            else dictVisible
        )
        for nodeNested in listNested:
            listPending.append((id(nodeNested), nodeNested, dictForChildren))
    return dictByScope


def _fdictBindingsVisibleInScope(nodeScope, listOwn, dictInherited):
    """Return the inherited bindings, shadowed and extended by this scope."""
    setRebound = _fsetNamesBoundInScope(nodeScope, listOwn)
    dictCommandLists = {
        sName: nodeValue
        for sName, nodeValue in dictInherited["dictCommandLists"].items()
        if sName not in setRebound
    }
    setProcessModuleNames = (
        dictInherited["setProcessModuleNames"] - setRebound
    ) | _fsetProcessModuleNamesInScope(listOwn)
    setDockerModuleNames = (
        dictInherited["setDockerModuleNames"] - setRebound
    ) | _fsetDockerModuleNamesInScope(listOwn)
    setClientNames = dictInherited["setClientNames"] - setRebound
    setSdkBoundNames = dictInherited["setSdkBoundNames"] - setRebound
    dictCommandLists.update(_fdictListLiteralsInScope(listOwn))
    setClientNames |= _fsetClientNamesInScope(listOwn, setDockerModuleNames)
    setSdkBoundNames |= _fsetSdkBoundNamesInScope(listOwn, setClientNames)
    return {
        "dictCommandLists": dictCommandLists,
        "setClientNames": setClientNames,
        "setSdkBoundNames": setSdkBoundNames,
        "setProcessModuleNames": setProcessModuleNames,
        "setLauncherNames": (
            dictInherited["setLauncherNames"] - setRebound
        ) | _fsetLauncherNamesInScope(listOwn, setProcessModuleNames),
        "setDockerModuleNames": setDockerModuleNames,
        "dictModuleAliases": dict(
            {
                sName: sModule
                for sName, sModule in dictInherited[
                    "dictModuleAliases"
                ].items()
                if sName not in setRebound
            },
            **_fdictModuleAliasesInScope(listOwn),
        ),
    }


def _fsetNamesBoundInScope(nodeScope, listOwn):
    """Return every name this scope binds, however it binds it.

    Parameters count. A function taking ``listCommand`` as an argument
    binds it to something the scan cannot see, so an outer or
    module-level list of the same name must not answer for it.
    """
    setBound = set()
    argumentsScope = getattr(nodeScope, "args", None)
    if isinstance(argumentsScope, ast.arguments):
        for nodeArgument in (
            list(getattr(argumentsScope, "posonlyargs", []))
            + list(argumentsScope.args)
            + list(argumentsScope.kwonlyargs)
            + [argumentsScope.vararg, argumentsScope.kwarg]
        ):
            if nodeArgument is not None:
                setBound.add(nodeArgument.arg)
    for nodeChild in listOwn:
        if isinstance(nodeChild, ast.Name) and isinstance(
            nodeChild.ctx, (ast.Store, ast.Del),
        ):
            setBound.add(nodeChild.id)
        elif isinstance(nodeChild, ast.alias):
            setBound.add((nodeChild.asname or nodeChild.name).split(".")[0])
    return setBound


def _fdictListLiteralsInScope(listOwn):
    """Return names this scope assigns a readable list literal, once.

    A name assigned more than once in one scope resolves to nothing:
    two candidate values is not a resolution, and guessing one would be
    worse than declaring the site unresolved.
    """
    dictAssigned = {}
    setReassigned = set()
    for nodeChild in listOwn:
        if not isinstance(nodeChild, ast.Assign):
            continue
        if not isinstance(nodeChild.value, (ast.List, ast.Tuple)):
            continue
        for nodeTarget in nodeChild.targets:
            if not isinstance(nodeTarget, ast.Name):
                continue
            if nodeTarget.id in dictAssigned:
                setReassigned.add(nodeTarget.id)
            dictAssigned[nodeTarget.id] = nodeChild.value
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
    """Return the docker subcommand a launch runs, or None.

    Whether the call launches a process at all is the CALLER's question,
    because answering it needs the scope bindings this function does not
    have.
    """
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


def ftupleSourceLines(sModuleSource):
    """Split a module's source once, for repeated fingerprinting.

    Called ONCE per module and threaded to every fingerprint in it.
    ``ast.get_source_segment`` -- the obvious way to slice a node out of
    a file -- re-splits the ENTIRE source on every call, which is
    invisible on one node and quadratic over a module: measured at
    15.9 ms per node against a large file, so the attribution index,
    which fingerprints every Call/Attribute/Name, took minutes per
    module and timed the CI suite out at fifteen. Splitting once is the
    whole difference.
    """
    return tuple(sModuleSource.splitlines(keepends=True))


def _fsFingerprintNode(nodeReference, tupleSourceLines):
    """Return a digest of a reference's own source, sliced from the file.

    **The digest must depend on the researcher's code and on nothing
    else.** This used to hash ``ast.unparse(node)``, which reads well --
    it made the fingerprint immune to reformatting and comment edits --
    and was wrong in a way only a version matrix could show.
    ``ast.unparse`` REGENERATES source from the parse tree, and CPython
    changes its formatting between releases: on Python 3.14 the same
    unchanged functions unparsed differently and 46 fingerprints moved
    at once. A marker meant to answer "has the code this review depended
    on been edited?" was also answering "are you on a different Python?",
    which makes every manual disposition read as expired on one leg of
    the matrix and teaches a reader to ignore it.

    Slicing the file is the only construction whose stability does not
    rest on CPython internals. The alternatives were considered and are
    worse for this job:

    * normalising the AST before hashing keeps format-independence, but
      node FIELDS change between releases too (3.12 added
      ``type_params``), so the projection needs updating every release
      and fails silently -- drop a field and it stops noticing a real
      change;
    * tokenising and discarding comments looks version-stable and is
      not: 3.12 rewrote f-string tokenisation (PEP 701), so any function
      containing an f-string would differ across the supported range.

    The slice is taken here rather than through ``ast.get_source_segment``
    for speed (see :func:`ftupleSourceLines`) and for one property that
    function cannot offer: its own behaviour is CPython's to change,
    which is the exact dependence this fingerprint exists to escape.

    ``col_offset`` is a UTF-8 BYTE offset, so the slicing encodes and
    decodes rather than indexing characters; a module with a non-ASCII
    identifier or docstring would otherwise fingerprint a shifted span.

    The cost is real and is the safe direction: reformatting a function
    or editing a comment inside it now moves its scope fingerprint and
    expires the disposition bound to it. That costs a re-read of code
    somebody already reviewed. The failure it replaces cost the check
    its meaning on an entire Python version.
    """
    iFirstLine = getattr(nodeReference, "lineno", None)
    iLastLine = getattr(nodeReference, "end_lineno", None)
    if iFirstLine is None or iLastLine is None:
        # Never silently fall back to unparse: that would reintroduce
        # the version dependence for exactly the nodes nobody notices.
        raise RuntimeError(
            "Cannot fingerprint a node with no source position; the "
            "inventory's identity must come from the file, not from a "
            "regenerated approximation."
        )
    iStartByte = nodeReference.col_offset
    iEndByte = nodeReference.end_col_offset
    if iFirstLine == iLastLine:
        sSegment = tupleSourceLines[iFirstLine - 1].encode("utf-8")[
            iStartByte:iEndByte
        ].decode("utf-8")
    else:
        # Pad the first line to its column, exactly as
        # ``ast.get_source_segment(padded=True)`` does: a multi-line
        # segment keeps its opening indentation so re-indenting the
        # block still moves the digest. Tabs and form feeds are kept as
        # themselves; every other character becomes a space.
        sPrefix = tupleSourceLines[iFirstLine - 1].encode("utf-8")[
            :iStartByte
        ].decode("utf-8")
        sPadding = "".join(
            sCharacter if sCharacter in "\f\t" else " "
            for sCharacter in sPrefix
        )
        sFirst = sPadding + tupleSourceLines[iFirstLine - 1].encode(
            "utf-8",
        )[iStartByte:].decode("utf-8")
        sLast = tupleSourceLines[iLastLine - 1].encode("utf-8")[
            :iEndByte
        ].decode("utf-8")
        sSegment = "".join(
            (sFirst,) + tupleSourceLines[iFirstLine:iLastLine - 1] + (sLast,)
        )
    return hashlib.sha256(sSegment.encode("utf-8")).hexdigest()[:16]


def flistScanPackage():
    """Return every primitive call site in the package, ordinal-numbered."""
    return _flistNumberOrdinals(_tScanPackage()[0])


def flistAcquisitions():
    """Return every acquisition of a declared capability in the package.

    THE completeness record. A use site can vanish because nobody could
    decode what it ran; an acquisition cannot, because it is an import or
    an attribute load and the scan reads those exactly. What the record
    still cannot see is stated rather than implied: a capability handed
    to a module as an argument is acquired by whoever constructed it, and
    is a row THERE -- so a module holding a Docker client it received as
    a parameter carries no acquisition of its own, by design.
    """
    return _flistNumberAcquisitionOrdinals(_tScanPackage()[2])


def _flistNumberAcquisitionOrdinals(listAcquisitions):
    """Give repeated acquisition identities a stable ordinal."""
    dictCounts = {}
    for dictAcquisition in sorted(
        listAcquisitions,
        key=lambda acquisition: (
            acquisition["sFile"], acquisition["sScope"],
            acquisition["sCapability"], acquisition["sCapabilityName"],
            acquisition["sAcquisitionKind"], acquisition["sFingerprint"],
            acquisition["iLine"],
        ),
    ):
        tKey = (
            dictAcquisition["sFile"], dictAcquisition["sScope"],
            dictAcquisition["sCapability"],
            dictAcquisition["sCapabilityName"],
            dictAcquisition["sAcquisitionKind"],
        )
        dictAcquisition["iOrdinal"] = dictCounts.get(tKey, 0)
        dictCounts[tKey] = dictAcquisition["iOrdinal"] + 1
    return sorted(listAcquisitions, key=fsAcquisitionKey)


def fsAcquisitionKey(dictAcquisition):
    """Return the stable identity of one capability acquisition."""
    return "|".join((
        dictAcquisition["sFile"], dictAcquisition["sScope"],
        dictAcquisition["sCapability"], dictAcquisition["sCapabilityName"],
        dictAcquisition["sAcquisitionKind"], str(dictAcquisition["iOrdinal"]),
    ))


def flistUndisposedAcquisitions(dictInventory):
    """Return the acquisition keys carrying no reviewed disposition.

    FAIL-CLOSED, and the direction of the lookup is what makes it so: the
    scan is the authority on which acquisitions exist, and the record
    answers for each. An acquisition the record has never heard of counts
    as undisposed, so a new capability cannot arrive already exempt.
    """
    dictRecorded = {
        fsAcquisitionKey(dictAcquisition): dictAcquisition
        for dictAcquisition in dictInventory.get("listAcquisitions", [])
    }
    listUndisposed = []
    for dictScanned in flistAcquisitions():
        sKey = fsAcquisitionKey(dictScanned)
        dictRecordedEntry = dictRecorded.get(sKey)
        if dictRecordedEntry is None or dictRecordedEntry.get(
            "sFingerprint",
        ) != dictScanned["sFingerprint"]:
            listUndisposed.append(sKey)
        elif dictRecordedEntry.get(
            "sDisposition",
        ) not in SET_ACQUISITION_DISPOSITIONS:
            listUndisposed.append(sKey)
    return sorted(listUndisposed)


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
    """Return ``(rows, unresolved, acquisitions)`` for the whole package."""
    listRows = []
    listUnresolved = []
    listAcquisitions = []
    for pathModule in sorted(PATH_PACKAGE.rglob("*.py")):
        if "__pycache__" in pathModule.parts:
            continue
        sRelativePath = str(pathModule.relative_to(PATH_PACKAGE))
        sModuleSource = pathModule.read_text(encoding="utf-8")
        visitor = _VisitorCallSites(sRelativePath, sModuleSource)
        visitor.fnCollect(ast.parse(sModuleSource))
        listRows.extend(visitor.listRows)
        listUnresolved.extend(visitor.listUnresolvedSubprocessSites)
        listUnresolved.extend(visitor.listUnresolvedSdkSites)
        listAcquisitions.extend(visitor.listAcquisitions)
    return (listRows, listUnresolved, listAcquisitions)


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
        "listAcquisitionDrift": _flistAcquisitionDrift(dictInventory),
    }


def _flistAcquisitionDrift(dictInventory):
    """Return how the recorded acquisitions differ from a fresh scan.

    Compared the same way the rows and the blind spot are -- added,
    removed, duplicated, edited, plus the count field -- because an
    acquisition carries a REVIEWED disposition, and a disposition bound
    to an acquisition that moved underneath it is a claim about code
    nobody re-read.

    Both fingerprints are compared. ``sFingerprint`` answers "is this the
    same acquiring statement"; ``sScopeFingerprint`` answers "did what
    surrounds it change", which is what a reviewer actually looked at
    when they wrote ``guarded``.
    """
    listRecorded = dictInventory.get("listAcquisitions", [])
    dictRecorded = {
        fsAcquisitionKey(acquisition): acquisition
        for acquisition in listRecorded
    }
    dictScanned = {
        fsAcquisitionKey(acquisition): acquisition
        for acquisition in flistAcquisitions()
    }
    listDrift = [
        f"added {sKey}"
        for sKey in sorted(set(dictScanned) - set(dictRecorded))
    ] + [
        f"removed {sKey}"
        for sKey in sorted(set(dictRecorded) - set(dictScanned))
    ] + [
        f"duplicated {sKey}"
        for sKey in _flistDuplicatedAcquisitionKeys(listRecorded)
    ]
    if len(listRecorded) != len(dictScanned):
        listDrift.append(
            f"recorded list holds {len(listRecorded)} acquisitions, scan "
            f"found {len(dictScanned)}"
        )
    if dictInventory.get("iAcquisitionCount") != len(listRecorded):
        listDrift.append(
            f"count field says {dictInventory.get('iAcquisitionCount')}, "
            f"recorded list holds {len(listRecorded)}"
        )
    for sKey in sorted(set(dictRecorded) & set(dictScanned)):
        listDisagreeing = [
            sField for sField in ("sFingerprint", "sScopeFingerprint")
            if dictRecorded[sKey].get(sField) != dictScanned[sKey][sField]
        ]
        if listDisagreeing:
            listDrift.append(f"edited {sKey} ({', '.join(listDisagreeing)})")
    return listDrift


def _flistDuplicatedAcquisitionKeys(listAcquisitions):
    """Return acquisition keys appearing more than once in the record."""
    dictSeen = {}
    for dictAcquisition in listAcquisitions:
        sKey = fsAcquisitionKey(dictAcquisition)
        dictSeen[sKey] = dictSeen.get(sKey, 0) + 1
    return sorted(sKey for sKey, iCount in dictSeen.items() if iCount > 1)


def _flistBlindSpotDrift(dictInventory):
    """Return how the recorded blind spot differs from a fresh scan.

    The blind spot used to be compared by COUNT alone, so a recorded
    site's file, function, or kind could be rewritten by hand and the
    check stayed green as long as the total held. That matters more
    than it sounds: a manual disposition is bound to a site, and a
    disposition attached to a site that has silently moved is a claim
    about code nobody looked at.

    Keying by identity fixed that but opened a smaller hole of the same
    shape: a DUPLICATED recorded entry collapsed into one dictionary
    slot, so the recorded list could carry 39 entries while its count
    field said 38 and this check said nothing. Both are compared here
    now -- the same lesson the row check learned when
    ``listDuplicated`` was added to it.

    ``iLine`` is deliberately NOT compared. Rows omit line numbers from
    their identity for the same reason: every unrelated edit above a
    site would move it, and a check that cries drift on every edit
    teaches a reader to regenerate without looking, which is worse than
    the narrow hand-edit it would catch.
    """
    listRecordedSites = dictInventory.get("listUnresolvedSites", [])
    dictRecorded = {
        fsBlindSpotKey(site): site for site in listRecordedSites
    }
    dictScanned = {
        fsBlindSpotKey(site): site for site in flistUnresolvedSites()
    }
    listDrift = [
        f"added {sKey}" for sKey in sorted(set(dictScanned) - set(dictRecorded))
    ] + [
        f"removed {sKey}"
        for sKey in sorted(set(dictRecorded) - set(dictScanned))
    ] + [
        f"duplicated {sKey}"
        for sKey in _flistDuplicatedBlindSpotKeys(listRecordedSites)
    ]
    if len(listRecordedSites) != len(dictScanned):
        listDrift.append(
            f"recorded list holds {len(listRecordedSites)} sites, "
            f"scan found {len(dictScanned)}"
        )
    if dictInventory.get("iUnresolvedSiteCount") != len(listRecordedSites):
        listDrift.append(
            f"count field says "
            f"{dictInventory.get('iUnresolvedSiteCount')}, recorded list "
            f"holds {len(listRecordedSites)}"
        )
    for sKey in sorted(set(dictRecorded) & set(dictScanned)):
        listDisagreeing = [
            sField for sField in ("sFingerprint", "sScopeFingerprint")
            if dictRecorded[sKey].get(sField) != dictScanned[sKey][sField]
        ]
        if listDisagreeing:
            listDrift.append(f"edited {sKey} ({', '.join(listDisagreeing)})")
    return listDrift


def _flistDuplicatedBlindSpotKeys(listSites):
    """Return blind-spot keys appearing more than once in the record."""
    dictSeen = {}
    for dictSite in listSites:
        sKey = fsBlindSpotKey(dictSite)
        dictSeen[sKey] = dictSeen.get(sKey, 0) + 1
    return sorted(sKey for sKey, iCount in dictSeen.items() if iCount > 1)


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
    listAcquisitions = _flistMergeAcquisitions(dictExisting)
    return {
        "iAcquisitionCount": len(listAcquisitions),
        "listAcquisitions": listAcquisitions,
        "iUnresolvedSiteCount": len(listUnresolved),
        "listUnresolvedSites": listUnresolved,
        "sPurpose": (
            "Every acquisition of a declared capability in vaibify/, and "
            "every container-mutation call site, one entry each. "
            "Machine-derived identity; reviewer-classified meaning. See "
            "tools/generateMutationInventory.py."
        ),
        "iRowCount": len(listMerged),
        "listRows": listMerged,
    }


def _flistMergeAcquisitions(dictExisting):
    """Carry recorded dispositions onto a fresh acquisition scan.

    A disposition survives only while BOTH fingerprints hold. The scope
    hash is included deliberately: a reviewer writing ``guarded`` on an
    ``import subprocess`` was ruling on what the enclosing scope does
    with it, so an edit there has to send the ruling back for re-reading.
    """
    dictRecorded = {
        fsAcquisitionKey(acquisition): acquisition
        for acquisition in dictExisting.get("listAcquisitions", [])
    }
    listMerged = []
    for dictAcquisition in flistAcquisitions():
        dictPrevious = dictRecorded.get(fsAcquisitionKey(dictAcquisition))
        if dictPrevious is not None and all(
            dictPrevious.get(sField) == dictAcquisition[sField]
            for sField in ("sFingerprint", "sScopeFingerprint")
        ):
            for sField in TUPLE_ACQUISITION_REVIEWER_FIELDS:
                dictAcquisition[sField] = dictPrevious.get(
                    sField, S_UNCLASSIFIED,
                )
        listMerged.append(dictAcquisition)
    return listMerged


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
