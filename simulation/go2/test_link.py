"""End-to-end RoboPay link test (A1).

Starts the real tunnel binary, proves its zenoh session is live via a
config-update round trip, then publishes a simulated paid action and
asserts the MuJoCo episode runs and reaches the goal.
"""

import json
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).parent
REPO = HERE.parents[1]   # repository root
TUNNEL = REPO / "bin" / "tunnel"
RESULT = HERE / "last_action_result.json"


def wait_for(predicate, timeout, what):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if predicate():
            return
        time.sleep(0.5)
    raise TimeoutError(f"timed out waiting for {what}")


def main():
    if not TUNNEL.exists():
        sys.exit(f"tunnel binary missing — run `make build` in {REPO}")
    with open("/tmp/tunnel_test.log", "w") as log:
        tunnel = subprocess.Popen([str(TUNNEL), "-config", "tunnel/config.json"],
                                  cwd=REPO, stdout=log, stderr=log)
    link = None
    checks = {}
    try:
        time.sleep(3)   # let the tunnel open its zenoh session
        checks["tunnel_running"] = tunnel.poll() is None

        # 1) tunnel's own zenoh session receives our publish
        import zenoh
        s = zenoh.open(zenoh.Config())
        s.put("robot/config/test-robot", json.dumps({"price": "$0.004"}))
        time.sleep(1)
        s.close()
        logtext = pathlib.Path("/tmp/tunnel_test.log").read_text()
        checks["tunnel_zenoh_live"] = "config updated via zenoh" in logtext

        # 2) simulated paid action -> subscriber -> MuJoCo episode
        RESULT.unlink(missing_ok=True)
        link = subprocess.Popen(
            [sys.executable, "robopay_link.py", "--once"], cwd=HERE)
        time.sleep(3)   # let the subscriber declare itself
        subprocess.run(
            [sys.executable, "simulate_paid_action.py", "9.0", "1.5"],
            cwd=HERE, check=True)
        wait_for(lambda: link.poll() is not None, 120, "episode to finish")
        checks["episode_exit_ok"] = link.returncode == 0

        result = json.loads(RESULT.read_text())
        m = result["metrics"]
        checks["goal_reached"] = m["reached"]
        checks["no_collisions"] = m["collisions"] == 0
        print(json.dumps({"checks": checks, "result": result}, indent=1))
    finally:
        tunnel.terminate()
        if link and link.poll() is None:
            link.terminate()

    ok = all(checks.values())
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
