# RoboPay Webots Spot Tier 1 Robot Profile

## Profile Metadata

This profile defines the **RoboPay Webots Spot (Tier 1)** robot integration: a simulated Spot robot running in the Webots simulator with real actuator command handling and terminal-state-based settlement.

**Profile ID:** `robopay-webots-spot-tier1`  
**Simulator:** Webots  
**Execution Layer:** `registry/vendors/robopay/robopay_bridge.py`  
**Controller:** `webots_spot_controller.py`

## Enforced Trust Boundary

The Tunnel (`tunnel/`) acts as the enforced trust boundary:

1. **Payment Verification:** The tunnel runs x402 middleware and verifies payments before routing.
2. **Verified Action Injection:** Only requests with `payment_verified=True` reach the bridge.
3. **Replay Protection:** `PROCESSED_ACTIONS` deduplicates action IDs.
4. **No Direct Proof Validation:** The bridge does not validate `payment_proof` strings; it requires explicit Tunnel-set `payment_verified=True`.

## Real Actuator Command Execution

The Webots controller (`webots_spot_controller.py`) implements actual motor commands:

- **Device Interfaces:** GPS sensor for position, left/right wheel motors for velocity control.
- **Target Pose Computation:** Walk actions compute a 0.5m forward target from current position.
- **Motor Velocity Commands:** Motion commands set non-zero velocities to the wheels.

## Terminal State & Settlement

The simulator derives a terminal state (`success`, `failed`, `timeout`) from:

- **Position Feedback:** GPS-measured distance to target.
- **Time Tracking:** Elapsed action duration (max 10 seconds).
- **Success Criteria:** Within 0.05m of target or stand completes in >0.5s.
- **Failure Criteria:** Timeout after 10 seconds or network/device errors.

**Settlement Gate:**
- `settled = true` ⟺ `terminal_state = "success"` AND `payment_verified = true`
- `settled = false` ⟺ `terminal_state ∈ {timeout, failed, error}` OR `payment_verified = false`

## Sim-to-Sim Evidence Collection

### State File Logging

The controller writes its execution state to `${ROBOPAY_WEBOTS_STATE_FILE}` (default: `webots_state.json`). Each action writes:

```json
{
  "command": "walk",
  "execution_state": "success",
  "terminal_state": "success",
  "behavior": "walking",
  "position": {"x": 0.5, "y": 0.0, "z": 0.0},
  "target_pose": {"x": 1.0, "y": 0.0, "z": 0.0},
  "elapsed_seconds": 1.23
}
```

### Bridge Metrics Export

The bridge exports simulator metrics in every action response:

```json
{
  "actionId": "paid-walk-001",
  "status": "completed",
  "execution_time_ms": 1234,
  "settled": true,
  "simulator_metrics": {
    "execution_state": "success",
    "terminal_state": "success",
    "position": {"x": 0.5, "y": 0.0, "z": 0.0},
    "target_pose": {"x": 1.0, "y": 0.0, "z": 0.0},
    "command": "walk",
    "transport": "state-file",
    "payment_verified": true
  }
}
```

### Integration Test Output

Run `tests/test_integration_settlement.py` to generate annotated evidence:

```bash
$ python tests/test_integration_settlement.py
================================================================================
TIER 1 SETTLEMENT INTEGRATION TESTS
================================================================================

[SUCCESS CASE] actionId=paid-walk-001 settled=true
  Metrics: { "terminal_state": "success", "execution_state": "success", ... }

[FAILURE CASE] actionId=paid-walk-timeout settled=false (timeout)
  Metrics: { "terminal_state": "timeout", "execution_state": "timeout", ... }

[REJECTION CASE] actionId=unverified-walk settled=false (no tunnel verification)
  Reason: payment not verified by tunnel
```

## Test Coverage

### Unit Tests (`tests/test_robopay_bridge.py`)

- `test_normalize_action_*`: Action command normalization.
- `test_extract_simulator_metrics_*`: Metrics extraction from controller state.
- `test_build_result_settled_true_on_success_terminal_state`: Success settlement.
- `test_build_result_settled_false_on_failure_terminal_state`: Failure no-settlement.
- `test_tunnel_verified_payment_required_for_settlement`: Boundary verification.

### Integration Tests (`tests/test_integration_settlement.py`)

- `test_settlement_success_case_paid_action_terminal_success`: Complete paid → success → settled.
- `test_settlement_failure_case_timeout_no_settlement`: Timeout → no settlement.
- `test_settlement_rejected_no_tunnel_verification`: No verification → rejection.
- `test_terminal_state_computation_*`: Terminal state logic.

## Reproducible Execution

1. **Set State File:**
   ```bash
   export ROBOPAY_WEBOTS_STATE_FILE=$(mktemp)
   ```

2. **Run Bridge in Zenoh Mode:**
   ```bash
   python registry/vendors/robopay/robopay_bridge.py
   ```

3. **Publish Action Request (Verified):**
   ```bash
   python test_zenoh_loop.py  # Requires Zenoh setup
   ```

4. **Capture Metrics:**
   ```bash
   cat $ROBOPAY_WEBOTS_STATE_FILE | jq .
   ```

5. **Run Integration Tests:**
   ```bash
   python -m pytest tests/test_integration_settlement.py -v
   ```

## Verification Checklist

- [x] Tunnel enforces payment verification before settlement.
- [x] Controller implements real actuator commands (motor velocity).
- [x] Terminal state is derived from simulator feedback (GPS + timer).
- [x] Settlement only occurs on terminal success AND payment verified.
- [x] Failure cases (timeout) explicitly return `settled=false`.
- [x] Replay protection via action ID deduplication.
- [x] Metrics exported in bridge response and state file.
- [x] Integration tests demonstrate both success and failure paths.
