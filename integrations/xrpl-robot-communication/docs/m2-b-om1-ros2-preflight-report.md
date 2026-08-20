# M2-B OM1 / ROS2 Preflight Report

Date: 2026-08-19

## Scope

M2-B validates:

```text
XRPL x402 paid robot action
-> signed ActionEnvelope
-> Zenoh topic robot/tunnel/action
-> OM1 bridge
-> ROS2 /cmd_vel
```

M2-B is complete only when `/cmd_vel` is observed from ROS2 after an XRPL paid action is accepted.

## Current Machine Preflight

Repository:

```text
C:\workspace\XRPL-robot-communication
```

Observed environment:

```text
Windows shell ros2 command: unavailable
WSL distro: Ubuntu running
WSL /opt/ros/humble/setup.bash: missing
WSL ros2 command: unavailable
WSL node command: unavailable
WSL npm command: unavailable
WSL zenoh command: unavailable
WSL zenohd command: unavailable
WSL Python zenoh module: unavailable
OM1 bridge path under ~/workspace: not found
```

WSL also printed:

```text
wsl: Failed to mount E:\, see dmesg for more details.
```

That mount warning is not the main blocker. The main blocker is that ROS2 Humble and the OM1 bridge runtime are not present in this local WSL environment.

## Result

```text
M2-B was not executed on this machine.
ROS2 /cmd_vel was not observed.
OM1-sim/G1 movement was not observed.
```

Current verified milestone remains:

```text
M2-A complete: XRPL testnet payment -> signed ActionEnvelope -> real Zenoh publish -> zenoh sub received robot/tunnel/action.
```

## Required OM1 / ROS2 Machine

Run M2-B on a machine that has:

```text
Ubuntu 22.04 or compatible Linux
ROS2 Humble
geometry_msgs/msg/Twist
OM1 bridge source/runtime
Zenoh router or peer connectivity
Node.js and npm for XRPL-robot-communication
XRPL testnet payer seed available only as a local environment variable
```

## Reproduction Commands

Terminal 1, Zenoh router:

```bash
zenohd
```

Terminal 2, ROS2 `/cmd_vel` echo:

```bash
source /opt/ros/humble/setup.bash && ros2 topic echo /cmd_vel geometry_msgs/msg/Twist
```

Terminal 3, OM1 bridge:

```bash
cd ~/workspace/fabric_om1/fabric_om1sim_g1_bridge && source /opt/ros/humble/setup.bash && python3 src/fabric_to_om1_adapter.py --zenoh-topic robot/tunnel/action --cmd-vel-topic /cmd_vel --zenoh-endpoint tcp/127.0.0.1:7447
```

Terminal 4, XRPL action gateway:

```bash
cd ~/workspace/XRPL-robot-communication && XRPL_FACILITATOR_URL=https://xrpl-facilitator-testnet.t54.ai XRPL_NETWORK=xrpl:1 XRPL_ASSET=XRP XRPL_AMOUNT=1000 XRPL_PAY_TO=<XRPL testnet receive address> PUBLISHER=zenoh-cli ZENOH_TOPIC=robot/tunnel/action npm run dev:xrpl-testnet-actions
```

Terminal 5, send paid action:

```bash
cd ~/workspace/XRPL-robot-communication && XRPL_BUYER_SEED=<XRPL testnet payer seed> SKILL_ID=move_forward IDEMPOTENCY_KEY=xrpl-m2b-move-001 npm run send:xrpl-testnet-action
```

## Expected `/cmd_vel`

For `move_forward`:

```text
linear.x > 0
angular.z = 0
```

Additional skills to validate:

```text
turn_left -> angular.z > 0
turn_right -> angular.z < 0
stop -> zero Twist
```

## Completion Rule

Do not mark M2-B complete until:

```text
ros2 topic echo /cmd_vel
```

shows the expected Twist output after a paid XRPL action.
