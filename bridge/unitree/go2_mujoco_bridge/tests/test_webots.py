import tempfile
import unittest
from pathlib import Path

from go2_mujoco_bridge.webots import _webots_home


class WebotsPathTests(unittest.TestCase):
    def test_linux_style_installation_root_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "webots"
            (root / "resources").mkdir(parents=True)
            executable = root / "webots"
            executable.touch()
            self.assertEqual(_webots_home(executable), root)


if __name__ == "__main__":
    unittest.main()
