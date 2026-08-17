import g1_spec as spec
from simulator import MuJoCoSimulator
sim = MuJoCoSimulator()
print("FALL_PITCH=", spec.FALL_PITCH, "RECOVER_PITCH=", spec.RECOVER_PITCH,
      "MAX_TORQUE=", spec.MAX_TORQUE_BAL, "KP_BAL=", spec.KP_BAL, "KV_BAL=", spec.KV_BAL)
for label, w in [("recover", spec.PUSH_W_RECOVER), ("fall", spec.PUSH_W_FALL)]:
    r = sim.balance_recover({"push": w})
    m = r.metrics
    print(f"  {label:7s} push={w:4.1f} -> success={r.success} fell={m['fell']} "
          f"maxPitch={m['maxPitchRad']:.3f} endPitch={m['pitchRad']:+.3f} "
          f"steps={m['stepsUsed']}/{m['stepBudget']}")
print("--- sweep ---")
for w in [0.5, 1.0, 1.3, 1.6, 2.0, 3.0, 4.0, 5.0]:
    r = sim.balance_recover({"push": w})
    m = r.metrics
    print(f"  push={w:4.1f} success={r.success} fell={m['fell']} "
          f"maxPitch={m['maxPitchRad']:.3f}")
