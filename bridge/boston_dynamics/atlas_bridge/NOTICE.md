# Third-party model attribution

## Boston Dynamics Atlas v4

The Atlas v4 robot description used by this bridge is **not vendored in this
repository**. It is fetched at setup time from a pinned upstream commit, exactly
as recorded in [`models/model.lock.json`](models/model.lock.json):

| Field | Value |
| --- | --- |
| Upstream | <https://github.com/openai/roboschool> |
| Commit | `d32bcb2b35b94168b5ce27233ca62f3c8678886f` |
| Path | `roboschool/models_robot/atlas_description` |
| File | `urdf/atlas_v4_with_multisense.urdf` |
| License | MIT (`LICENSE.md` at the roboschool repository root) |

Roboschool is distributed by OpenAI under the MIT License. The Atlas robot and
the Atlas name are property of Boston Dynamics; the description files are used
here only to simulate the robot, and no Boston Dynamics source or binary is
redistributed by this repository.

Run `python -m bridge.boston_dynamics.atlas_bridge.download_atlas_model` to
fetch the description into the local cache before running any simulator.
