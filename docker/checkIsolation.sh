#!/usr/bin/env bash
#
# checkIsolation.sh
# Verifies that a Docker container's filesystem is walled off from the host.
# Run from inside the container.

set -euo pipefail

iExitCode=0

fnPrintHeader() {
    echo ""
    echo "========================================"
    echo "  $1"
    echo "========================================"
}

fbMountIsSafePath() {
    local sMountPoint="$1"
    local sSafePath
    for sSafePath in "/proc" "/dev" "/sys" "/etc/resolv.conf" \
                     "/etc/hostname" "/etc/hosts" "/tmp/.X11-unix"; do
        if [[ "$sMountPoint" == "$sSafePath" || "$sMountPoint" == "$sSafePath"/* ]]; then
            return 0
        fi
    done
    return 1
}

fnCheckSecretMount() {
    local sMountPoint="$1"
    local sLine="$2"
    local sReadOnly
    sReadOnly=$(echo "$sLine" | awk '{print $6}')
    if [[ "$sReadOnly" == ro,* || "$sReadOnly" == ro ]]; then
        echo "  [OK] $sMountPoint — read-only secret"
    else
        echo "  [WARN] $sMountPoint — secret mount is read-write"
        iExitCode=1
    fi
}

fnClassifyMount() {
    local sLine="$1"
    local sMountPoint sFilesystemType sSource
    sMountPoint=$(echo "$sLine" | awk '{print $5}')
    sFilesystemType=$(echo "$sLine" | awk '{for(i=1;i<=NF;i++) if($i=="-") print $(i+1)}')
    sSource=$(echo "$sLine" | awk '{print $4}')
    fbMountIsSafePath "$sMountPoint" && return 0
    [[ "$sFilesystemType" == "overlay" || "$sFilesystemType" == "tmpfs" ]] && return 0
    [[ "$sSource" == /docker/containers/* ]] && return 0
    if [[ "$sSource" == /docker/volumes/* || "$sSource" == /var/lib/docker/* || "$sSource" == /var/lib/containerd/* ]]; then
        echo "  [OK] $sMountPoint — Docker named volume (isolated from host)"
    elif [[ "$sMountPoint" == /run/secrets/* ]]; then
        fnCheckSecretMount "$sMountPoint" "$sLine"
    else
        echo "  [FAIL] $sMountPoint — possible host bind mount (source: $sSource, type: $sFilesystemType)"
        return 1
    fi
}

fnCheckBindMounts() {
    fnPrintHeader "Filesystem Bind Mounts"
    local bFoundBindMount=false
    while IFS= read -r sLine; do
        if ! fnClassifyMount "$sLine"; then
            bFoundBindMount=true
            iExitCode=1
        fi
    done < /proc/1/mountinfo
    if ! $bFoundBindMount; then
        echo "  [OK] No host bind mounts detected"
    fi
}

fsGetListeningOutput() {
    if command -v ss > /dev/null 2>&1; then
        ss -tlnp 2>/dev/null || true
    elif command -v netstat > /dev/null 2>&1; then
        netstat -tlnp 2>/dev/null || true
    fi
}

fnPrintPortWarning() {
    local sListening="$1"
    local iPortCount="$2"
    echo "  [INFO] $iPortCount port(s) listening inside the container:"
    echo "$sListening" | grep "LISTEN" | awk '{print "         " $0}'
    echo ""
    echo "  Note: Listening ports are only reachable from the host if"
    echo "  the container was started with -p flags. Run this on the"
    echo "  host to check: docker port <container_name>"
}

fnCheckNetworkPorts() {
    fnPrintHeader "Network: Listening Ports"
    local sListening
    sListening=$(fsGetListeningOutput)
    if [ -z "$sListening" ]; then
        echo "  [WARN] Neither ss nor netstat available; cannot check listening ports"
        return
    fi
    local iPortCount
    iPortCount=$(echo "$sListening" | grep -c "LISTEN" || true)
    if [[ "$iPortCount" -eq 0 ]]; then
        echo "  [OK] No ports listening inside the container"
    else
        fnPrintPortWarning "$sListening" "$iPortCount"
    fi
}

fnCheckDockerSocket() {
    fnPrintHeader "Docker Socket Access"

    if [[ -S /var/run/docker.sock ]]; then
        echo "  [FAIL] Docker socket is mounted — container can control the host's Docker daemon"
        iExitCode=1
    else
        echo "  [OK] Docker socket is not accessible"
    fi
}

fnCheckPrivilegedMode() {
    fnPrintHeader "Privileged Mode"

    # In privileged mode, the container can see all host devices
    local iDeviceCount
    iDeviceCount=$(find /dev/ -maxdepth 1 -mindepth 1 2>/dev/null | wc -l)

    # Privileged containers typically expose 50+ devices; unprivileged ~15
    if [[ "$iDeviceCount" -gt 40 ]]; then
        echo "  [WARN] $iDeviceCount devices in /dev — container may be running in privileged mode"
        iExitCode=1
    else
        echo "  [OK] $iDeviceCount devices in /dev — consistent with unprivileged mode"
    fi
}

fnPrintSummary() {
    fnPrintHeader "Summary"

    if [[ "$iExitCode" -eq 0 ]]; then
        echo "  All checks passed. Container appears isolated from the host."
    else
        echo "  One or more checks flagged a concern. Review the output above."
    fi
    echo ""
}

echo "Container Isolation Check"
echo "Running from: $(hostname) as $(whoami)"
echo "Date: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

fnCheckBindMounts
fnCheckNetworkPorts
fnCheckDockerSocket
fnCheckPrivilegedMode
fnPrintSummary

exit "$iExitCode"
