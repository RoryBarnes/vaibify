"""Tests for uncovered lines in vaibify.gui.routeContext."""

import os

import pytest

from vaibify.config import registryManager
from vaibify.gui.routeContext import (
    RouteContext,
    fdictStampDockerIdForJournal,
)


class TestStampDockerIdForJournal:
    """The file-write payload stamp is mode-aware (host-mode plan §5)."""

    @pytest.fixture(autouse=True)
    def fixtureIsolateRegistry(self, tmp_path, monkeypatch):
        sRegistryDir = str(tmp_path / ".vaibify")
        monkeypatch.setattr(
            registryManager, "_S_REGISTRY_DIRECTORY", sRegistryDir,
        )
        monkeypatch.setattr(
            registryManager, "_S_REGISTRY_PATH",
            os.path.join(sRegistryDir, "registry.json"),
        )
        monkeypatch.setattr(
            registryManager, "_S_LOCK_PATH",
            os.path.join(sRegistryDir, "registry.lock"),
        )

    def test_container_resource_keeps_the_docker_stamp(self):
        assert fdictStampDockerIdForJournal("abc123def") == {
            "sDockerContainerId": "abc123def",
        }

    def test_host_resource_omits_the_key_entirely(self):
        registryManager.fnSaveRegistry({"listProjects": [{
            "sName": "my-host-proj", "sDirectory": "/tmp/x",
            "sMode": "host",
        }]})
        assert fdictStampDockerIdForJournal("my-host-proj") == {}

    def test_registered_container_project_still_stamps(self):
        registryManager.fnSaveRegistry({"listProjects": [{
            "sName": "my-container-proj", "sDirectory": "/tmp/y",
        }]})
        assert fdictStampDockerIdForJournal("my-container-proj") == {
            "sDockerContainerId": "my-container-proj",
        }


def _fdictBuildRawContext():
    """Return a minimal raw context dict for testing."""
    return {
        "docker": "mockDockerConnection",
        "workflows": {"cid1": {"sName": "wf"}},
        "paths": {"cid1": "/workspace/wf.json"},
        "terminals": {"sess1": "termObj"},
        "containerUsers": {"cid1": "rory"},
        "pipelineTasks": {"cid1": "taskObj"},
        "sSessionToken": "tok123",
        "require": lambda: True,
        "save": lambda sCid, dictWf: f"saved-{sCid}",
        "variables": lambda sCid: {"sUser": "rory"},
        "workflowDir": lambda sCid: f"/workspace/{sCid}",
    }


class TestTypedPropertyAccess:
    """Cover every @property and method accessor (lines 30-81)."""

    def test_docker(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert ctx.connectionDocker == "mockDockerConnection"

    def test_workflows(self):
        ctx = RouteContext(_fdictBuildRawContext())
        dictWorkflows = ctx.dictWorkflows
        assert "cid1" in dictWorkflows

    def test_paths(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert ctx.dictPaths["cid1"] == "/workspace/wf.json"

    def test_terminals(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert ctx.dictTerminals["sess1"] == "termObj"

    def test_container_users(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert ctx.dictContainerUsers["cid1"] == "rory"

    def test_pipeline_tasks(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert ctx.dictPipelineTasks["cid1"] == "taskObj"

    def test_session_token(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert ctx.sSessionToken == "tok123"

    def test_session_token_default(self):
        ctx = RouteContext({})
        assert ctx.sSessionToken == ""

    def test_require(self):
        listCalls = []
        dictRaw = _fdictBuildRawContext()
        dictRaw["require"] = lambda: listCalls.append("required")
        ctx = RouteContext(dictRaw)
        assert ctx.fnRequireDocker() is None
        assert listCalls == ["required"]

    def test_save(self):
        listCalls = []
        dictRaw = _fdictBuildRawContext()
        dictRaw["save"] = (
            lambda sContainerId, dictWorkflow:
            listCalls.append((sContainerId, dictWorkflow))
        )
        ctx = RouteContext(dictRaw)
        assert ctx.fnSaveWorkflow("cid1", {"sName": "wf"}) is None
        assert listCalls == [("cid1", {"sName": "wf"})]

    def test_variables(self):
        ctx = RouteContext(_fdictBuildRawContext())
        dictVars = ctx.fdictGetVariables("cid1")
        assert dictVars["sUser"] == "rory"

    def test_workflow_dir(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert ctx.fsGetWorkflowDirectory("cid1") == "/workspace/cid1"


class TestDictCompatibleAccess:
    """Cover dict-protocol methods (lines 85-107)."""

    def test_getitem(self):
        ctx = RouteContext({"sKey": "val"})
        assert ctx["sKey"] == "val"

    def test_setitem(self):
        ctx = RouteContext({})
        ctx["sNew"] = 42
        assert ctx["sNew"] == 42

    def test_contains_true(self):
        ctx = RouteContext({"sKey": "val"})
        assert "sKey" in ctx

    def test_contains_false(self):
        ctx = RouteContext({})
        assert "sMissing" not in ctx

    def test_delitem(self):
        ctx = RouteContext({"sKey": "val"})
        del ctx["sKey"]
        assert "sKey" not in ctx

    def test_get_existing(self):
        ctx = RouteContext({"sKey": "val"})
        assert ctx.get("sKey") == "val"

    def test_get_missing_default(self):
        ctx = RouteContext({})
        assert ctx.get("sMissing", "fallback") == "fallback"

    def test_setdefault_missing(self):
        ctx = RouteContext({})
        sResult = ctx.setdefault("sKey", "default")
        assert sResult == "default"
        assert ctx["sKey"] == "default"

    def test_setdefault_existing(self):
        ctx = RouteContext({"sKey": "existing"})
        sResult = ctx.setdefault("sKey", "other")
        assert sResult == "existing"

    def test_pop_existing(self):
        ctx = RouteContext({"sKey": "val"})
        sResult = ctx.pop("sKey")
        assert sResult == "val"
        assert "sKey" not in ctx

    def test_pop_missing_with_default(self):
        ctx = RouteContext({})
        sResult = ctx.pop("sMissing", "fallback")
        assert sResult == "fallback"

    def test_pop_missing_raises(self):
        ctx = RouteContext({})
        with pytest.raises(KeyError):
            ctx.pop("sMissing")
