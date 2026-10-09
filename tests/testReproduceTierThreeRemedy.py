"""A failed Tier 3 pull must name the command that can still get the image.

Tier 3 of `vaibify reproduce` pulls the pinned image from a registry
and nowhere else. A project whose image lives only in its environment
archive fails there with Docker's error alone, although
`vaibify reproduce --from ... --prepare` would obtain the same image
through the archived deposit.
"""

import subprocess

from vaibify.cli import commandReproduce


def testFailedPullNamesTheArchiveRoute(tmp_path, monkeypatch, capsys):
    pathEnvironment = tmp_path / ".vaibify" / "environment.json"
    pathEnvironment.parent.mkdir()
    pathEnvironment.write_text("{}")
    monkeypatch.setattr(
        commandReproduce, "_fsLoadImageDigest",
        lambda pathFile, sRepo: "example/image@sha256:" + "0" * 64,
    )
    monkeypatch.setattr(
        commandReproduce.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 1, stdout="", stderr="manifest unknown",
        ),
    )
    assert commandReproduce.fbVerifyTier3(str(tmp_path)) is False
    sOutput = capsys.readouterr().out
    assert "manifest unknown" in sOutput
    assert "--prepare" in sOutput
    assert "archived deposit" in sOutput
