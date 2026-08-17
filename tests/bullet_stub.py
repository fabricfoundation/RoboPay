"""PyBullet stub for unit testing without PyBullet installed."""
import sys


class Stub:
    """Minimal stub that records calls and simulates door physics."""
    def __init__(self):
        self.calls = []
        # Door simulation state
        self._door_angle = 0.0
        self._friction = 0.3
        self._grip_force = 0.0
        self._contact_samples = 0
        self._peak_force = 0.0
        self._steps = 0
        self._max_steps = 400
        # Track all simulator instances for syncing
        self._sim_targets = []

    def __getattr__(self, name):
        def wrapper(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "loadURDF":
                return 1
            if name == "createCollisionShape":
                return 1
            if name == "createMultiBody":
                return 2
            if name == "createConstraint":
                return 1
            if name == "changeConstraint":
                pass
            if name == "setCollisionFilterGroupMask":
                pass
            if name == "setJointMotorControl2":
                # Track gripper state (joint 4, 5 are fingers)
                if args and len(args) > 2:
                    joint_idx = args[1] if len(args) > 1 else None
                    mode = args[2] if len(args) > 2 else None
                    target = kwargs.get('targetPosition', args[3] if len(args) > 3 else None)
                    if joint_idx in (4, 5) and mode == POSITION_CONTROL:
                        self._grip_force = target if target else self._grip_force
            if name == "stepSimulation":
                self._steps += 1
                self._simulate_step()
                # Sync to all registered simulators
                for target in self._sim_targets:
                    target._door_angle = self._door_angle
                    target._peak_force = self._peak_force
                    target._contact_samples = self._contact_samples
            if name == "getContactPoints":
                return self._get_contacts()
            if name == "getJointState":
                return [0.0] * 10
            if name == "getBasePositionAndOrientation":
                return ([0.0] * 3, [0.0] * 4)
            if name == "getJointPos":
                return 0.0
            if name == "getWorldAxis":
                return [0.0, 0.0, 1.0]
            if name == "changeDynamics":
                # args: (bodyUniqueId, linkIndex, **kwargs)
                if 'lateralFriction' in kwargs:
                    self._friction = kwargs['lateralFriction']
                pass
            return 0
        return wrapper

    def _simulate_step(self):
        """Simulate door physics."""
        # Grip phase: track contact when gripper closes
        if self._grip_force < 0.050:
            self._contact_samples += 1
            self._peak_force = max(self._peak_force, 0.8)

        # Door pull phase: after ~200 steps (past approach + descend + grip)
        if self._steps > 200:
            pull_progress = min(1.0, (self._steps - 200) / 100.0)
            max_angle = 0.6 if self._friction < 1.0 else 0.02
            self._door_angle = pull_progress * max_angle

    def _get_contacts(self):
        """Return contact points if gripped."""
        if self._peak_force > 0 and self._steps > 120:
            return [(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, self._peak_force)]
        return []

    def register_sim(self, sim):
        """Register a simulator to receive physics sync."""
        self._sim_targets.append(sim)

    def unregister_sim(self, sim):
        """Unregister a simulator."""
        if sim in self._sim_targets:
            self._sim_targets.remove(sim)


S = Stub()

# Also expose stub methods directly at module level for convenience
loadURDF = S.loadURDF
createCollisionShape = S.createCollisionShape
createMultiBody = S.createMultiBody
createConstraint = S.createConstraint
changeConstraint = S.changeConstraint
setCollisionFilterGroupMask = S.setCollisionFilterGroupMask
setJointMotorControl2 = S.setJointMotorControl2
stepSimulation = S.stepSimulation
getContactPoints = S.getContactPoints
getJointState = S.getJointState
getBasePositionAndOrientation = S.getBasePositionAndOrientation
changeDynamics = S.changeDynamics
GEOM_BOX = 0
GEOM_SPHERE = 1
GEOM_CYLINDER = 2
GEOM_PLANE = 3
JOINT_REVOLUTE = 0
JOINT_PRISMATIC = 1
JOINT_FIXED = 2
DYNAMIC = 0
KINEMATIC = 1
INERTIA_MASS = 1
INERTIA_ixx = 0.01
INERTIA_ixy = 0.0
INERTIA_ixz = 0.0
INERTIA_iyy = 0.01
INERTIA_izy = 0.0
INERTIA_izz = 0.01

# PyBullet motor control mode constants
POSITION_CONTROL = 0
VELOCITY_CONTROL = 1
TORQUE_CONTROL = 2


# Patch pybullet module
sys.modules["pybullet"] = S
sys.modules["pymunk"] = S


def available():
    return False
