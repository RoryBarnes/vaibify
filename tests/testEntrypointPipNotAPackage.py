"""fnPipInstall says "nothing to install" instead of "pip failed".

Two repositories with no ``setup.py`` or ``pyproject.toml`` were
written as ``pip_editable`` by the wizard, and every container start
reported ``pip-install: pip install -e failed`` for each: a symptom
with no remedy. Sources entrypoint.sh in a subshell with a fake pip on
PATH, the pattern of testEntrypointBuildBinary.py.
"""

import os
import subprocess

import pytest


_S_ENTRYPOINT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "vaibify", "containerImage",
    "entrypoint.sh",
))


def _fsRunPipInstall(tmp_path, bWithProjectFile):
    sRepo = tmp_path / "repo"
    sRepo.mkdir()
    if bWithProjectFile:
        (sRepo / "pyproject.toml").write_text("[project]\nname='x'\n")
    sFakeBin = tmp_path / "bin"
    sFakeBin.mkdir()
    (sFakeBin / "pip").write_text("#!/bin/bash\necho PIP-CALLED\nexit 0\n")
    os.chmod(sFakeBin / "pip", 0o755)
    (tmp_path / ".vaibify").mkdir()
    sScript = (
        "set +e\n"
        f"WORKSPACE={tmp_path}\nexport WORKSPACE\n"
        f"PATH={sFakeBin}:$PATH\nexport PATH\n"
        f"source {_S_ENTRYPOINT}\n"
        f'fnPipInstall "{sRepo}" "repo"\n'
        'printf "WARNING-LINE:%s\\n" "${saStartupWarnings[@]}"\n'
    )
    return subprocess.run(
        ["bash", "-c", sScript], capture_output=True, text=True,
    ).stdout


@pytest.mark.falsification
def test_a_repository_with_nothing_to_install_is_said_so_and_pip_is_not_run(
    tmp_path,
):
    """Kills: dropping the project-file check, under which pip is run
    and its refusal is reported as a failed install."""
    sOutput = _fsRunPipInstall(tmp_path, bWithProjectFile=False)
    assert "PIP-CALLED" not in sOutput
    assert "WARNING-LINE:repo: pip-not-a-package:" in sOutput
    assert "installMethod: reference" in sOutput


def test_a_repository_with_a_project_file_is_pip_installed(tmp_path):
    sOutput = _fsRunPipInstall(tmp_path, bWithProjectFile=True)
    assert "PIP-CALLED" in sOutput
    assert "pip-not-a-package" not in sOutput
