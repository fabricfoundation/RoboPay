import tempfile
import unittest
from pathlib import Path

from download_x2_model import _file_sha256, _official_text_sha256
from x2_inspection_bridge.control_core import X2InspectionControlCore, TARGET_POSES
from x2_inspection_bridge.runner import run_inspection


class X2PolicyTests(unittest.TestCase):
    def test_model_hash_normalizes_checkout_line_endings(self):
        with tempfile.TemporaryDirectory() as temporary:
            lf_path = Path(temporary) / "lf.xml"
            crlf_path = Path(temporary) / "crlf.xml"
            lf_path.write_bytes(b"<model>\n  <joint/>\n</model>\n")
            crlf_path.write_bytes(b"<model>\r\n  <joint/>\r\n</model>\r\n")
            self.assertNotEqual(_file_sha256(lf_path), _file_sha256(crlf_path))
            self.assertEqual(
                _official_text_sha256(lf_path), _official_text_sha256(crlf_path)
            )

    def test_feedback_is_required_before_target_advances(self):
        policy = X2InspectionControlCore(("left",))
        policy.reset()
        plan = policy.compute_plan({"sim_time": 10.0, "inspection_joint_positions": [0.0] * 11})
        self.assertEqual(plan.target_index, 0)
        self.assertEqual(policy.completed_targets, [])

    def test_measured_tolerance_and_dwell_complete_target(self):
        policy = X2InspectionControlCore(("left",))
        measured = list(TARGET_POSES["left"])
        policy.compute_plan({"sim_time": 1.0, "inspection_joint_positions": measured})
        policy.compute_plan({"sim_time": 1.56, "inspection_joint_positions": measured})
        self.assertEqual(policy.phase, "COMPLETE")
        self.assertEqual(policy.completed_targets, ["left"])

    def test_official_mjcf_completes_all_targets(self):
        result = run_inspection()
        self.assertTrue(result["success"])
        self.assertEqual(result["targets_confirmed"], ["left", "center", "right"])
        self.assertEqual(result["model_source_commit"], "77f43eb0904dae4c48ccd9154fee824f8ffd4d38")
        self.assertEqual(result["support_fixture"], "pelvis safety fixture with feet on floor")

    def test_actual_mujoco_episode_applies_safe_stop(self):
        checks = 0

        def stop_requested():
            nonlocal checks
            checks += 1
            return checks > 100

        result = run_inspection(stop_requested=stop_requested)
        self.assertFalse(result["success"])
        self.assertTrue(result["safe_stop_applied"])
        self.assertEqual(result["completion_reason"], "safe_stopped")

    def test_minimum_speed_single_target_contract_executes(self):
        result = run_inspection(max_duration_seconds=5, targets=("center",), speed_scale=0.5)
        self.assertTrue(result["success"])
        self.assertEqual(result["targets_confirmed"], ["center"])
        self.assertEqual(result["target_confirmations"][0]["target"], "center")


if __name__ == "__main__":
    unittest.main()
