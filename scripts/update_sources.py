#!/usr/bin/env python3
"""Pin the newest upstream commits with the same factory dependency version."""

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path


UPSTREAMS = {
    "TMX_REF": "https://github.com/CourtHive/TMX.git",
    "SERVER_REF": "https://github.com/CourtHive/competition-factory-server.git",
}
DEPENDENCY = "tods-competition-factory"
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$", re.ASCII)


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE
    ).strip()


def factory_version(repo: Path, ref: str) -> str | None:
    try:
        package = json.loads(git(repo, "show", f"{ref}:package.json"))
    except subprocess.CalledProcessError:
        return None
    return package.get("dependencies", {}).get(DEPENDENCY)


def previous_version(repo: Path, ref: str) -> tuple[str, str] | None:
    """Find the last commit before the current dependency version began."""
    while True:
        change = git(
            repo, "log", "--first-parent", "-m", "-G", DEPENDENCY,
            "--max-count=1", "--format=%H", ref, "--", "package.json",
        )
        if not change:
            return None
        try:
            parent = git(repo, "rev-parse", f"{change}^1")
        except subprocess.CalledProcessError:
            return None  # The dependency was introduced in the root commit.
        old_version = factory_version(repo, parent)
        if old_version != factory_version(repo, ref):
            return (parent, old_version) if old_version else None
        ref = parent  # The dependency line changed without changing its version.


def version_key(version: str) -> tuple:
    match = VERSION.fullmatch(version)
    if not match:
        raise ValueError(f"Unsupported factory version {version!r}; expected an exact semver")
    major, minor, patch, prerelease = match.groups()
    identifiers = tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in (prerelease or "").split(".")
    )
    return (int(major), int(minor), int(patch), prerelease is None, identifiers)


def latest_pair(tmx_repo: Path, server_repo: Path) -> tuple[str, str, str]:
    repos = {"TMX_REF": tmx_repo, "SERVER_REF": server_repo}
    refs = {name: git(repo, "rev-parse", "HEAD") for name, repo in repos.items()}
    versions = {name: factory_version(repo, refs[name]) for name, repo in repos.items()}
    if None in versions.values():
        raise ValueError("An upstream HEAD has no tods-competition-factory dependency")
    while versions["TMX_REF"] != versions["SERVER_REF"]:
        newer = max(repos, key=lambda name: version_key(versions[name]))
        previous = previous_version(repos[newer], refs[newer])
        if previous is None:
            raise ValueError("No shared tods-competition-factory version in upstream history")
        refs[newer], versions[newer] = previous
    return refs["TMX_REF"], refs["SERVER_REF"], versions["TMX_REF"]


def update_sources(path: Path, tmx_repo: Path, server_repo: Path) -> bool:
    tmx_ref, server_ref, version = latest_pair(tmx_repo, server_repo)
    content = path.read_text()
    current = dict(re.findall(r"^(TMX_REF|SERVER_REF)=([0-9a-f]{40})$", content, re.MULTILINE))
    if len(current) == 2:
        pinned_versions = {
            factory_version(repo, current[name])
            for name, repo in (("TMX_REF", tmx_repo), ("SERVER_REF", server_repo))
        }
        if len(pinned_versions) == 1 and None not in pinned_versions:
            pinned_version = pinned_versions.pop()
            if version_key(version) < version_key(pinned_version):
                raise ValueError(f"Refusing to downgrade factory from {pinned_version} to {version}")
    replacements = {"TMX_REF": tmx_ref, "SERVER_REF": server_ref}
    for name, ref in replacements.items():
        pattern = rf"^{name}=[0-9a-f]{{40}}$"
        updated, count = re.subn(pattern, f"{name}={ref}", content, flags=re.MULTILINE)
        if count != 1:
            raise ValueError(f"Expected exactly one full commit ID for {name} in {path}")
        content = updated
    changed = content != path.read_text()
    if changed:
        path.write_text(content)
    print(f"Selected tods-competition-factory {version}: TMX {tmx_ref}, server {server_ref}")
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
                ["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout", url, str(destination)],
                check=True,
            )
            repositories[name] = destination
        update_sources(args.sources, repositories["TMX_REF"], repositories["SERVER_REF"])


if __name__ == "__main__":
    main()
