import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.update_sources import latest_sources, update_sources


def command(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def create_repo(path, releases, untagged=None):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    command(path, "config", "user.name", "Test")
    command(path, "config", "user.email", "test@example.com")
    for version, factory in releases:
        (path / "package.json").write_text(json.dumps({
            "version": version, "dependencies": {"tods-competition-factory": factory}
        }))
        command(path, "add", "package.json")
        command(path, "commit", "-qm", f"release {version}")
        command(path, "tag", f"v{version}")
    if untagged:
        (path / "package.json").write_text(json.dumps({
            "version": untagged, "dependencies": {"tods-competition-factory": "7.5.0"}
        }))
        command(path, "add", "package.json")
        command(path, "commit", "-qm", "unreleased changes")


def pins(tmx="v8.1.0", server="v4.1.0", pub="v1.1.0"):
    return {"TMX_REF": tmx, "SERVER_REF": server, "PUB_REF": pub}


class UpdateSourcesTests(unittest.TestCase):
    def test_selects_newest_compatible_tags_and_ignores_untagged_head(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_repo(root / "tmx", [("8.1.0", "7.3.1"), ("8.2.0", "7.4.0")], "8.3.0")
            create_repo(root / "server", [("4.1.0", "7.3.1"), ("4.2.0", "7.4.0")])
            create_repo(root / "pub", [("1.1.0", "7.3.1"), ("1.2.0", "7.4.0")])
            self.assertEqual(latest_sources(root / "tmx", root / "server", root / "pub", pins()),
                             ("v8.2.0", "v4.2.0", "v1.2.0", "7.4.0"))

    def test_keeps_shared_factory_version_and_updates_only_within_each_major(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_repo(root / "tmx", [("8.1.0", "7.3.1"), ("8.2.0", "7.3.1"),
                                       ("8.3.0", "7.4.0"), ("9.0.0", "7.5.0")])
            create_repo(root / "server", [("4.1.0", "7.3.1"), ("5.0.0", "7.5.0")])
            create_repo(root / "pub", [("1.1.0", "7.3.1"), ("1.2.0", "7.4.0"),
                                       ("2.0.0", "7.5.0")])
            self.assertEqual(latest_sources(root / "tmx", root / "server", root / "pub", pins()),
                             ("v8.2.0", "v4.1.0", "v1.1.0", "7.3.1"))
            with self.assertRaisesRegex(ValueError, "No shared"):
                latest_sources(root / "tmx", root / "server", root / "pub",
                               pins(tmx="v8.3.0"))

            sources = root / "sources.env"
            sources.write_text("# pins\nTMX_REF=v8.1.0\nSERVER_REF=v4.1.0\n"
                               "PUB_REF=v1.1.0\nCOMPONENTS_VERSION=6.10.1\n")
            self.assertTrue(update_sources(sources, root / "tmx", root / "server", root / "pub"))
            self.assertEqual(sources.read_text(), "# pins\nTMX_REF=v8.2.0\nSERVER_REF=v4.1.0\n"
                             "PUB_REF=v1.1.0\nCOMPONENTS_VERSION=6.10.1\n")
            self.assertFalse(update_sources(sources, root / "tmx", root / "server", root / "pub"))

    def test_fails_without_shared_release_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_repo(root / "tmx", [("8.1.0", "7.3.1")])
            create_repo(root / "server", [("4.1.0", "7.4.0")])
            create_repo(root / "pub", [("1.1.0", "7.3.1")])
            with self.assertRaisesRegex(ValueError, "No shared"):
                latest_sources(root / "tmx", root / "server", root / "pub", pins())


if __name__ == "__main__":
    unittest.main()
