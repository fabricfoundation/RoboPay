# Validation report — Atlas DRC/v4 legacy profile

Validated locally on 2026-08-20 with the pinned Atlas DRC/v4 source and Webots
R2025a model. The live payment and continuous visual evidence were captured on
2026-08-21 from source commit
`184c06aa6dd1ffff502563694e9b3cbaafc67263`.
The follow-up commit changes only the versioned evidence package and its
registry binding assertion; it does not change the Tunnel, bridge, policy,
simulator, workflow, or model assets exercised by the recording.

| Gate | Result | Measured evidence |
|---|---:|---|
| MuJoCo task | pass | State-feedback torque controller completed 4/4 half-waves; measured stroke `0.65003 rad`; finite state; peak bounded torque `75 Nm` |
| Webots task | pass | Measured-position controller completed 4/4 half-waves; stroke `0.55761 rad`; minimum root height `0.91602 m`; minimum upright cosine `0.99844`; `stable_base=true` |
| Sim-to-Sim | pass | Same policy ID, parameters and four measured turning points in both engines; all five comparison gates pass; score `1.0` |
| Go Tunnel | pass | Production Tunnel builds and all Go tests pass with x402 Go `v0.0.0-20260529172747-45d81d46e5bd` |
| Invalid payment | pass | Real Tunnel/router/Zenoh test receives facilitator HTTP 200 with `isValid:false`; returns HTTP 402, ActionEvents=0, simulator outputs=0, settlements=0 |
| Cold-start paid flow | pass | Bridge readiness is published after the action subscription; unpaid 402 is followed by the first paid 202 without a warm-up action; one real MuJoCo wave, one correlated result and one settlement |
| Failure/timeout/replay | pass | Simulator failure, silence timeout, payment replay and restart replay all leave settlement calls at zero |
| WebSocket fragmentation | pass | Continuation frames are assembled before decoding the first Fabric response |
| Registry drift | pass | Profile, skills, catalog, price, topics, examples and execution mapping validate together |
| Model identity | pass | Pinned DRC/v4 URDF has 30 movable one-DoF joints; electric Atlas is documented separately as 56 DoF with continuous range; no compatibility or electric-model claim |
| Current-HEAD Base Sepolia receipt | pass | Unpaid `402`; first paid `202`; correlated `atlas-wave-1787284097` success; settlement transaction [`0x2435125...777c`](https://sepolia.basescan.org/tx/0x2435125b61c5e003671316111a34aa063cf5e2c4694c135f55b967e08e27777c); committed receipt SHA-256 `459756fbc6d3fe7a2eab16af0782545db6915de5ffaf0c4ed3b3d0489aa7b249`; raw runner artifact SHA-256 `807e24bdf8d8171c2382ab5c7e2646a08e7bb372f86fdf46facaa2131e77176d` |
| Current-HEAD continuous visual evidence | pass | [Continuous split-screen recording](https://github.com/user-attachments/assets/9a0b622f-e521-44f2-8101-071cde2fae28) keeps terminal and MuJoCo visible through the complete wave and matching BaseScan success; recording SHA-256 `6f004724c9c48df34be62785c0ac7ed148464d9c379d09dd3097fea8f6b58f01` |

The negative-payment suite uses a recording facilitator only to return a
deterministic invalid verdict. It does not mock the Go Tunnel, x402 middleware,
Zenoh publication boundary, durable replay store, or simulator-side action
boundary. Positive simulator success is covered separately with the real
MuJoCo and Webots runtimes.
