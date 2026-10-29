"""The small hardening items: a process ceiling and constant-time token checks.

Source: ``vaibify/config/projectConfig.py`` (``pidsLimit``),
``vaibify/docker/containerManager.py`` (``--pids-limit``),
``vaibify/gui/pipelineServer.py`` and ``vaibify/gui/containerOwnership.py``
(token comparisons).

A token compared with ``==`` leaks how many leading bytes matched
through the comparison's duration; ``secrets.compare_digest`` is the
standard library's constant-time comparison and the one every token
check here goes through. The structural test reads the two functions'
source so a comparison written back as ``==`` fails here, not in an
audit.
"""

import ast
import inspect

import pytest
from types import SimpleNamespace

from vaibify.config import projectConfig
from vaibify.config.projectConfig import (
    I_DEFAULT_PIDS_LIMIT, _fbValidateResourceLimits, fconfigFromYamlDict,
)
from vaibify.docker.containerManager import _fnAddPidsLimit
from vaibify.gui import containerOwnership, pipelineServer


def testThePidLimitDefaultsAndIsConfigurable():
    assert I_DEFAULT_PIDS_LIMIT == 16384
    assert fconfigFromYamlDict({"projectName": "demo"}).iPidsLimit == 16384
    assert fconfigFromYamlDict(
        {"projectName": "demo", "pidsLimit": 4096},
    ).iPidsLimit == 4096
    assert projectConfig._fdictConfigToYamlDict(
        fconfigFromYamlDict({"projectName": "demo", "pidsLimit": 4096}),
    )["pidsLimit"] == 4096


@pytest.mark.falsification
def testThePidLimitMustBeAPositiveInteger():
    """Zero, a negative, a float, a string and a bool are all refused.

    Kills: ``_fbValidatePidsLimit`` accepting any integer.
    """
    assert _fbValidateResourceLimits({"pidsLimit": 1})
    assert _fbValidateResourceLimits({})
    for objBad in (0, -1, 1.5, "many", True):
        assert not _fbValidateResourceLimits({"pidsLimit": objBad}), objBad


def testTheRunArgumentsCarryThePidLimit():
    saRunArgs = []
    _fnAddPidsLimit(SimpleNamespace(iPidsLimit=2048), saRunArgs)
    assert saRunArgs == ["--pids-limit", "2048"]
    saRunArgs = []
    _fnAddPidsLimit(SimpleNamespace(), saRunArgs)
    assert saRunArgs == ["--pids-limit", str(I_DEFAULT_PIDS_LIMIT)]


def _flistEqualityComparisonsWith(fnFunction, setNames):
    """Return the ``==`` comparisons in a function that touch ``setNames``."""
    treeAst = ast.parse(inspect.getsource(fnFunction))
    listFound = []
    for node in ast.walk(treeAst):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, ast.Eq) for op in node.ops):
            continue
        for nodeSide in [node.left] + list(node.comparators):
            sName = getattr(nodeSide, "id", None) or getattr(nodeSide, "attr", None)
            if sName in setNames:
                listFound.append(ast.dump(node))
    return listFound


def testTokenComparisonsAreConstantTime():
    """No ``==`` touches a token; both checks go through ``compare_digest``."""
    assert _flistEqualityComparisonsWith(
        pipelineServer.fbHasAgentToken,
        {"sHeaderToken", "sQueryToken", "sExpectedToken"},
    ) == []
    assert "compare_digest" in inspect.getsource(pipelineServer.fbHasAgentToken)
    fnLookup = containerOwnership.frecordOwnerAuthorizedByAgentToken
    assert _flistEqualityComparisonsWith(
        fnLookup, {"sAgentToken", "sPresentedToken"},
    ) == []
    assert "compare_digest" in inspect.getsource(fnLookup)
