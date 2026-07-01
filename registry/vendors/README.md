# Robot Vendor Registry

This directory is the home for downstream Robot Action Profile submissions.

Agents, reviewers, and vendor teams should treat each profile directory as a
machine-readable description of one robot runtime profile.

## Where To Submit

Create one directory per vendor, robot model, and profile:

```text
registry/
  vendors/
    <vendor>/
      <robotModel>/
        <profileId>/
```

Example:

```text
registry/
  vendors/
    unitree/
      g1/
        unitree.g1.om1-sim-g1.v1/
```

## Required Profile Files

Each profile directory should contain:

```text
robot.profile.yaml
skills.yaml
functions.yaml
payment-policy.yaml
execution-mapping.yaml
examples/
docs/
  validation-report.md
```

## How Agents Should Read A Profile

An agent should read the files in this order:

1. `robot.profile.yaml` to understand the robot, runtime, transport, and profile version.
2. `skills.yaml` to understand what the robot can do and which parameters are allowed.
3. `functions.yaml` to understand which API functions to call.
4. `payment-policy.yaml` to understand whether payment is required and how payment success is represented.
5. `execution-mapping.yaml` to understand how an accepted action reaches the robot runtime.
6. `examples/` to see concrete action envelopes.
7. `docs/validation-report.md` to review test evidence and known limitations.

## Contribution Guidelines

Use the shared guidelines and templates:

```text
docs/robot-action-profiles/contribution-guidelines.md
docs/robot-action-profiles/templates/
```

## Boundary

Profiles should describe robot capabilities and runtime mapping. They should not
include private keys, wallet secrets, API keys, local `.env` files, generated
build artifacts, or binary cache files.

Robot-side systems should not charge, verify, or settle payment a second time
for the same authorized action envelope.
