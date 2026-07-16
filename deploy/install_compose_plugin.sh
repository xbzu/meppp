#!/bin/sh
set -eu
umask 077

version="v2.40.3"
plugin_dir="${DOCKER_CLI_PLUGIN_DIR:-/usr/local/lib/docker/cli-plugins}"
target="$plugin_dir/docker-compose"

case "$(uname -m)" in
    x86_64|amd64)
        asset="docker-compose-linux-x86_64"
        expected="dba9d98e1ba5bfe11d88c99b9bd32fc4a0624a30fafe68eea34d61a3e42fd372"
        ;;
    aarch64|arm64)
        asset="docker-compose-linux-aarch64"
        expected="d26373b19e89160546d15407516cc59f453030d9bc5b43ba7faf16f7b4980137"
        ;;
    *)
        echo "unsupported architecture: $(uname -m)" >&2
        exit 1
        ;;
esac

if [ -e "$target" ]; then
    echo "refusing to overwrite existing Docker Compose plugin: $target" >&2
    exit 1
fi

command -v curl >/dev/null
command -v sha256sum >/dev/null
install -d -m 755 "$plugin_dir"
temporary=$(mktemp "$plugin_dir/.docker-compose.XXXXXX")
cleanup() {
    rm -f "$temporary"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

curl --fail --show-error --silent --location \
    "https://github.com/docker/compose/releases/download/$version/$asset" \
    --output "$temporary"
printf '%s  %s\n' "$expected" "$temporary" | sha256sum --check --status
install -m 755 "$temporary" "$target"

reported=$("$target" version --short)
case "$reported" in
    "$version"|"${version#v}") ;;
    *)
        echo "unexpected Docker Compose version: $reported" >&2
        rm -f "$target"
        exit 1
        ;;
esac

docker compose version
echo "installed checksum-verified Docker Compose $version at $target"
