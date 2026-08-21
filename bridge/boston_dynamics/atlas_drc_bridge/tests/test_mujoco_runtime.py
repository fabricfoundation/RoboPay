"""Physics regression for the pinned Atlas DRC legacy model."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from atlas_drc_bridge.contracts import validate_wave_params
from atlas_drc_bridge.model import load_mujoco_model, resolve_model_dir
from atlas_drc_bridge.runtime import MAX_TORQUE_NM, run_wave_episode


class AtlasMuJoCoRuntimeTests(unittest.TestCase):
    def test_original_visual_meshes_load_without_becoming_colliders(self) -> None:
        try:
            resolve_model_dir()
        except FileNotFoundError as error:
            self.skipTest(str(error))
        import mujoco

        model = load_mujoco_model(visual=True)
        self.assertGreaterEqual(model.nmesh, 23)
        mesh_geometries = model.geom_type == int(mujoco.mjtGeom.mjGEOM_MESH)
        self.assertTrue(mesh_geometries.any())
        self.assertTrue((model.geom_contype[mesh_geometries] == 0).all())
        self.assertTrue((model.geom_conaffinity[mesh_geometries] == 0).all())

    def test_closed_loop_wave_has_measured_state_change(self) -> None:
        try:
            resolve_model_dir()
        except FileNotFoundError as error:
            self.skipTest(str(error))
        result = run_wave_episode(
            validate_wave_params({"cycles": 1, "amplitudeRad": 0.30, "maxDurationSec": 6})
        )
        self.assertTrue(result["finite_state"])
        self.assertTrue(result["success"], result)
        self.assertGreaterEqual(result["completed_half_waves"], 2)
        self.assertGreaterEqual(result["measured_wave_stroke_rad"], 0.405)
        self.assertLessEqual(result["peak_commanded_torque_nm"], MAX_TORQUE_NM)


if __name__ == "__main__":
    unittest.main(verbosity=2)
