"""Boston Dynamics Atlas simulator bridge for RoboPay."""

__all__ = ["run_obstacle_nav"]


def run_obstacle_nav(*args, **kwargs):
    """Lazily load the MuJoCo runner so Webots can import the pure policy core."""

    from .runner import run_obstacle_nav as _run_obstacle_nav

    return _run_obstacle_nav(*args, **kwargs)
