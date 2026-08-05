# AGIBot X2 MuJoCo bridge

The package fails closed unless a MuJoCo model is loaded. The default
`models/x2_headless.xml` is a self-contained, primitive-geometry X2 model for
clean-checkout CI and reviewer evidence. `models/x2.xml` retains the vendor
visual model, whose optional meshes are Git-LFS assets in the upstream
`ioai-tech/robot_description` repository.

```bash
git clone https://github.com/ioai-tech/robot_description.git
git -C robot_description lfs pull
ros2 launch mujoco_bridge_agibot_x2 bridge.launch.py \
  model_path:=/absolute/path/to/robot_description/mjcf/agibot/x2.xml
```

The terminal result is published only after MuJoCo advances the policy and
reports a finite, measurable `state_delta`; missing models and failed policies
produce `FAILED` and never actuate `/cmd_vel`.

This package is the AGIBot X2 Tier‑1 integration for RoboPay. The RoboPay tunnel
performs x402 verification and settlement before publishing an accepted event
on `robot/tunnel/action`. This bridge parses that event, maps safe X2 commands,
publishes `/cmd_vel`, and optionally steps the official X2 MuJoCo model.

## Local ROS 2 run

```bash
colcon build --packages-select mujoco_bridge_agibot_x2
source install/setup.bash
ros2 launch mujoco_bridge_agibot_x2 bridge.launch.py \
  model_path:=/absolute/path/to/robot_description/mjcf/agibot/x2.xml
```

Safety limits are intentionally conservative: forward `0.5 m/s`, backward
`0.3 m/s`, turn `0.2 rad/s`. Unknown, `stop`, `standing_balance`, and
`wave_arm` actions produce a stationary base command unless a future X2 joint
controller is explicitly enabled.

## Verification

The package includes parser and mapper tests. CI runs Python compilation,
MuJoCo installation, and these tests on Ubuntu 22.04. A bounty submission
should additionally attach a recording of the x402 `402 → PAYMENT-SIGNATURE →
200 accepted → Zenoh → MuJoCo motion` flow.
