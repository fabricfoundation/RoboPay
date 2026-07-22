# DOBOT controller project

`src0.lua` is the team-authored controller logic used for the physical CR3V
demonstration. It is published so a reviewer can verify that the Tier 3 skill
is a composed, multi-stage behavior rather than a built-in DOBOT action.

The original six Cartesian and joint-coordinate records are deliberately not
published. They encode a specific workcell and are unsafe to reuse on another
installation. `points.template.json` preserves the semantic point names and
control-flow contract; every integrator must reteach and validate all points.

The template is **not deployable as-is**. Before exporting a controller
project:

1. establish the correct tool, payload, user frame, safety zones, collision
   settings, gripper wiring, and digital-output polarity;
2. teach P1 through P6 at conservative speed with the workcell cleared;
3. verify the 20 mm positive-user-Z relative lift is safe in that user frame;
4. manually run the complete project at least three times;
5. export a newly versioned artifact and record its SHA-256 in local config;
6. set `safety.approved` only after the site safety review.

The bridge accepts no caller-provided coordinates, raw commands, project names,
speed, I/O values, or tool settings. It maps the public skill to one fixed,
locally approved project.

The authors confirmed permission to publish this controller logic. No separate
license is asserted here because the RoboPay repository currently has no root
license file; final licensing follows the repository maintainers' decision.
