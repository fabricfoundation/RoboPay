# Booster K1 active-inspection profile

Tier 1 simulator-only profile for Booster K1. The paid
`inspect_target_sequence` skill uses the official 22-DoF robot model and a
shared closed-loop controller in MuJoCo and Webots R2025a.

The fixed-base inspection stand is deliberate and is represented in both
simulators with both feet on the floor. This profile makes no walking claim.
Head and arm motion is applied through robot actuators; target progression
depends on measured joint state. See the bridge
[README](../../../../../../bridge/booster/k1_inspection_bridge/README.md)
for clean-checkout, visual recording, payment, and validation commands.

Price is 0.001 USDC on Base Sepolia (`eip155:84532`). The deployment payee is
supplied by `ROBO_PAYEE_ADDRESS`; no identity or payment private key is stored
in this profile.
