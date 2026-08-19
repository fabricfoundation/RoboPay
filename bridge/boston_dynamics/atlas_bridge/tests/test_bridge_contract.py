"""The tunnel bridge must actually execute the registered skill.

These tests exist because of a real defect: the bridge was left wired to a
previous skill (``navigate_obstacles`` / ``run_obstacle_nav``) after the profile
moved to ``inspect_shelf``. Nothing caught it — the simulator tests never
imported the bridge, so the module did not even import successfully.

Everything here drives the same code path Zenoh drives, using the action
envelopes that ship in the registry profile, and checks the correlation fields
the tunnel needs to match an asynchronous result to a paid request.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from bridge.boston_dynamics.atlas_bridge import bridge as bridge_module
from bridge.boston_dynamics.atlas_bridge.bridge import (
    ALLOWED_ACTIONS,
    INSPECTION_PARAMS,
    PROFILE_ID,
    AtlasActionHandler,
    inspection_params,
)
from bridge.boston_dynamics.atlas_bridge.idempotency import IdempotencyStore

PROFILE_DIR = (
    Path(bridge_module.__file__).resolve().parents[3]
    / "registry" / "vendors" / "boston-dynamics" / "atlas"
    / "boston-dynamics.atlas.mujoco-pybullet-webots-shelf-inspection.v1"
)


def envelope(name: str) -> bytes:
    """The action envelope shipped in the profile, byte for byte."""
    return (PROFILE_DIR / "examples" / f"action-envelope.{name}.json").read_bytes()


def custom_envelope(**payload) -> bytes:
    body = {
        "payload": {
            "action": "inspect_shelf",
            "skill_id": "inspect_shelf",
            "robot_id": "atlas-sim-01",
            "action_id": "act-test-0001",
            "idempotency_key": "idem-test-0001",
            "params": {},
            **payload,
        },
        "timestamp": "2026-08-19T00:00:00Z",
    }
    return json.dumps(body).encode("utf-8")


class Recorder:
    """Captures whatever the handler would have published on the tunnel."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    def __call__(self, payload: bytes) -> None:
        self.messages.append(json.loads(payload.decode("utf-8")))

    @property
    def last(self) -> dict:
        assert self.messages, "the bridge published nothing"
        return self.messages[-1]


def fake_success(max_duration_seconds: float, stop_requested=None) -> dict:
    return {
        "success": True,
        "status": "success",
        "targets_completed": 3,
        "targets_total": 3,
        "sim_duration_seconds": max_duration_seconds,
    }


def handler(execute=fake_success, **kwargs) -> tuple[AtlasActionHandler, Recorder]:
    """A handler with an isolated in-memory idempotency store."""
    recorder = Recorder()
    kwargs.setdefault("idempotency", IdempotencyStore(path=None))
    return (
        AtlasActionHandler(recorder, execute=execute, synchronous=True, **kwargs),
        recorder,
    )


# -- the bridge and the registry must describe the same robot ---------------
def test_bridge_profile_id_matches_the_registry():
    profile = yaml.safe_load((PROFILE_DIR / "robot.profile.yaml").read_text(encoding="utf-8"))
    assert PROFILE_ID == profile["profileId"]


def test_bridge_executes_exactly_the_registered_skills():
    skills = yaml.safe_load((PROFILE_DIR / "skills.yaml").read_text(encoding="utf-8"))
    assert ALLOWED_ACTIONS == {entry["skillId"] for entry in skills["skills"]}


def test_bridge_accepts_exactly_the_registered_parameters():
    skills = yaml.safe_load((PROFILE_DIR / "skills.yaml").read_text(encoding="utf-8"))
    declared = next(s for s in skills["skills"] if s["skillId"] == "inspect_shelf")
    assert INSPECTION_PARAMS == set(declared["params"])


# -- the shipped envelopes must actually work -------------------------------
def test_shipped_inspect_envelope_reaches_the_skill():
    executed = {}

    def execute(max_duration_seconds: float, stop_requested=None) -> dict:
        executed["max_duration_seconds"] = max_duration_seconds
        return fake_success(max_duration_seconds, stop_requested)

    action, recorder = handler(execute=execute)
    assert action.handle(envelope("inspect_shelf")) == "executed"
    assert executed["max_duration_seconds"] == 30
    assert recorder.last["status"] == "success"


def test_shipped_stop_envelope_is_accepted():
    action, recorder = handler()
    assert action.handle(envelope("stop")) == "stop"
    assert recorder.last["status"] == "success"
    assert recorder.last["result"]["safe_stop_applied"] is True


# -- correlation fields the tunnel needs ------------------------------------
def test_result_echoes_every_correlation_field():
    action, recorder = handler()
    action.handle(custom_envelope(action_id="act-corr-77", idempotency_key="idem-corr-77"))
    result = recorder.last
    assert result["action_id"] == "act-corr-77"
    assert result["idempotency_key"] == "idem-corr-77"
    assert result["robot_id"] == "atlas-sim-01"
    assert result["skill_id"] == "inspect_shelf"
    assert result["profile_id"] == PROFILE_ID
    # Always the sha256:<hex> form the execution mapping declares, including for
    # an empty parameter set.
    assert result["params_hash"].startswith("sha256:")
    assert len(result["params_hash"]) == len("sha256:") + 64


def test_params_hash_always_matches_the_published_format():
    """``execution-mapping.yaml`` declares sha256:<hex>; nothing may differ."""
    action, recorder = handler()
    for params in ({}, {"maxDurationSec": 12}):
        action.handle(custom_envelope(action_id=f"act-{len(params)}", params=params))
        digest = recorder.last["params_hash"]
        assert digest.startswith("sha256:")
        assert len(digest) == len("sha256:") + 64


def test_missing_skill_id_is_not_inferred_from_the_action():
    """A caller that omits skill_id has not said what it is paying for."""
    body = json.loads(custom_envelope().decode())
    del body["payload"]["skill_id"]
    action, recorder = handler()
    assert action.handle(json.dumps(body).encode()) == "failure"
    assert recorder.last["result"]["error_code"] == "MISSING_IDENTITY"


def test_params_hash_is_derived_from_the_parameters():
    action, recorder = handler()
    action.handle(custom_envelope(params={"maxDurationSec": 12}))
    first = recorder.last["params_hash"]
    action.handle(custom_envelope(params={"maxDurationSec": 12}))
    assert recorder.last["params_hash"] == first
    action.handle(custom_envelope(params={"maxDurationSec": 13}))
    assert recorder.last["params_hash"] != first


# -- rejection paths --------------------------------------------------------
def test_unregistered_action_is_refused_without_executing():
    calls = []
    action, recorder = handler(execute=lambda **kw: calls.append(kw) or fake_success(1))
    action.handle(custom_envelope(action="navigate_obstacles", skill_id="navigate_obstacles"))
    assert recorder.last["status"] == "failure"
    assert recorder.last["result"]["error_code"] == "UNREGISTERED_ACTION"
    assert calls == []


def test_action_and_skill_must_agree():
    action, recorder = handler()
    action.handle(custom_envelope(action="inspect_shelf", skill_id="stop"))
    assert recorder.last["result"]["error_code"] == "ACTION_SKILL_MISMATCH"


def test_action_for_another_robot_is_ignored():
    action, recorder = handler()
    assert action.handle(custom_envelope(robot_id="some-other-robot")) == "ignored_foreign_robot"
    assert recorder.messages == []


def test_malformed_payload_is_rejected_before_simulation():
    action, recorder = handler()
    assert action.handle(b"not json at all") == "rejected_malformed"
    assert recorder.messages == []


@pytest.mark.parametrize(
    "params",
    [
        {"maxDurationSec": 1},          # below the declared minimum
        {"maxDurationSec": 600},        # above the declared maximum
        {"maxDurationSec": "thirty"},   # wrong type
        {"maxDurationSec": True},       # bool is not a number here
        {"side": "left"},               # parameter the skill does not declare
    ],
)
def test_invalid_parameters_are_refused(params):
    action, recorder = handler()
    action.handle(custom_envelope(params=params))
    assert recorder.last["status"] == "failure"
    assert recorder.last["result"]["error_code"] in {"INVALID_PARAMS", "INVALID_DURATION"}


def test_stop_rejects_parameters():
    action, recorder = handler()
    action.handle(custom_envelope(action="stop", skill_id="stop", params={"maxDurationSec": 10}))
    assert recorder.last["result"]["error_code"] == "INVALID_PARAMS"


def test_simulator_failure_is_reported_not_swallowed():
    def explode(max_duration_seconds: float, stop_requested=None) -> dict:
        raise RuntimeError("simulator exploded")

    action, recorder = handler(execute=explode)
    action.handle(custom_envelope())
    assert recorder.last["status"] == "failure"
    assert recorder.last["result"]["error_code"] == "SIMULATOR_EXECUTION_ERROR"


def test_failed_execution_is_reported_as_failure():
    def failing(max_duration_seconds: float, stop_requested=None) -> dict:
        return {"success": False, "status": "failure", "targets_completed": 1}

    action, recorder = handler(execute=failing)
    action.handle(custom_envelope())
    assert recorder.last["status"] == "failure"


def test_duration_default_matches_the_declared_default():
    assert inspection_params({}) == 30.0
    assert inspection_params({"maxDurationSec": 12}) == 12.0


# -- the real thing, end to end --------------------------------------------
@pytest.mark.slow
def test_shipped_envelope_drives_the_real_simulator():
    """No stubs: the profile's own envelope runs the MuJoCo inspection."""
    action, recorder = handler(execute=None)
    assert action.handle(envelope("inspect_shelf")) == "executed"

    result = recorder.last
    assert result["status"] == "success"
    assert result["skill_id"] == "inspect_shelf"
    assert result["profile_id"] == PROFILE_ID
    assert result["result"]["targets_completed"] == result["result"]["targets_total"] == 3
    assert result["result"]["shelf_contacts"] == 0
    assert result["result"]["fall_detected"] is False
    assert result["result"]["robot_model"] == "Boston Dynamics Atlas v4"


# -- schema compatibility with whatever the tunnel forwards ------------------
def camel_envelope(**payload) -> bytes:
    """The same action expressed in camelCase, as a JS/Go caller would send it."""
    body = {
        "payload": {
            "action": "inspect_shelf",
            "skillId": "inspect_shelf",
            "robotId": "atlas-sim-01",
            "actionId": "act-camel-0001",
            "idempotencyKey": "idem-camel-0001",
            "params": {"maxDurationSec": 12},
            **payload,
        },
        "transaction_details": {"paymentPayload": {"amount": "1000"}},
        "timestamp": "2026-08-19T00:00:00Z",
    }
    return json.dumps(body).encode("utf-8")


def test_camel_case_envelope_is_understood():
    """The tunnel forwards the caller's body verbatim, so casing is theirs.

    A bridge that only understood snake_case would pass every local test and
    then silently ignore a real Fabric request.
    """
    action, recorder = handler()
    assert action.handle(camel_envelope()) == "executed"
    result = recorder.last
    assert result["action_id"] == "act-camel-0001"
    assert result["idempotency_key"] == "idem-camel-0001"
    assert result["robot_id"] == "atlas-sim-01"
    assert result["skill_id"] == "inspect_shelf"
    assert result["status"] == "success"


def test_both_spellings_produce_the_same_params_hash():
    action, recorder = handler()
    action.handle(camel_envelope())
    camel_hash = recorder.last["params_hash"]
    action.handle(custom_envelope(params={"maxDurationSec": 12}))
    assert recorder.last["params_hash"] == camel_hash


def test_params_hash_is_recomputed_not_trusted():
    """A caller cannot declare one params hash and send different parameters."""
    body = json.loads(camel_envelope().decode())
    body["payload"]["paramsHash"] = "sha256:deadbeef"
    action, recorder = handler()
    action.handle(json.dumps(body).encode())
    assert recorder.last["params_hash"] != "sha256:deadbeef"


def test_payment_details_survive_the_parse():
    from bridge.boston_dynamics.atlas_bridge.bridge import load_event_parser

    event = load_event_parser()(camel_envelope())
    assert event.payment_payload == {"amount": "1000"}


# -- identity the tunnel correlates on is mandatory -------------------------
@pytest.mark.parametrize("missing", ["action_id", "robot_id", "skill_id", "idempotency_key"])
def test_missing_identity_is_refused(missing):
    """A result nobody can correlate is worse than no result."""
    body = json.loads(custom_envelope().decode())
    if missing == "robot_id":
        # An absent robot_id is not addressed to this robot at all.
        body["payload"]["robot_id"] = ""
        action, recorder = handler()
        assert action.handle(json.dumps(body).encode()) == "ignored_foreign_robot"
        return
    body["payload"][missing] = ""
    action, recorder = handler()
    assert action.handle(json.dumps(body).encode()) == "failure"
    assert recorder.last["result"]["error_code"] == "MISSING_IDENTITY"
    assert missing in recorder.last["result"]["message"]


def test_complete_identity_is_accepted():
    action, recorder = handler()
    assert action.handle(custom_envelope()) == "executed"
    assert recorder.last["status"] == "success"
