import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.update_sources import latest_sources, update_sources


def command(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def create_repo(path, versions):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    command(path, "config", "user.name", "Test")
    command(path, "config", "user.email", "test@example.com")
    commits = []
    for index, version in enumerate(versions):
        (path / "package.json").write_text(json.dumps({"dependencies": {"tods-competition-factory": version}}))
        (path / "change.txt").write_text(str(index))
        command(path, "add", ".")
        command(path, "commit", "-qm", f"commit {index}")
        commits.append(command(path, "rev-parse", "HEAD"))
    return commits


class UpdateSourcesTests(unittest.TestCase):
    def test_uses_all_heads_when_they_share_a_new_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tmx = create_repo(root / "tmx", ["7.3.1", "7.4.0", "7.4.0"])
            server = create_repo(root / "server", ["7.3.1", "7.4.0", "7.4.0"])
            pub = create_repo(root / "pub", ["7.3.1", "7.4.0", "7.4.0"])
            self.assertEqual(latest_sources(root / "tmx", root / "server", root / "pub"),
                             (tmx[2], server[2], pub[2], "7.4.0"))

    def test_uses_latest_commits_for_shared_version_when_one_head_is_ahead(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tmx = create_repo(root / "tmx", ["7.3.1", "7.3.1", "7.4.0"])
            server = create_repo(root / "server", ["7.3.1", "7.3.1", "7.3.1"])
            pub = create_repo(root / "pub", ["7.3.1", "7.4.0", "7.4.0"])
            self.assertEqual(latest_sources(root / "tmx", root / "server", root / "pub"),
                             (tmx[1], server[2], pub[0], "7.3.1"))

            sources = root / "sources.env"
            sources.write_text(f"# pins\nTMX_REF={tmx[0]}\nSERVER_REF={server[0]}\nPUB_REF={pub[0]}\n")
            self.assertTrue(update_sources(sources, root / "tmx", root / "server", root / "pub"))
            self.assertEqual(sources.read_text(),
                             f"# pins\nTMX_REF={tmx[1]}\nSERVER_REF={server[2]}\nPUB_REF={pub[0]}\n")
            self.assertFalse(update_sources(sources, root / "tmx", root / "server", root / "pub"))

    def test_fails_without_shared_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            create_repo(root / "tmx", ["7.3.1"])
            create_repo(root / "server", ["7.4.0"])
            create_repo(root / "pub", ["7.3.1"])
            with self.assertRaisesRegex(ValueError, "No shared"):
                latest_sources(root / "tmx", root / "server", root / "pub")


if __name__ == "__main__":
    unittest.main()
