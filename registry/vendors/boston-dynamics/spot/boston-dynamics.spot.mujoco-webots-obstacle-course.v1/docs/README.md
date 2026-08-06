# Boston Dynamics Spot obstacle-course profile

Scope: simulator-only.  The profile executes a closed-loop Spot navigation
episode in MuJoCo and validates the same controller logic with the Webots Spot
PROTO.  It does not claim physical Boston Dynamics hardware validation.

The published contract currently exposes only `side: left`, because that is
the reference corridor with paired MuJoCo/Webots evidence. Its `payTo` address
is configured for Base Sepolia testing only; use a separately reviewed payee
and policy before enabling mainnet settlement.

The shared Gateway/Tunnel protocol currently identifies the robot with `?id=`
and does not define a signed robot-to-payee handshake. Per maintainer guidance,
that binding is tracked as an upstream protocol dependency rather than an
invented EIP-191 exchange. Deployment configuration binds the robot ID and
testnet payee; neither the Tunnel nor the Spot bridge receives or stores a
payer private key.

## Skill and payment quote

The registry profile supplies the simulator-only Spot identity, bounded
navigation skill, and safe-stop skill. `GET /robots/{robotId}/skills` exposes
their enabled state and price before a payer signs; this robot-scoped profile
supplies the schemas and movement limits. An unsigned request for that action
receives HTTP 402 from the public Gateway. Its `PAYMENT-REQUIRED` header is the
live x402 quote for the configured Base Sepolia USDC amount, network, and
payee; it is returned before any simulator actuation.

See `bridge/boston_dynamics/spot_mujoco_bridge/README.md` for setup and run
commands.  The recorded model source is MuJoCo Menagerie commit
`71f066ad0be9cd271f7ed58c030243ef157af9f4`; its BSD-3-Clause asset set is
downloaded locally instead of committed.

For recording a paid visual run, the Windows-native Spot bridge may opt in to
`SPOT_MUJOCO_VIEWER=1`. It starts the MuJoCo window only for the correlated
Zenoh action received from the Tunnel, holds the terminal pose for the bounded
`SPOT_MUJOCO_VIEWER_HOLD_SECONDS`, then publishes the result that permits
execution-gated settlement. The operator payer checks the Gateway's unsigned
HTTP 402 quote before signing. Local logs, replay state, raw takes, and secrets
remain outside this profile and are not committed.

## Recorded paid execution

[Watch the Windows MuJoCo paid-action recording](evidence/spot-paid-mujoco-demo-2026-07-28.mp4).
It shows the simulator view triggered from the paid Gateway action and the
settled terminal result; it is evidence for this simulator-only profile, not
physical Spot hardware validation.

## Safe stop

The registered `stop` action sets the bridge stop event even while navigation
is running. MuJoCo receives the neutral 12-actuator command and zero simulated
velocity; the interrupted navigation publishes `completion_reason:
safe_stopped` as failure, so it cannot settle. The stop request publishes its
own correlated success result. Navigation is additionally bounded to 60
seconds and `speedScale` 0.25 through 1.0.
