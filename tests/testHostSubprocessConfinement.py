"""Host-mode subprocess launch is confined to the host gateway.

Host mode replaces the Docker substrate with subprocess execution on
the host, implemented by ``vaibify/host/hostConnection.py`` (under
construction by a parallel workstream; this file is its guardrail,
landed first so the rule is in force from the first host module). The
Docker leg has exactly this shape of invariant already:
``testTheGatewayIsTheOnlyModuleThatCallsExecRun`` keeps ``exec_run``
inside ``dockerConnection.py`` because a caller past the gateway is
past every check by construction. The host leg's primitive is the
subprocess launch itself, so the confinement is over ACQUISITION of a
subprocess-launching capability — an import or an attribute load, the
thing a scan reads exactly — never over decoding what a launch runs.

TWO NETS, and this file is only the first. The mutation inventory
(``tools/generateMutationInventory.py`` scanning the whole ``vaibify``
package into ``tests/mutationInventory.json``) fails closed the moment
``vaibify/host/`` appears with subprocess acquisitions: every one
becomes an inventory row that ``--check`` reports as drift until it is
written and classified. This file is the narrower, structural
statement — the acquisitions may exist ONLY in the gateway module —
which the inventory, a record rather than a rule, cannot make.
"""

import ast
import pathlib

import pytest


PATH_REPOSITORY = pathlib.Path(__file__).resolve().parent.parent
PATH_HOST_PACKAGE = PATH_REPOSITORY / "vaibify" / "host"

# The one module under vaibify/host/ allowed to acquire a
# subprocess-launching capability.
S_HOST_GATEWAY_MODULE = "hostConnection.py"

# The subprocess-launching vocabulary, mirroring the mutation
# inventory's process-launch capability where the host leg meets it:
# modules whose import IS the acquisition, os's process-creating
# surface (a prefix rule, because every ``exec*`` and ``spawn*`` in
# os replaces or forks the process), and asyncio's two subprocess
# constructors.
SET_PROCESS_LAUNCH_MODULES = frozenset({"subprocess", "pty"})
SET_OS_PROCESS_MEMBERS = frozenset({
    "system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp",
})
TUPLE_OS_PROCESS_MEMBER_PREFIXES = ("exec", "spawn")
SET_ASYNCIO_PROCESS_MEMBERS = frozenset({
    "create_subprocess_exec", "create_subprocess_shell",
})


def _fbNamesOsProcessMember(sMember):
    """Return True when sMember is os's process-creating surface."""
    return sMember in SET_OS_PROCESS_MEMBERS or sMember.startswith(
        TUPLE_OS_PROCESS_MEMBER_PREFIXES
    )


def _fdictMapImportedModuleAliases(treeModule):
    """Return {bound name: root module} for every plain import."""
    dictAliases = {}
    for node in ast.walk(treeModule):
        if not isinstance(node, ast.Import):
            continue
        for aliasImported in node.names:
            sRoot = aliasImported.name.split(".")[0]
            sBound = aliasImported.asname or sRoot
            dictAliases[sBound] = sRoot
    return dictAliases


def _flistScanImportAcquisitions(treeModule):
    """Return (iLine, sDescription) for import-statement acquisitions."""
    listFound = []
    for node in ast.walk(treeModule):
        if isinstance(node, ast.Import):
            for aliasImported in node.names:
                sRoot = aliasImported.name.split(".")[0]
                if sRoot in SET_PROCESS_LAUNCH_MODULES:
                    listFound.append(
                        (node.lineno, f"import {aliasImported.name}")
                    )
        elif isinstance(node, ast.ImportFrom) and not node.level:
            sModule = node.module or ""
            sRoot = sModule.split(".")[0]
            for aliasImported in node.names:
                bDangerous = (
                    sRoot in SET_PROCESS_LAUNCH_MODULES
                    or (sModule == "os"
                        and _fbNamesOsProcessMember(aliasImported.name))
                    or (sModule == "asyncio"
                        and aliasImported.name
                        in SET_ASYNCIO_PROCESS_MEMBERS)
                )
                if bDangerous:
                    listFound.append((
                        node.lineno,
                        f"from {sModule} import {aliasImported.name}",
                    ))
    return listFound


def _flistScanAttributeAcquisitions(treeModule, dictAliases):
    """Return (iLine, sDescription) for attribute-load acquisitions.

    Members are resolved through the module each name is actually
    bound to, so ``import os as operatingSystem`` followed by
    ``operatingSystem.execvp`` is still os.execvp — matching on the
    spelling alone is the classification-by-spelling defect the
    mutation inventory's scanner already fixed once.
    """
    listFound = []
    for node in ast.walk(treeModule):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Name):
            continue
        sRoot = dictAliases.get(node.value.id, "")
        bDangerous = (
            (sRoot == "os" and _fbNamesOsProcessMember(node.attr))
            or (sRoot == "asyncio"
                and node.attr in SET_ASYNCIO_PROCESS_MEMBERS)
        )
        if bDangerous:
            listFound.append(
                (node.lineno, f"{node.value.id}.{node.attr}")
            )
    return listFound


def flistScanSubprocessAcquisitions(sSource):
    """Return every (iLine, sDescription) subprocess acquisition."""
    treeModule = ast.parse(sSource)
    dictAliases = _fdictMapImportedModuleAliases(treeModule)
    return sorted(
        _flistScanImportAcquisitions(treeModule)
        + _flistScanAttributeAcquisitions(treeModule, dictAliases)
    )


def flistCollectHostConfinementViolations(pathHostPackage):
    """Return every acquisition outside the gateway under one root."""
    listViolations = []
    for pathModule in sorted(pathHostPackage.rglob("*.py")):
        if "__pycache__" in pathModule.parts:
            continue
        if pathModule.name == S_HOST_GATEWAY_MODULE:
            continue
        sRelative = str(pathModule.relative_to(pathHostPackage))
        for iLine, sDescription in flistScanSubprocessAcquisitions(
            pathModule.read_text(encoding="utf-8"),
        ):
            listViolations.append(f"{sRelative}:{iLine}: {sDescription}")
    return listViolations


def testHostSubprocessLaunchIsConfinedToTheHostGateway():
    """No module under vaibify/host/ but the gateway launches processes.

    The host leg's analogue of
    ``testTheGatewayIsTheOnlyModuleThatCallsExecRun``: a helper module
    that acquired ``subprocess`` itself would sit past whatever checks
    the host gateway grows, by construction. Passes vacuously while
    ``vaibify/host/`` does not exist — a PASS, not a skip, because the
    rule is in force from the first host module and a skip would
    report "not run" for a rule that is holding. The moment a second
    host module imports a launcher, this fails.

    Second net, verified when this invariant landed:
    ``tools/generateMutationInventory.py --check`` was confirmed clean,
    and its scan covers the whole ``vaibify`` package — so every
    subprocess acquisition a future ``vaibify/host/`` module makes also
    lands in ``tests/mutationInventory.json`` as drift until written
    and classified (see
    ``testTheInventoryScanRootCoversTheHostTree``).
    """
    if not PATH_HOST_PACKAGE.is_dir():
        # vaibify/host/ has not landed yet; the confinement holds
        # vacuously over an empty module set.
        return
    listViolations = flistCollectHostConfinementViolations(
        PATH_HOST_PACKAGE,
    )
    assert listViolations == [], (
        f"these vaibify/host/ modules acquire a subprocess-launching "
        f"capability outside {S_HOST_GATEWAY_MODULE}, bypassing the "
        f"host gateway's checks by construction: {listViolations}. "
        f"Route the launch through the gateway instead."
    )


@pytest.mark.falsification
def testHostConfinementScannerDetectsEachAcquisitionShape(tmp_path):
    """The scanner sees every acquisition shape, driven over a real tree.

    The confinement test passes vacuously today (``vaibify/host/`` does
    not exist), so a broken scanner would look exactly like a healthy
    one there. This drives the SAME walker over a synthetic host tree
    carrying one acquisition of each declared shape — module import,
    aliased module import, from-import of an os process member, an
    aliased os attribute load, an asyncio subprocess constructor, and
    ``pty.spawn`` — plus a gateway module whose own ``subprocess``
    import must NOT be flagged.

    Kills: making flistCollectHostConfinementViolations treat every
    module as the exempt gateway (the scan skipping the host tree's
    files).
    """
    pathHost = tmp_path / "host"
    pathHost.mkdir()
    (pathHost / "hostConnection.py").write_text(
        "import subprocess\n",
        encoding="utf-8",
    )
    (pathHost / "sneakyHelper.py").write_text(
        "import subprocess as launcher\n"
        "from os import execvp\n"
        "from asyncio import create_subprocess_shell\n"
        "import os as operatingSystem\n"
        "import asyncio\n"
        "import pty\n"
        "\n"
        "def fnLaunchEverything(sCommand):\n"
        "    operatingSystem.spawnlp(0, sCommand, sCommand)\n"
        "    asyncio.create_subprocess_exec(sCommand)\n"
        "    operatingSystem.getcwd()\n",
        encoding="utf-8",
    )
    listViolations = flistCollectHostConfinementViolations(pathHost)
    listExpectedFragments = [
        "import subprocess",
        "from os import execvp",
        "from asyncio import create_subprocess_shell",
        "import pty",
        "operatingSystem.spawnlp",
        "asyncio.create_subprocess_exec",
    ]
    for sFragment in listExpectedFragments:
        assert any(
            sFragment in sViolation for sViolation in listViolations
        ), (
            f"the scanner missed the acquisition shape {sFragment!r}; "
            f"it reported only {listViolations}"
        )
    assert not any(
        "hostConnection.py" in sViolation for sViolation in listViolations
    ), "the gateway's own acquisition was flagged as a violation"
    assert not any(
        "getcwd" in sViolation for sViolation in listViolations
    ), "a harmless os attribute was flagged; the vocabulary is closed"


def testTheInventoryScanRootCoversTheHostTree():
    """The mutation inventory's scan cannot structurally exclude host/.

    The docstring claim that the inventory is the second net is only
    true while its scan root is the whole package. This resolves the
    generator's root and fails if ``vaibify/host/`` could ever sit
    outside it.
    """
    import importlib.util
    pathTool = (
        PATH_REPOSITORY / "tools" / "generateMutationInventory.py"
    )
    specTool = importlib.util.spec_from_file_location(
        "generateMutationInventoryForHostConfinement", pathTool,
    )
    moduleTool = importlib.util.module_from_spec(specTool)
    specTool.loader.exec_module(moduleTool)
    assert moduleTool.PATH_PACKAGE == PATH_HOST_PACKAGE.parent, (
        "the inventory scans a root that does not contain "
        "vaibify/host/, so host acquisitions would escape the record"
    )
