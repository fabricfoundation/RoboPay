# Contributing to RoboPay

Thanks for contributing to RoboPay — the paid-action runtime that connects
robots, simulators, cameras, drones, and other physical devices to the Fabric
network.

This guide explains how to contribute well: how to structure a **robot profile
submission**, what the **platform conventions** are, and what a reviewer will
check. Read it once before opening a PR and you will save everyone a round
trip.

---

## What can you contribute?

Two kinds of contributions, both valuable:

1. **Robot profiles (bounty submissions)** — a concrete robot + simulation +
   paid skill set, expressed under `registry/vendors/`. This is how the robot
   bounties are claimed.
2. **Platform infrastructure** — the shared pieces every profile depends on:
   the Go tunnel, the Python/ROS2 bridges, CI workflows, validators,
   documentation. Improvements here benefit every robot at once.

---

## Repository layout

```
.
├── tunnel/          # Go tunnel + x402 paid-action runtime (payment-verified actions)
├── bridge/          # ROS2 + Python adapters: Zenoh action events → robot commands
│   └── common/zenoh_bridge/                 # shared, runtime-independent helpers
├── registry/        # robot profiles: registry/vendors/<vendor>/<robot>/<profile>.v1/
├── scripts/         # validators and tooling (registry validation, …)
├── docs/            # architecture docs and images
├── Makefile         # builds/runs/tests the tunnel and the bridge
└── pyproject.toml   # Python quality baseline (ruff + pytest)
```

The tunnel keeps an outbound WebSocket to the Fabric proxy, verifies x402
micropayments, and publishes only **payment-verified** actions on the Zenoh
topic `robot/tunnel/action`. The bridge subscribes there and drives the
robot. **Payment, routing, and execution stay separated by design.**

---

## Contributing a robot profile

A profile is a self-contained directory:

```
registry/vendors/<vendor>/<robot>/<profile>.v1/
├── robot.profile.yaml     # identity, runtime, maintainers, scope
├── skills.yaml            # the paid skills this robot exposes
├── functions.yaml         # function/action signatures the skills map to
├── payment-policy.yaml    # per-skill pricing and settlement rules
├── execution-mapping.yaml # how an action reaches the simulator/bridge
├── skill-catalog.json     # machine-readable skill catalogue
├── docs/
│   └── validation-report.md   # what was validated — and what was NOT
├── examples/              # sample action envelopes (JSON)
└── tests/                 # contract tests for the skills (skill-contract.test.yaml, …)
```

### Naming

- Directory names are lowercase and hyphenated: `unitree.go2.mujoco-webots-obstacle-nav.v1`.
- The directory name must match `profileId` in `robot.profile.yaml`.
- Profile directory names must be **unique** across the whole registry — this
  is what prevents cross-PR collisions on robot/profile identities.

### The Zenoh action contract

The bridge-facing contract is the envelope the tunnel publishes on
`robot/tunnel/action`:

```json
{
  "payload": { "action": "navigate_obstacles", "params": { "waypoints": [] } },
  "transaction_details": {},
  "timestamp": "2026-01-01T00:00:00Z"
}
```

- `payload.action` must match a skill id declared in `skills.yaml`.
- `payload.params` must satisfy the skill's parameter schema.
- Do **not** invent a parallel topic namespace for the same traffic — the
  backend bridge will not see it.

### The honesty rule

`docs/validation-report.md` must describe **exactly** what was validated and
what was not: real hardware vs simulation, live settlement vs simulated
settlement, manual vs automated evidence. A precise, humble report is worth
more than a confident one — reviewers verify claims against code and CI logs.

### Submitting

1. Branch from the latest `main`.
2. Build the profile directory, the bridge wiring, and the evidence.
3. Run the registry validator locally:
   ```sh
   python scripts/validate_registry.py registry
   ```
   It must exit cleanly. Fix any unparseable YAML/JSON or duplicate names.
4. Open the PR with a description that names the **skills**, the **simulation
   stack**, the **verification** you actually ran, and the **honest gaps**.

---

## Platform contribution conventions

### Go (`tunnel/`)

- Format with `gofmt`; keep CI's `golangci-lint` green (`make lint`).
- Tests are table-driven and live next to the code (`*_test.go`).
- Run the full check before pushing:
  ```sh
  cd tunnel && go test ./... && go vet ./...
  ```

### Python (`bridge/`, `scripts/`)

- Lint and format with `ruff` (config in `pyproject.toml`):
  ```sh
  ruff check .
  ruff format --check .
  ```
- New logic gets unit tests under `bridge/common/zenoh_bridge/tests/`:
  ```sh
  pytest -v
  ```
- The lint scope deliberately covers the **pure-logic** modules that are
  testable without a ROS 2 runtime; keep it that way as the baseline grows.

### CI workflows (`.github/workflows/`)

- Pin third-party actions to **full commit SHAs** (no floating tags, no
  `master` install scripts).
- Give every job `timeout-minutes`, least-privilege `permissions:
  contents: read`, and `concurrency` with `cancel-in-progress: true`.
- Use `paths` filters so workflows only run when their files change.

### Commits and PRs

- Conventional commits: `feat:`, `fix:`, `ci:`, `docs:`, `refactor:`, `test:`.
- One logical change per PR; small diffs review faster and merge sooner.
- Do **not** commit secrets, keys, or local absolute paths (see
  `tunnel/.env.example` for what an env file looks like).

---

## Review checklist (what maintainers verify)

1. Profile directory is unique and correctly named (`profileId` == dir name).
2. Every YAML/JSON in the registry parses; validator is clean.
3. Skills declared in `skills.yaml` have matching `functions.yaml`,
   `payment-policy.yaml`, and `execution-mapping.yaml` entries.
4. `docs/validation-report.md` states what was validated — and what was not.
5. Claims (sim-to-sim agreement, settlement, motion) are reproducible from
   the PR's code, scripts, or CI logs.
6. No secrets committed.

---

## Getting help

- Open an issue for questions that affect the platform broadly.
- Mention a maintainer on the PR if a first-time-contributor CI approval is
  required to run the workflows.

Thank you for making RoboPay better — every profile and every platform fix
makes the whole network of paid machines more useful.
