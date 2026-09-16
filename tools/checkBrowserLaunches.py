"""Answer whether Chromium actually starts, and say why if it does not.

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
"""

import sys


def fbChromiumLaunches():
    """Return True when a Chromium process starts and stops cleanly."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as errorImport:
        print(f"playwright is not importable: {errorImport}")
        return False
    try:
        with sync_playwright() as playwrightRunning:
            browserChromium = playwrightRunning.chromium.launch()
            sVersion = browserChromium.version
            browserChromium.close()
    except Exception as errorLaunch:
        print(f"Chromium did not launch: {errorLaunch}")
        return False
    print(f"Chromium {sVersion} launched and closed cleanly.")
    return True


def main():
    """Return a process exit code: 0 when Chromium launched."""
    return 0 if fbChromiumLaunches() else 1


if __name__ == "__main__":
    sys.exit(main())
