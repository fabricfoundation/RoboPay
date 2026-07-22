# Physical acceptance runbook

This runbook produces reviewer-visible proof for
`agibot.x2.aimdk-robopay.v1` without publishing private user or site data. It
supersedes the earlier settlement-before-execution field procedure.

## 1. Preflight and safety

- Confirm the robot is an AgiBot X2 running the documented AimDK_X2 stack.
- Establish a 1.5 m clear radius and assign one physical safety operator.
- Keep the vendor remote/e-stop in the operator's hand.
- Check battery, balance, feet contact, and Stable Standing Mode.
- Verify `/aimdk_5Fmsgs/srv/SetMcPresetMotion` is present.
- Synchronize the relay and robot clocks with a trusted time source.
- Check that the camera frame contains no faces, badges, serial numbers,
  screens, hostnames, usernames, or private IP addresses.
- Start a fresh SQLite replay database; archive its SHA-256 after the run.

Stop immediately if posture, balance, service state, or the safety operator is
not ready.

## 2. Processes and trace marker

Start the shared Fabric relay/tunnel, Zenoh router, and X2 adapter in separate
terminals. Create one non-secret trace marker, for example:

```text
actionId: act_review_x2_wave_<UTC timestamp>
robotId shown publicly: agibot-x2-demo-***
skillId: x2_right_wave
```

Display the same masked `actionId` marker in the payer, relay, Zenoh, bridge,
status, and video views. Never display a private key or payment signature.

## 3. Discovery and unpaid gate

Record:

1. Robot discovery identifies an AgiBot X2 physical profile.
2. Skill discovery returns only `x2_right_wave`.
3. The displayed price is 0.002 USDC on Base Sepolia.
4. An unsigned/unpaid POST returns HTTP 402.
5. Relay and Zenoh counters show zero action publications for that failed POST.

## 4. Paid success path

With the operator ready, submit one fresh paid request. Capture a synchronized
view of:

- immediate HTTP accepted/pending response and `actionId`;
- relay receipt with wallet/transaction values masked;
- authorization TTL at or below 300 seconds and issuance within 30 seconds of
  the robot clock;
- `robot/tunnel/action` envelope field names and correlation values;
- adapter `action_accepted` audit line;
- AimDK area `2`, motion `1002`, state, and vendor `taskId`;
- the physical X2 right-hand wave;
- `robot/tunnel/result` and the action status endpoint;
- settlement only after terminal success.

If AimDK reports only `RUNNING`, record the result as pending and stop: do not
label it success and do not settle. Obtain an explicit terminal completion
signal before claiming the strict contract has passed.

## 5. Failure and replay path

Use safe failure conditions that do not move the robot:

| Test | Expected observation |
| --- | --- |
| Tampered `paramsHash` | Rejected before Zenoh; zero actuation; no settlement |
| Expired payment evidence | `PAYMENT_EXPIRED`; zero actuation; no settlement |
| Authorization TTL above 300 seconds | `PAYMENT_INVALID`; zero actuation; no settlement |
| Authorization issued over 30 seconds in the future | `PAYMENT_INVALID`; zero actuation; no settlement |
| Replay after adapter restart | `DUPLICATE`; no second wave; no settlement |
| AimDK service deliberately unavailable | Error result; zero actuation; no settlement |

Do not create a failure by destabilizing the robot, obstructing an arm, or
triggering an unsafe posture.

## 6. Evidence manifest

Create a manifest in the final PR description or validation report with:

| Evidence ID | Artifact | Required correlation |
| --- | --- | --- |
| `AGX2-DISCOVERY` | Redacted discovery screenshot/log | robot profile + skill + price |
| `AGX2-402` | Redacted unpaid response and zero-publish log | request marker |
| `AGX2-ACTION` | Redacted action delivery log | actionId + paramsHash |
| `AGX2-AIMDK` | Adapter/AimDK log | actionId + taskId + terminal state |
| `AGX2-VIDEO` | Short physical execution video | visible trace marker + wave |
| `AGX2-RESULT` | Result/status log | same actionId + final status |
| `AGX2-NOSETTLE` | Failure/replay log | actionId + error + no settlement |

Record SHA-256, byte length, capture time, and redaction status for every
artifact. Keep original unredacted artifacts outside Git. Include only a
redacted, compressed review copy or stable reviewer-accessible link in the PR.

## 7. Final sign-off

- Safety operator signs off that only one right-hand wave occurred per accepted
  unique action.
- Integration owner confirms every public artifact passed privacy review.
- Payment owner confirms failed/pending actions were not settled.
- Reviewer can map payment → action → AimDK task → result → physical video using
  the evidence table without access to private infrastructure details.
