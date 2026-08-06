# Lynx M20 Pro simulator profile

This registry profile declares the bounded, payment-gated
`navigate_obstacle_course` and safe `stop` skills for the DeepRobotics Lynx M20
Pro simulator. The complete operator runbook, model provenance, visual
MuJoCo/Webots commands, payment guarantees and test commands live in the
[bridge README](../../../../../../bridge/deep_robotics/m20_pro_mujoco_bridge/README.md).

The source model is the vendor-published M20 16-DOF wheeled-legged base pinned
in `execution-mapping.yaml`; it is not a claim that Pro-only sensors or compute
are simulated. Webots is generated from the locked vendor URDF, then validated
independently against MuJoCo using measured base pose.

The physical red course obstacle begins in the route. As in the Spot profile,
the controller uses measured simulator base pose and the profile-owned course
geometry to determine clearance, then yields with zero wheel command; the
environment actor clears laterally, and the robot resumes. This is deliberately
documented as a yield-and-resume behavior rather than a made-up claim that the
locked M20 model simulates steering or M20 Pro LiDAR hardware.

## Evidence contract

- CI runs the real Go Tunnel/x402 middleware, real Zenoh and real M20 MuJoCo
  bridge for invalid-payment, first-paid-action, failure/timeout and replay
  assertions.
- CI runs the vendor-MJCF MuJoCo episode plus Webots R2025a generated from the
  same locked vendor URDF.
- A trusted push/workflow-dispatch job uses Base Sepolia secrets and uploads a
  generated receipt/result artifact. Do not treat locally generated payment
  signatures or controlled facilitator results as public-chain evidence.
