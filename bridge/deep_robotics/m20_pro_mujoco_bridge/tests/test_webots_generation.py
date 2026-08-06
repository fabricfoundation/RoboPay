from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from generate_webots_proto import generate


class M20WebotsGenerationTests(unittest.TestCase):
    def test_vendor_urdf_generates_r2025a_proto_with_all_motors(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m20_webots_proto_") as temp_dir:
            output = generate(Path(temp_dir) / "DeepRoboticsM20.proto")
            contents = output.read_text(encoding="utf-8")
        self.assertTrue(contents.startswith("#VRML_SIM R2025a"))
        self.assertNotIn("\\", contents)
        self.assertEqual(contents.count("RotationalMotor {"), 16)
        self.assertIn('name "fl_wheel_joint"', contents)
        self.assertIn('name "hr_knee_joint"', contents)
        self.assertIn("castShadows FALSE", contents)


if __name__ == "__main__":
    unittest.main(verbosity=2)
