# RoboPay

Fabric RoboPay connects robots, simulators, cameras, drones, and other physical devices to the Fabric network. It provides a secure paid-action runtime that receives remote action requests, verifies payment through the robot-side tunnel flow, and routes approved actions to connected machines.

## Overview

Fabric introduces a payment layer for machines. RoboPay is the execution component of this stack, exposing machine capabilities as paid endpoints.

A core design principle is that **payment, routing, and execution are separated**. The Fabric backend/proxy receives a paid action request and routes it to the correct robot tunnel by `robotId`. It does not directly verify x402 payment in the production tunnel flow.

The robot-side `tunnel` receives the action request, runs its payment middleware — [x402](#3-start-the-tunnel) or [MPP](#mpp-machine-payments-protocol), whichever the payer used — verifies or rejects the payment, and only publishes a verified action to the robot execution layer after successful verification. The robot controller still owns final safety — **a verified payment is not permission to move unconditionally**.

![RoboPay action flow](docs/images/flow.png)

## Repository layout

```
.
├── tunnel/          # Go tunnel + x402 paid-action runtime
│   └── config.json  # robot_id, payee address, price, network
├── bridge/          # ROS2 bridge: Zenoh action events → robot /cmd_vel
│   ├── common/zenoh_bridge/                 # shared Zenoh + action parsing
│   └── unitree/{g1,go2,tron1}/isaac_sim_bridge/   # per-robot ROS2 packages
└── Makefile         # builds/runs the tunnel and the bridge
```

The simulator itself is **not** vendored here. Isaac Sim scenes and policies live in the [OM1-sim](https://github.com/OpenMind/OM1-sim) repo.


## 1. Start the simulator (Isaac Sim / OM1-sim)

The simulator lives in a separate repo, [OpenMind/OM1-sim](https://github.com/OpenMind/OM1-sim). It requires Ubuntu 22.04, ROS2 Humble, an NVIDIA GPU, and Isaac Sim 5.1.0+.

```bash
git clone https://github.com/OpenMind/OM1-sim.git
cd OM1-sim

export ISAACSIM_ROOT=/path/to/isaacsim
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
source /opt/ros/humble/setup.bash
cd isaac_sim && "$ISAACSIM_ROOT/python.sh" run.py --robot_type g1
```

The sim subscribes to ROS2 `/cmd_vel` and drives the robot policy from it.

## 2. Start the bridge

The bridge is a ROS2 workspace under `bridge/`. It needs ROS2 Humble and a Python environment with `eclipse-zenoh`, managed with [uv](https://docs.astral.sh/uv/).

```bash
uv venv --python 3.10
source .venv/bin/activate
uv pip install eclipse-zenoh

make bridge-build
make bridge-run                 # defaults to G1; ROBOT=go2 or ROBOT=tron1 to switch
```

Package names are `isaac_sim_bridge_g1`, `isaac_sim_bridge_go2`, and `isaac_sim_bridge_tron1` (G1 is validated; Go2 and Tron1 are placeholders). The adapter subscribes to the Zenoh topic `robot/tunnel/action` and republishes mapped velocities on ROS2 `/cmd_vel`.

## 3. Start the tunnel

The tunnel (`tunnel/`) keeps an outbound WebSocket to the Fabric proxy, verifies x402 micropayments, and publishes accepted actions to the same Zenoh topic the bridge listens on.

Set the payee address (and any overrides) in `tunnel/config.json`:

```json
{
  "robot_id": "my-robot",
  "evm_payee_address": "0xYourAddress",
  "price": "0.002",
  "network": "eip155:84532"
}
```

| Field                    | Required      | Default         | Description                                                |
|--------------------------|---------------|-----------------|------------------------------------------------------------|
| `robot_id`               | No            | random UUID     | Unique robot identifier                                     |
| `evm_payee_address`      | **Yes**       | —               | EVM address to receive x402 payments                        |
| `price`                  | No            | `0.001`         | Price per action, in whole token units                      |
| `network`                | No            | `eip155:8453`   | CAIP-2 network ID (e.g. `eip155:84532`)                     |
| `token_address`          | No            | network default | ERC-20 the price is charged in                              |
| `token_name`             | For `eip3009` | —               | Token's `name()`, forms the EIP-712 domain the payer signs  |
| `token_version`          | No            | `1`             | Token version used in the EIP-712 domain                    |
| `token_decimals`         | No            | `6`             | Token decimals, used to convert `price` to atomic units     |
| `token_transfer_method`  | No            | `eip3009`       | `eip3009` or `permit2` — how the payment settles            |
| `token_supports_eip2612` | No            | `false`         | `permit2` only: payer signs a permit instead of approving   |

`price` is a decimal amount in whole units of the payment token, converted to atomic units using
`token_decimals` — with `token_decimals: 18`, `"1"` charges `1000000000000000000`. A leading `$`
is optional and carries no meaning; it only reads as dollars when the token is a stablecoin.

### Custom payment token

For well-known chains x402 already knows which stablecoin to use (USDC on Base, and so on), so
`token_address` can be omitted. On any other chain there is no default and requests fail with
`no default stablecoin configured for network <network>` — set `token_address` to register the
token as that network's default asset at startup. See
[`tunnel/config.example.json`](tunnel/config.example.json).

`token_transfer_method` decides how the facilitator moves the tokens:

- **`eip3009`** (default) — the payer signs a `TransferWithAuthorization` message and the
  facilitator calls `transferWithAuthorization` on the token. **Only works if the token actually
  implements EIP-3009** (USDC and friends). Against a plain ERC-20 the signature is produced
  happily and settlement then reverts. `token_name`/`token_version` must match the token's own
  EIP-712 domain (its `name()`, not its symbol) or the signature will not verify.
- **`permit2`** — the payer signs a Permit2 witness and the facilitator settles through the x402
  exact Permit2 proxy. Works with **any** plain ERC-20, at the cost of a one-time
  `approve(0x000000000022D473030F116dDEE9F6B43aC78BA3, …)` from each payer. The signed domain is
  Permit2's own, so `token_name`/`token_version` are neither required nor advertised. Set
  `token_supports_eip2612: true` only if the token has `permit()`, which lets the payer skip the
  approval transaction.

The facilitator has to support the chosen method too — it is the one that submits the settlement
transaction.

### MPP (Machine Payments Protocol)

The tunnel also speaks [MPP](https://mpp.dev), the machine-payments standard
co-authored by Stripe and Tempo. MPP is a 402 flow like x402, but it rides the
standard HTTP authentication framework: the tunnel answers an unpaid request
with `WWW-Authenticate: Payment …`, the payer retries with
`Authorization: Payment …`, and a verified action comes back with a
`Payment-Receipt`.

Those headers are disjoint from x402's `PAYMENT-REQUIRED` / `PAYMENT-SIGNATURE`,
so **both protocols are offered on the same `POST /action` route** and the payer
picks one. An unpaid request gets a single 402 carrying both challenges; a
request with an MPP credential is settled over MPP and never touches x402.
Either way the accepted action lands on the same Zenoh topic, tagged with
`transaction_details.protocol` (`"mpp"` or `"x402"`) so the robot side can tell
them apart.

#### Payment credentials

MPP's specification is payment-method agnostic and registers methods for cards
and Stripe, EVM chains, Solana, Stellar, Hedera, Lightning, NEAR intents, and
Tempo. **The Go SDK ([`mpp-go`](https://github.com/tempoxyz/mpp-go)) implements
exactly one of them** — the Tempo `charge` method, which settles in a stablecoin
(USDC on Tempo mainnet, AlphaUSD on the Moderato testnet). Card, Stripe, and the
other chain methods currently exist only in the TypeScript and Python SDKs, so
the tunnel is **stablecoin-only** for now.

Adding a method later is additive rather than a rewrite: `mpp-go`'s
`server.Method` / `server.Intent` are exported interfaces and
`server.ComposeMiddleware` advertises several methods in one 402. See
[`tunnel/internal/mppay/mppay.go`](tunnel/internal/mppay/mppay.go).

#### Configuration

MPP is off by default. Turn it on in `tunnel/config.json` and set the challenge
signing secret in the environment:

```json
{
  "robot_id": "my-robot",
  "evm_payee_address": "0xYourAddress",
  "price": "0.002",
  "network": "eip155:84532",
  "mpp_enabled": true,
  "mpp_network": "eip155:42431"
}
```

```bash
export MPP_SECRET_KEY="$(openssl rand -base64 32)"
```

| Field               | Required | Default              | Description                                                        |
|---------------------|----------|----------------------|--------------------------------------------------------------------|
| `mpp_enabled`       | No       | `false`              | Offer MPP alongside x402 (also settable via `MPP_ENABLED`)         |
| `mpp_network`       | No       | `eip155:4217`        | Tempo chain, CAIP-2: `eip155:4217` mainnet, `eip155:42431` Moderato |
| `mpp_payee_address` | No       | `evm_payee_address`  | Tempo address to receive MPP payments                              |
| `mpp_currency`      | No       | chain default        | Token contract charged in (USDC on mainnet, AlphaUSD on Moderato)  |
| `mpp_decimals`      | No       | `6`                  | Token decimals, used to convert `price` to atomic units            |
| `mpp_realm`         | No       | `robot_id`           | Authentication realm advertised in the challenge                   |

MPP reuses `price`, so a robot charges the same amount over either protocol.
Like the x402 fields, all of these can be hot-reloaded over the
`robot/config/<robot_id>` Zenoh topic.

| Variable            | Required | Description                                                                    |
|---------------------|----------|--------------------------------------------------------------------------------|
| `MPP_SECRET_KEY`    | **Yes**  | ≥32 bytes; HMAC-binds issued challenge IDs, so a short one makes them forgeable |
| `MPP_RPC_URL`       | No       | Tempo JSON-RPC endpoint; only needed for a chain other than mainnet/Moderato    |
| `MPP_ENABLED`       | No       | Overrides `mpp_enabled`                                                        |
| `MPP_NETWORK`       | No       | Overrides `mpp_network`                                                        |
| `MPP_PAYEE_ADDRESS` | No       | Overrides `mpp_payee_address`                                                  |
| `MPP_CURRENCY`      | No       | Overrides `mpp_currency`                                                       |
| `MPP_DECIMALS`      | No       | Overrides `mpp_decimals`                                                       |
| `MPP_REALM`         | No       | Overrides `mpp_realm`                                                          |

Every MPP field has an environment override, so a deployment can carry its whole
payment setup in `.env` — see [`tunnel/.env.example`](tunnel/.env.example).

#### SDK versions

MPP is young and its SDKs move at different speeds, so the server and the payer
have to agree on the Tempo transaction wire format. This combination is verified
working end to end on Moderato:

| Side   | Package                   | Version  |
|--------|---------------------------|----------|
| Server | `github.com/tempoxyz/mpp-go` | `v0.2.0` |
| Server | `github.com/tempoxyz/tempo-go` | `v0.5.0` — **explicitly bumped** |
| Client | `pympp[tempo]` (PyPI)     | `0.10.1` |

**The `tempo-go` bump is load-bearing.** `mpp-go v0.2.0` only requires
`tempo-go v0.4.1`, whose `tempotx.Deserialize` rejects the signature envelope
that current Python and TypeScript clients emit — every payment fails with:

```json
{"title":"Invalid Payload","detail":"failed to deserialize transaction payload","status":400}
```

`tempo-go v0.5.0` fixes it ([accept legacy recovery IDs in signature
envelopes](https://github.com/tempoxyz/tempo-go/pull/57)) and `mpp-go v0.2.0`
compiles against it unchanged, so `tunnel/go.mod` pins it directly. Do not let it
drift back down.

#### Testing against Moderato

Pin `mpp_currency` explicitly on testnet. Left blank, the Go SDK advertises
**AlphaUSD** (`0x20c0…0001`) while the Python and TypeScript SDKs default to
**pathUSD** (`0x20c0…0000`) — the payer must hold whichever token the tunnel
advertises. The [Moderato faucet](https://docs.tempo.xyz/quickstart/faucet) mints
both, along with BetaUSD and ThetaUSD:

```bash
cast rpc tempo_fundAddress <PAYER_ADDRESS> --rpc-url https://rpc.moderato.tempo.xyz
```

Fund the **payer**, not the payee. The tunnel only ever needs the payee's
address (`mpp_payee_address`, or `evm_payee_address` by default) — never its key.

To see what a robot accepts without spending anything, send one unpaid request
and read the challenges off the 402:

```bash
curl -i -X POST http://api.fabric.foundation/api/core/robots/test-robot/action \
     -H 'Content-Type: application/json' -d '{"command":"ping"}'
```

A robot offering both protocols answers with `WWW-Authenticate: Payment …` (MPP)
and `PAYMENT-REQUIRED` (x402) on the same response. Both survive the proxy and
the WebSocket tunnel in each direction, as does the payer's `Authorization:
Payment …` on the retry.

Build and run from the repo root (the `Makefile` operates inside `tunnel/`):

```bash
make build
make run
make test
```

`mpp-go` requires **Go 1.26** or newer, which is the module's minimum. `make lint`
therefore needs **golangci-lint v2.9.0** or newer — it refuses to lint a module
targeting a newer Go than it was itself compiled with, and v2.9.0 is the first
release built with 1.26. Reinstall if you have an older one.

The tunnel reads `.env` from its **working directory**, so run it through
`make run` (which enters `tunnel/` first) or `cd tunnel` before launching the
binary by hand. Started from the repo root, `tunnel/.env` is silently skipped and
MPP comes up disabled with no error.

Common environment overrides:

| Variable          | Default                                          | Description                       |
|-------------------|--------------------------------------------------|-----------------------------------|
| `PROXY_WS_URL`    | `wss://api.fabric.foundation/api/core/ws/robot`  | WebSocket URL of the tunnel proxy |
| `FACILITATOR_URL` | `https://x402.org/facilitator`                   | x402 payment facilitator endpoint |
| `GIN_MODE`        | `release`                                        | `debug` for verbose HTTP logs     |

## 4. Register the robot on BitAgent (Unibase AIP) — optional

With `AIP_ENABLED=true`, the tunnel additionally registers the robot as an
A2A-compatible agent on the BitAgent network (Unibase AIP), so any AIP client
or agent can discover and call it. The integration is built on the
[Unibase AIP Go SDK](https://github.com/unibaseio/aip-go-sdk) — see
`tunnel/internal/aipagent/agent.go`, which wraps the robot in a single
`wrappers.ExposeAsA2A(...)` call.

How AIP traffic flows:

```
AIP client → AIP gateway (/robots/<robot_id>/…) → Fabric proxy (ws) → tunnel
           → AIP handler → Zenoh topic robot/tunnel/action → bridge → /cmd_vel
```

The tunnel serves the A2A contract endpoints (`/.well-known/agent-card.json`,
`/invoke`, …) on any route not owned by the paid-action API, and the gateway
proxies them to the robot verbatim.

### Configuration

Copy the example env file and fill in your credentials (the tunnel loads
`.env` from its working directory on start):

```bash
cp tunnel/.env.example tunnel/.env
```

| Variable             | Required | Description                                              |
|----------------------|----------|----------------------------------------------------------|
| `AIP_ENABLED`        | yes      | Set `true` to enable BitAgent/AIP registration           |
| `CHAIN`              | no       | Chain preset: `bsc-testnet`, `bsc-mainnet`, `base-sepolia` or `base-mainnet` — sets both the x402 payment network and the AIP registration chain |
| `UNIBASE_PROXY_AUTH` | no*      | Bearer token — your account is resolved from it (falls back to `PRIVY_TOKEN`) |
| `AIP_USER_ID`        | no*      | Token-less fallback: wallet address to register under    |
| `AIP_ENDPOINT`       | no       | AIP platform URL (default `https://api.aip.unibase.com`) |
| `GATEWAY_URL`        | no       | AIP gateway URL (default `https://gateway.aip.unibase.com`) |
| `AIP_PUBLIC_BASE_URL`| no       | Public gateway base (default `https://api.fabric.foundation/api/core`) |
| `AIP_AGENT_NAME`     | no       | Display name (default `Robot <robot_id>`)                |
| `AIP_LOCAL_PORT`     | no       | Local port the SDK binds (default `8000`)                |

\* When neither is set, the tunnel walks you through a one-time browser
authorization on first run — open the printed URL, approve with your wallet,
and paste the token back. It is cached in
`~/.config/unibase-aip-sdk/config.json` for subsequent runs:

```
=== Unibase Authorization ===
[1/3] Fetching authorization URL ...
[2/3] Open this URL in your browser and approve:

  https://auth.pay.unibase.com?code=<one-time-code>

[3/3] Paste your Authorization token below and press Enter:
```

Then start the tunnel as usual (`make run`). On success the log shows:

```
registering robot as AIP agent  robot_id=<id>  endpoint_url=…/robots/<id>
ws connected to proxy           robot_id=<id>
```

Actions received via AIP are published to the same Zenoh topic
(`robot/tunnel/action`) as paid x402 actions, so the bridge and robot-side
safety logic are identical for both paths.
