"""Skill acceptance test for the Spot controller (no Zenoh needed).

Exercises every skill through the controller's policy entrypoint and checks
the physics metrics that prove the action actually happened:

  * wave          -> the front-right paw lifts at least 0.15 m above ground
  * sit           -> the body crouches at least 0.10 m below home height
  * stand         -> the body returns to the home stance height (~0.434 m)
  * bow           -> the torso pitches at least 10 deg (front dips)
  * nod           -> the body bobs by a measurable amount
  * turn_to_face  -> the body yaws toward the requested heading and reports
                     the achieved yaw and remaining error honestly
  * hold          -> holds the stance
  * unknown skill -> UNKNOWN_SKILL error result

Every successful skill must return the robot to the home stance afterwards
(body height within 0.02 m of home) so a sequence of paid actions can run
back to back without accumulating error.

Prints PASS/FAIL, exits nonzero on failure.
"""

import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))

from spot_control import HOME_BODY_Z, SpotController  # noqa: E402

MODEL = pathlib.Path(HERE).parent / "models" / "mujoco_menagerie" \
    / "boston_dynamics_spot" / "scene.xml"


def main():
    c = SpotController(str(MODEL))
    checks = {}

    # --- hold -----------------------------------------------------------
    r = c.execute("hold", {"seconds": 0.5})
    checks["hold_success"] = r.status == "success"
    checks["hold_stance_stable"] = abs(r.metrics["bodyZ"] - HOME_BODY_Z) < 0.02

    # --- wave -----------------------------------------------------------
    r = c.execute("wave", {})
    checks["wave_success"] = r.status == "success"
    checks["wave_paw_lifted"] = r.metrics.get("pawLift", 0) > 0.15
    checks["wave_recovers"] = abs(r.metrics["bodyZ"] - HOME_BODY_Z) < 0.02

    # --- sit ------------------------------------------------------------
    r = c.execute("sit", {})
    checks["sit_success"] = r.status == "success"
    checks["sit_crouches"] = r.metrics.get("sitDepth", 0) > 0.10
    checks["sit_recovers"] = abs(r.metrics["bodyZ"] - HOME_BODY_Z) < 0.02

    # --- stand ----------------------------------------------------------
    r = c.execute("stand", {})
    checks["stand_success"] = r.status == "success"
    checks["stand_returns_home"] = abs(
        r.metrics.get("standHeight", 0) - HOME_BODY_Z) < 0.02

    # --- bow ------------------------------------------------------------
    r = c.execute("bow", {})
    checks["bow_success"] = r.status == "success"
    checks["bow_pitches"] = r.metrics.get("bowPitchDeg", 0) > 10.0
    checks["bow_recovers"] = abs(r.metrics["bodyZ"] - HOME_BODY_Z) < 0.02

    # --- nod ------------------------------------------------------------
    r = c.execute("nod", {})
    checks["nod_success"] = r.status == "success"
    checks["nod_bobs"] = r.metrics.get("nodDepth", 0) > 0.02
    checks["nod_recovers"] = abs(r.metrics["bodyZ"] - HOME_BODY_Z) < 0.02

    # --- turn_to_face ---------------------------------------------------
    r = c.execute("turn_to_face", {"headingDeg": 30.0})
    checks["turn_success"] = r.status == "success"
    checks["turn_rotates_toward"] = r.metrics.get("achievedYawDeg", 0) > 4.0
    checks["turn_honest_error"] = "finalHeadingErrorDeg" in r.metrics
    checks["turn_recovers"] = abs(r.metrics["bodyZ"] - HOME_BODY_Z) < 0.02

    # --- unknown skill --------------------------------------------------
    r = c.execute("backflip", {})
    checks["unknown_skill"] = r.status == "error" \
        and r.error and r.error.get("code") == "UNKNOWN_SKILL"

    print(json.dumps({"checks": checks}, indent=1))
    ok = all(checks.values())
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
