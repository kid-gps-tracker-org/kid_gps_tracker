#!/usr/bin/env python3
"""
Git tag から Kconfig にバージョンを注入
例: v1.2.3 → CONFIG_APP_VERSION="1.2.3"
"""
import re
import sys
from pathlib import Path

def extract_version_from_tag(tag: str) -> str:
    """v1.2.3 から 1.2.3 を抽出"""
    match = re.match(r'v?(\d+\.\d+\.\d+)', tag)
    if not match:
        raise ValueError(f"Invalid tag format: {tag}")
    return match.group(1)

def inject_version_to_kconfig(version: str, kconfig_path: Path):
    """Kconfig の CONFIG_APP_VERSION を更新"""
    with open(kconfig_path, 'r') as f:
        content = f.read()

    # default "1.0.0" の行を置換
    updated = re.sub(
        r'default\s+"[0-9]+\.[0-9]+\.[0-9]+"',
        f'default "{version}"',
        content
    )

    with open(kconfig_path, 'w') as f:
        f.write(updated)

    print(f"✓ Injected version {version} into Kconfig")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: version_inject.py <tag>")
        sys.exit(1)

    tag = sys.argv[1]
    version = extract_version_from_tag(tag)
    kconfig_path = Path(__file__).parent.parent / "Kconfig"
    inject_version_to_kconfig(version, kconfig_path)
