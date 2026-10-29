"""Where a project's Zenodo tokens live: host keyring slots, keyed by project identity.

A Zenodo token used to live in the CONTAINER keyring, which the
in-container agent can read and write. Since 2026-10-29 it lives only on
the host, in the researcher's own OS keyring, in one of two kinds of slot:

- **Active**: ``zenodo_token_<instance>:<sProjectUuid>``. Token-bearing
  actions read ONLY active slots. A token the researcher enters on the
  host goes straight to its active slot; entering it is the confirmation.
- **Pending**: ``<containerSlot>:<sProjectUuid>:pending``, with the
  legacy ``zenodo_token`` slot spelled ``zenodo_token_unassigned``.
  Pending slots are written only by the migration out of the container
  and read only by the confirmation flow. Because the slot is separate,
  a migrated value is never usable until the researcher promotes it, on
  every crash and restart path, by construction rather than by a flag.

The project identity is the registry's random ``sProjectUuid``
(:mod:`vaibify.config.registryManager`), so a project the agent renames
keeps its tokens and a project it invents gets none.

Every write here is followed by a read-back, and only an equal read-back
is reported as stored: a locked macOS keychain, or a backend that
accepts writes and drops them, must never be mistaken for a token that
landed.
"""

__all__ = [
    "LIST_CONTAINER_ZENODO_SLOTS",
    "S_UNASSIGNED_INSTANCE",
    "T_ZENODO_INSTANCES",
    "TokenStoreError",
    "TokenStoreUnreadable",
    "fsActiveSlot",
    "fsPendingSlot",
    "fsInstanceOfContainerSlot",
    "fsReadSlot",
    "fnStoreSlotVerified",
    "fnDeleteSlot",
    "fsReadActiveToken",
    "fnStoreActiveToken",
    "fnDeleteActiveToken",
    "fbHasActiveToken",
    "flistPendingSlotsFor",
]

T_ZENODO_INSTANCES = ("sandbox", "production")
S_UNASSIGNED_INSTANCE = "unassigned"
S_LEGACY_CONTAINER_SLOT = "zenodo_token"

# Every slot a container keyring may hold a Zenodo token in: the two
# instance slots, the pre-namespaced legacy slot, and the two snapshot
# slots the old connect flow copied a working token into before a new
# one overwrote it. The migration finishes each of them.
LIST_CONTAINER_ZENODO_SLOTS = [
    "zenodo_token_sandbox",
    "zenodo_token_production",
    S_LEGACY_CONTAINER_SLOT,
    "zenodo_token_sandbox_backup",
    "zenodo_token_production_backup",
]


class TokenStoreError(RuntimeError):
    """The host keyring did not durably store what it was given."""


class TokenStoreUnreadable(RuntimeError):
    """The host keyring could not be read; absence was NOT established."""


def _fnValidateInstance(sInstance):
    if sInstance not in T_ZENODO_INSTANCES:
        raise ValueError(
            f"{sInstance!r} is not a Zenodo instance; the instances are "
            f"{list(T_ZENODO_INSTANCES)}"
        )


def _fnValidateProjectUuid(sProjectUuid):
    if not sProjectUuid or not isinstance(sProjectUuid, str) or (
        not sProjectUuid.replace("-", "").isalnum()
    ):
        raise ValueError(f"not a project identity: {sProjectUuid!r}")


def fsActiveSlot(sInstance, sProjectUuid):
    """Return the ACTIVE slot name: the one token-bearing actions read."""
    _fnValidateInstance(sInstance)
    _fnValidateProjectUuid(sProjectUuid)
    return f"zenodo_token_{sInstance}:{sProjectUuid}"


def fsPendingSlot(sContainerSlot, sProjectUuid):
    """Return the PENDING slot a migrated container slot is staged into."""
    if sContainerSlot not in LIST_CONTAINER_ZENODO_SLOTS:
        raise ValueError(
            f"{sContainerSlot!r} is not a container Zenodo slot; they are "
            f"{LIST_CONTAINER_ZENODO_SLOTS}"
        )
    _fnValidateProjectUuid(sProjectUuid)
    sStem = (
        f"zenodo_token_{S_UNASSIGNED_INSTANCE}"
        if sContainerSlot == S_LEGACY_CONTAINER_SLOT else sContainerSlot
    )
    return f"{sStem}:{sProjectUuid}:pending"


def fsInstanceOfContainerSlot(sContainerSlot):
    """Return the instance a container slot names, or '' for the legacy slot."""
    if sContainerSlot not in LIST_CONTAINER_ZENODO_SLOTS:
        raise ValueError(f"{sContainerSlot!r} is not a container Zenodo slot")
    for sInstance in T_ZENODO_INSTANCES:
        if sContainerSlot.startswith(f"zenodo_token_{sInstance}"):
            return sInstance
    return ""


def fsReadSlot(sSlot):
    """Return the slot's value, or '' when the keyring holds none.

    Only an absent entry answers ''. A keyring that cannot be read (a
    locked keychain, a missing backend) raises
    :class:`TokenStoreUnreadable`, because "could not check" must never
    be reported as "no token stored": the researcher would be told to
    add a token they may already have.
    """
    from vaibify.config.secretManager import fsRetrieveSecret
    try:
        return fsRetrieveSecret(sSlot, "keyring") or ""
    except KeyError:
        return ""
    except Exception as error:  # noqa: BLE001 -- the backend's own words are the reason
        raise TokenStoreUnreadable(
            f"the host keyring could not be read for slot {sSlot!r}: "
            f"{type(error).__name__}: {error}"
        ) from error


def fnStoreSlotVerified(sSlot, sValue):
    """Write the slot and read it back; raise unless the two agree.

    A keyring that raises, or one that accepts the write and answers
    the read with something else, is reported as a failure to store,
    never as a token that landed.
    """
    from vaibify.config.secretManager import fnStoreSecret
    if not sValue:
        raise ValueError("a Zenodo token slot is never written empty")
    try:
        fnStoreSecret(sSlot, sValue, "keyring")
        sReadBack = fsReadSlot(sSlot)
    except Exception as error:  # noqa: BLE001 -- the backend's own words are the reason
        raise TokenStoreError(
            f"the host keyring did not store slot {sSlot!r}: "
            f"{type(error).__name__}: {error}"
        ) from error
    if sReadBack != sValue:
        raise TokenStoreError(
            f"the host keyring read back a different value for slot "
            f"{sSlot!r} than was written; nothing is treated as stored"
        )


def fnDeleteSlot(sSlot):
    """Remove the slot; an absent slot is not an error."""
    from vaibify.config.secretManager import fnDeleteSecret
    fnDeleteSecret(sSlot, "keyring")


def fsReadActiveToken(sInstance, sProjectUuid):
    """Return the project's ACTIVE token for an instance, or ''.

    Pending slots are never consulted here: a token the researcher has
    not confirmed is not theirs to spend.
    """
    return fsReadSlot(fsActiveSlot(sInstance, sProjectUuid))


def fnStoreActiveToken(sInstance, sProjectUuid, sToken):
    """Write the active slot, verified by read-back."""
    fnStoreSlotVerified(fsActiveSlot(sInstance, sProjectUuid), sToken)


def fnDeleteActiveToken(sInstance, sProjectUuid):
    """Remove the project's active token for an instance."""
    fnDeleteSlot(fsActiveSlot(sInstance, sProjectUuid))


def fbHasActiveToken(sInstance, sProjectUuid):
    """Return True when an active token is stored for the instance."""
    return bool(fsReadActiveToken(sInstance, sProjectUuid))


def flistPendingSlotsFor(sProjectUuid):
    """Return the (containerSlot, pendingSlot) pairs that hold a value."""
    return [
        (sContainerSlot, fsPendingSlot(sContainerSlot, sProjectUuid))
        for sContainerSlot in LIST_CONTAINER_ZENODO_SLOTS
        if fsReadSlot(fsPendingSlot(sContainerSlot, sProjectUuid))
    ]
