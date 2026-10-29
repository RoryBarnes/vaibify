"""After the migration, the container keyring holds no Zenodo slot.

Real-daemon half of ``tests/testZenodoTokenMigration.py``. A throwaway
container gets the image's keyring shape (``keyring`` with the
``keyrings.alt`` plaintext backend, the container user's own data
directory) and the container USER plants a token in the sandbox slot,
as the old connect flow did. The migration then runs against the real
adapter: the typed read as the container user, the delete exec, and
the read-back that confirms the copy is gone. The host side is the
suite's hermetic keyring, so what is asserted about the host is which
slot received the value, and what is asserted about the container is
that none of its five Zenodo slots holds anything afterwards.

Live-daemon convention (``testDockerConnectionLive``): skipped without
a daemon unless ``VAIBIFY_REQUIRE_DOCKER_DAEMON`` demands one.
"""

import os
import secrets

import pytest

from tests.liveContainerLabels import fdictLabels
from tests.testDockerConnectionLive import fnRequireDaemonReachable
from vaibify.config import zenodoMigrationJournal as journal
from vaibify.config import zenodoTokenSlots as slots
from vaibify.gui import zenodoTokenAccess, zenodoTokenMigration
from vaibify.reproducibility import zenodoClient

pytestmark = pytest.mark.docker_live

S_IMAGE = "python:3.10-slim"
S_USER = "researcher"
S_HOME = "/home/" + S_USER
S_UUID = "e" * 32
S_PLANTED_TOKEN = "planted-" + secrets.token_hex(6)

_S_SETUP_AS_ROOT = f"""
set -e
useradd -m -u 1000 {S_USER} && chmod 750 {S_HOME}
pip install --quiet --no-cache-dir keyring keyrings.alt
"""
_S_PLANT_AS_USER = f"""
set -e
mkdir -p {S_HOME}/.config/python_keyring {S_HOME}/.local/share/python_keyring
printf '[backend]\\ndefault-keyring=keyrings.alt.file.PlaintextKeyring\\n' \
    > {S_HOME}/.config/python_keyring/keyringrc.cfg
python3 -c "import keyring; keyring.set_password('vaibify', \
'zenodo_token_sandbox', '{S_PLANTED_TOKEN}')"
"""


@pytest.fixture(scope="module")
def tLiveContainer():
    """Yield (container, connection) with a token planted by the container user."""
    fnRequireDaemonReachable()
    import docker
    from vaibify.docker.dockerConnection import (
        DockerConnection, _fnEnsureDockerHost,
    )
    _fnEnsureDockerHost()
    clientDocker = docker.from_env()
    try:
        clientDocker.images.get(S_IMAGE)
    except docker.errors.ImageNotFound:
        clientDocker.images.pull(S_IMAGE)
    container = clientDocker.containers.run(
        S_IMAGE, ["sleep", "900"], detach=True, labels=fdictLabels(),
        name=f"vaibifyZenodoMigration{secrets.token_hex(4)}",
        environment={
            "PYTHON_KEYRING_BACKEND": "keyrings.alt.file.PlaintextKeyring",
        },
    )
    try:
        for sSetup, sAsUser in (
            (_S_SETUP_AS_ROOT, "root"), (_S_PLANT_AS_USER, S_USER),
        ):
            iExit, baOutput = container.exec_run(
                ["sh", "-c", sSetup], user=sAsUser,
            )
            assert iExit == 0, (sSetup, baOutput)
        yield container, DockerConnection()
    finally:
        container.remove(force=True)


def _fsReadSlotInContainer(container, sSlot):
    iExit, baOutput = container.exec_run(
        ["python3", "-c",
         "import keyring; print(keyring.get_password('vaibify', "
         f"{sSlot!r}) or '')"],
        user=S_USER,
    )
    assert iExit == 0, baOutput
    return baOutput.decode("utf-8").strip()


def testTheContainerKeyringHoldsNoZenodoSlotAfterTheMigration(
    tLiveContainer, tmp_path, monkeypatch, fixtureHermeticKeyring,
):
    container, connection = tLiveContainer
    assert _fsReadSlotInContainer(container, "zenodo_token_sandbox") == (
        S_PLANTED_TOKEN
    ), "the plant must be live for the removal to mean anything"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        zenodoTokenAccess, "fsProjectUuidForContainer",
        lambda connectionDocker, sContainerId: S_UUID,
    )
    monkeypatch.setattr(
        zenodoTokenMigration.zenodoClient, "fdictProbeToken",
        lambda sService, sToken: {
            "sOutcome": zenodoClient.S_PROBE_UNDETERMINED,
            "sDetail": "offline test", "listDepositTitles": [],
        },
    )
    dictDescribed = zenodoTokenMigration.fdictRunMigrationPass(
        connection, container.id,
    )
    for sSlot in slots.LIST_CONTAINER_ZENODO_SLOTS:
        assert _fsReadSlotInContainer(container, sSlot) == "", sSlot
    assert slots.fsReadSlot(
        slots.fsPendingSlot("zenodo_token_sandbox", S_UUID),
    ) == S_PLANTED_TOKEN
    assert slots.fsReadActiveToken("sandbox", S_UUID) == ""
    assert journal.fdictSlotRecord(S_UUID, "zenodo_token_sandbox")["sState"] == (
        journal.S_STATE_REMOVED
    )
    assert dictDescribed["bAwaitingDecision"] is True
    assert dictDescribed["bRotationAdvised"] is True
    assert S_PLANTED_TOKEN not in str(dictDescribed)
    sJournal = os.path.join(journal.fsMigrationsDirectory(), f"zenodo-{S_UUID}.json")
    with open(sJournal, encoding="utf-8") as fileJournal:
        assert S_PLANTED_TOKEN not in fileJournal.read()
