# Model Card — TRON2 RL deployment policies

> **Status:** every entry below is a **template** with `⚠ TO CONFIRM`
> placeholders. The model owner and legal must complete each field
> before the first public release. Do **not** cut a public tag while
> any `⚠ TO CONFIRM` remains.

This document covers the four ONNX weight files checked into
`controllers/model/` in this repository. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) §2 for the
license-status summary; this file is the operational / behavioral
model card.

## Model index

| ID | Path | Size (bytes) | SHA-256 | Role |
|----|------|-------------:|---------|------|
| `SF_TRON2A/policy.onnx`  | `controllers/model/SF_TRON2A/policy.onnx`  | 791 050 | `0b353a087c912c33b9ba690560f3501cf7bf2bf25fde91c07ee2bdfb36502d3a` | Policy network for `SF_TRON2A` (sole-ankle biped) |
| `SF_TRON2A/encoder.onnx` | `controllers/model/SF_TRON2A/encoder.onnx` | 588 998 | `5f7e2b8865fda7c284f0dd98b79f5c1d78935c83cbf43bf311d768154937111e` | Observation encoder for `SF_TRON2A` |
| `WF_TRON2A/policy.onnx`  | `controllers/model/WF_TRON2A/policy.onnx`  | 770 148 | `3000df452681056738a15b46fa67f4f8436b34bbb6dcc6b22fa08b1b1f8dd071` | Policy network for `WF_TRON2A` (wheeled-foot biped) |
| `WF_TRON2A/encoder.onnx` | `controllers/model/WF_TRON2A/encoder.onnx` | 503 276 | `507d0630d78873f7aabfeab4eae9d7669610d709fcc903c4296d1908da54b3e7` | Observation encoder for `WF_TRON2A` |

SHA-256 recorded 2026-07-16. All four blobs are byte-identical to
`tron2-rl-deploy-ros/tron2_controllers/config/{SF,WF}_TRON2A/policy/{policy,encoder}.onnx`
in the sibling repository — this file **and** that repo's
`THIRD_PARTY_NOTICES.md §3` must be updated together.

Consumed by:

- `SF_TRON2A/*` → `controllers/SolefootController.py`
- `WF_TRON2A/*` → `controllers/WheelfootController.py`

---

## SF_TRON2A/policy.onnx

- **Path:** `controllers/model/SF_TRON2A/policy.onnx`
- **Size / SHA-256:** 791 050 B — `0b353a087c912c33b9ba690560f3501cf7bf2bf25fde91c07ee2bdfb36502d3a`
- **Checkpoint id / hash:** ⚠ TO CONFIRM (map SHA-256 above to the internal training-run checkpoint id)
- **Training run (framework, commit, date):** ⚠ TO CONFIRM
- **Training data description:** ⚠ TO CONFIRM (simulator + domain
  randomization ranges, or real-world logs, or both — with data
  license status)
- **Evaluation:** ⚠ TO CONFIRM (sim benchmark; real-hardware velocity
  tracking, joint-limit safety, thermal envelope)
- **Intended use:** locomotion control for the `SF_TRON2A`
  (sole-ankle) variant, driven at the tick rate configured in
  `controllers/model/SF_TRON2A/params.yaml`, on a robot suspended /
  mounted for initial bring-up.
- **Out-of-scope use:** any hardware variant other than `SF_TRON2A`;
  any operating envelope outside the training / evaluation
  distribution; unsuspended operation before the operator has
  confirmed the target behavior.
- **Known limitations:** ⚠ TO CONFIRM
- **Redistribution status:** ⚠ TO CONFIRM (Apache-2.0 with the code /
  separate license / controlled external download only)

## SF_TRON2A/encoder.onnx

- **Path:** `controllers/model/SF_TRON2A/encoder.onnx`
- **Size / SHA-256:** 588 998 B — `5f7e2b8865fda7c284f0dd98b79f5c1d78935c83cbf43bf311d768154937111e`
- **Checkpoint id / hash:** ⚠ TO CONFIRM
- **Training run:** ⚠ TO CONFIRM
- **Training data description:** ⚠ TO CONFIRM
- **Evaluation:** ⚠ TO CONFIRM
- **Intended use:** observation encoding paired with the SF policy
  above; not intended to be used with any other policy.
- **Out-of-scope use:** as above.
- **Known limitations:** ⚠ TO CONFIRM
- **Redistribution status:** ⚠ TO CONFIRM

## WF_TRON2A/policy.onnx

- **Path:** `controllers/model/WF_TRON2A/policy.onnx`
- **Size / SHA-256:** 770 148 B — `3000df452681056738a15b46fa67f4f8436b34bbb6dcc6b22fa08b1b1f8dd071`
- **Checkpoint id / hash:** ⚠ TO CONFIRM
- **Training run:** ⚠ TO CONFIRM
- **Training data description:** ⚠ TO CONFIRM
- **Evaluation:** ⚠ TO CONFIRM
- **Intended use:** locomotion control for the `WF_TRON2A`
  (wheeled-foot) variant, driven at the tick rate configured in
  `controllers/model/WF_TRON2A/params.yaml`, on a robot suspended /
  mounted for initial bring-up.
- **Out-of-scope use:** any hardware variant other than `WF_TRON2A`;
  operating envelope outside the training / evaluation distribution;
  unsuspended operation before the operator has confirmed behavior.
- **Known limitations:** ⚠ TO CONFIRM
- **Redistribution status:** ⚠ TO CONFIRM

## WF_TRON2A/encoder.onnx

- **Path:** `controllers/model/WF_TRON2A/encoder.onnx`
- **Size / SHA-256:** 503 276 B — `507d0630d78873f7aabfeab4eae9d7669610d709fcc903c4296d1908da54b3e7`
- **Checkpoint id / hash:** ⚠ TO CONFIRM
- **Training run:** ⚠ TO CONFIRM
- **Training data description:** ⚠ TO CONFIRM
- **Evaluation:** ⚠ TO CONFIRM
- **Intended use:** observation encoding paired with the WF policy
  above; not intended to be used with any other policy.
- **Out-of-scope use:** as above.
- **Known limitations:** ⚠ TO CONFIRM
- **Redistribution status:** ⚠ TO CONFIRM

---

## Update procedure

Any change to a `*.onnx` file under `controllers/model/` must, in the
same pull request:

1. Update the size in the "Model index" table above.
2. Update the checkpoint id / hash and training-run fields in the
   corresponding section.
3. Update the matching row in
   [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) §2.
4. Update [`CHANGELOG.md`](CHANGELOG.md) under `[Unreleased]`.

CI enforces (1) and (3): a `*.onnx` addition without a matching
`THIRD_PARTY_NOTICES.md` and `MODEL_CARD.md` reference will fail the
merge.
