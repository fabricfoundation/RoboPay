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

- All payment suites run the real Go Tunnel/x402 middleware and real Zenoh.
  Invalid payment is checked at the real M20 bridge boundary with a
  non-executing invocation counter; the first paid action runs the real bridge
  and vendor-MJCF MuJoCo; failure, timeout and replay use a controlled terminal
  result peer at the Zenoh boundary so unsuccessful outcomes are deterministic.
- CI runs the vendor-MJCF MuJoCo episode plus Webots R2025a generated from the
  same locked vendor URDF.
- A trusted push/workflow-dispatch job uses Base Sepolia secrets and uploads a
  generated receipt/result artifact. Do not treat locally generated payment
  signatures or controlled facilitator results as public-chain evidence.
- [Versioned paid Base Sepolia visual recording](evidence/m20-pro-paid-base-sepolia-visual-e2e-2026-08-06.mp4)
  shows the real Tunnel, live payment, correlated simulator completion, and
  execution-gated settlement path.
