"""Robot-side entrypoint for abb-real-001.

Runs the Zenoh robot node: subscribes to robot/tunnel/action, executes the
skill via the MuJoCo executor, publishes robot/tunnel/result.

On Linux (zenoh available) this uses the real Zenoh library. On Windows, where
zenoh has no wheels, it exits with a clear message -- run it inside the
ubuntu-22.04 CI / a Linux box.

    python -m flow.node
"""
from flow.zenoh_transport import ZenohRobotNode, _HAS_ZENOH
from flow.executor import MuJoCoExecutor


def main():
    if not _HAS_ZENOH:
        raise SystemExit(
            "zenoh is not installed on this platform. "
            "Run the robot node on Linux (ubuntu-22.04) where zenoh wheels exist."
        )
    node = ZenohRobotNode(MuJoCoExecutor())
    print("abb-real-001 robot node (MuJoCo) listening on robot/tunnel/action ...")
    try:
        node.serve()
    except KeyboardInterrupt:
        node.stop()


if __name__ == "__main__":
    main()
