"""Actuator addressing derived from the loaded model, never hand-written.

The previous revision of this bridge kept a hand-maintained ``ACTUATOR_ORDER``
list.  It silently disagreed with the compiled model, so 27 of 30 control
channels were cross-wired (knee commands reached the elbow).  Everything here is
read back out of the compiled ``MjModel`` instead, and :func:`validate` turns any
future drift into an immediate, loud failure.
"""

from __future__ import annotations

from dataclasses import dataclass

import mujoco
import numpy as np


@dataclass(frozen=True)
class ActuatorMap:
    """Model-derived addressing for the actuated joints, in ``data.ctrl`` order."""

    names: tuple[str, ...]
    qpos_addresses: np.ndarray
    qvel_addresses: np.ndarray
    effort_limits: np.ndarray

    def index(self, joint: str) -> int:
        return self.names.index(joint)

    def vector(self, values: dict[str, float], default: float = 0.0) -> np.ndarray:
        """Build a ctrl-ordered vector from a ``{joint: value}`` mapping."""
        unknown = set(values) - set(self.names)
        if unknown:
            raise KeyError(f"Unknown Atlas joints: {sorted(unknown)}")
        return np.array([values.get(name, default) for name in self.names], dtype=np.float64)

    def __len__(self) -> int:
        return len(self.names)


def build(model: mujoco.MjModel) -> ActuatorMap:
    """Read the actuator layout straight out of a compiled model."""
    joint_ids = model.actuator_trnid[:, 0]
    names = tuple(model.joint(int(jid)).name for jid in joint_ids)
    return ActuatorMap(
        names=names,
        qpos_addresses=model.jnt_qposadr[joint_ids].copy(),
        qvel_addresses=model.jnt_dofadr[joint_ids].copy(),
        effort_limits=np.abs(model.actuator_gear[:, 0]).astype(np.float64),
    )


def validate(model: mujoco.MjModel, expected_efforts: dict[str, float]) -> ActuatorMap:
    """Build the map and assert it matches the upstream URDF joint efforts.

    Raises ``ValueError`` when the compiled model and the pinned URDF disagree on
    which joints exist or on how strong they are.
    """
    actuators = build(model)
    missing = sorted(set(expected_efforts) - set(actuators.names))
    extra = sorted(set(actuators.names) - set(expected_efforts))
    if missing or extra:
        raise ValueError(
            f"Actuator set does not match the pinned URDF (missing={missing}, extra={extra})"
        )
    for name, effort in zip(actuators.names, actuators.effort_limits):
        if abs(effort - expected_efforts[name]) > 1e-6:
            raise ValueError(
                f"Effort limit drift on {name}: model={effort} urdf={expected_efforts[name]}"
            )
    return actuators
