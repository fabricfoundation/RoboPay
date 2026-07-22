# Reachy Mini RoboPay Integration

A paid RoboPay action starts a closed-loop object-tracking episode on the official Hugging Face Reachy Mini model and validates the behavior across MuJoCo and Webots.

```text
x402 paid request
  -> Fabric Gateway
  -> RoboPay Tunnel
  -> Zenoh robot/tunnel/action
  -> Reachy Mini bridge
  -> FSM: SCANNING -> TRACKING -> EXPRESSIVE
  -> MuJoCo / Webots physics
  -> correlated robot/reachy_mini/metrics
```

## Simulation task

The Reachy Mini has no arms, so the implemented skill is an expressive head-tracking task. A paid action such as `look_at_apple` causes the controller to track the requested object using the official Reachy Mini MJCF, torso yaw, the six-DOF Stewart neck, and two antenna actuators.

The policy reads the live simulator state (`head_xmat` and target position) at every step and computes the control command dynamically. No recorded trajectory or predefined animation is replayed.

## Sim-to-sim validation

The same task policy is evaluated independently in MuJoCo and Webots. The Webots run launches the real Webots binary in batch/no-rendering mode and reads the result produced by the Webots controller.

Validated simulators:

- MuJoCo using the official Reachy Mini model;
- Webots R2025a in WSL2.

Representative results:

| Target | Simulator | Tracked | Success rate | Min error | Duration |
|---|---|---:|---:|---:|---:|
| Apple | MuJoCo | yes | 1.0 | 0.150 rad | 3.01 s |
| Croissant | MuJoCo | yes | 1.0 | 0.196 rad | 3.01 s |
| Duck | MuJoCo | yes | 1.0 | 0.473 rad | 3.01 s |
| Apple | Webots | yes | 1.0 | 0.340 rad | 12.00 s |
| Croissant | Webots | yes | 1.0 | 0.196 rad | 12.00 s |
| Duck | Webots | yes | 1.0 | 0.473 rad | 12.00 s |

Overall sim-to-sim robustness score: **1.0**.

## RoboPay payment integration

### Payment gate

`test_payment_gate.py` verifies that:

```text
unpaid POST /action -> HTTP 402 + x402 payment requirements
malformed JSON      -> HTTP 400
```

No unpaid action is published to the robot topic.

### Deterministic positive-payment path

`test_e2e_paid_action.py` uses the real compiled Go Tunnel binary, real Zenoh, the real Reachy Mini simulator, real MuJoCo execution, and real Webots sim-to-sim validation.

The local Fabric proxy and facilitator are deterministic test doubles only. The request still passes through the real Tunnel and produces a real Zenoh `ActionEvent`.

```text
paid HTTP request
  -> real Tunnel
  -> Zenoh ActionEvent
  -> Reachy Mini bridge
  -> correlated MuJoCo/Webots metrics
```

### Live Base Sepolia path

`test_base_sepolia_tunnel_e2e.py` validates the public payment path using the public Fabric Gateway API, the public x402 facilitator, the real Go Tunnel binary, Base Sepolia USDC settlement, and correlated simulator metrics.

The test only succeeds after both payment settlement and simulator correlation are confirmed.

### Complete live payment flow

The live request uses the following public endpoints and payment parameters:

| Component | Value |
|---|---|
| Fabric action endpoint | `https://api.fabric.foundation/api/core/robots/reachy-mini-kauker/action` |
| Tunnel WebSocket | `wss://api.fabric.foundation/api/core/ws/robot` |
| x402 facilitator | `https://x402.org/facilitator` |
| Network | `eip155:84532` (Base Sepolia) |
| Asset | USDC (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`) |
| Amount | `1000` base units (`$0.001`) |
| Payee | `0x39a315667d557B1425bb1e5D371DD66d300c98c1` |

The request sequence is:

```text
1. POST /robots/reachy-mini-kauker/action without payment
   -> Fabric Gateway returns HTTP 402 and x402 requirements.

2. The payer signs the returned requirements locally with an EVM wallet.

3. The signed PAYMENT-SIGNATURE is sent to the same public action endpoint.

4. The Fabric Gateway routes the request through the public Tunnel WebSocket.

5. The Tunnel sends the payment payload to the x402 facilitator for
   verification and settlement on Base Sepolia.

6. After successful settlement, the Tunnel publishes an ActionEvent on
   Zenoh topic robot/tunnel/action.

7. The Reachy Mini bridge executes the paid action and publishes metrics on
   robot/reachy_mini/metrics using the original request_id as correlation_id.
```

The successful live response was:

```http
HTTP/1.1 200 OK
Content-Type: application/json

{"status":"accepted"}
```

The facilitator settlement response was:

```json
{
  "success": true,
  "payer": "0x338FC32a408b601cAb027d867d8192C03895Ff61",
  "transaction": "0x92c91ab7fc9731ec9f05f485cd8e2ff5cd97998eda08d1da910c60e370159d7e",
  "network": "eip155:84532"
}
```

Live transaction evidence:

[Base Sepolia transaction](https://sepolia.basescan.org/tx/0x92c91ab7fc9731ec9f05f485cd8e2ff5cd97998eda08d1da910c60e370159d7e)

Observed live result:

```json
{
  "execution_status": "SUCCESS",
  "tracking_success_rate": 1.0,
  "task_completed": true,
  "simulators_evaluated": ["MuJoCo", "Webots"],
  "overall_sim2sim_robustness_score": 1.0
}
```

Correlation ID:

```text
base-sepolia-reachy-1784747256
```

## Tunnel fix

The Tunnel now uses the configured `ZENOH_CONFIG` for both the ActionEvent publisher and the configuration subscriber.

Previously, the Tunnel could return `HTTP 200` while opening Zenoh with a different configuration from the bridge. Publication failures are now returned as `HTTP 503` instead of being reported as successful payments.

## Reproduce locally

From the repository root:

```bash
make build
make test
python3 bridge/reachy_mini/test_e2e_paid_action.py
```

Expected result:

```text
paid request status=200
execution_status: SUCCESS
tracking_success_rate: 1.0
task_completed: true
simulators_evaluated: [MuJoCo, Webots]
overall_sim2sim_robustness_score: 1.0
OK
```

## Optional ROS2 execution

```bash
source /opt/ros/humble/setup.bash
source .venv_ros2/bin/activate

make ROBOT=reachy_mini bridge-build
make ROBOT=reachy_mini bridge-run
```

With the ROS2 bridge already running:

```bash
REACHY_BRIDGE_EXTERNAL=1 \
python3 bridge/reachy_mini/test_e2e_paid_action.py
```

No private keys or secrets are included in this PR.
