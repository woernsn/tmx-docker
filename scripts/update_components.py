#!/usr/bin/env python3
"""Update the manager's bundled CSS from a published courthive-components release."""

import argparse
import base64
import hashlib
import io
import json
import re
import subprocess
import tarfile
import urllib.parse
from pathlib import Path


PACKAGE = "courthive-components"
REGISTRY = f"https://registry.npmjs.org/{PACKAGE}"
PIN = re.compile(r"^COMPONENTS_VERSION=(\d+\.\d+\.\d+)$", re.MULTILINE)
STABLE = re.compile(r"^\d+\.\d+\.\d+$", re.ASCII)
MAX_METADATA_BYTES = 10_000_000
MAX_ARCHIVE_BYTES = 15_000_000
MAX_ASSET_BYTES = 2_000_000


def get_bytes(url: str, limit: int) -> bytes:
    content = subprocess.check_output([
        "curl", "--fail", "--silent", "--show-error", "--location", "--retry", "3",
        "--max-time", "30", "--max-filesize", str(limit),
        "--header", "Accept: application/vnd.npm.install-v1+json", url,
    ])
    if len(content) > limit:
        raise ValueError(f"Download exceeds {limit} bytes: {url}")
    return content


def version_key(version: str) -> tuple[int, int, int]:
    if not STABLE.fullmatch(version):
        raise ValueError(f"Expected a stable version, got {version!r}")
    return tuple(map(int, version.split(".")))


def select_release(metadata: dict, current: str) -> dict | None:
    major = version_key(current)[0]
    versions = metadata.get("versions", {})
    candidates = [
        version for version in versions
        if STABLE.fullmatch(version)
        and version_key(version)[0] == major
        and version_key(version) > version_key(current)
    ]
    return versions[max(candidates, key=version_key)] if candidates else None


def package_assets(archive: bytes, release: dict) -> tuple[bytes, bytes]:
    integrity = release.get("dist", {}).get("integrity", "")
    if not integrity.startswith("sha512-"):
        raise ValueError("Release has no SHA-512 integrity value")
    expected = base64.b64decode(integrity.removeprefix("sha512-"), validate=True)
    if hashlib.sha512(archive).digest() != expected:
        raise ValueError("Package integrity check failed")

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
        def read(name: str) -> bytes:
            member = package.getmember(name)
            if not member.isfile() or member.size > MAX_ASSET_BYTES:
                raise ValueError(f"Invalid package asset: {name}")
            return package.extractfile(member).read()

        manifest = json.loads(read("package/package.json"))
        if (manifest.get("name"), manifest.get("version"), manifest.get("license")) != (
            PACKAGE, release["version"], "MIT"
        ):
            raise ValueError("Package manifest does not match the selected MIT release")
        stylesheet = read("package/dist/courthive-components.css")
        license_notice = read("package/LICENSE")
    if not stylesheet or not license_notice.startswith(b"MIT License\n"):
        raise ValueError("Package is missing its stylesheet or MIT license notice")
    return stylesheet, license_notice


def update_components(sources: Path, stylesheet: Path, license_file: Path,
                      metadata: dict, archive: bytes | None = None) -> bool:
    content = sources.read_text()
    matches = PIN.findall(content)
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one COMPONENTS_VERSION in {sources}")
    current = matches[0]
    release = select_release(metadata, current)
    if release is None:
        print(f"No newer {PACKAGE} release within major version {version_key(current)[0]}")
        return False
    if archive is None:
        url = release["dist"]["tarball"]
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "registry.npmjs.org":
            raise ValueError(f"Unexpected package URL: {url}")
        archive = get_bytes(url, MAX_ARCHIVE_BYTES)
    css, notice = package_assets(archive, release)
    version = release["version"]
    stylesheet.write_bytes(css)
    license_file.write_bytes(notice)
    sources.write_text(PIN.sub(f"COMPONENTS_VERSION={version}", content))
    print(f"Selected {PACKAGE} {version}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=Path("sources.env"))
    parser.add_argument("--stylesheet", type=Path, default=Path("manager/static/courthive-components.css"))
    parser.add_argument("--license", dest="license_file", type=Path,
                        default=Path("manager/COURTHIVE-COMPONENTS-LICENSE"))
    args = parser.parse_args()
    metadata = json.loads(get_bytes(REGISTRY, MAX_METADATA_BYTES))
    update_components(args.sources, args.stylesheet, args.license_file, metadata)


if __name__ == "__main__":
    main()
