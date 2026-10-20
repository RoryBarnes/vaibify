"""A Click test runner that keeps stderr apart, on every Click version.

Click 8.2 removed ``CliRunner(mix_stderr=...)`` and always separates the
two streams; earlier versions need ``mix_stderr=False`` to do so. The
CI matrix installs both, so a test that asserts on stderr builds its
runner here.
"""

from click.testing import CliRunner


__all__ = ["frunnerSeparatingStreams"]


def frunnerSeparatingStreams():
    """Return a CliRunner whose result keeps stderr apart from stdout."""
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()
