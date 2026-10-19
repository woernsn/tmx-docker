import base64
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.update_components import package_assets, select_release, update_components


def release(version, archive):
    return {
        "name": "courthive-components",
        "version": version,
        "license": "MIT",
        "dist": {"integrity": "sha512-" + base64.b64encode(hashlib.sha512(archive).digest()).decode()},
    }


def package(version, css=b".button{color:red}", notice=b"MIT License\n"):
    files = {
        "package/package.json": json.dumps({
            "name": "courthive-components", "version": version, "license": "MIT"
        }).encode(),
        "package/dist/courthive-components.css": css,
        "package/LICENSE": notice,
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


class UpdateComponentsTests(unittest.TestCase):
    def test_selects_newest_stable_release_within_current_major(self):
        versions = {version: {"version": version, "license": "MIT"} for version in (
            "6.2.1", "6.10.1", "6.11.0-beta.1", "7.0.0"
        )}
        self.assertEqual(select_release({"versions": versions}, "6.2.0")["version"], "6.10.1")
        self.assertIsNone(select_release({"versions": versions}, "6.10.1"))

    def test_updates_pin_css_and_license_together(self):
        archive = package("6.10.1", css=b".button{color:blue}", notice=b"MIT License\nCourtHive\n")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources, css, notice = (root / name for name in ("sources.env", "style.css", "LICENSE"))
            sources.write_text("TMX_REF=example\nCOMPONENTS_VERSION=6.2.0\n")
            css.write_bytes(b"old css")
            notice.write_bytes(b"old notice")
            metadata = {"versions": {"6.10.1": release("6.10.1", archive)}}
            self.assertTrue(update_components(sources, css, notice, metadata, archive))
            self.assertEqual(sources.read_text(), "TMX_REF=example\nCOMPONENTS_VERSION=6.10.1\n")
            self.assertEqual(css.read_bytes(), b".button{color:blue}")
            self.assertEqual(notice.read_bytes(), b"MIT License\nCourtHive\n")

    def test_rejects_tampered_package(self):
        archive = package("6.10.1")
        with self.assertRaisesRegex(ValueError, "integrity"):
            package_assets(archive + b"tampered", release("6.10.1", archive))


if __name__ == "__main__":
    unittest.main()
