# AGIBot X2 active-inspection profile

Tier 1 simulator-only profile for AGIBot X2. The paid
`inspect_target_sequence` skill uses the official X2 Ultra v1.4 model with 31
actuated joints and a
shared closed-loop controller in MuJoCo and Webots R2025a.

The fixed-base inspection stand is deliberate and is represented in both
simulators with both feet on the floor. This profile makes no walking claim.
Head and arm motion is applied through robot actuators; target progression
depends on measured joint state. See the bridge
[README](../../../../../../bridge/agibot/x2_inspection_bridge/README.md)
for clean-checkout, visual recording, payment, and validation commands.

Price is 0.001 USDC on Base Sepolia (`eip155:84532`). The deployment payee is
supplied by `ROBO_PAYEE_ADDRESS`; no identity or payment private key is stored
in this profile.

The source-bound continuous paid-action recording, trusted JSON receipt,
action ID, Base Sepolia transaction, and content hashes are recorded in
[`evidence/evidence-manifest.yaml`](evidence/evidence-manifest.yaml). The
versioned [recording](evidence/agibot-x2-current-head-85fc510.mp4) keeps the
terminal and native MuJoCo viewer visible through the complete three-target
action and matching BaseScan receipt.
