"""System-package name validation tests (F-B-10)."""

from types import SimpleNamespace

import pytest

from vaibify.cli.commandBuild import (
    fnValidateSystemPackageNames,
    fnWriteSystemPackages,
)


def test_validate_well_formed_package_names_passes():
    """Standard apt names are accepted."""
    fnValidateSystemPackageNames([
        "build-essential", "libssl-dev", "python3.11", "g++",
        "ca-certificates", "tzdata",
    ])


def test_validate_empty_list_passes():
    """An empty package list raises nothing."""
    fnValidateSystemPackageNames([])
    fnValidateSystemPackageNames(None)


def test_validate_rejects_command_injection():
    """A name with a shell metacharacter is rejected."""
    with pytest.raises(ValueError) as excInfo:
        fnValidateSystemPackageNames(["bash;rm"])
    assert "bash;rm" in str(excInfo.value)


def test_validate_rejects_uppercase_name():
    """An uppercase name violates the apt schema and is rejected."""
    with pytest.raises(ValueError):
        fnValidateSystemPackageNames(["Bash"])


def test_validate_rejects_leading_dash():
    """A name starting with '-' is rejected."""
    with pytest.raises(ValueError):
        fnValidateSystemPackageNames(["-rf"])


def test_validate_rejects_empty_name():
    """An empty entry is rejected."""
    with pytest.raises(ValueError):
        fnValidateSystemPackageNames([""])


def test_fnWriteSystemPackages_raises_on_bad_name(tmp_path):
    """The writer surfaces validation errors before opening the file."""
    config = SimpleNamespace(
        listSystemPackages=["good-pkg", "BAD;NAME"],
    )
    with pytest.raises(ValueError):
        fnWriteSystemPackages(config, str(tmp_path))


def test_fnWriteSystemPackages_writes_file_when_clean(tmp_path):
    """A clean package list lands on disk as expected."""
    config = SimpleNamespace(listSystemPackages=["build-essential", "git"])
    fnWriteSystemPackages(config, str(tmp_path))
    sPath = tmp_path / "system-packages.txt"
    assert sPath.exists()
    assert "build-essential" in sPath.read_text()
