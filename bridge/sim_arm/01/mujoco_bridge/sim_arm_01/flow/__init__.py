"""Pay-to-actuate flow for sim-arm-01 (transport-agnostic, no ROS2 dependency).

This package implements the full RoboPay payment-safety flow so it can run in
CI and be reproduced by a reviewer with a single command:

    accepted/pending  ->  simulation  ->  actionId-correlated terminal result
                                       ->  success-only settlement

The documented Zenoh topics (robot/tunnel/action, robot/tunnel/result) are used
verbatim; for reproducibility the demo/tests carry them over an in-process bus
instead of a live Zenoh router. The real Zenoh runtime lives in sim_arm_01/node.py.
"""
from .envelope import (
    ACTION_TOPIC, RESULT_TOPIC, ActionEnvelope, ResultEnvelope, params_hash,
)
from .payment import PaymentGuard, PaymentError
from .executor import execute_skill, ROBOT_ID
from .relay import RoboPayRelay, InProcBus, RobotNode

__all__ = [
    "ACTION_TOPIC", "RESULT_TOPIC", "ActionEnvelope", "ResultEnvelope",
    "params_hash", "PaymentGuard", "PaymentError", "execute_skill", "ROBOT_ID",
    "RoboPayRelay", "InProcBus", "RobotNode",
]
