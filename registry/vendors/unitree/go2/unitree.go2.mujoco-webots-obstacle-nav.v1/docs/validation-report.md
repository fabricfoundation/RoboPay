# Validation report

Validated locally on 2026-08-10 with the pinned official models.

| Gate | Result | Measured evidence |
|---|---:|---|
| MuJoCo task | pass | Started at yaw 0, turned under pose feedback, and reached the goal in 28.162 s; final distance 0.320 m; path 3.999 m; minimum clearance 0.416 m; 0 obstacle contacts |
| Webots task | pass | Started at yaw 0, visibly turned through the route, and reached the goal in 16.496 s; final distance 0.319 m; path 3.971 m; minimum clearance 0.354 m; 0 obstacle contacts |
| Sim-to-Sim | pass | `shared_policy_match=true` for policy ID, goal, four-waypoint route, start pose, gait frequency, stride, lift and steering limit |
| Go Tunnel | pass | Production Tunnel build and all Go tests pass |
| Invalid payment | pass | Real router/x402 middleware receives facilitator HTTP 200 with `isValid:false`; response is HTTP 402, ActionEvents=0, executable commands=0, settlements=0 |
| Failure/timeout/replay | pass | Real Tunnel + Zenoh test injects terminal failure and silence; settlement calls stay 0; replay remains rejected after restart |
| Cold-start transport | pass | WebSocket continuation frames are reassembled; the bridge-ready event is emitted only after the action subscription exists, so the first paid action requires no warm-up |
| Registry drift | pass | Profile, skills, catalog, price, topics and execution mapping validate together |

The negative-payment suite uses a recording facilitator only to deterministically
return verification/settlement outcomes. It does not mock the Tunnel router,
x402 middleware, Zenoh publication boundary, or durable replay store. The
failure/timeout suite uses an injected Zenoh simulator peer to produce a
deterministic terminal failure or silence; positive simulator success is covered
separately by the real MuJoCo and Webots task suites. Live Base Sepolia evidence
is a separate trusted push/workflow job because fork PR events do not receive
repository secrets.
