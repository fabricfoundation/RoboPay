# Atlas DRC/v4 MuJoCo + Webots bridge

This bridge implements the simulator-only Tier 1 profile
`boston-dynamics.atlas-drc.mujoco-webots-wave.v1`. It deliberately targets the
public DARPA-era hydraulic Atlas DRC/v4 model, not Boston Dynamics' current
electric Atlas product.

The complete setup, action contract, payment safety, evidence procedure and
troubleshooting guide are documented in the profile
[README](../../../registry/vendors/boston-dynamics/atlas/boston-dynamics.atlas-drc.mujoco-webots-wave.v1/docs/README.md).

Quick local model checks:

```bash
python download_atlas_model.py
python run_paid_wave.py
python run_sim2sim_validation.py
```

On Windows, use `run_live_base_sepolia_visual.ps1` for the current-commit paid
recording flow. It pauses before payment and never stores or prints the payer
private key.
