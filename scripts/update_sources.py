#!/usr/bin/env python3
"""Pin compatible upstream release tags within each project's current major version."""

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path


UPSTREAMS = {
    "TMX_REF": "https://github.com/CourtHive/TMX.git",
    "SERVER_REF": "https://github.com/CourtHive/competition-factory-server.git",
    "PUB_REF": "https://github.com/CourtHive/courthive-public.git",
}
DEPENDENCY = "tods-competition-factory"
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$", re.ASCII)
RELEASE_TAG = re.compile(r"^v\d+\.\d+\.\d+$", re.ASCII)
PIN = re.compile(r"^(TMX_REF|SERVER_REF|PUB_REF)=(v\d+\.\d+\.\d+)$", re.MULTILINE)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE
    ).strip()


def package_info(repo: Path, ref: str) -> dict | None:
    try:
        return json.loads(git(repo, "show", f"{ref}:package.json"))
    except subprocess.CalledProcessError:
        return None


def version_key(version: str) -> tuple:
    match = VERSION.fullmatch(version)
    if not match:
        raise ValueError(f"Unsupported version {version!r}; expected an exact semver")
    major, minor, patch, prerelease = match.groups()
    identifiers = tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in (prerelease or "").split(".")
    )
    return (int(major), int(minor), int(patch), prerelease is None, identifiers)


def release_versions(repo: Path, pinned: str) -> dict[str, str]:
    """Map factory dependency versions to the newest eligible release tag."""
    tags = [tag for tag in git(repo, "tag", "--list").splitlines() if RELEASE_TAG.fullmatch(tag)]
    if pinned not in tags:
        raise ValueError(f"Pinned release tag {pinned} is missing from {repo}")
    pinned_major = version_key(pinned[1:])[0]
    eligible = sorted(
        (tag for tag in tags
         if version_key(tag[1:])[0] == pinned_major
         and version_key(tag[1:]) >= version_key(pinned[1:])),
        key=lambda tag: version_key(tag[1:]), reverse=True,
    )
    versions = {}
    for tag in eligible:
        package = package_info(repo, tag)
        if not package or package.get("version") != tag[1:]:
            if tag == pinned:
                raise ValueError(f"Pinned tag {tag} does not match its package version")
            continue
        dependency = package.get("dependencies", {}).get(DEPENDENCY)
        if dependency:
            version_key(dependency)
            versions.setdefault(dependency, tag)
    if not versions:
        raise ValueError(f"No eligible release of {repo} declares {DEPENDENCY}")
    return versions


def latest_sources(tmx_repo: Path, server_repo: Path, pub_repo: Path,
                   current: dict[str, str]) -> tuple[str, str, str, str]:
    repos = {"TMX_REF": tmx_repo, "SERVER_REF": server_repo, "PUB_REF": pub_repo}
    versions = {name: release_versions(repo, current[name]) for name, repo in repos.items()}
    shared = set.intersection(*(set(releases) for releases in versions.values()))
    if not shared:
        raise ValueError("No shared tods-competition-factory version among eligible release tags")
    factory = max(shared, key=version_key)
    return (versions["TMX_REF"][factory], versions["SERVER_REF"][factory],
            versions["PUB_REF"][factory], factory)


def update_sources(path: Path, tmx_repo: Path, server_repo: Path, pub_repo: Path) -> bool:
    content = path.read_text()
    pins = PIN.findall(content)
    current = dict(pins)
    if len(pins) != 3 or set(current) != set(UPSTREAMS):
        raise ValueError(f"Expected one stable release tag for each upstream in {path}")
    tmx_ref, server_ref, pub_ref, version = latest_sources(
        tmx_repo, server_repo, pub_repo, current
    )
    replacements = {"TMX_REF": tmx_ref, "SERVER_REF": server_ref, "PUB_REF": pub_ref}
    for name, ref in replacements.items():
        pattern = rf"^{name}=v\d+\.\d+\.\d+$"
        updated, count = re.subn(pattern, f"{name}={ref}", content, flags=re.MULTILINE)
        if count != 1:
            raise ValueError(f"Expected exactly one stable release tag for {name} in {path}")
        content = updated
    changed = content != path.read_text()
    if changed:
        path.write_text(content)
    print(f"Selected tods-competition-factory {version}: TMX {tmx_ref}, server {server_ref}, pub {pub_ref}")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=Path("sources.env"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="tmx-sources-") as temporary:
        repositories = {}
        for name, url in UPSTREAMS.items():
            destination = Path(temporary) / name.lower()
            subprocess.run(
                ["git", "clone", "--quiet", "--tags", "--filter=blob:none", "--no-checkout", url, str(destination)],
                check=True,
            )
            repositories[name] = destination
        update_sources(args.sources, repositories["TMX_REF"], repositories["SERVER_REF"], repositories["PUB_REF"])


if __name__ == "__main__":
    main()
