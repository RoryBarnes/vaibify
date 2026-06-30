"""Mutation-coverage tests for environmentSnapshot.

Each test closes a specific coverage hole found by mutation testing:
container toolchain capture, the non-dict environment.json guard, the
legacy top-level ``sImageDigest`` fallback, the exact-64 image-id-digest
length boundary, and the defensive input-dict copy.
"""

import json

import pytest

from vaibify.reproducibility.environmentSnapshot import (
    _fbIsImageIdDigest,
    fbEnvironmentDigestPinned,
    fdictCaptureSystemTools,
    fdictReadEnvironmentJson,
    fnWriteEnvironmentJson,
)


_S_SIXTY_FOUR_HEX = "a" * 64


class _FakeContainerAdapter:
    """Container-rooted adapter: no local root, non-empty sRootPath."""

    def __init__(self, dictResponses):
        self.sRootPath = "/workspace/project"
        self._dictResponses = dictResponses
        self.listRunCalls = []

    def fsLocalRootOrNone(self):
        return None

    def ftRunCommand(self, saCommand, fTimeoutSeconds):
        self.listRunCalls.append(list(saCommand))
        return self._dictResponses[saCommand[0]]


# ------------------------------------------------------------------
# Hole 1: container interpreter/compiler/os-release capture
# ------------------------------------------------------------------


def test_container_system_tools_capture_records_adapter_values():
    """The container branch records the container's toolchain identity.

    The python, gcc, and os-release bytes are what determine bit-level
    reproducibility because the workflow runs inside the container. A
    mutant that emits None (or drops gcc/os-release) must be caught.
    """
    filesFake = _FakeContainerAdapter({
        "python3": (0, "3.11.7 (main, Jan 1 2026)\n", ""),
        "gcc": (0, "gcc (Debian 12.2.0) 12.2.0\nmore detail\n", ""),
        "cat": (0, 'NAME="Debian GNU/Linux"\nVERSION_ID="12"\n', ""),
    })

    dictResult = fdictCaptureSystemTools(filesFake)

    assert dictResult["sPython"] == "3.11.7 (main, Jan 1 2026)"
    assert dictResult["sGcc"] == "gcc (Debian 12.2.0) 12.2.0"
    assert dictResult["sOsRelease"] == (
        'NAME="Debian GNU/Linux"\nVERSION_ID="12"\n'
    )
    assert dictResult["sLibc"] is None
    listFirstArgs = [saCommand[0] for saCommand in filesFake.listRunCalls]
    assert listFirstArgs == ["python3", "gcc", "cat"]


def test_container_system_tools_gcc_and_osrelease_failure_yield_none():
    """Nonzero gcc/os-release probes become None; python still captured."""
    filesFake = _FakeContainerAdapter({
        "python3": (0, "3.10.13\n", ""),
        "gcc": (127, "", "gcc: not found"),
        "cat": (1, "", "cat: /etc/os-release: No such file"),
    })

    dictResult = fdictCaptureSystemTools(filesFake)

    assert dictResult["sPython"] == "3.10.13"
    assert dictResult["sGcc"] is None
    assert dictResult["sOsRelease"] is None


# ------------------------------------------------------------------
# Hole 2: non-dict environment.json guard
# ------------------------------------------------------------------


def _fnWriteEnvironmentPayload(pathRepo, payload):
    """Write a raw JSON payload to ``<repo>/.vaibify/environment.json``."""
    pathDir = pathRepo / ".vaibify"
    pathDir.mkdir(parents=True, exist_ok=True)
    (pathDir / "environment.json").write_text(json.dumps(payload))


@pytest.mark.parametrize("payload", [[1, 2, 3], "oops"])
def test_non_dict_environment_json_returns_none_without_crash(
    tmp_path, payload,
):
    """A top-level array or scalar reads as None, never AttributeError."""
    _fnWriteEnvironmentPayload(tmp_path, payload)

    assert fdictReadEnvironmentJson(str(tmp_path)) is None
    assert fbEnvironmentDigestPinned(str(tmp_path)) is False


# ------------------------------------------------------------------
# Hole 3: legacy top-level sImageDigest fallback
# ------------------------------------------------------------------


def test_top_level_registry_digest_pins(tmp_path):
    """A legacy top-level ``image@sha256:<64 hex>`` counts as pinned."""
    _fnWriteEnvironmentPayload(tmp_path, {
        "sImageDigest": "vaibify@sha256:" + _S_SIXTY_FOUR_HEX,
    })

    assert fbEnvironmentDigestPinned(str(tmp_path)) is True


def test_top_level_local_image_id_pins(tmp_path):
    """A legacy top-level ``sha256:<64 hex>`` image ID counts as pinned."""
    _fnWriteEnvironmentPayload(tmp_path, {
        "sImageDigest": "sha256:" + _S_SIXTY_FOUR_HEX,
    })

    assert fbEnvironmentDigestPinned(str(tmp_path)) is True


# ------------------------------------------------------------------
# Hole 4: exact-64 image-id-digest length boundary
# ------------------------------------------------------------------


def test_over_long_image_id_is_not_pinned(tmp_path):
    """A 65-hex sha256 string is garbage and must NOT pin the gate."""
    _fnWriteEnvironmentPayload(tmp_path, {
        "dictContainer": {"sImageDigest": "sha256:" + "a" * 65},
    })

    assert fbEnvironmentDigestPinned(str(tmp_path)) is False


def test_image_id_digest_length_boundary_is_exactly_64():
    """``_fbIsImageIdDigest`` accepts exactly 64 hex, rejects 65."""
    assert _fbIsImageIdDigest("sha256:" + "a" * 64) is True
    assert _fbIsImageIdDigest("sha256:" + "a" * 65) is False


# ------------------------------------------------------------------
# Hole 5: defensive input-dict copy
# ------------------------------------------------------------------


def test_write_environment_json_does_not_mutate_caller_dict(tmp_path):
    """The caller's dict is never contaminated with annotation keys."""
    dictInput = {"sImageDigest": "image@sha256:deadbeef"}
    setKeysBefore = set(dictInput.keys())

    fnWriteEnvironmentJson(str(tmp_path), dictInput)

    assert "sTimestamp" not in dictInput
    assert "sSchemaVersion" not in dictInput
    assert set(dictInput.keys()) == setKeysBefore
