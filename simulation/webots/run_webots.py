"""Run one Go2 navigation episode in Webots, headless.

Generates the world for the given layout, starts Webots in batch mode and
attaches the extern controller, then prints the resulting metrics JSON.

Usage: python3 run_webots.py [world_name]
"""

import json
import os
import pathlib
import socket
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).parent
WEBOTS_HOME = os.environ.get("WEBOTS_HOME", "/Applications/Webots.app")
if WEBOTS_HOME.endswith(".app"):   # macOS bundle
    WEBOTS = f"{WEBOTS_HOME}/Contents/MacOS/webots"
    WEBOTS_PY = f"{WEBOTS_HOME}/Contents/lib/controller/python"
else:                              # linux install prefix
    WEBOTS = f"{WEBOTS_HOME}/webots"
    WEBOTS_PY = f"{WEBOTS_HOME}/lib/controller/python"
PORT = 1234


def wait_for_port(port, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(1)
    raise TimeoutError(f"webots did not listen on :{port}")


def run_world(name, timeout=900):
    world = HERE / "worlds" / f"{name}.wbt"
    task = HERE / "worlds" / f"{name}_task.json"
    result = HERE / "worlds" / f"{name}_result.json"
    result.unlink(missing_ok=True)

    log = open("/tmp/webots_run.log", "w")
    webots = subprocess.Popen(
        [WEBOTS, "--batch", "--minimize", "--no-rendering", "--mode=fast",
         f"--port={PORT}", "--stdout", "--stderr", str(world)],
        stdout=log, stderr=log)
    env = dict(os.environ,
               WEBOTS_HOME=WEBOTS_HOME,
               WEBOTS_CONTROLLER_URL=f"tcp://127.0.0.1:{PORT}/Go2",
               PYTHONPATH=WEBOTS_PY,
               TASK_FILE=str(task),
               RESULT_FILE=str(result))
    try:
        wait_for_port(PORT, 120)   # first launch can be slow
        controller = subprocess.run(
            [sys.executable, str(HERE / "controllers" / "go2_nav_webots.py")],
            env=env, timeout=timeout)
        if controller.returncode != 0:
            raise RuntimeError(f"controller exited {controller.returncode}")
        return json.loads(result.read_text())
    finally:
        webots.terminate()
        try:
            webots.wait(10)
        except subprocess.TimeoutExpired:
            webots.kill()
        log.close()


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "go2_nav"
    metrics = run_world(name)
    metrics.pop("trajectory", None)
    print(json.dumps(metrics, indent=1))
