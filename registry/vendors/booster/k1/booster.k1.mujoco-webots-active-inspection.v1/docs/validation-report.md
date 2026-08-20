# Validation report

## Provenance and task

- Official source: `BoosterRobotics/booster_assets`
- Pinned commit: `508cbee6ca9ae6fbc8c0b38dd58785a6f3fc61a2`
- Model: `robots/K1/K1_22dof.xml` and `K1_22dof.urdf`
- License: BSD-3-Clause
- Task: closed-loop inspection of left, center, and right targets using the
  official K1 head and arm joints in a disclosed fixed-base safety stand

The action is not a prerecorded animation. At every engine tick, the shared
controller reads measured joint positions, computes target error, and advances
only after the error remains within 0.09 rad for 0.40 seconds. MuJoCo uses a
torque-PD adapter; Webots uses a position-motor adapter.

## Acceptance gates

The mandatory workflow covers:

1. Go Tunnel build and tests.
2. Registry schema and bridge contract validation.
3. Official-model MuJoCo execution with all three targets confirmed.
4. Webots execution and Sim-to-Sim score 1.0 with an identical policy contract.
5. Invalid paid-shaped signature or missing facilitator verdict: HTTP 402,
   zero ActionEvents, zero executable simulator commands, zero settlement calls.
6. Correlated success/failure, paid stop, safe interruption, failure/timeout
   non-settlement, idempotency replay, and payment replay.
7. WebSocket continuation-frame assembly and first paid action after clean
   startup after an explicit subscriber-readiness handshake, without a warm-up
   action.
8. Required trusted-run Base Sepolia evidence with a public transaction hash.

## Validated environment

- Local OS: Windows 11 Pro 64-bit, build 26100
- Python: 3.12.10 locally; CI target 3.10
- Zenoh: eclipse-zenoh 1.9.0; native Python transport (ROS2 not used)
- MuJoCo: 3.10.0 in a clean validation virtual environment
- Webots: R2025a
- Model integrity (canonical LF bytes): MJCF SHA-256
  `51954b13...b50bf83`; URDF SHA-256 `03e82242...fe2174`

## Local validation results

- [x] Official K1 model download and hash verification
- [x] Exact dependency pins install cleanly; `pip check` reports no conflicts,
  including `requests==2.33.0`
- [x] `inspect_target_sequence` in MuJoCo: success, 3/3 targets, 1.716 s,
  1,716 control steps, 0.1819 m left-hand and 0.1799 m right-hand peak motion
- [x] `inspect_target_sequence` in Webots: success, 3/3 targets, 2.304 s,
  288 control steps, measured maximum motor velocity 2.9999 rad/s
- [x] Sim-to-Sim: score 1.0 and exact shared-policy match
- [x] MuJoCo safe stop: `safe_stop_applied: true`, inspection failure
- [x] Local Python suite: 12 tests passed, covering message parsing,
  correlation, routing, invalid parameters, foreign robot, success/failure,
  stop, the minimum-speed contract, official-model execution, and WebSocket
  continuation-frame reassembly; 2 Linux Tunnel tests were skipped locally and
  remain mandatory in CI
- [x] Registry schema: passed
- [x] Real Go Tunnel invalid-payment and non-settlement tests: passed in the
  mandatory Linux CI gate, including `isValid: false`, missing verdict, zero
  ActionEvents, zero simulator commands, and zero settlement calls
- [x] Trusted Base Sepolia cold-start settlement: HTTP 402 challenge, first paid
  action HTTP 202 without warm-up, correlated `succeeded`, and `settled: true`;
  transaction [`0x8629549e...f440c`](https://sepolia.basescan.org/tx/0x8629549e3e7907ae61cd10480144220cc03b4e9d52c08044959aa1f6bc4f440c)
- [x] CI evidence artifacts: MuJoCo, Sim-to-Sim, and Base Sepolia JSON uploaded
  by [workflow run 31418321557](https://github.com/RobotDeveloper1/RoboPay/actions/runs/31418321557)
- [x] Paid-action simulator screen recording paired with the terminal evidence:
  stable simultaneous layout, exact source commit `34b3106`, unpaid HTTP 402,
  first paid HTTP 202, visibly distinct left/center/right poses, correlated
  `succeeded`, post-success settlement, and the matching BaseScan success page.
  The [evidence manifest](evidence/evidence-manifest.yaml) binds action
  `k1-active-inspection-1786793529`, transaction `0xf0ffd8d9...ad95a`, recording
  SHA-256 `92c1ae57...d791f0`, and the committed trusted JSON artifact.

Commands and machine-readable outputs are documented in the bridge README.
The workflow artifact remains authoritative for automated CI. The final visual
run is separately bound to its executed source commit in
`docs/evidence/evidence-manifest.yaml`; its trusted JSON result is committed in
the same directory. Private configuration and downloaded third-party assets
remain ignored.

## Safety and limitations

- Simulator-only; no physical K1 hardware has been validated.
- The support stand fixes only the root. All 22 robot joints retain their
  official dynamics and actuator limits.
- Both feet visibly rest on the simulator floor; the stand prevents falling
  and is not presented as an autonomous-balance controller.
- No free-base pose is written during execution.
- `stop` applies a neutral motor command and zeros articulated velocity.
- Unknown actions and out-of-contract parameters fail before simulator entry.
- Settlement occurs only after an exactly correlated successful result.
