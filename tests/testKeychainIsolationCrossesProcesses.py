"""No test process, parent or child, may reach the real OS keyring.

``fixtureHermeticKeyring`` in ``tests/conftest.py`` patches
``secretManager._fmoduleLoadKeyring`` through ``monkeypatch``, which
reaches the pytest process and nothing it spawns. Over a hundred test
files spawn subprocesses, so the guarantee its docstring states --
"no test can read, overwrite, or delete the researcher's real stored
credentials" -- held in-process and failed one process boundary away.

Observed rather than theorised: a suite run on macOS raised four
keychain approval dialogs for service ``vaibify``
(researcher-reported, 2026-09-04). The reads are the visible half. A
child reaching ``_fnDeleteKeyringEntry`` would delete a working
credential and the suite would report nothing at all.

The backend a child selects is asserted here because it IS the
mechanism: with the null backend a child cannot prompt, cannot read,
and cannot destroy, whatever code path it takes.
"""

import subprocess
import sys

import pytest

from vaibify.config import secretManager


_S_REPORT_BACKEND_PROGRAM = (
    "import keyring; print(type(keyring.get_keyring()).__module__)"
)


def _fsChildKeyringBackendModule():
    """Return the keyring backend module name a child process selects."""
    processResult = subprocess.run(
        [sys.executable, "-c", _S_REPORT_BACKEND_PROGRAM],
        capture_output=True, text=True,
    )
    assert processResult.returncode == 0, processResult.stderr
    return processResult.stdout.strip()


@pytest.mark.falsification
def test_a_child_process_cannot_reach_the_real_keyring():
    """A spawned process gets the null backend, not the host keychain.

    Asserted on the child's own report rather than on the environment
    variable: reading back what the parent exported would pass against
    a child that ignored it.

    Kills: deleting the ``PYTHON_KEYRING_BACKEND`` export from
    ``tests/conftest.py``, so a spawned process selects the host's
    real keyring backend again.
    """
    sBackendModule = _fsChildKeyringBackendModule()
    assert sBackendModule == "keyring.backends.null", (
        "a child process selected the backend "
        f"{sBackendModule!r}; on the researcher's machine that is the "
        "real keychain, which prompts on read and destroys on delete"
    )


def test_the_in_process_fake_still_answers_after_the_child_fix():
    """The env var must not have displaced the in-process fake.

    The two halves cover different processes and neither implies the
    other: a null backend in this process would answer every read with
    None, quietly breaking the tests that seed a credential.
    """
    secretManager.fnStoreSecret("probeName", "probeValue", "keyring")
    assert secretManager.fsRetrieveSecret("probeName", "keyring") == (
        "probeValue"
    )
