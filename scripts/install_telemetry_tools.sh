#!/usr/bin/env bash
set -euo pipefail
if [[ $# == 1 && ( "$1" == --help || "$1" == -h ) ]]; then
  echo 'Usage: TOOLS_DIR=/absolute/path bash scripts/install_telemetry_tools.sh node|server'
  exit 0
fi
role="${1:-node}"
if [[ $# -gt 1 || ( "$role" != node && "$role" != server ) ]]; then
  echo "Use node or server" >&2
  exit 2
fi
system="$(uname -s)"
[[ "$system" == Linux ]] || { echo "Unsupported operating system: $system (expected Linux)" >&2; exit 2; }
machine="$(uname -m)"
case "$machine" in
  aarch64|arm64) release_arch=arm64 ;;
  x86_64|amd64) release_arch=amd64 ;;
  *) echo "Unsupported architecture: $machine (expected ARM64 or x86_64)" >&2; exit 2 ;;
esac
: "${TOOLS_DIR:?Set TOOLS_DIR to a local telemetry tools directory}"
tools_dir="$TOOLS_DIR"
for dependency in curl tar unzip sha256sum; do
  command -v "$dependency" >/dev/null || { echo "Required tool not found: $dependency" >&2; exit 2; }
done
mkdir -p "$tools_dir"
cd "$tools_dir"
download() {
  local url="$1" destination="$2"
  # Keep a usable archive intact if a new download is interrupted.
  curl -fL --retry 3 --retry-all-errors --continue-at - --output "$destination.part" "$url"
  mv -f "$destination.part" "$destination"
}
download "https://github.com/prometheus/node_exporter/releases/download/v1.9.1/node_exporter-1.9.1.linux-$release_arch.tar.gz" node_exporter.tar.gz
tar xzf node_exporter.tar.gz
download "https://github.com/prometheus-community/smartctl_exporter/releases/download/v0.14.0/smartctl_exporter-0.14.0.linux-$release_arch.tar.gz" smartctl_exporter.tar.gz
tar xzf smartctl_exporter.tar.gz
if [[ "$role" == server ]]; then
  download "https://github.com/prometheus/prometheus/releases/download/v3.5.0/prometheus-3.5.0.linux-$release_arch.tar.gz" prometheus.tar.gz
  tar xzf prometheus.tar.gz
  download "https://dl.grafana.com/oss/release/grafana-12.1.0.linux-$release_arch.tar.gz" grafana.tar.gz
  tar xzf grafana.tar.gz
  download "https://github.com/grafana/loki/releases/download/v3.7.3/loki-linux-$release_arch.zip" loki.zip
  unzip -qo loki.zip
  chmod 0755 "loki-linux-$release_arch"
else
  download "https://github.com/grafana/alloy/releases/download/v1.19.2/alloy-linux-$release_arch.zip" alloy.zip
  unzip -qo alloy.zip
  chmod 0755 "alloy-linux-$release_arch"
fi
sha256sum ./*.tar.gz ./*.zip > downloaded-archives.sha256
