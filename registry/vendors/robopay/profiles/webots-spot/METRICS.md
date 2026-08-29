# Simulator Metrics Reference

This file documents the metrics exported by the Webots Spot controller and bridge during action execution.

## State File Metrics

The controller writes state to `${ROBOPAY_WEBOTS_STATE_FILE}` with the following schema:

```json
{
  "command": "walk|stand|sit|move_forward|move_backward|stop",
  "execution_state": "idle|running|success|failed|timeout|error",
  "terminal_state": "success|failed|timeout|error",
  "behavior": "idling|standing|walking|sitting|moving|halted|error",
  "position": {
    "x": 0.5,
    "y": 0.0,
    "z": 0.0
  },
  "target_pose": {
    "x": 1.0,
    "y": 0.0,
    "z": 0.0
  },
  "elapsed_seconds": 1.23,
  "timestamp": "2026-07-25T12:34:56.789Z"
}
```

## Bridge Response Metrics

The bridge exports simulator metrics in the `simulator_metrics` field of action responses:

```json
{
  "actionId": "paid-walk-001",
  "status": "completed|failed|rejected|timeout",
  "execution_time_ms": 1234,
  "settled": true|false,
  "simulator_metrics": {
    "execution_state": "success|running|timeout|error",
    "terminal_state": "success|failed|timeout|error",
    "position": {
      "x": 0.5,
      "y": 0.0,
      "z": 0.0
    },
    "target_pose": {
      "x": 1.0,
      "y": 0.0,
      "z": 0.0
    },
    "command": "walk",
    "transport": "state-file|zenoh|cli",
    "payment_verified": true|false
  }
}
```

## Key Metrics

### execution_state
Current execution state of the action. Transitions:
- `idle` → `running` (action started)
- `running` → `success` (action completed, goal reached)
- `running` → `failed` (error during execution)
- `running` → `timeout` (exceeded MAX_ACTION_DURATION_SECONDS)

### terminal_state
Terminal state reached at end of action (only set when action completes):
- `success`: Goal reached within tolerance and time constraints
- `failed`: Error or constraint violation
- `timeout`: Exceeded maximum duration
- `error`: Communication or device error

### position
Current GPS-derived position `[x, y, z]` in simulator coordinates.

### target_pose
Target pose computed for walk actions:
- Calculated as current position + 0.5m forward offset
- For stand/sit: unchanged from current position

### elapsed_seconds
Wall-clock time elapsed since action start. Settlement gate checks:
- `success`: elapsed < MAX_ACTION_DURATION_SECONDS (10s)
- `timeout`: elapsed ≥ MAX_ACTION_DURATION_SECONDS

### payment_verified
Boolean flag set by Tunnel verification. Settlement requires:
- `payment_verified = true` AND
- `terminal_state = "success"`
