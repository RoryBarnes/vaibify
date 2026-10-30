"""A scratch root under $HOME, shared by the browser lane and the suite.

Two conftests need a scratch directory the container daemon can see:
the browser lane, whose project-creation path guard admits only
directories beneath the user's home, and the suite-wide redirect of
the ephemeral secret root, whose files a live test mounts into a real
container. On macOS Colima shares only $HOME into its VM, so a bare
system temp directory is invisible to the daemon in both cases.
"""

import pathlib


# A gitignored scratch root INSIDE the checkout. The project-creation
# path guard permits only directories beneath the user's home, and the
# checkout is beneath it — but a bare temp dir in the repo ROOT gets
# git-added by a later bulk commit when a killed run leaves it behind
# (that is how tmpu2uv9b_9/ shipped into history). Confining the temp
# dirs to a gitignored directory keeps that litter uncommittable while
# still exercising the real path guard.
#
# "the checkout is beneath it" is an ASSUMPTION, and the falsification
# harness breaks it: it runs every mutation inside a disposable git
# worktree under the system temp directory, which is not under $HOME on
# macOS. Any journey touching a $HOME-jailed route then fails there for
# an environment reason rather than a real one — which reports a
# falsification as unconfirmed and reads as an undefended guard
# (2026-08-21). So the root follows the checkout when the checkout is
# under $HOME, and otherwise falls back to a directory that is, keeping
# the path guard genuinely exercised in both places.
#
# The fallback sits UNDER ~/.vaibify deliberately, and moving it out
# would silently drop coverage. A host project rooted below a
# ``.vaibify`` ancestor is the shape that exposed
# fsDeriveRepoRootFromDirectory cutting the repo root at the FIRST
# ``.vaibify`` instead of the project's own: every polled path landed
# under an unrelated ancestor and the host path guard refused each one
# as an escape. Nothing but this fallback drove that path, and it found
# the bug on its first CI run.
def fpathHomeVisibleTempRoot():
    """Return a scratch root that is under $HOME in every checkout."""
    pathCheckout = pathlib.Path(__file__).resolve().parents[1]
    pathHome = pathlib.Path.home().resolve()
    if pathCheckout == pathHome or pathHome in pathCheckout.parents:
        return pathCheckout / ".browserLaneTmp"
    return pathHome / ".vaibify" / "browserLaneTmp"
