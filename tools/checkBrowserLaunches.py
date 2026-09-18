"""Answer whether a browser engine actually starts, and say why not.

The browser lane installs Chromium without running ``apt-get``, because
the runner image already ships the shared libraries Chromium links
against and the package mirror it would otherwise reach has repeatedly
been unreachable. That is a reasonable belief about an image, not a
proof, and an unproven belief in a setup step is how a lane comes to
fail twenty tests for one missing ``.so``.

So this exits 0 only after a real browser process has started and
stopped. The lane runs it, and on a non-zero exit fetches the OS
dependencies and runs it again. Nothing here is a substitute for the
lane itself -- launching a browser says nothing about whether the
dashboard works.

Prints the launch error rather than a traceback, because the reader is
looking at a CI log and the useful part of a Playwright launch failure
is the missing-library list it prints itself.

The engine is an argument because the lane now drives three of them and
each links against different OS libraries: WebKit in particular needs
packages the runner image does not ship, so "Chromium starts" is no
evidence at all about the engine actually under test.
"""

import sys

S_DEFAULT_ENGINE = "chromium"
T_SUPPORTED_ENGINES = ("chromium", "firefox", "webkit")


def fbBrowserLaunches(sEngine):
    """Return True when the engine starts and stops cleanly."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as errorImport:
        print(f"playwright is not importable: {errorImport}")
        return False
    try:
        with sync_playwright() as playwrightRunning:
            browserUnderTest = getattr(playwrightRunning, sEngine).launch()
            sVersion = browserUnderTest.version
            browserUnderTest.close()
    except Exception as errorLaunch:
        print(f"{sEngine} did not launch: {errorLaunch}")
        return False
    print(f"{sEngine} {sVersion} launched and closed cleanly.")
    return True


def main():
    """Return a process exit code: 0 when the named engine launched."""
    sEngine = sys.argv[1] if len(sys.argv) > 1 else S_DEFAULT_ENGINE
    if sEngine not in T_SUPPORTED_ENGINES:
        print(
            f"unknown engine {sEngine!r}; expected one of "
            f"{', '.join(T_SUPPORTED_ENGINES)}"
        )
        return 2
    return 0 if fbBrowserLaunches(sEngine) else 1


if __name__ == "__main__":
    sys.exit(main())
