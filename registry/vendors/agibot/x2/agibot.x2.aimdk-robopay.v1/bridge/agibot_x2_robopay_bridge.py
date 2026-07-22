#!/usr/bin/env python3
"""RoboPay Zenoh-to-AimDK adapter for the validated AgiBot X2 right wave.

The adapter is deliberately fail-closed.  It validates the complete normalized
paid-action envelope, claims the action in a persistent SQLite replay store,
calls AimDK at most once, and publishes a correlated structured result.

AimDK ``RUNNING`` means accepted/in progress.  It is *not* converted to a
successful result and is never settlement-eligible.  Only an explicit AimDK
``CommonState.SUCCESS`` response is emitted as terminal success.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


PROFILE_ID = "agibot.x2.aimdk-robopay.v1"
SKILL_ID = "x2_right_wave"
ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"
AIMDK_SERVICE = "/aimdk_5Fmsgs/srv/SetMcPresetMotion"
DEFAULT_NETWORK = "eip155:84532"
DEFAULT_ASSET = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
DEFAULT_AMOUNT = "2000"
DEFAULT_MAX_AUTH_TTL_SEC = 300
DEFAULT_FUTURE_CLOCK_SKEW_SEC = 30
MAX_CONFIGURABLE_AUTH_TTL_SEC = 3600
MAX_CONFIGURABLE_CLOCK_SKEW_SEC = 300

ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
EVM_ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")


class ContractError(ValueError):
    """A safe, externally reportable action-contract failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("INVALID_ENVELOPE", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _decode_json(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys)
    except ContractError:
        raise
    except (json.JSONDecodeError, TypeError) as exc:
        raise ContractError("INVALID_JSON", "action must be valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ContractError("INVALID_ENVELOPE", "action envelope must be an object")
    return value


def canonical_params_json(params: Mapping[str, Any]) -> str:
    """Return the profile's deterministic UTF-8 JSON representation."""

    return json.dumps(
        params,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_params_hash(params: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_params_json(params).encode("utf-8")).hexdigest()


def _parse_payment_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ContractError(
            "PAYMENT_INVALID", f"payment.{field} must be an RFC3339 UTC timestamp"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ContractError(
            "PAYMENT_INVALID", f"payment.{field} is not a valid timestamp"
        ) from exc
    return parsed.astimezone(timezone.utc)


def _require_exact_keys(
    value: Mapping[str, Any], required: set[str], context: str, error_code: str
) -> None:
    missing = sorted(required - set(value))
    extra = sorted(set(value) - required)
    if missing:
        raise ContractError(
            error_code, f"{context} missing fields: {', '.join(missing)}"
        )
    if extra:
        raise ContractError(
            error_code, f"{context} has unsupported fields: {', '.join(extra)}"
        )


@dataclass(frozen=True)
class ValidationConfig:
    robot_id: str
    payee_address: str
    network: str = DEFAULT_NETWORK
    asset: str = DEFAULT_ASSET
    amount: str = DEFAULT_AMOUNT
    max_authorization_ttl_sec: int = DEFAULT_MAX_AUTH_TTL_SEC
    future_clock_skew_sec: int = DEFAULT_FUTURE_CLOCK_SKEW_SEC

    def __post_init__(self) -> None:
        if not ID_PATTERN.fullmatch(self.robot_id):
            raise ValueError("configured robot ID must be 8-128 safe characters")
        if not EVM_ADDRESS_PATTERN.fullmatch(self.payee_address):
            raise ValueError("configured payee address must be a 20-byte EVM address")
        if not EVM_ADDRESS_PATTERN.fullmatch(self.asset):
            raise ValueError("configured asset must be a 20-byte EVM address")
        if not self.amount.isdigit() or int(self.amount) <= 0:
            raise ValueError(
                "configured amount must be a positive integer in smallest units"
            )
        if (
            isinstance(self.max_authorization_ttl_sec, bool)
            or not isinstance(self.max_authorization_ttl_sec, int)
            or not 1 <= self.max_authorization_ttl_sec <= MAX_CONFIGURABLE_AUTH_TTL_SEC
        ):
            raise ValueError(
                "maximum authorization TTL must be 1-"
                f"{MAX_CONFIGURABLE_AUTH_TTL_SEC} seconds"
            )
        if (
            isinstance(self.future_clock_skew_sec, bool)
            or not isinstance(self.future_clock_skew_sec, int)
            or not 0 <= self.future_clock_skew_sec <= MAX_CONFIGURABLE_CLOCK_SKEW_SEC
        ):
            raise ValueError(
                f"future clock skew must be 0-{MAX_CONFIGURABLE_CLOCK_SKEW_SEC} seconds"
            )


@dataclass(frozen=True)
class PaymentEvidence:
    provider: str
    authorization_id: str
    network: str
    asset: str
    amount: str
    pay_to: str
    issued_at: datetime
    expires_at: datetime
    status: str
    settled: bool


@dataclass(frozen=True)
class Action:
    action_id: str
    robot_id: str
    skill_id: str
    params: dict[str, Any]
    params_hash: str
    idempotency_key: str
    payment: PaymentEvidence


def parse_action(
    raw: str,
    config: ValidationConfig,
    *,
    now: datetime | None = None,
) -> Action:
    envelope = _decode_json(raw)
    _require_exact_keys(
        envelope,
        {
            "actionId",
            "robotId",
            "skillId",
            "params",
            "paramsHash",
            "idempotencyKey",
            "payment",
        },
        "action envelope",
        "INVALID_ENVELOPE",
    )

    action_id = envelope["actionId"]
    robot_id = envelope["robotId"]
    skill_id = envelope["skillId"]
    params = envelope["params"]
    params_hash = envelope["paramsHash"]
    idempotency_key = envelope["idempotencyKey"]
    payment = envelope["payment"]

    if not isinstance(action_id, str) or not ID_PATTERN.fullmatch(action_id):
        raise ContractError(
            "INVALID_ENVELOPE", "actionId must be 8-128 safe characters"
        )
    if not isinstance(robot_id, str) or robot_id != config.robot_id:
        raise ContractError(
            "WRONG_ROBOT", "robotId does not match this bridge identity"
        )
    if skill_id != SKILL_ID:
        raise ContractError(
            "UNKNOWN_SKILL", "only x2_right_wave is enabled by this profile"
        )
    if not isinstance(idempotency_key, str) or not ID_PATTERN.fullmatch(
        idempotency_key
    ):
        raise ContractError(
            "INVALID_ENVELOPE", "idempotencyKey must be 8-128 safe characters"
        )

    if not isinstance(params, dict):
        raise ContractError("INVALID_PARAMS", "params must be an object")
    _require_exact_keys(params, {"interrupt"}, "params", "INVALID_PARAMS")
    if not isinstance(params["interrupt"], bool):
        raise ContractError("INVALID_PARAMS", "params.interrupt must be boolean")

    if not isinstance(params_hash, str) or not HASH_PATTERN.fullmatch(params_hash):
        raise ContractError(
            "PARAMS_HASH_MISMATCH", "paramsHash must be lowercase SHA-256 hex"
        )
    expected_hash = canonical_params_hash(params)
    if not hmac.compare_digest(params_hash, expected_hash):
        raise ContractError(
            "PARAMS_HASH_MISMATCH", "paramsHash does not match canonical params"
        )

    if not isinstance(payment, dict):
        raise ContractError("PAYMENT_INVALID", "payment must be an object")
    payment_fields = {
        "provider",
        "authorizationId",
        "verified",
        "status",
        "settled",
        "network",
        "asset",
        "amount",
        "payTo",
        "issuedAt",
        "expiresAt",
    }
    _require_exact_keys(payment, payment_fields, "payment", "PAYMENT_INVALID")

    if payment["provider"] != "x402" or payment["verified"] is not True:
        raise ContractError(
            "PAYMENT_INVALID", "payment must be relay-verified x402 authorization"
        )
    if not isinstance(payment["authorizationId"], str) or not ID_PATTERN.fullmatch(
        payment["authorizationId"]
    ):
        raise ContractError(
            "PAYMENT_INVALID", "payment.authorizationId must be a safe identifier"
        )
    if payment["status"] != "authorized" or payment["settled"] is not False:
        raise ContractError(
            "PAYMENT_INVALID",
            "payment must be authorized and unsettled before robot execution",
        )
    if payment["network"] != config.network:
        raise ContractError(
            "PAYMENT_INVALID", "payment network does not match the profile"
        )
    if (
        not isinstance(payment["asset"], str)
        or payment["asset"].lower() != config.asset.lower()
    ):
        raise ContractError(
            "PAYMENT_INVALID", "payment asset does not match the profile"
        )
    if payment["amount"] != config.amount:
        raise ContractError(
            "PAYMENT_INVALID", "payment amount does not match the skill price"
        )
    if (
        not isinstance(payment["payTo"], str)
        or payment["payTo"].lower() != config.payee_address.lower()
    ):
        raise ContractError(
            "PAYMENT_INVALID", "payment payee does not match the robot wallet binding"
        )
    issued_at = _parse_payment_time(payment["issuedAt"], "issuedAt")
    expires_at = _parse_payment_time(payment["expiresAt"], "expiresAt")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if issued_at > current + timedelta(seconds=config.future_clock_skew_sec):
        raise ContractError(
            "PAYMENT_INVALID",
            "payment authorization exceeds the permitted future clock skew",
        )
    if expires_at <= issued_at:
        raise ContractError(
            "PAYMENT_INVALID", "payment expiry must be later than issuance"
        )
    authorization_ttl = (expires_at - issued_at).total_seconds()
    if authorization_ttl > config.max_authorization_ttl_sec:
        raise ContractError(
            "PAYMENT_INVALID",
            "payment authorization TTL exceeds the configured maximum",
        )
    if expires_at <= current:
        raise ContractError("PAYMENT_EXPIRED", "payment authorization has expired")

    return Action(
        action_id=action_id,
        robot_id=robot_id,
        skill_id=skill_id,
        params=dict(params),
        params_hash=params_hash,
        idempotency_key=idempotency_key,
        payment=PaymentEvidence(
            provider="x402",
            authorization_id=payment["authorizationId"],
            network=payment["network"],
            asset=payment["asset"],
            amount=payment["amount"],
            pay_to=payment["payTo"],
            issued_at=issued_at,
            expires_at=expires_at,
            status="authorized",
            settled=False,
        ),
    )


class ReplayStore:
    """Persistent, atomic action/idempotency claim store."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(
                parents=True, exist_ok=True
            )
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS actions (
                idempotency_key TEXT PRIMARY KEY,
                action_id TEXT NOT NULL UNIQUE,
                authorization_id TEXT NOT NULL UNIQUE,
                params_hash TEXT NOT NULL,
                state TEXT NOT NULL,
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    def claim(self, action: Action) -> bool:
        timestamp = _utc_now()
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    """
                    INSERT INTO actions (
                        idempotency_key, action_id, authorization_id, params_hash,
                        state, result_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'processing', NULL, ?, ?)
                    """,
                    (
                        action.idempotency_key,
                        action.action_id,
                        action.payment.authorization_id,
                        action.params_hash,
                        timestamp,
                        timestamp,
                    ),
                )
                self._connection.execute("COMMIT")
                return True
            except sqlite3.IntegrityError:
                self._connection.execute("ROLLBACK")
                return False
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def finalize(self, action: Action, result: Mapping[str, Any]) -> None:
        encoded = json.dumps(
            result, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        with self._lock:
            self._connection.execute(
                """
                UPDATE actions
                   SET state = ?, result_json = ?, updated_at = ?
                 WHERE idempotency_key = ? AND action_id = ?
                """,
                (
                    str(result["status"]),
                    encoded,
                    _utc_now(),
                    action.idempotency_key,
                    action.action_id,
                ),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()


@dataclass(frozen=True)
class ExecutionOutcome:
    status: str
    message: str
    task_id: int | None = None
    vendor_state: int | str | None = None
    vendor_code: int | None = None
    error_code: str | None = None
    simulated: bool = False


class DryRunExecutor:
    def execute(self, action: Action) -> ExecutionOutcome:
        _audit_log(
            "aimdk_dry_run",
            actionId=action.action_id,
            skillId=action.skill_id,
            area=2,
            motion=1002,
            interrupt=action.params["interrupt"],
        )
        return ExecutionOutcome(
            status="pending",
            message="Dry run validated the mapping; no physical action was executed",
            vendor_state="DRY_RUN",
            error_code=None,
            simulated=True,
        )

    def close(self) -> None:
        return None


class AimdkExecutor:
    """Single-attempt AimDK executor.

    The service call is never automatically resubmitted after an ambiguous
    timeout, because the robot may have received the first request even when
    the ROS response was lost.
    """

    def __init__(
        self, *, service_wait_sec: float = 10.0, response_timeout_sec: float = 2.0
    ) -> None:
        import rclpy
        from aimdk_msgs.msg import (
            CommonState,
            McControlArea,
            McPresetMotion,
            RequestHeader,
        )
        from aimdk_msgs.srv import SetMcPresetMotion
        from rclpy.node import Node

        self.rclpy = rclpy
        self.CommonState = CommonState
        self.McControlArea = McControlArea
        self.McPresetMotion = McPresetMotion
        self.RequestHeader = RequestHeader
        self.SetMcPresetMotion = SetMcPresetMotion
        self.response_timeout_sec = response_timeout_sec

        rclpy.init(args=None)

        class PresetNode(Node):
            pass

        self.node: Any = PresetNode("robopay_agibot_x2_bridge")
        self.client = self.node.create_client(SetMcPresetMotion, AIMDK_SERVICE)
        if not self.client.wait_for_service(timeout_sec=service_wait_sec):
            self.close()
            raise ContractError(
                "ROBOT_UNAVAILABLE", f"AimDK service unavailable: {AIMDK_SERVICE}"
            )

    def execute(self, action: Action) -> ExecutionOutcome:
        request = self.SetMcPresetMotion.Request()
        request.header = self.RequestHeader()
        request.header.stamp = self.node.get_clock().now().to_msg()
        request.area = self.McControlArea()
        request.area.value = 2
        request.motion = self.McPresetMotion()
        request.motion.value = 1002
        request.interrupt = action.params["interrupt"]
        if hasattr(request, "ani_path"):
            request.ani_path = ""
        if hasattr(request, "play_timestamp"):
            request.play_timestamp = 0

        future = self.client.call_async(request)
        self.rclpy.spin_until_future_complete(
            self.node, future, timeout_sec=self.response_timeout_sec
        )
        if not future.done():
            return ExecutionOutcome(
                status="error",
                message="AimDK response timed out; outcome is unknown and request was not retried",
                error_code="ACTION_OUTCOME_UNKNOWN",
            )

        response = future.result()
        if response is None:
            return ExecutionOutcome(
                status="error",
                message="AimDK service returned no response",
                error_code="ACTION_FAILED",
            )

        task_response = getattr(response, "response", response)
        header = getattr(task_response, "header", None)
        code = getattr(header, "code", None)
        state = getattr(task_response, "state", None)
        state_value = _state_value(state)
        task_id = getattr(task_response, "task_id", None)
        success_state = getattr(self.CommonState, "SUCCESS", object())
        running_state = getattr(self.CommonState, "RUNNING", object())

        if code == 0 and state_value == success_state:
            return ExecutionOutcome(
                status="success",
                message="AgiBot X2 reported the preset motion completed",
                task_id=task_id,
                vendor_state=state_value,
                vendor_code=code,
            )
        if state_value == running_state or (code == 0 and state_value is None):
            return ExecutionOutcome(
                status="pending",
                message="AimDK accepted the preset motion but has not reported final completion",
                task_id=task_id,
                vendor_state=state_value,
                vendor_code=code,
            )
        return ExecutionOutcome(
            status="error",
            message="AimDK rejected or failed the preset motion",
            task_id=task_id,
            vendor_state=state_value,
            vendor_code=code,
            error_code="ACTION_REJECTED",
        )

    def close(self) -> None:
        node = getattr(self, "node", None)
        if node is not None:
            node.destroy_node()
            self.node = None
        rclpy = getattr(self, "rclpy", None)
        if rclpy is not None and rclpy.ok():
            rclpy.shutdown()


def _state_value(state: Any) -> Any:
    """Normalize ROS message-wrapper and direct integer state representations."""

    return getattr(state, "value", state)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _audit_log(event: str, **fields: Any) -> None:
    safe = {"event": event, "timestamp": _utc_now(), **fields}
    print(json.dumps(safe, sort_keys=True, ensure_ascii=False), flush=True)


def _partial_identifiers(raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(value, dict):
        return {}
    partial: dict[str, str] = {}
    for source, target in (
        ("actionId", "actionId"),
        ("robotId", "robotId"),
        ("skillId", "skillId"),
        ("idempotencyKey", "idempotencyKey"),
        ("paramsHash", "paramsHash"),
    ):
        candidate = value.get(source)
        if isinstance(candidate, str) and len(candidate) <= 128:
            partial[target] = candidate
    return partial


def _base_result(
    action: Action | None, partial: Mapping[str, str] | None = None
) -> dict[str, Any]:
    partial = partial or {}
    return {
        "schemaVersion": "robot-action-result.v1",
        "actionId": action.action_id
        if action
        else partial.get("actionId", "unidentified"),
        "robotId": action.robot_id
        if action
        else partial.get("robotId", "unidentified"),
        "skillId": action.skill_id
        if action
        else partial.get("skillId", "unidentified"),
        "idempotencyKey": (
            action.idempotency_key
            if action
            else partial.get("idempotencyKey", "unidentified")
        ),
        "paramsHash": action.params_hash
        if action
        else partial.get("paramsHash", "unidentified"),
        "timestamp": _utc_now(),
    }


def error_result(
    action: Action | None,
    code: str,
    message: str,
    *,
    partial: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return {
        **_base_result(action, partial),
        "status": "error",
        "settlementEligible": False,
        "error": {"code": code, "message": message},
    }


def outcome_result(action: Action, outcome: ExecutionOutcome) -> dict[str, Any]:
    if outcome.status == "error":
        return error_result(
            action,
            outcome.error_code or "ACTION_FAILED",
            outcome.message,
        )
    settlement_eligible = outcome.status == "success" and not outcome.simulated
    return {
        **_base_result(action),
        "status": outcome.status,
        "settlementEligible": settlement_eligible,
        "result": {
            "message": outcome.message,
            "vendor": {
                "service": AIMDK_SERVICE,
                "taskId": outcome.task_id,
                "state": outcome.vendor_state,
                "code": outcome.vendor_code,
            },
            "simulated": outcome.simulated,
        },
    }


class Bridge:
    def __init__(
        self,
        executor: DryRunExecutor | AimdkExecutor | Any,
        replay_store: ReplayStore,
        config: ValidationConfig,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.executor = executor
        self.replay_store = replay_store
        self.config = config
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._execution_lock = threading.Lock()

    def process_raw(self, raw: str) -> dict[str, Any]:
        partial = _partial_identifiers(raw)
        try:
            action = parse_action(raw, self.config, now=self.now())
        except ContractError as exc:
            _audit_log(
                "action_rejected", code=exc.code, actionId=partial.get("actionId")
            )
            return error_result(None, exc.code, str(exc), partial=partial)

        try:
            claimed = self.replay_store.claim(action)
        except Exception:
            _audit_log("replay_store_error", actionId=action.action_id)
            return error_result(
                action, "REPLAY_STORE_ERROR", "persistent replay store unavailable"
            )
        if not claimed:
            _audit_log("action_rejected", code="DUPLICATE", actionId=action.action_id)
            return error_result(
                action,
                "DUPLICATE",
                "actionId or idempotencyKey was already claimed; action was not re-executed",
            )

        if not self._execution_lock.acquire(blocking=False):
            result = error_result(
                action, "ROBOT_BUSY", "another physical action is in progress"
            )
            self.replay_store.finalize(action, result)
            return result

        try:
            _audit_log(
                "action_accepted",
                actionId=action.action_id,
                robotId=action.robot_id,
                skillId=action.skill_id,
                idempotencyKey=action.idempotency_key,
                paramsHash=action.params_hash,
            )
            try:
                outcome = self.executor.execute(action)
            except ContractError as exc:
                result = error_result(action, exc.code, str(exc))
            except Exception as exc:
                _audit_log(
                    "adapter_exception",
                    actionId=action.action_id,
                    exceptionType=type(exc).__name__,
                )
                result = error_result(
                    action,
                    "ACTION_FAILED",
                    "unexpected robot adapter failure",
                )
            else:
                result = outcome_result(action, outcome)
            self.replay_store.finalize(action, result)
            _audit_log(
                "action_result",
                actionId=action.action_id,
                status=result["status"],
                settlementEligible=result["settlementEligible"],
            )
            return result
        finally:
            self._execution_lock.release()


def _decode_sample(sample: Any) -> str:
    payload = getattr(sample, "payload", sample)
    if callable(payload):
        payload = payload()
    if hasattr(payload, "to_bytes"):
        payload = payload.to_bytes()
    elif hasattr(payload, "bytes"):
        payload = payload.bytes() if callable(payload.bytes) else payload.bytes
    if isinstance(payload, bytes):
        return payload.decode("utf-8")
    return str(payload)


def run_stdin(bridge: Bridge) -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print("stdin is empty", file=sys.stderr)
        return 2
    result = bridge.process_raw(raw)
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0 if result["status"] in {"success", "pending"} else 1


def _zenoh_config(zenoh: Any, endpoints: list[str]) -> Any:
    config = zenoh.Config()
    if endpoints:
        config.insert_json5("connect/endpoints", json.dumps(endpoints))
    return config


def run_zenoh(
    bridge: Bridge,
    *,
    action_topic: str,
    result_topic: str,
    endpoints: list[str],
) -> int:
    import zenoh

    session = zenoh.open(_zenoh_config(zenoh, endpoints))
    publisher = session.declare_publisher(result_topic)

    def callback(sample: Any) -> None:
        try:
            raw = _decode_sample(sample)
            result = bridge.process_raw(raw)
        except Exception:
            result = error_result(
                None, "BRIDGE_INTERNAL_ERROR", "bridge callback failed"
            )
        publisher.put(json.dumps(result, sort_keys=True, separators=(",", ":")))

    subscriber = session.declare_subscriber(action_topic, callback)
    _audit_log("bridge_ready", actionTopic=action_topic, resultTopic=result_topic)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            subscriber.undeclare()
            publisher.undeclare()
        finally:
            session.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot-id", default=os.getenv("ROBOPAY_ROBOT_ID"))
    parser.add_argument("--payee-address", default=os.getenv("ROBOPAY_PAYEE_ADDRESS"))
    parser.add_argument(
        "--expected-network", default=os.getenv("ROBOPAY_NETWORK", DEFAULT_NETWORK)
    )
    parser.add_argument(
        "--expected-asset", default=os.getenv("ROBOPAY_ASSET", DEFAULT_ASSET)
    )
    parser.add_argument(
        "--expected-amount", default=os.getenv("ROBOPAY_AMOUNT", DEFAULT_AMOUNT)
    )
    parser.add_argument(
        "--max-authorization-ttl-sec",
        type=int,
        default=os.getenv("ROBOPAY_MAX_AUTH_TTL_SEC", str(DEFAULT_MAX_AUTH_TTL_SEC)),
    )
    parser.add_argument(
        "--future-clock-skew-sec",
        type=int,
        default=os.getenv(
            "ROBOPAY_FUTURE_CLOCK_SKEW_SEC", str(DEFAULT_FUTURE_CLOCK_SKEW_SEC)
        ),
    )
    parser.add_argument(
        "--state-db",
        default=os.getenv("ROBOPAY_STATE_DB", ".robopay/agibot-x2-replay.sqlite3"),
    )
    parser.add_argument(
        "--action-topic", default=os.getenv("ROBOPAY_ACTION_TOPIC", ACTION_TOPIC)
    )
    parser.add_argument(
        "--result-topic", default=os.getenv("ROBOPAY_RESULT_TOPIC", RESULT_TOPIC)
    )
    parser.add_argument(
        "--zenoh-connect",
        action="append",
        default=[],
        help="Zenoh endpoint, repeatable (for example tcp/127.0.0.1:7447)",
    )
    parser.add_argument("--service-wait-sec", type=float, default=10.0)
    parser.add_argument("--response-timeout-sec", type=float, default=2.0)
    parser.add_argument(
        "--stdin", action="store_true", help="process one envelope from stdin"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="validate without physical execution"
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.robot_id:
        print("ROBOPAY_ROBOT_ID or --robot-id is required", file=sys.stderr)
        return 2
    if not args.payee_address:
        print("ROBOPAY_PAYEE_ADDRESS or --payee-address is required", file=sys.stderr)
        return 2
    if args.service_wait_sec <= 0 or args.response_timeout_sec <= 0:
        print("service timeouts must be positive", file=sys.stderr)
        return 2

    try:
        config = ValidationConfig(
            robot_id=args.robot_id,
            payee_address=args.payee_address,
            network=args.expected_network,
            asset=args.expected_asset,
            amount=args.expected_amount,
            max_authorization_ttl_sec=args.max_authorization_ttl_sec,
            future_clock_skew_sec=args.future_clock_skew_sec,
        )
        replay_store = ReplayStore(args.state_db)
        executor: DryRunExecutor | AimdkExecutor
        executor = (
            DryRunExecutor()
            if args.dry_run
            else AimdkExecutor(
                service_wait_sec=args.service_wait_sec,
                response_timeout_sec=args.response_timeout_sec,
            )
        )
    except (ValueError, ContractError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    bridge = Bridge(executor, replay_store, config)
    try:
        if args.stdin:
            return run_stdin(bridge)
        return run_zenoh(
            bridge,
            action_topic=args.action_topic,
            result_topic=args.result_topic,
            endpoints=args.zenoh_connect,
        )
    finally:
        executor.close()
        replay_store.close()


if __name__ == "__main__":
    raise SystemExit(main())
