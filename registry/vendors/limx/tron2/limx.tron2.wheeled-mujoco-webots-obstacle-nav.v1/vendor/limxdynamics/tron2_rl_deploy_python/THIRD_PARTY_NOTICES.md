# Third-Party Notices

`tron2-rl-deploy-python` (TRON2 RL deployment — Python) is distributed
under the Apache License 2.0 (see [`LICENSE`](LICENSE) and
[`NOTICE`](NOTICE)).

This file lists third-party components, vendored / referenced runtime
dependencies, the LimX SDK submodule, the checked-in ONNX model files,
and documentation media, so downstream users can comply with all
applicable licenses and re-distribution terms.

> **Status:** items marked `⚠ TO CONFIRM` are pending sign-off from the
> model / SDK / product / legal owners. Do not cut a public release
> while any `⚠ TO CONFIRM` entry remains. Model weights and SDK
> re-distribution are the two highest-risk items in this repository —
> see also [`MODEL_CARD.md`](MODEL_CARD.md).

---

## 1. First-party source (LimX Dynamics)

| Path | Kind | License | Notes |
|------|------|---------|-------|
| `main.py` | Python entry point | Apache-2.0 | Selects SF/WF controller by `ROBOT_TYPE`. |
| `controllers/__init__.py` | Python package init | Apache-2.0 | |
| `controllers/SolefootController.py` | Python control code | Apache-2.0 | Real-hardware controller: loads ONNX, drives q / dq / tau / Kp / Kd. |
| `controllers/WheelfootController.py` | Python control code | Apache-2.0 | Real-hardware controller: loads ONNX, drives q / dq / tau / Kp / Kd. |
| `controllers/model/*/params.yaml` | Configuration | Apache-2.0 ⚠ TO CONFIRM | Confirm no per-serial calibration constants are embedded. |

---

## 2. Checked-in model weights (ONNX)

This repository ships **four ONNX files** under `controllers/model/`.
They are **binary weights, not source**, and their license status is
independent of the Apache-2.0 license on the surrounding code.

| Path | Size (bytes) | SHA-256 | Model card row | Training run | Training data source | Redistribution |
|------|-------------:|---------|----------------|--------------|----------------------|----------------|
| `controllers/model/SF_TRON2A/policy.onnx`  | 791 050 | `0b353a087c912c33b9ba690560f3501cf7bf2bf25fde91c07ee2bdfb36502d3a` | [SF_TRON2A / policy](MODEL_CARD.md#sf_tron2apolicyonnx)  | ⚠ TO CONFIRM | ⚠ TO CONFIRM | ⚠ TO CONFIRM |
| `controllers/model/SF_TRON2A/encoder.onnx` | 588 998 | `5f7e2b8865fda7c284f0dd98b79f5c1d78935c83cbf43bf311d768154937111e` | [SF_TRON2A / encoder](MODEL_CARD.md#sf_tron2aencoderonnx) | ⚠ TO CONFIRM | ⚠ TO CONFIRM | ⚠ TO CONFIRM |
| `controllers/model/WF_TRON2A/policy.onnx`  | 770 148 | `3000df452681056738a15b46fa67f4f8436b34bbb6dcc6b22fa08b1b1f8dd071` | [WF_TRON2A / policy](MODEL_CARD.md#wf_tron2apolicyonnx)  | ⚠ TO CONFIRM | ⚠ TO CONFIRM | ⚠ TO CONFIRM |
| `controllers/model/WF_TRON2A/encoder.onnx` | 503 276 | `507d0630d78873f7aabfeab4eae9d7669610d709fcc903c4296d1908da54b3e7` | [WF_TRON2A / encoder](MODEL_CARD.md#wf_tron2aencoderonnx) | ⚠ TO CONFIRM | ⚠ TO CONFIRM | ⚠ TO CONFIRM |

Evidence collected 2026-07-16: SHA-256 digests computed by `sha256sum`
on the tracked working-tree files. All four ONNX blobs are
**byte-identical** to the corresponding files in the sibling
`tron2-rl-deploy-ros/tron2_controllers/config/{SF,WF}_TRON2A/policy/`
paths — this is one set of models under two locations. Any owner
decision must be applied to both repos consistently. Reproduce with:

```bash
sha256sum controllers/model/*/policy.onnx controllers/model/*/encoder.onnx
```

**Owner action required.** For each ONNX file above, the model owner
and legal must confirm:

1. **Provenance** — the exact training run / checkpoint / commit that
   produced this weight blob (SHA-256 recorded above pins the object).
2. **Training data** — that no proprietary, customer, or third-party
   licensed data was used, or that any such data permits releasing a
   derived policy under Apache-2.0.
3. **Redistribution** — whether the weight blob itself may be
   re-distributed under Apache-2.0, under a separate license, or must
   be moved to a controlled external download.

Until every row above is cleared, the model files remain in the tree
under an explicit `⚠ TO CONFIRM` hold. Do **not** silently remove them —
this is a legal / policy decision, not an engineering one.

---

## 3. LimX SDK submodule

| Item | Value |
|------|-------|
| Submodule path | `limxsdk-lowlevel/` |
| Upstream URL | `https://github.com/limxdynamics/limxsdk-lowlevel.git` |
| Pinned commit | `17a4b25d40d3a71435d2144ac668e72784cc4179` (see `.gitmodules`) |
| Public upstream reachability | ✅ verified 2026-07-16 — the pin corresponds to upstream tag **`2.2.0`** (`git ls-remote https://github.com/limxdynamics/limxsdk-lowlevel.git` returns the same object at `refs/tags/2.2.0`). |
| License | ⚠ TO CONFIRM (SDK upstream repository) |
| Redistribution as a dependency | ⚠ TO CONFIRM |
| Runtime linkage | Python (`import limxsdk`) via vendor wheel |

The SDK is consumed at runtime through a wheel that users install
manually (see `README.md` — "Environment setup"). This repository
**does not** ship the wheel, `.so`, or `.dll` binaries. The submodule
pin is intentionally not moved; the SDK owner must clear (a) the
upstream repository's license and (b) whether depending on it via a
submodule is acceptable for a public release. Do **not** change the
submodule pin without vendor / owner sign-off.

---

## 4. Python runtime dependencies (not vendored)

The following are **runtime dependencies only** — they are neither
vendored nor packaged in this repository, but downstream users must
install them (`pip install …`) to run `main.py`.

| Package | Purpose | License | Notes |
|---------|---------|---------|-------|
| **onnxruntime** | ONNX inference for policy / encoder | MIT | Critical dependency: without it, `SolefootController` / `WheelfootController` cannot load `policy.onnx` / `encoder.onnx`. Users install `onnxruntime` (CPU) or `onnxruntime-gpu` per their platform. |
| numpy | Array math in the observation / action pipeline | BSD-3-Clause | |
| scipy | `scipy.spatial.transform.Rotation` (IMU quat → rotation) | BSD-3-Clause | |
| PyYAML | Load `controllers/model/*/params.yaml` | MIT | |
| pygame | Optional joystick input (`use_pygame_joystick=True`) | LGPL-2.1-or-later | Optional. If a downstream distribution links against pygame in a way that triggers LGPL obligations, document it in the downstream distribution — this repository imports it lazily. |
| limxsdk (vendor wheel) | Robot connection, state / IMU / joystick / cmd IO | ⚠ TO CONFIRM | Installed by the user from `limxsdk-lowlevel/python3/{amd64,aarch64}/limxsdk-*-py3-none-any.whl`. See §3. |

None of the above are bundled here; users must obtain and license them
independently.

---

## 5. Documentation media

| Path | Kind | Provenance | License |
|------|------|------------|---------|
| `doc/deploy.jpg` | Photo / render | ⚠ TO CONFIRM | ⚠ TO CONFIRM |
| `doc/sf.GIF` | Real-world video capture | ⚠ TO CONFIRM | ⚠ TO CONFIRM |
| `doc/wf.GIF` | Real-world video capture | ⚠ TO CONFIRM | ⚠ TO CONFIRM |
| `doc/sfmj-ezgif.com-video-to-gif-converter.gif` | Simulation capture | ⚠ TO CONFIRM | ⚠ TO CONFIRM |
| `doc/wfmj-ezgif.com-video-to-gif-converter.gif` | Simulation capture | ⚠ TO CONFIRM | ⚠ TO CONFIRM |

Before release, run:

```bash
exiftool doc/*.jpg doc/*.GIF doc/*.gif \
  | grep -iE '(gps|serial|make|model|software|author|artist|copyright)'
```

and strip anything that discloses office locations, camera serials,
individual contributors' names, or non-public products, unless the
content owner intentionally keeps it:

```bash
exiftool -all= doc/*.jpg doc/*.GIF doc/*.gif
```

Real-world captures (`sf.GIF`, `wf.GIF`, `deploy.jpg`) additionally
require a **content review** for visible individuals, facility
identifiers, or non-public hardware.

---

## 6. What this repository does **not** include

- No SDK binaries (`.so`, `.dll`, `.dylib`, `.lib`, `.whl`) — SDK is
  installed by the user from a vendor wheel.
- No PyTorch / training checkpoints (`.pt`, `.pth`, `.ckpt`) — only the
  four ONNX inference blobs listed in §2.
- No factory calibration values or per-serial calibration files.
- No motion / bag / trajectory data.
- No firmware.
- No customer- or site-specific configuration.
- No pre-baked robot IPs; the `<robot-ip>` token in `README.md` is a
  placeholder that users substitute with their own robot / simulator
  IP (see `SECURITY.md#private-ip-handling`).

For robot description assets (URDF / MuJoCo XML / meshes), see the
sibling `robot-description` repository.

---

## 7. Update procedure

Whenever a model, submodule pin, dependency, or documentation image is
added or changed:

1. Update the corresponding row in this file.
2. Re-run the EXIF strip (§5) and, for new images / GIFs, a visual
   content review.
3. If a new `*.onnx` is added, **also** add a row in §2 and a matching
   section in [`MODEL_CARD.md`](MODEL_CARD.md). CI will fail the merge
   otherwise.
4. If the change touches an `⚠ TO CONFIRM` row, block the merge on
   written sign-off from the responsible owner.
