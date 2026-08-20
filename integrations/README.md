# Payment Network Integrations

This directory contains snapshot integration packages for payment networks that can authorize robot actions and publish accepted ActionEnvelopes to the robot tunnel topic.

Each package should be developed and validated in its own standalone repository first. After validation, it can be imported here as a snapshot so the root Go tunnel client remains stable and reviewable.

## Integration Index

| Provider | Package | Source Repository | Source Commit | Status | Validation |
| --- | --- | --- | --- | --- | --- |
| AEON BNB x402 | `integrations/aeon-robot-communication/` | `https://github.com/fabricfoundation/Aeon-robot-communication` | `a96c5a2` | M2 bridge-stage passed | `docs/m2-aeon-zenoh-om1-validation-report.md` |
| XRPL x402 | `integrations/xrpl-robot-communication/` | `https://github.com/fabricfoundation/XRPL-robot-communication` | `dc8f495` | M2-A Zenoh passed, M2-B pending | `docs/m2-a-xrpl-testnet-zenoh-plan.md` |

## Package Rules

Each integration package should include:

- `integration.yaml`
- `README.md`
- source code and tests needed to reproduce the validation
- `.env.example` with placeholders only
- validation reports under `docs/`
- runbooks for local mock proof and real runtime proof

Do not include:

- `.env` or `.env.*` files with real values
- wallet seeds, private keys, API keys, or facilitator credentials
- `node_modules/`, `.tools/`, `dist/`, `coverage/`, logs, screenshots with secrets, or generated caches
- changes to the root Go runtime unless the integration explicitly requires a reviewed runtime interface change

## Import Process

1. Develop in a standalone repository.
2. Complete the required validation milestones.
3. Commit and push the standalone repository.
4. Import a tracked-file snapshot into `integrations/<provider>-robot-communication/`.
5. Add or update `integration.yaml`.
6. Update this index.
7. Verify no secrets or generated artifacts were imported.
8. Open a pull request targeting `add-tunneling`, not `main`.

The root `robot-tunnel-client` service should remain the stable tunnel runtime. Payment network experiments should live inside `integrations/` until they are mature enough to justify root-level runtime changes.
