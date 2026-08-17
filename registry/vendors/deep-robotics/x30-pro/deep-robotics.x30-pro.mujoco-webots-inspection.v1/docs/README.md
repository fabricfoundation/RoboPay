# DeepRobotics X30 Pro simulator profile

`deep-robotics.x30-pro.mujoco-webots-inspection.v1` is a simulator-only, payment-gated profile for a fixed quadruped inspection lane. `perform_inspection_gait` has no public motion parameters: that eliminates route drift between the catalog, bridge, and evidence commands.

The X30 source model is pinned by commit and checksums in the bridge model lock. It contains twelve official leg joints. MuJoCo uses an in-memory free-base/actuator overlay and Webots regenerates a PROTO from the locked URDF; neither changes the vendor source model on disk. The physical X30 Pro sensor stack is out of scope and is not simulated or claimed.

The action is controlled by a measured-state task controller, not a prerecorded animation or learned-policy claim. Two physical blockers start in front of the robot, with the first 1.05 m from the initial base pose. The controller advances through settle, first evasion/pass, second evasion/pass and goal-hold phases only when each simulator's own state satisfies the phase gate. Terminal success requires at least 0.85 m body-forward progress, the measured lateral detour, physical-course approach, finish-line crossing, zero blocker contact, safe height and tilt, and finite simulator state. The measured terminal values and phase transitions are carried into the correlated action result. A stopped, invalid, unverified, malformed, failed, timed-out, or replayed action is not a success and cannot settle.

No X30 payment recording or transaction receipt is included until it is generated from this profile's branch.
