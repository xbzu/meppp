#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

SOURCES = (
    "https://www.cloudflare.com/ips-v4",
    "https://www.cloudflare.com/ips-v6",
)


def fetch_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for url in SOURCES:
        request = Request(url, headers={"User-Agent": "meppp-cloudflare-ip-refresh/1"})
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310
                payload = response.read().decode("ascii")
        except (OSError, URLError, UnicodeError) as error:
            raise RuntimeError(f"could not fetch Cloudflare network list: {url}") from error
        for line in payload.splitlines():
            if line.strip():
                networks.append(ipaddress.ip_network(line.strip(), strict=True))

    unique_networks = sorted(
        set(networks), key=lambda item: (item.version, int(item.network_address))
    )
    versions = {network.version for network in unique_networks}
    if len(unique_networks) < 10 or versions != {4, 6}:
        raise RuntimeError("Cloudflare network list failed sanity checks")
    return unique_networks


def render(networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> str:
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    lines = [
        "# Generated from Cloudflare's official IPv4/IPv6 lists.",
        f"# generated_at={generated}",
        "# Re-run deploy/update_cloudflare_real_ip.py before each release.",
    ]
    lines.extend(f"set_real_ip_from {network};" for network in networks)
    lines.extend(("real_ip_header CF-Connecting-IP;", "real_ip_recursive on;", ""))
    return "\n".join(lines)


def atomic_write(output: Path, content: str) -> None:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate an Nginx real-IP include from Cloudflare's official network lists."
    )
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    networks = fetch_networks()
    atomic_write(arguments.output, render(networks))
    print(f"wrote {len(networks)} verified Cloudflare networks to {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
