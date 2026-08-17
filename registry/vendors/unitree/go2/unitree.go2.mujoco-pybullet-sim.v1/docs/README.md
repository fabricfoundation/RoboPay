# Unitree Go2 — robot action profile (simulator)

Profile ID: `unitree.go2.mujoco-pybullet-sim.v1`

Scope: **simulator-only**. A paid RoboPay action arriving on the Zenoh topic
`robot/tunnel/action` starts a Go2 skill episode on the official MuJoCo model
(`google-deepmind/mujoco_menagerie` `unitree_go2`). Nine skills are
available (wave, sit, stand, stop, bow, nod, turn_to_face, hold,
navigate_obstacle), each driven by a joint-space trajectory controller —
never by a recorded animation or a built-in demo motion.

| file | what it describes |
|---|---|
| `robot.profile.yaml` | robot identity, Zenoh runtime, action/result topics |
| `skills.yaml` | the 9 skills, their params and limits |
| `functions.yaml` | agent-facing REST contract (`/action`, 402 + `PAYMENT-REQUIRED`) |
| `payment-policy.yaml` | x402 pricing per skill, settle-on-success rule |
| `execution-mapping.yaml` | how each skill maps to the simulator runtime + metrics |
| `examples/` | sample paid action envelope |
| `tests/` | skill-contract cases (success, replay, unknown skill, tampering, unpaid, obstacle nav) |
| `validation-report.md` | full validation evidence, sim-to-sim results, limitations |

See `simulation/README.md` for setup, tests, wire contract and troubleshooting.
