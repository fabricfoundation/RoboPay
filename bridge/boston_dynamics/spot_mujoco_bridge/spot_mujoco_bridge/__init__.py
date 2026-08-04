"""Boston Dynamics Spot simulator bridge for RoboPay."""

__all__ = ["run_obstacle_course"]


def run_obstacle_course(*args, **kwargs):
    """Lazily load the MuJoCo runner so Webots can import the pure policy core."""

    from .runner import run_obstacle_course as _run_obstacle_course

    return _run_obstacle_course(*args, **kwargs)
