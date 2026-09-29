"""The agents' layers are checked against the environment they stand on.

Every rule is driven through REAL layer tarballs built in memory and
read by the same reader ``docker save`` output goes through, so a test
cannot pass on a dictionary the reader would never produce. Each rule
has a case that must be caught, and the passing case is the shape real
agent installers produce: this module found no violation in the
Claude, Codex, Antigravity, OpenCode and Node overlays stacked on a
real image (2026-09-28), and a file-level diff of the Gemini, Cline,
Pi and OpenHands overlays the same day found only additions.
"""

import gzip
import hashlib
import io
import json
import tarfile

import pytest

from vaibify.reproducibility.agentLayerSeparation import (
    fdictReadLayerEntries,
    fdictReadSavedLayers,
    flistFindSeparationViolations,
)


S_HOME = "/home/researcher"
DICT_BASE_CONFIG = {
    "Env": [
        f"PATH={S_HOME}/.local/bin:/opt/conda/bin:/usr/local/bin:/usr/bin:/bin",
        "LANG=C.UTF-8",
    ],
    "Entrypoint": ["/usr/local/bin/entrypoint.sh"], "Cmd": ["/bin/bash"],
    "User": "researcher", "WorkingDir": "/workspace",
}


def _fbaBuildLayer(listMembers):
    """Return a layer tarball: ``(sPath, sKind, baContent, iMode)`` members."""
    bufferLayer = io.BytesIO()
    with tarfile.open(fileobj=bufferLayer, mode="w") as tarLayer:
        for sPath, sKind, baContent, iMode in listMembers:
            infoMember = tarfile.TarInfo(sPath.lstrip("/"))
            infoMember.mode = iMode
            infoMember.uid = infoMember.gid = 1000 if sPath.startswith(S_HOME) else 0
            if sKind == "directory":
                infoMember.type = tarfile.DIRTYPE
                tarLayer.addfile(infoMember)
            else:
                infoMember.size = len(baContent)
                tarLayer.addfile(infoMember, io.BytesIO(baContent))
    return bufferLayer.getvalue()


def _fdictLayer(listMembers):
    return fdictReadLayerEntries(io.BytesIO(_fbaBuildLayer(listMembers)))


def _ffile(sPath, baContent=b"x", iMode=0o755):
    return (sPath, "file", baContent, iMode)


def _fdirectory(sPath, iMode=0o755):
    return (sPath, "directory", b"", iMode)


BA_BASHRC = b"# ~/.bashrc\nalias ll='ls -l'\n"


def _flistBaseLayers():
    return [
        _fdictLayer([
            _fdirectory("/usr/bin"), _ffile("/usr/bin/python3"),
            _ffile("/usr/bin/uv"), _fdirectory("/opt/conda/bin"),
            _ffile("/opt/conda/bin/python"),
            _fdirectory("/opt/conda/lib/python3.12/site-packages"),
            _fdirectory(S_HOME), _fdirectory(S_HOME + "/.local/bin"),
            _ffile(S_HOME + "/.bashrc", BA_BASHRC, 0o644),
            _fdirectory("/usr/lib/aarch64-linux-gnu"),
        ]),
    ]


def _flistViolations(listAgentMembers, dictAgentConfig=None):
    return flistFindSeparationViolations(
        _flistBaseLayers(), [_fdictLayer(listAgentMembers)],
        DICT_BASE_CONFIG, dictAgentConfig or DICT_BASE_CONFIG,
    )


def _flistRules(listViolations):
    return sorted({dictViolation["sRule"] for dictViolation in listViolations})


def test_what_real_agent_installers_do_passes():
    listViolations = _flistViolations([
        _fdirectory(S_HOME), _fdirectory(S_HOME + "/.local/bin"),
        _ffile(S_HOME + "/.local/bin/claude"),
        _ffile(S_HOME + "/.claude.json", b"{}", 0o600),
        _fdirectory(S_HOME + "/.local/share/uv/tools/openhands/lib/python3.12/site-packages"),
        _ffile(S_HOME + "/.local/share/uv/tools/openhands/lib/python3.12/site-packages/a.py"),
        _ffile("/usr/bin/node"), _ffile("/usr/lib/node_modules/npm/index.js"),
        _ffile("/var/lib/dpkg/status", b"changed", 0o644),
        _ffile("/var/log/apt/history.log", b"log", 0o644),
        _ffile("/tmp/cc-socks", b"", 0o600),
        _ffile(S_HOME + "/.bashrc", BA_BASHRC + (
            b"\n# Added by an installer\n"
            b'export PATH="/home/researcher/.local/bin:$PATH"\n'
            b"export PATH=$HOME/.opencode/bin:$PATH\n"
        ), 0o644),
        _ffile(S_HOME + "/.opencode/bin/opencode"),
    ], dict(DICT_BASE_CONFIG, Env=[
        f"PATH={S_HOME}/.local/bin:{S_HOME}/.local/bin:/opt/conda/bin:"
        "/usr/local/bin:/usr/bin:/bin",
        "LANG=C.UTF-8", "NPM_CONFIG_PREFIX=/home/researcher/.local",
    ]))
    assert listViolations == []


@pytest.mark.falsification
def test_overwriting_or_removing_an_environment_file_is_caught():
    """Rule 1. Kills: skipping paths the environment already holds."""
    assert _flistRules(_flistViolations([
        _ffile("/usr/bin/python3", b"a different interpreter"),
    ])) == ["overwrites"]
    assert _flistRules(_flistViolations([
        ("/opt/conda/bin/.wh.python", "file", b"", 0o644),
    ])) == ["removes"]
    assert _flistRules(_flistViolations([
        _fdirectory("/opt/conda/bin", 0o777),
    ])) == ["overwrites"]


@pytest.mark.falsification
def test_a_command_the_environment_already_runs_is_caught():
    """Rule 2: ``~/.local/bin`` comes first, so an agent's ``uv`` wins.

    Kills: dropping the shadowed-command rule.
    """
    listViolations = _flistViolations([_ffile(S_HOME + "/.local/bin/uv")])
    assert _flistRules(listViolations) == ["shadows"]
    assert _flistRules(_flistViolations([
        _ffile(S_HOME + "/.opencode/bin/python"),
        _ffile(S_HOME + "/.bashrc", BA_BASHRC + (
            b"export PATH=$HOME/.opencode/bin:$PATH\n"
        ), 0o644),
    ])) == ["shadows"], "a directory a startup file prepends is searched too"


@pytest.mark.falsification
def test_adding_where_programs_search_is_caught():
    """Rule 3. Kills: dropping the searched-location rule."""
    for sPath in (
        "/opt/conda/lib/python3.12/site-packages/zz.pth",
        "/usr/lib/aarch64-linux-gnu/libblas.so.3",
        "/etc/profile.d/agent.sh",
        "/etc/ld.so.preload",
        S_HOME + "/.config/matplotlib/matplotlibrc",
        "/usr/share/fonts/agent/font.ttf",
    ):
        assert _flistRules(_flistViolations([_ffile(sPath)])) == ["searched"], sPath


@pytest.mark.falsification
def test_a_startup_file_may_only_gain_path_prepends():
    """Rule 4. Kills: accepting any appended line."""
    assert _flistRules(_flistViolations([
        _ffile(S_HOME + "/.bashrc", BA_BASHRC + (
            b"export LD_LIBRARY_PATH=/opt/agent/lib\n"
        ), 0o644),
    ])) == ["startup"]
    assert _flistRules(_flistViolations([
        _ffile(S_HOME + "/.bashrc", b"# rewritten from scratch\n", 0o644),
    ])) == ["startup"]


@pytest.mark.falsification
def test_the_images_settings_may_only_gain_path_directories():
    """Rule 5. Kills: skipping the environment-variable comparison."""
    assert _flistRules(_flistViolations([], dict(
        DICT_BASE_CONFIG, Env=DICT_BASE_CONFIG["Env"] + ["OMP_NUM_THREADS=1"],
    ))) == ["settings"]
    assert _flistRules(_flistViolations([], dict(
        DICT_BASE_CONFIG, Env=["PATH=/usr/bin:/opt/conda/bin", "LANG=C.UTF-8"],
    ))) == ["settings"], "reordering PATH changes which program runs"
    assert _flistRules(_flistViolations([], dict(
        DICT_BASE_CONFIG, Entrypoint=["/agent.sh"],
    ))) == ["settings"]


def _fbaSaveArchive(listLayerBytes, bCompress):
    """Return a docker-save-shaped archive holding the given layer blobs."""
    bufferArchive = io.BytesIO()
    with tarfile.open(fileobj=bufferArchive, mode="w") as tarArchive:
        for baLayer in listLayerBytes + [json.dumps({"config": 1}).encode()]:
            baBlob = gzip.compress(baLayer) if bCompress else baLayer
            infoBlob = tarfile.TarInfo(
                "blobs/sha256/" + hashlib.sha256(baBlob).hexdigest(),
            )
            infoBlob.size = len(baBlob)
            tarArchive.addfile(infoBlob, io.BytesIO(baBlob))
    return bufferArchive.getvalue()


@pytest.mark.parametrize("bCompress", [False, True])
def test_layers_are_named_by_their_uncompressed_digest(bCompress):
    """Blobs are identified by what ``docker image inspect`` lists.

    Includes an EMPTY layer: gzipped it is a few dozen bytes, and a
    size threshold that skipped small blobs left real images reported
    as unreadable (measured).
    """
    baFull = _fbaBuildLayer([_ffile("/usr/bin/node")])
    baEmpty = _fbaBuildLayer([])
    setWanted = {
        "sha256:" + hashlib.sha256(ba).hexdigest() for ba in (baFull, baEmpty)
    }
    dictLayers = fdictReadSavedLayers(
        io.BytesIO(_fbaSaveArchive([baFull, baEmpty], bCompress)), setWanted,
    )
    assert set(dictLayers) == setWanted
    assert "/usr/bin/node" in dictLayers[
        "sha256:" + hashlib.sha256(baFull).hexdigest()
    ]


# ------------------------------------------------------------------
# The deposit-time gate
# ------------------------------------------------------------------


S_ENVIRONMENT_ID = "sha256:" + "e" * 64
S_AGENTS_ID = "sha256:" + "a" * 64
DICT_AGENTS_ON_THE_PIN = {
    "sImageDigest": S_AGENTS_ID, "sImageId": S_AGENTS_ID,
    "sEnvironmentImageDigest": S_ENVIRONMENT_ID,
    "sEnvironmentImageId": S_ENVIRONMENT_ID,
}


def _fnPatchTheImages(monkeypatch, sPinnedLabel, listViolations):
    from vaibify.reproducibility import agentLayerSeparation, environmentSnapshot
    listChecked = []
    monkeypatch.setattr(
        environmentSnapshot, "_flistReadOverlaysLabel",
        lambda sImage: [s for s in sPinnedLabel.split(",") if s],
    )

    def fdictCheck(sEnvironment, sAgents):
        listChecked.append((sEnvironment, sAgents))
        return {"listViolations": listViolations, "iAgentLayers": 1, "iAddedPaths": 9}
    monkeypatch.setattr(
        agentLayerSeparation, "fdictCheckAgentLayerSeparation", fdictCheck,
    )
    return listChecked


@pytest.mark.falsification
def test_a_pin_that_holds_agents_is_never_deposited(monkeypatch):
    """An image whose own label names agents is not the agent-free environment.

    Kills: skipping the pinned image's own label.
    """
    from vaibify.reproducibility.agentLayerSeparation import (
        AgentLayerSeparationError, fdictVerifyAgentsLeaveThePinAlone,
    )
    _fnPatchTheImages(monkeypatch, "jupyter,claude", [])
    with pytest.raises(AgentLayerSeparationError, match="contains coding agents"):
        fdictVerifyAgentsLeaveThePinAlone(
            S_ENVIRONMENT_ID, {"sImageId": S_ENVIRONMENT_ID},
        )


@pytest.mark.falsification
def test_agents_that_reach_the_environment_stop_the_deposit(monkeypatch):
    """The layer check runs on the agents above the pin, and a violation refuses.

    Kills: depositing whatever the layer check found.
    """
    from vaibify.reproducibility.agentLayerSeparation import (
        AgentLayerSeparationError, fdictVerifyAgentsLeaveThePinAlone,
    )
    listChecked = _fnPatchTheImages(monkeypatch, "jupyter", [
        {"sRule": "shadows", "sPath": "/usr/local/bin/uv", "sDetail": "d"},
    ])
    with pytest.raises(AgentLayerSeparationError, match="/usr/local/bin/uv"):
        fdictVerifyAgentsLeaveThePinAlone(S_ENVIRONMENT_ID, DICT_AGENTS_ON_THE_PIN)
    assert listChecked == [(S_ENVIRONMENT_ID, S_AGENTS_ID)]


def test_the_gate_names_each_relation(monkeypatch):
    from vaibify.reproducibility.agentLayerSeparation import (
        fdictVerifyAgentsLeaveThePinAlone,
    )
    listChecked = _fnPatchTheImages(monkeypatch, "", [])
    assert fdictVerifyAgentsLeaveThePinAlone(
        S_ENVIRONMENT_ID, DICT_AGENTS_ON_THE_PIN,
    ) == {"sRelation": "agents-above", "iAddedPaths": 9}
    assert fdictVerifyAgentsLeaveThePinAlone(
        S_AGENTS_ID, DICT_AGENTS_ON_THE_PIN,
    )["sRelation"] == "same"
    assert fdictVerifyAgentsLeaveThePinAlone(
        "sha256:" + "0" * 64, DICT_AGENTS_ON_THE_PIN,
    )["sRelation"] == "unrelated"
    assert len(listChecked) == 1, "only agents above the pin are checked"


@pytest.mark.falsification
def test_the_deposit_refuses_before_saving_anything(monkeypatch, tmp_path):
    """The gate runs before ``docker save``, on the real deposit function.

    Kills: dropping the gate from the deposit.
    """
    from vaibify.gui.routes import environmentArchiveRoutes
    from vaibify.reproducibility import agentLayerSeparation, environmentSnapshot
    listDeposited = []
    monkeypatch.setattr(
        environmentArchiveRoutes.imageDeposit, "fsResolveDepositScratchDirectory",
        lambda: str(tmp_path),
    )
    monkeypatch.setattr(
        environmentArchiveRoutes.imageDeposit, "fdictDepositImageArchive",
        lambda *aArgs, **dictKwargs: listDeposited.append(aArgs) or {},
    )
    monkeypatch.setattr(
        environmentSnapshot, "fdictCaptureLiveImageIdentity",
        lambda sContainer: DICT_AGENTS_ON_THE_PIN,
    )
    _fnPatchTheImages(monkeypatch, "", [
        {"sRule": "overwrites", "sPath": "/usr/bin/python3", "sDetail": "d"},
    ])
    with pytest.raises(agentLayerSeparation.AgentLayerSeparationError):
        environmentArchiveRoutes._fdictDepositSynchronously(
            "cid", {},
            {"sImageDigest": S_ENVIRONMENT_ID, "sArchitecture": "arm64"},
            {"sZenodoService": "sandbox", "dictParentArchive": {}},
            "token", None,
        )
    assert listDeposited == []


def test_the_deposit_names_the_agents_it_leaves_out():
    from vaibify.gui.routes import environmentArchiveRoutes
    dictMetadata = environmentArchiveRoutes._fdictBuildArchiveDepositMetadata(
        {"sWorkflowName": "w"},
        {"listAgentOverlays": ["claude", "codex"]},
        {"dictAiProvenance": {"dictAgentCliVersions": {"claude": "2.1.0"}}},
    )
    assert "claude (2.1.0), codex" in dictMetadata["sDescription"]
    assert "not part of it" in dictMetadata["sDescription"]
    assert "coding agents" not in environmentArchiveRoutes.\
        _fdictBuildArchiveDepositMetadata({"sWorkflowName": "w"})["sDescription"]
