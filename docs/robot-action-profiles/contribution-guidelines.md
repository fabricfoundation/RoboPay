# Robot Action Profile Contribution Guidelines

## Goal

A Robot Action Profile tells agents and maintainers:

- what robot is being integrated;
- what actions the robot can perform;
- how an agent should call those actions;
- which actions require payment;
- how the action is delivered to the robot runtime;
- how the team validated the integration.

Each submission should be small, reviewable, and specific to one robot model or runtime profile.

## Required Directory Structure

Robot teams should submit files under their own vendor and robot directory:

```text
registry/
  vendors/
    <vendor>/
      <robotModel>/
        <profileId>/
          robot.profile.yaml
          skills.yaml
          functions.yaml
          payment-policy.yaml
          execution-mapping.yaml
          examples/
          docs/
            validation-report.md
```

Example:

```text
registry/
  vendors/
    unitree/
      g1/
        om1-sim-g1-v1/
          robot.profile.yaml
          skills.yaml
          functions.yaml
          payment-policy.yaml
          execution-mapping.yaml
          examples/
          docs/
            validation-report.md
```

Recommended `profileId` format:

```text
<vendor>.<robotModel>.<runtime-or-capability>.v1
```

Example:

```text
unitree.g1.om1-sim-g1.v1
```

## Required Files

### `robot.profile.yaml`

Describes the robot, runtime, transport, bridge, and maintainers.

Use the template:

```text
docs/robot-action-profiles/templates/robot.profile.yaml
```

### `skills.yaml`

Describes what the robot can do.

Use it to define stable `skillId` values, parameters, safety limits, and whether payment is required.

Use the template:

```text
docs/robot-action-profiles/templates/skills.yaml
```

### `functions.yaml`

Describes how an agent should call the robot action API.

Agents should read this file to understand the request flow:

```text
list skills -> request action -> receive 402 if payment is required -> submit paid action
```

Use the template:

```text
docs/robot-action-profiles/templates/functions.yaml
```

### `payment-policy.yaml`

Describes payment requirements for each paid skill.

Payment policies must make it clear how payment success is verified and which header carries the payment payload.

Use the template:

```text
docs/robot-action-profiles/templates/payment-policy.yaml
```

### `execution-mapping.yaml`

Describes how an authorized action envelope is delivered to the robot runtime.

For example, an action envelope may be published to a Zenoh topic and mapped by a bridge to ROS2 `/cmd_vel`.

Use the template:

```text
docs/robot-action-profiles/templates/execution-mapping.yaml
```

### `examples/`

Each profile should include at least one example action envelope.

The example should show:

- `robotId`
- `skillId`
- `params`
- `idempotencyKey`
- `payment`

### `docs/validation-report.md`

Each team must include a validation report.

Use the template:

```text
docs/robot-action-profiles/templates/validation-report.md
```

## Review Checklist

Before opening a pull request, make sure:

- required YAML files are included;
- at least one example action envelope is included;
- validation report is included;
- no private keys, API keys, `.env` files, or secrets are committed;
- no `node_modules/`, `dist/`, `logs/`, or `*.pyc` files are committed;
- movement actions include speed and duration limits;
- paid actions define payment requirements;
- emergency stop or safe stop behavior is described.

## Pull Request Rules

All submissions must go through pull requests.

Recommended branch name:

```text
vendor/<vendor>/<robotModel>/<profileId>
```

Example:

```text
vendor/unitree/g1/om1-sim-g1-v1
```

PR title format:

```text
Add robot profile: <vendor>/<robotModel>/<profileId>
```

Example:

```text
Add robot profile: unitree/g1/unitree.g1.om1-sim-g1.v1
```

One PR should normally add or update one Robot Action Profile.

## Important Principles

- Agents should read `skills.yaml` and `functions.yaml` to understand what to call.
- Payment requirements should be described in `payment-policy.yaml`.
- Robot execution details should be described in `execution-mapping.yaml`.
- Robot-side systems should not charge, verify, or settle payment a second time for the same authorized action.
- The shared target is:

```text
paid authorization -> ActionEnvelope -> robot skill execution
```
