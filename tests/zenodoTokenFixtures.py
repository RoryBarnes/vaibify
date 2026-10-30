"""Let a route test that is about something else have host-side Zenodo slots.

Since 2026-10-29 a Zenodo token lives only in the HOST keyring, in an
ACTIVE slot keyed by the registry's project identity
(``vaibify/config/zenodoTokenSlots.py``); the container keyring holds
none and no route reads one there. The tests of the deposits, the
promotion and the credential setup predate that and are about THEIR
behaviour; this fixture gives every container those tests serve one
registered identity and a way to seed its slots in the suite's
hermetic keyring, so they keep asserting what they always asserted.
It is imported explicitly by the modules that need it, never applied
globally; ``tests/testZenodoTokenSlots.py`` and
``tests/testZenodoTokenMigration.py`` test the slots and the move with
the real identity resolution.
"""

import pytest

from vaibify.config import zenodoTokenSlots
from vaibify.gui import zenodoTokenAccess

S_TEST_PROJECT_UUID = "0123456789abcdef0123456789abcdef"


class HostZenodoSlots:
    """Seed and read one project's host slots inside the hermetic keyring."""

    def __init__(self, keyringFake):
        self.keyringFake = keyringFake

    def fnStoreActive(self, sInstance, sToken):
        zenodoTokenSlots.fnStoreActiveToken(
            sInstance, S_TEST_PROJECT_UUID, sToken,
        )

    def fsActive(self, sInstance):
        return zenodoTokenSlots.fsReadActiveToken(sInstance, S_TEST_PROJECT_UUID)

    def fnStorePending(self, sContainerSlot, sToken):
        zenodoTokenSlots.fnStoreSlotVerified(
            zenodoTokenSlots.fsPendingSlot(sContainerSlot, S_TEST_PROJECT_UUID),
            sToken,
        )

    def fnBreakEveryRead(self, sReason="keychain locked"):
        """Make the keyring unreadable, as a locked macOS keychain is."""
        def fnRefuse(sService, sName):
            raise RuntimeError(sReason)
        self.keyringFake.get_password = fnRefuse


@pytest.fixture(autouse=True)
def fixtureHostZenodoSlots(monkeypatch, fixtureHermeticKeyring):
    """Every served container is one registered project with host slots."""
    monkeypatch.setattr(
        zenodoTokenAccess, "fsProjectUuidForContainer",
        lambda connectionDocker, sContainerId: S_TEST_PROJECT_UUID,
    )
    return HostZenodoSlots(fixtureHermeticKeyring)
