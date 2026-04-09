"""Tests for uncovered lines in vaibify.gui.routeContext."""

import pytest

from vaibify.gui.routeContext import RouteContext


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
        "setAllowedContainers": {"cid1", "cid2"},
        "require": lambda: True,
        "save": lambda sCid, dictWf: f"saved-{sCid}",
        "variables": lambda sCid: {"sUser": "rory"},
        "workflowDir": lambda sCid: f"/workspace/{sCid}",
    }


class TestTypedPropertyAccess:
    """Cover every @property and method accessor (lines 30-81)."""

    def test_docker(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert ctx.docker == "mockDockerConnection"

    def test_workflows(self):
        ctx = RouteContext(_fdictBuildRawContext())
        dictWorkflows = ctx.workflows
        assert "cid1" in dictWorkflows

    def test_paths(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert ctx.paths["cid1"] == "/workspace/wf.json"

    def test_terminals(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert ctx.terminals["sess1"] == "termObj"

    def test_container_users(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert ctx.containerUsers["cid1"] == "rory"

    def test_pipeline_tasks(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert ctx.pipelineTasks["cid1"] == "taskObj"

    def test_session_token(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert ctx.sSessionToken == "tok123"

    def test_session_token_default(self):
        ctx = RouteContext({})
        assert ctx.sSessionToken == ""

    def test_set_allowed_containers(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert "cid1" in ctx.setAllowedContainers

    def test_set_allowed_containers_default(self):
        ctx = RouteContext({})
        assert ctx.setAllowedContainers == set()

    def test_require(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert ctx.require() is True

    def test_save(self):
        ctx = RouteContext(_fdictBuildRawContext())
        sResult = ctx.save("cid1", {"sName": "wf"})
        assert sResult == "saved-cid1"

    def test_variables(self):
        ctx = RouteContext(_fdictBuildRawContext())
        dictVars = ctx.variables("cid1")
        assert dictVars["sUser"] == "rory"

    def test_workflow_dir(self):
        ctx = RouteContext(_fdictBuildRawContext())
        assert ctx.workflowDir("cid1") == "/workspace/cid1"


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
