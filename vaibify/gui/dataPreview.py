"""Preview data files inside a Docker container."""

import posixpath


def fsPreviewDataFile(
    connectionDocker, sContainerId, sFilePath, sDirectory,
):
    """Return a short preview of a data file's contents or structure."""
    sAbsPath = _fsResolvePath(sFilePath, sDirectory)
    sExtension = posixpath.splitext(sAbsPath)[1].lower()
    if sExtension == ".npy":
        return _fsPreviewNpy(connectionDocker, sContainerId, sAbsPath)
    if sExtension in (".h5", ".hdf5"):
        return _fsPreviewHdf5(connectionDocker, sContainerId, sAbsPath)
    return _fsPreviewText(connectionDocker, sContainerId, sAbsPath)


def _fsResolvePath(sFilePath, sDirectory):
    """Return absolute path, joining with directory if relative."""
    if posixpath.isabs(sFilePath):
        return sFilePath
    return posixpath.join(sDirectory, sFilePath)


def _fsPreviewNpy(connectionDocker, sContainerId, sAbsPath):
    """Preview a .npy file with shape, dtype, and summary statistics."""
    sCommand = (
        "python3 -c \""
        "import numpy as np; "
        "d=np.load(" + repr(sAbsPath) + ",allow_pickle=False); "
        "print(f'shape={d.shape} dtype={d.dtype}'); "
        "f=d.flatten(); "
        "print(f'first={f[0]!r} last={f[-1]!r}'); "
        "print(f'min={f.min()!r} max={f.max()!r} mean={f.mean()!r}')"
        "\""
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    return sOutput.strip() if iExitCode == 0 else "(unreadable)"


def _fsPreviewHdf5(connectionDocker, sContainerId, sAbsPath):
    """Preview an HDF5 file's datasets with shape and summary stats."""
    sCommand = (
        "python3 -c \""
        "import h5py, numpy as np; "
        "f=h5py.File(" + repr(sAbsPath) + ",'r'); "
        "items=[]; "
        "f.visititems(lambda n,o: items.append(n) "
        "if isinstance(o,h5py.Dataset) else None); "
        "[print(f'dataset:{n} shape={f[n].shape} dtype={f[n].dtype} "
        "first={np.array(f[n]).flatten()[0]!r} "
        "last={np.array(f[n]).flatten()[-1]!r}') "
        "for n in items[:10]]; "
        "f.close()\""
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    return sOutput.strip() if iExitCode == 0 else "(unreadable)"


def _fsPreviewText(connectionDocker, sContainerId, sAbsPath):
    """Preview first and last lines of a text file."""
    from .pipelineRunner import fsShellQuote
    sQuoted = fsShellQuote(sAbsPath)
    sCommand = (
        f"head -10 {sQuoted} 2>/dev/null;"
        f" echo '...';"
        f" tail -3 {sQuoted} 2>/dev/null"
    )
    iExitCode, sOutput = connectionDocker.ftResultExecuteCommand(
        sContainerId, sCommand
    )
    return sOutput.strip() if iExitCode == 0 else "(unreadable)"
