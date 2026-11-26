import json
import tarfile
import tempfile
import unittest
from pathlib import Path

import pack_release as pr
import spark_install as si

IMAGE = "sha256:" + "d" * 64


class ReleaseKitTest(unittest.TestCase):
    def test_the_release_kit_lists_its_images_and_carries_the_release_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            kit = Path(tmp) / "kit"
            (kit / "src" / "scripts" / "spark").mkdir(parents=True)
            (kit / "src" / "scripts" / "spark" / "spark_install.py").write_text("# installer\n")
            (kit / "ui-output.tgz").write_bytes(b"ui")
            (kit / "KIT.json").write_text(json.dumps({"format": 1, "label": "26.11.1-dev+abc",
                                                      "version": "26.11.1", "commit": "c" * 40}))
            pin = {"name": "bge-m3", "manifest": si.model_manifest("bge-m3"), "sha256": "a" * 64, "bytes": 5}
            pr.release_kit(kit, Path(tmp) / "out.tgz", "26.11.1.2", {"vivesec-graph": IMAGE}, [pin])
            with tarfile.open(Path(tmp) / "out.tgz") as archive:
                names = archive.getnames()
                info = json.load(archive.extractfile("KIT.json"))
        self.assertNotIn("ui-output.tgz", names)
        self.assertIn("src/scripts/spark/spark_install.py", info["files"])
        self.assertEqual((info["label"], info["build_label"]), ("26.11.1.2", "26.11.1-dev+abc"))
        self.assertEqual(si.kit_images(info), {"vivesec-graph": IMAGE})
        self.assertEqual(si.kit_models(info), [pin])

    def test_versions(self):
        dev = {"version": "26.11.1", "label": "26.11.1-dev+abc"}
        pr.check_version("26.11.1.2", dev)
        for version in ("26.11.1", "26.12.1.1", "latest"):  # a release needs the release tag
            self.assertRaises(SystemExit, pr.check_version, version, dev)
        pr.check_version("26.11.1", {"version": "26.11.1", "label": "26.11.1"})


if __name__ == "__main__":
    unittest.main()
