# door-arm-001 --- README

# door-arm-001

A Tier 1 RoboPay skill: **open_door** — grip a door handle and pull the door open.

## Robot

- **4-DoF manipulator** (pan, shoulder, elbow, wristp)
- **MuJoCo + PyBullet** sim-to-sim backends
- **x402 payment** on Base Sepolia (USDC)

## Skill

| Parameter | Default | Options |
|-----------|---------|---------|
| `door`    | `open`  | `open`, `stuck`, `out_of_range`, `normal`, `far_door` |
| `maxSteps`| `400`   | integer override |

**Success**: door opens ≥ 29° with measured contact force ≥ 0.25 N  
**Failure modes**: stuck (high friction), out_of_range, timeout, grasp_failed

## Running

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
python -m flow.demo --door open
```

## Evidence

- Video: `docs/evidence/door-open-success.mp4`
- Manifest: `docs/evidence/evidence-manifest.yaml`
