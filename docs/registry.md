# Profile Registry

Every robot profile submitted to this repository — whether for a bounty, a
vendor showcase, or a simulation study — lives under `registry/vendors/`.
This document is the contract for how a profile is laid out, named, and
validated. Follow it and a reviewer can understand your submission in minutes.

> **Status: living standard.** The registry is young and this guide grows with
> it. The CI validator (`scripts/validate_registry.py`) is deliberately
> permissive; the invariants below that it enforces today are marked **[CI]**.

## Layout

```
registry/
└── vendors/
    └── <vendor>/                # lowercase, e.g. unitree, boston-dynamics
        └── <robot>/             # lowercase, e.g. go2, spot
            └── <profile>.v1/    # one directory per profile version
                ├── robot.profile.yaml        # the descriptor (required)
                ├── skills.yaml               # priced/registered skills
                ├── functions.yaml            # action catalogue
                ├── payment-policy.yaml       # pricing + settlement rules
                ├── execution-mapping.yaml    # action → simulator/robot mapping
                ├── examples/                 # sample action envelopes
                ├── tests/                    # contract tests
                └── docs/
                    ├── README.md             # how to run the profile
                    └── validation-report.md  # evidence of validation
```

Only the **descriptor** is mandatory. The rest of the catalogue is strongly
recommended — it is what makes a profile machine-readable and auditable.

## Naming conventions

- **Vendor and robot directories**: lowercase, hyphenated (`boston-dynamics`,
  `deep-robotics-m20-pro`).
- **Profile directory**: `<vendor>.<robot>.<simulation>.v<N>`, matching the
  descriptor's `profileId` (e.g. `unitree.go2.mujoco-pybullet-sim.v1`).
- **Versioning**: keep `.v1/` as the directory; bump `profileVersion` inside
  the descriptor for revisions. Create `.v2/` only for a breaking contract.
- **Profile directory names must be unique across the whole registry** — this
  is what stops two submissions from silently claiming the same robot profile
  id. **[CI]**

## The descriptor (`robot.profile.yaml`)

The single source of truth about a profile. Recommended fields:

| Field | Meaning |
|---|---|
| `schemaVersion` | `robot-profile.v1` |
| `vendor` / `robotModel` | the robot identity |
| `robotType` | the simulator/approach, e.g. `mujoco-pybullet-sim` |
| `profileId` / `profileVersion` | stable id and revision (see naming) |
| `runtime` | transport, Zenoh topics, bridge, simulators |
| `maintainers` | GitHub handles responsible for the profile |
| `status` | `experimental` / `validated` |
| `scope` | honesty boundary, e.g. `simulator-only` |

Keep the descriptor declarative. Do **not** embed secrets, private keys,
wallet private material, or local machine paths.

## Reserved Zenoh topics

The shared tunnel publishes actions on **`robot/tunnel/action`** and results on
**`robot/tunnel/result`**. Config/registration traffic uses the
`robot/config/…` namespace. Profiles must consume exactly those topics — never
invent a parallel namespace for the same traffic, or the backend bridge will
not see it.

## Validation

`.github/workflows/registry-validation.yml` runs on every change touching
`registry/**` and enforces:

1. every YAML/JSON file in the registry **parses**; **[CI]**
2. profile directory names are **unique**; **[CI]**
3. every profile directory carries a **descriptor**. **[advisory]**

It also prints a full inventory for reviewers. To validate locally:

```sh
python scripts/validate_registry.py registry
```

> **Extensibility.** The CI job also acts as the registry gate's shell: any
> stricter per-profile validator placed at `scripts/registry/validate_*.py` is
> auto-discovered and enforced by the same job. Permissive structure checks and
> strict cross-document checks coexist under one gate instead of diverging.

## Submission checklist

- [ ] Profile lives under `registry/vendors/<vendor>/<robot>/<profile>.v1/`
- [ ] `robot.profile.yaml` present with `vendor`, `robotModel`, `profileId`,
      `profileVersion`, `runtime`, `maintainers`
- [ ] Profile directory name is unique
- [ ] All YAML/JSON parses (`python scripts/validate_registry.py registry` is clean)
- [ ] Uses the reserved Zenoh topics (`robot/tunnel/action`, `robot/tunnel/result`)
- [ ] `docs/validation-report.md` describes exactly what was validated — and
      what was **not** (e.g. "settlement simulated", "hardware untested")
- [ ] No secrets, keys, or local paths committed
