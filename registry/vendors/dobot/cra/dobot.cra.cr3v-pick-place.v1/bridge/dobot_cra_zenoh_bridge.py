#!/usr/bin/env python3
"""Execute an approved DOBOT CRA project from RoboPay Zenoh action envelopes.

The bridge is deliberately narrow: a public skill maps to one locally approved
controller project. Request payloads can never supply coordinates, raw DOBOT
commands, speed, I/O values, project names, or tool settings.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import socket
import sqlite3
import sys
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol


SAFETY_ACK = "I_HAVE_VERIFIED_THE_CRA_SAFETY_SETUP"
PROJECT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
ACTION_FIELDS = {
    "actionId",
    "robotId",
    "skillId",
    "params",
    "paramsHash",
    "idempotencyKey",
    "payment",
}
PAYMENT_FIELDS = {
    "provider",
    "network",
    "asset",
    "amount",
    "payTo",
    "authorizationId",
    "verified",
    "status",
    "settled",
    "issuedAt",
    "expiresAt",
}

ROBOT_MODES = {
    1: "initializing",
    2: "brake_open",
    3: "powered_off",
    4: "disabled",
    5: "enabled_idle",
    6: "backdrive",
    7: "running",
    8: "single_move",
    9: "error",
    10: "paused",
    11: "collision",
}


class BridgeError(RuntimeError):
    """An envelope or robot execution failed safely."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class PaymentAuthorization:
    provider: str
    network: str
    asset: str
    amount: str
    pay_to: str
    authorization_id: str
    verified: bool
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
    issued_at: datetime
    expires_at: datetime
    payment: PaymentAuthorization


@dataclass(frozen=True)
class DobotReply:
    error_id: int
    values: tuple[float, ...]
    raw: str


class Executor(Protocol):
    def execute(self, action: Action, skill: dict[str, Any]) -> dict[str, Any]: ...

    def close(self) -> None: ...


def canonical_params_hash(params: dict[str, Any]) -> str:
    encoded = json.dumps(
        params,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise BridgeError("INVALID_ENVELOPE", f"{field} must be an RFC3339 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise BridgeError(
            "INVALID_ENVELOPE", f"{field} must be an RFC3339 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise BridgeError("INVALID_ENVELOPE", f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _require_identifier(
    value: Any, field: str, *, code: str = "INVALID_ENVELOPE"
) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise BridgeError(
            code,
            f"{field} must be 1-128 ASCII letters, digits, '.', '_', ':', or '-'",
        )
    return value


def _require_payment_string(payment: dict[str, Any], field: str) -> str:
    value = payment.get(field)
    if not isinstance(value, str) or not value:
        raise BridgeError("PAYMENT_INVALID", f"payment.{field} must be a string")
    return value


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BridgeError("INVALID_JSON", f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise BridgeError("INVALID_JSON", f"non-finite JSON number {value} is not allowed")


def _require_exact_fields(
    value: dict[str, Any], expected: set[str], label: str, code: str
) -> None:
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise BridgeError(code, f"{label} fields are invalid: {'; '.join(details)}")


def parse_action(
    raw: str,
    expected_robot_id: str,
    *,
    now: datetime | None = None,
    max_ttl_s: float = 300,
    clock_skew_s: float = 30,
) -> Action:
    try:
        encoded_size = len(raw.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise BridgeError("INVALID_JSON", "action envelope is not valid UTF-8") from exc
    if encoded_size > 65536:
        raise BridgeError("INVALID_ENVELOPE", "action envelope exceeds 65536 bytes")
    try:
        body = json.loads(
            raw,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except BridgeError:
        raise
    except json.JSONDecodeError as exc:
        raise BridgeError("INVALID_JSON", f"invalid action JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise BridgeError("INVALID_ENVELOPE", "action envelope must be a JSON object")
    _require_exact_fields(body, ACTION_FIELDS, "action envelope", "INVALID_ENVELOPE")

    action_id = _require_identifier(body.get("actionId"), "actionId")
    robot_id = body.get("robotId")
    if robot_id != expected_robot_id:
        raise BridgeError(
            "WRONG_ROBOT",
            "robotId does not match the locally configured robot",
        )
    skill_id = _require_identifier(body.get("skillId"), "skillId")
    idempotency_key = _require_identifier(body.get("idempotencyKey"), "idempotencyKey")

    params = body.get("params")
    if not isinstance(params, dict):
        raise BridgeError("INVALID_ENVELOPE", "params must be a JSON object")
    params_hash = body.get("paramsHash")
    if not isinstance(params_hash, str) or not SHA256_RE.fullmatch(params_hash):
        raise BridgeError("INVALID_ENVELOPE", "paramsHash must be a SHA-256 hex digest")
    expected_hash = canonical_params_hash(params)
    if not hmac.compare_digest(params_hash.lower(), expected_hash):
        raise BridgeError(
            "PARAMS_HASH_MISMATCH",
            "paramsHash does not match the canonical params object",
        )

    payment_raw = body.get("payment")
    if not isinstance(payment_raw, dict):
        raise BridgeError("PAYMENT_INVALID", "payment authorization is missing")
    _require_exact_fields(payment_raw, PAYMENT_FIELDS, "payment", "PAYMENT_INVALID")

    issued_at = _parse_timestamp(payment_raw.get("issuedAt"), "payment.issuedAt")
    expires_at = _parse_timestamp(payment_raw.get("expiresAt"), "payment.expiresAt")
    if expires_at <= issued_at:
        raise BridgeError(
            "PAYMENT_INVALID", "payment.expiresAt must be later than payment.issuedAt"
        )
    if (expires_at - issued_at).total_seconds() > max_ttl_s:
        raise BridgeError("PAYMENT_INVALID", "payment validity window exceeds policy")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if issued_at > current + timedelta(seconds=clock_skew_s):
        raise BridgeError("PAYMENT_NOT_YET_VALID", "payment.issuedAt is in the future")
    if expires_at < current - timedelta(seconds=clock_skew_s):
        raise BridgeError("PAYMENT_EXPIRED", "payment authorization has expired")

    if type(payment_raw.get("verified")) is not bool:
        raise BridgeError("PAYMENT_INVALID", "payment.verified must be a boolean")
    if type(payment_raw.get("settled")) is not bool:
        raise BridgeError("PAYMENT_INVALID", "payment.settled must be a boolean")
    payment = PaymentAuthorization(
        provider=_require_payment_string(payment_raw, "provider"),
        network=_require_payment_string(payment_raw, "network"),
        asset=_require_payment_string(payment_raw, "asset"),
        amount=_require_payment_string(payment_raw, "amount"),
        pay_to=_require_payment_string(payment_raw, "payTo"),
        authorization_id=_require_identifier(
            payment_raw.get("authorizationId"),
            "payment.authorizationId",
            code="PAYMENT_INVALID",
        ),
        verified=payment_raw["verified"],
        status=_require_payment_string(payment_raw, "status"),
        settled=payment_raw["settled"],
    )
    return Action(
        action_id=action_id,
        robot_id=robot_id,
        skill_id=skill_id,
        params=params,
        params_hash=params_hash.lower(),
        idempotency_key=idempotency_key,
        issued_at=issued_at,
        expires_at=expires_at,
        payment=payment,
    )


def validate_payment(payment: PaymentAuthorization, expected: dict[str, Any]) -> None:
    pay_to_env = expected["pay_to_env"]
    expected_pay_to = os.getenv(pay_to_env)
    if not expected_pay_to:
        raise BridgeError(
            "BRIDGE_CONFIGURATION_ERROR",
            f"required payee wallet environment variable {pay_to_env} is not set",
        )
    if not EVM_ADDRESS_RE.fullmatch(expected_pay_to):
        raise BridgeError(
            "BRIDGE_CONFIGURATION_ERROR",
            f"{pay_to_env} must contain an EVM address",
        )
    comparisons = {
        "provider": (payment.provider, expected["provider"], False),
        "network": (payment.network, expected["network"], False),
        "asset": (payment.asset, expected["asset"], True),
        "amount": (payment.amount, expected["amount"], False),
        "payTo": (payment.pay_to, expected_pay_to, True),
    }
    for label, (actual, wanted, casefold) in comparisons.items():
        matches = (
            actual.casefold() == wanted.casefold() if casefold else actual == wanted
        )
        if not matches:
            raise BridgeError("PAYMENT_POLICY_MISMATCH", f"payment {label} mismatch")
    if payment.settled:
        raise BridgeError(
            "PAYMENT_ALREADY_SETTLED",
            "bridge requires authorization before execution, not prior settlement",
        )
    if not payment.verified or payment.status != "authorized":
        raise BridgeError(
            "PAYMENT_NOT_AUTHORIZED", "payment must be relay-verified and authorized"
        )


def parse_dobot_reply(raw: str) -> DobotReply:
    text = raw.strip()
    if "Not Tcp" in text:
        raise BridgeError(
            "CONTROLLER_NOT_TCP_MODE",
            "controller rejected the command because TCP/IP mode is disabled",
        )
    match = re.match(r"\s*(-?\d+)", text)
    if not match:
        raise BridgeError("CONTROLLER_REPLY_INVALID", "unrecognized Dobot reply")
    values: tuple[float, ...] = ()
    result_match = re.search(r"\{([^{}]*)\}", text)
    if result_match and result_match.group(1).strip():
        try:
            values = tuple(
                float(part.strip())
                for part in result_match.group(1).split(",")
                if part.strip()
            )
        except ValueError as exc:
            raise BridgeError(
                "CONTROLLER_REPLY_INVALID", "Dobot reply contains invalid values"
            ) from exc
    return DobotReply(error_id=int(match.group(1)), values=values, raw=text)


def parse_script_name_reply(raw: str) -> str | None:
    text = raw.strip()
    match = re.match(r"^\s*(-?\d+)\s*,\s*\{(.*?)\}\s*(?:,.*)?;?\s*$", text)
    if not match:
        raise BridgeError("CONTROLLER_REPLY_INVALID", "unrecognized GetScrName reply")
    error_id = int(match.group(1))
    if error_id == -1:
        return None
    if error_id != 0:
        raise BridgeError(
            "CONTROLLER_COMMAND_FAILED", f"GetScrName failed with ErrorID={error_id}"
        )
    value = match.group(2).strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    if not PROJECT_NAME_RE.fullmatch(value):
        raise BridgeError(
            "CONTROLLER_REPLY_INVALID", "GetScrName returned an unsafe project name"
        )
    return value


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BridgeError(
            "BRIDGE_CONFIGURATION_ERROR", "config file not found"
        ) from exc
    except json.JSONDecodeError as exc:
        raise BridgeError(
            "BRIDGE_CONFIGURATION_ERROR", f"config is not valid JSON: {exc}"
        ) from exc
    if not isinstance(config, dict):
        raise BridgeError("BRIDGE_CONFIGURATION_ERROR", "config must be an object")
    validate_config(config, execution=False)
    return config


def _positive_bounded(
    mapping: dict[str, Any], key: str, maximum: float, *, minimum: float = 0
) -> float:
    try:
        value = float(mapping[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise BridgeError(
            "BRIDGE_CONFIGURATION_ERROR", f"safety.{key} must be numeric"
        ) from exc
    if not minimum < value <= maximum:
        raise BridgeError(
            "BRIDGE_CONFIGURATION_ERROR",
            f"safety.{key} must be greater than {minimum} and at most {maximum}",
        )
    return value


def validate_config(config: dict[str, Any], *, execution: bool) -> None:
    for key in (
        "robot_id",
        "robot_model",
        "robot_ip_env",
        "controller_firmware",
        "action_topic",
        "result_topic",
        "replay_db",
    ):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise BridgeError(
                "BRIDGE_CONFIGURATION_ERROR", f"config field {key} is required"
            )
    if config["action_topic"] == config["result_topic"]:
        raise BridgeError(
            "BRIDGE_CONFIGURATION_ERROR", "action and result topics must differ"
        )
    dashboard_port = config.get("dashboard_port", 29999)
    if type(dashboard_port) is not int or not 1 <= dashboard_port <= 65535:
        raise BridgeError(
            "BRIDGE_CONFIGURATION_ERROR",
            "dashboard_port must be an integer between 1 and 65535",
        )
    endpoints = config.get("zenoh_connect_endpoints")
    if not isinstance(endpoints, list) or not all(
        isinstance(item, str) and item for item in endpoints
    ):
        raise BridgeError(
            "BRIDGE_CONFIGURATION_ERROR",
            "zenoh_connect_endpoints must be a non-empty string array",
        )
    expected = config.get("expected_payment")
    if not isinstance(expected, dict):
        raise BridgeError(
            "BRIDGE_CONFIGURATION_ERROR", "expected_payment must be an object"
        )
    for key in ("provider", "network", "asset", "amount", "pay_to_env"):
        if not isinstance(expected.get(key), str) or not expected[key]:
            raise BridgeError(
                "BRIDGE_CONFIGURATION_ERROR", f"expected_payment.{key} is required"
            )
    if not EVM_ADDRESS_RE.fullmatch(expected["asset"]):
        raise BridgeError(
            "BRIDGE_CONFIGURATION_ERROR",
            "expected_payment.asset must be an EVM token address",
        )
    if not expected["amount"].isdigit() or int(expected["amount"]) <= 0:
        raise BridgeError(
            "BRIDGE_CONFIGURATION_ERROR",
            "expected_payment.amount must be a positive integer string",
        )
    safety = config.get("safety")
    if not isinstance(safety, dict):
        raise BridgeError("BRIDGE_CONFIGURATION_ERROR", "safety must be an object")
    _positive_bounded(safety, "controller_reply_timeout_s", 5)
    start_timeout = _positive_bounded(safety, "script_start_timeout_s", 10)
    script_timeout = _positive_bounded(safety, "script_timeout_s", 60)
    if script_timeout <= start_timeout:
        raise BridgeError(
            "BRIDGE_CONFIGURATION_ERROR",
            "script_timeout_s must exceed script_start_timeout_s",
        )
    _positive_bounded(safety, "poll_interval_s", 1, minimum=0.01 - 1e-12)
    _positive_bounded(safety, "max_action_ttl_s", 600)
    _positive_bounded(safety, "clock_skew_s", 120)

    skills = config.get("skills")
    if not isinstance(skills, dict) or not skills:
        raise BridgeError("BRIDGE_CONFIGURATION_ERROR", "skills must be non-empty")
    for skill_id, skill in skills.items():
        if not IDENTIFIER_RE.fullmatch(skill_id) or not isinstance(skill, dict):
            raise BridgeError("BRIDGE_CONFIGURATION_ERROR", "invalid skill entry")
        if skill.get("type") != "run_script" or skill.get("allowed_params") != []:
            raise BridgeError(
                "BRIDGE_CONFIGURATION_ERROR",
                f"skill {skill_id} must be a parameter-free run_script mapping",
            )
        if not PROJECT_NAME_RE.fullmatch(str(skill.get("project_name", ""))):
            raise BridgeError(
                "BRIDGE_CONFIGURATION_ERROR",
                f"skill {skill_id} has invalid project_name",
            )
        digest = skill.get("project_artifact_sha256")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise BridgeError(
                "BRIDGE_CONFIGURATION_ERROR",
                f"skill {skill_id} requires a project artifact SHA-256",
            )
        if (
            not isinstance(skill.get("artifact_path_env"), str)
            or not skill["artifact_path_env"]
        ):
            raise BridgeError(
                "BRIDGE_CONFIGURATION_ERROR",
                f"skill {skill_id} requires artifact_path_env",
            )

    if execution:
        if safety.get("approved") is not True:
            raise BridgeError(
                "SAFETY_NOT_APPROVED", "safety.approved must be true for real execution"
            )
        robot_ip = os.getenv(config["robot_ip_env"])
        if not robot_ip:
            raise BridgeError(
                "BRIDGE_CONFIGURATION_ERROR",
                f"{config['robot_ip_env']} must contain the controller address",
            )
        for skill_id, skill in skills.items():
            path_value = os.getenv(skill["artifact_path_env"])
            if not path_value:
                raise BridgeError(
                    "ARTIFACT_NOT_CONFIGURED",
                    f"{skill['artifact_path_env']} is required for {skill_id}",
                )
            artifact = Path(path_value)
            if not artifact.is_file():
                raise BridgeError(
                    "ARTIFACT_NOT_FOUND", "approved artifact file not found"
                )
            actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
            if actual.lower() != skill["project_artifact_sha256"].lower():
                raise BridgeError(
                    "ARTIFACT_HASH_MISMATCH",
                    f"approved artifact digest mismatch for {skill_id}",
                )


def _fingerprint(action: Action) -> str:
    encoded = json.dumps(
        {
            "actionId": action.action_id,
            "robotId": action.robot_id,
            "skillId": action.skill_id,
            "paramsHash": action.params_hash,
            "authorizationId": action.payment.authorization_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ReplayStore:
    """Durable, fail-closed idempotency state backed by SQLite."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._connect()) as database:
            with database:
                database.execute(
                    """
                    CREATE TABLE IF NOT EXISTS actions (
                        idempotency_key TEXT PRIMARY KEY,
                        fingerprint TEXT NOT NULL,
                        action_id TEXT NOT NULL UNIQUE,
                        authorization_id TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        result_json TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        database = sqlite3.connect(self.path, timeout=5)
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("PRAGMA synchronous=FULL")
        return database

    def reserve(self, action: Action) -> tuple[str, dict[str, Any] | None]:
        fingerprint = _fingerprint(action)
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock, closing(self._connect()) as database:
            with database:
                database.execute("BEGIN IMMEDIATE")
                row = database.execute(
                    "SELECT fingerprint, status, result_json FROM actions "
                    "WHERE idempotency_key = ?",
                    (action.idempotency_key,),
                ).fetchone()
                if row is None:
                    action_replay = database.execute(
                        "SELECT 1 FROM actions WHERE action_id = ?",
                        (action.action_id,),
                    ).fetchone()
                    if action_replay:
                        raise BridgeError(
                            "ACTION_ID_REPLAY",
                            "actionId was previously used with another idempotency key",
                        )
                    authorization_replay = database.execute(
                        "SELECT 1 FROM actions WHERE authorization_id = ?",
                        (action.payment.authorization_id,),
                    ).fetchone()
                    if authorization_replay:
                        raise BridgeError(
                            "PAYMENT_AUTHORIZATION_REPLAY",
                            "payment authorization was previously used for another action",
                        )
                    database.execute(
                        "INSERT INTO actions VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                        (
                            action.idempotency_key,
                            fingerprint,
                            action.action_id,
                            action.payment.authorization_id,
                            "in_flight",
                            timestamp,
                            timestamp,
                        ),
                    )
                    return "reserved", None
                previous_fingerprint, status, result_json = row
                if previous_fingerprint != fingerprint:
                    raise BridgeError(
                        "IDEMPOTENCY_CONFLICT",
                        "idempotencyKey was previously bound to a different action",
                    )
                if result_json:
                    return "cached", json.loads(result_json)
                raise BridgeError(
                    "IDEMPOTENCY_IN_FLIGHT",
                    f"idempotent action has unresolved durable state ({status})",
                    retryable=True,
                )

    def finish(self, action: Action, result: dict[str, Any]) -> None:
        encoded = json.dumps(result, separators=(",", ":"), sort_keys=True)
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock, closing(self._connect()) as database:
            with database:
                cursor = database.execute(
                    "UPDATE actions SET status = ?, result_json = ?, updated_at = ? "
                    "WHERE idempotency_key = ? AND fingerprint = ?",
                    (
                        result["status"],
                        encoded,
                        timestamp,
                        action.idempotency_key,
                        _fingerprint(action),
                    ),
                )
                if cursor.rowcount != 1:
                    raise BridgeError(
                        "REPLAY_STORE_ERROR",
                        "failed to persist the terminal action result",
                    )


class DryRunExecutor:
    def execute(self, action: Action, skill: dict[str, Any]) -> dict[str, Any]:
        return {
            "message": "Dry-run validation completed; no controller command was sent",
            "dryRun": True,
            "projectName": skill["project_name"],
            "artifactSha256": skill["project_artifact_sha256"],
        }

    def close(self) -> None:
        return None


class DobotController:
    def __init__(self, config: dict[str, Any], sdk_dir: str | Path) -> None:
        sdk_path = Path(sdk_dir).resolve()
        if not (sdk_path / "dobot_api.py").is_file():
            raise BridgeError("SDK_NOT_FOUND", "dobot_api.py not found in --sdk-dir")
        if str(sdk_path) not in sys.path:
            sys.path.insert(0, str(sdk_path))
        try:
            from dobot_api import DobotApiDashboard
        except Exception as exc:
            raise BridgeError(
                "SDK_IMPORT_FAILED", "failed to import Dobot SDK"
            ) from exc

        reply_timeout = float(config["safety"]["controller_reply_timeout_s"])

        class BoundedDashboard(DobotApiDashboard):
            def __init__(self, ip: str, port: int, timeout: float) -> None:
                self._robopay_timeout = timeout
                self._robopay_lock = threading.Lock()
                self.ip = ip
                self.port = port
                self.text_log = False
                try:
                    self.socket_dobot = socket.create_connection((ip, port), timeout)
                    self.socket_dobot.setsockopt(
                        socket.SOL_SOCKET, socket.SO_RCVBUF, 144000
                    )
                    self.socket_dobot.settimeout(timeout)
                except OSError as exc:
                    raise BridgeError(
                        "CONTROLLER_UNAVAILABLE", "failed to connect to controller"
                    ) from exc

            def sendRecvMsg(self, command: str) -> str:  # noqa: N802
                deadline = time.monotonic() + self._robopay_timeout
                with self._robopay_lock:
                    try:
                        self.socket_dobot.sendall(command.encode("utf-8"))
                        response = bytearray()
                        while b";" not in response:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise TimeoutError("controller reply deadline exceeded")
                            self.socket_dobot.settimeout(remaining)
                            chunk = self.socket_dobot.recv(4096)
                            if not chunk:
                                raise ConnectionError(
                                    "controller closed the connection"
                                )
                            response.extend(chunk)
                            if len(response) > 65536:
                                raise ValueError(
                                    "controller reply exceeded 65536 bytes"
                                )
                    except (OSError, TimeoutError, ValueError) as exc:
                        command_name = command.split("(", 1)[0]
                        raise BridgeError(
                            "CONTROLLER_IO_FAILED",
                            f"Dobot {command_name} failed; command was not retried",
                        ) from exc
                try:
                    return response.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise BridgeError(
                        "CONTROLLER_REPLY_INVALID", "controller returned non-UTF-8 data"
                    ) from exc

        robot_ip = os.environ[config["robot_ip_env"]]
        self.dashboard = BoundedDashboard(
            robot_ip, int(config.get("dashboard_port", 29999)), reply_timeout
        )

    def _reply(self, method_name: str, *args: Any) -> DobotReply:
        method = getattr(self.dashboard, method_name, None)
        if method is None:
            raise BridgeError("SDK_METHOD_MISSING", f"SDK lacks {method_name}()")
        reply = parse_dobot_reply(str(method(*args)))
        if reply.error_id != 0:
            raise BridgeError(
                "CONTROLLER_COMMAND_FAILED",
                f"Dobot {method_name} failed with ErrorID={reply.error_id}",
            )
        return reply

    def robot_mode(self) -> int:
        reply = self._reply("RobotMode")
        if not reply.values:
            raise BridgeError("CONTROLLER_REPLY_INVALID", "RobotMode returned no mode")
        return int(reply.values[0])

    def run_script(self, project_name: str) -> None:
        self._reply("RunScript", json.dumps(project_name, ensure_ascii=False))

    def get_script_name(self) -> str | None:
        return parse_script_name_reply(str(self.dashboard.sendRecvMsg("GetScrName()")))

    def stop(self) -> None:
        self._reply("Stop")

    def close(self) -> None:
        close = getattr(self.dashboard, "close", None)
        if callable(close):
            try:
                close()
            finally:
                if hasattr(self.dashboard, "socket_dobot"):
                    setattr(self.dashboard, "socket_dobot", 0)


class DobotExecutor:
    def __init__(self, config: dict[str, Any], sdk_dir: str | Path) -> None:
        self.config = config
        self.controller = DobotController(config, sdk_dir)
        mode = self.controller.robot_mode()
        active_project = self.controller.get_script_name()
        if mode != 5 or active_project is not None:
            self.controller.close()
            raise BridgeError(
                "ROBOT_NOT_IDLE",
                "bridge startup requires enabled-idle mode 5 and no active project",
            )

    def _wait_for_script(self, project_name: str) -> dict[str, Any]:
        safety = self.config["safety"]
        started_at = time.monotonic()
        start_deadline = started_at + float(safety["script_start_timeout_s"])
        finish_deadline = started_at + float(safety["script_timeout_s"])
        interval = float(safety["poll_interval_s"])
        running_observed = False
        name_confirmed = False
        last_mode = -1
        last_project: str | None = None

        while time.monotonic() < finish_deadline:
            last_mode = self.controller.robot_mode()
            if last_mode == 9:
                raise BridgeError("CONTROLLER_ERROR", "controller entered error mode")
            if last_mode == 10:
                raise BridgeError(
                    "ACTION_PAUSED", "controller project entered pause mode"
                )
            if last_mode == 11:
                raise BridgeError("COLLISION_DETECTED", "controller reported collision")
            last_project = self.controller.get_script_name()
            if last_project is not None and last_project != project_name:
                raise BridgeError(
                    "UNEXPECTED_ACTIVE_PROJECT",
                    "controller reported a different active project",
                )

            running_observed = running_observed or last_mode == 7
            name_confirmed = name_confirmed or last_project == project_name
            if (
                running_observed
                and name_confirmed
                and last_mode == 5
                and last_project is None
            ):
                return {
                    "runningModeObserved": True,
                    "projectNameConfirmed": True,
                    "completionMode": 5,
                    "activeProjectAtCompletion": None,
                }
            if (
                not (running_observed and name_confirmed)
                and time.monotonic() >= start_deadline
            ):
                raise BridgeError(
                    "ACTION_START_TIMEOUT", "project start was not confirmed in time"
                )
            if last_mode not in {5, 7}:
                raise BridgeError(
                    "UNEXPECTED_ROBOT_MODE",
                    f"project entered unexpected mode {last_mode}",
                )
            time.sleep(interval)

        raise BridgeError(
            "ACTION_TIMEOUT",
            f"project exceeded the {safety['script_timeout_s']} second deadline",
        )

    def execute(self, action: Action, skill: dict[str, Any]) -> dict[str, Any]:
        project_name = skill["project_name"]
        if (
            self.controller.robot_mode() != 5
            or self.controller.get_script_name() is not None
        ):
            raise BridgeError("ROBOT_NOT_IDLE", "robot is not enabled and idle")
        started = time.monotonic()
        try:
            self.controller.run_script(project_name)
            evidence = self._wait_for_script(project_name)
        except Exception:
            try:
                self.controller.stop()
            except Exception:
                pass
            raise
        return {
            "message": "Custom two-cycle pick-and-place project completed",
            "projectName": project_name,
            "artifactSha256": skill["project_artifact_sha256"],
            "durationMs": round((time.monotonic() - started) * 1000),
            "controllerEvidence": evidence,
        }

    def close(self) -> None:
        self.controller.close()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _partial_identifiers(raw: str) -> dict[str, str]:
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    if not isinstance(body, dict):
        return {}
    partial: dict[str, str] = {}
    for field in ("actionId", "robotId", "skillId", "idempotencyKey"):
        candidate = body.get(field)
        if isinstance(candidate, str) and IDENTIFIER_RE.fullmatch(candidate):
            partial[field] = candidate
    params_hash = body.get("paramsHash")
    if isinstance(params_hash, str) and SHA256_RE.fullmatch(params_hash):
        partial["paramsHash"] = params_hash.lower()
    return partial


def _base_result(
    action: Action | None, partial: dict[str, str] | None = None
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
        "paramsHash": (
            action.params_hash if action else partial.get("paramsHash", "unidentified")
        ),
        "timestamp": _timestamp(),
    }


def success_result(action: Action, result: dict[str, Any]) -> dict[str, Any]:
    dry_run = result.get("dryRun") is True
    return {
        **_base_result(action),
        "status": "pending" if dry_run else "success",
        "settlementEligible": not dry_run,
        "result": result,
    }


def error_result(
    action: Action | None,
    error: BridgeError,
    *,
    duration_ms: int | None = None,
    partial: dict[str, str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        **_base_result(action, partial),
        "status": "error",
        "settlementEligible": False,
        "error": {
            "code": error.code,
            "message": str(error),
            "retryable": error.retryable,
        },
    }
    if duration_ms is not None:
        result["durationMs"] = duration_ms
    return result


class Bridge:
    def __init__(self, config: dict[str, Any], executor: Executor) -> None:
        self.config = config
        self.executor = executor
        self.replay_store = ReplayStore(config["replay_db"])
        self._execution_lock = threading.Lock()

    def process_raw(self, raw: Any, *, now: datetime | None = None) -> dict[str, Any]:
        try:
            raw_text = _decode_sample(raw)
        except BridgeError as exc:
            return error_result(None, exc)
        partial = _partial_identifiers(raw_text)
        try:
            action = parse_action(
                raw_text,
                self.config["robot_id"],
                now=now,
                max_ttl_s=float(self.config["safety"]["max_action_ttl_s"]),
                clock_skew_s=float(self.config["safety"]["clock_skew_s"]),
            )
        except BridgeError as exc:
            return error_result(None, exc, partial=partial)

        try:
            validate_payment(action.payment, self.config["expected_payment"])
            skill = self.config["skills"].get(action.skill_id)
            if not isinstance(skill, dict):
                raise BridgeError("SKILL_NOT_FOUND", "skillId is not locally approved")
            if action.params:
                raise BridgeError(
                    "PARAMS_NOT_ALLOWED", "this skill accepts no parameters"
                )
        except BridgeError as exc:
            return error_result(action, exc)

        try:
            reservation, cached = self.replay_store.reserve(action)
        except BridgeError as exc:
            return error_result(action, exc)
        except Exception:
            return error_result(
                action,
                BridgeError(
                    "REPLAY_STORE_ERROR",
                    "persistent replay protection is unavailable",
                ),
            )
        if reservation == "cached" and cached is not None:
            replay = error_result(
                action,
                BridgeError(
                    "DUPLICATE",
                    "action already has a terminal result and was not re-executed",
                ),
            )
            replay["delivery"] = {
                "code": "DUPLICATE",
                "cached": True,
                "robotActuated": False,
                "previousResultReference": {
                    "actionId": cached.get("actionId"),
                    "idempotencyKey": cached.get("idempotencyKey"),
                },
            }
            return replay

        if not self._execution_lock.acquire(blocking=False):
            result = error_result(
                action,
                BridgeError(
                    "ROBOT_BUSY", "another robot action is executing", retryable=True
                ),
            )
            try:
                self.replay_store.finish(action, result)
            except Exception:
                return error_result(
                    action,
                    BridgeError(
                        "REPLAY_STORE_ERROR",
                        "failed to persist robot-busy result",
                    ),
                )
            return result

        started = time.monotonic()
        try:
            try:
                execution_result = self.executor.execute(action, skill)
                result = success_result(action, execution_result)
            except BridgeError as exc:
                result = error_result(
                    action, exc, duration_ms=round((time.monotonic() - started) * 1000)
                )
            except Exception:
                result = error_result(
                    action,
                    BridgeError("ACTION_FAILED", "unexpected robot execution failure"),
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
            try:
                self.replay_store.finish(action, result)
            except Exception:
                return error_result(
                    action,
                    BridgeError(
                        "REPLAY_STORE_ERROR",
                        "terminal result persistence failed; operator reconciliation required",
                    ),
                )
            return result
        finally:
            self._execution_lock.release()

    def close(self) -> None:
        self.executor.close()


def _decode_sample(raw: Any) -> str:
    try:
        if isinstance(raw, str):
            return raw
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        payload = getattr(raw, "payload", raw)
        if callable(payload):
            payload = payload()
        if hasattr(payload, "to_bytes"):
            return payload.to_bytes().decode("utf-8")
        if hasattr(payload, "bytes"):
            value = payload.bytes
            if callable(value):
                value = value()
            return bytes(value).decode("utf-8")
        return str(payload)
    except UnicodeDecodeError as exc:
        raise BridgeError("INVALID_JSON", "Zenoh action payload is not UTF-8") from exc


class ZenohRunner:
    def __init__(self, bridge: Bridge, config: dict[str, Any]) -> None:
        try:
            import zenoh
        except ImportError as exc:
            raise BridgeError(
                "ZENOH_NOT_INSTALLED", "install dependencies from requirements.txt"
            ) from exc
        self.zenoh = zenoh
        self.bridge = bridge
        self.config = config
        zenoh_config = zenoh.Config()
        zenoh_config.insert_json5(
            "mode", json.dumps(config.get("zenoh_mode", "client"))
        )
        zenoh_config.insert_json5(
            "connect/endpoints", json.dumps(config["zenoh_connect_endpoints"])
        )
        self.session = zenoh.open(zenoh_config)
        self.publisher = self.session.declare_publisher(config["result_topic"])
        self.subscriber = self.session.declare_subscriber(
            config["action_topic"], self._on_action
        )
        self._workers: set[threading.Thread] = set()
        self._worker_lock = threading.Lock()
        self._closing = False

    def _publish(self, result: dict[str, Any]) -> None:
        encoded = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        self.publisher.put(encoded)
        print(
            "result "
            f"actionId={result.get('actionId')} status={result.get('status')} "
            f"settlementEligible={result.get('settlementEligible')}"
        )

    def _work(self, sample: Any) -> None:
        try:
            try:
                result = self.bridge.process_raw(sample)
            except BridgeError as exc:
                print(f"rejected code={exc.code} message={exc}", file=sys.stderr)
                return
            self._publish(result)
        finally:
            current = threading.current_thread()
            with self._worker_lock:
                self._workers.discard(current)

    def _on_action(self, sample: Any) -> None:
        with self._worker_lock:
            if self._closing:
                return
            worker = threading.Thread(target=self._work, args=(sample,), daemon=False)
            self._workers.add(worker)
            worker.start()

    def run(self) -> int:
        print(
            f"zenoh ready actionTopic={self.config['action_topic']} "
            f"resultTopic={self.config['result_topic']}"
        )
        try:
            while True:
                time.sleep(0.25)
        except KeyboardInterrupt:
            return 0
        finally:
            self.close()

    def close(self) -> None:
        with self._worker_lock:
            self._closing = True
        try:
            self.subscriber.undeclare()
            while True:
                with self._worker_lock:
                    workers = list(self._workers)
                if not workers:
                    break
                for worker in workers:
                    worker.join(timeout=0.25)
            self.publisher.undeclare()
        finally:
            self.session.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Bridge JSON config")
    parser.add_argument("--sdk-dir", help="Official TCP-IP-Python-V4 SDK directory")
    parser.add_argument(
        "--execute", action="store_true", help="Enable real controller calls"
    )
    parser.add_argument(
        "--safety-ack",
        default=os.getenv("DOBOT_CRA_SAFETY_ACK"),
        help="Required explicit acknowledgement for --execute",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Process one envelope without Zenoh (dry-run/testing only)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(args.config)
    if args.execute:
        validate_config(config, execution=True)
        if args.safety_ack != SAFETY_ACK:
            raise BridgeError(
                "SAFETY_ACK_REQUIRED", f"set DOBOT_CRA_SAFETY_ACK={SAFETY_ACK}"
            )
        if not args.sdk_dir:
            raise BridgeError("SDK_NOT_FOUND", "--sdk-dir is required with --execute")
        executor: Executor = DobotExecutor(config, args.sdk_dir)
        print("bridge mode=REAL_EXECUTION")
    else:
        executor = DryRunExecutor()
        print("bridge mode=DRY_RUN; no controller command can be sent")

    bridge = Bridge(config, executor)
    try:
        if args.stdin:
            result = bridge.process_raw(sys.stdin.read())
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result["status"] in {"success", "pending"} else 1
        return ZenohRunner(bridge, config).run()
    finally:
        bridge.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BridgeError as exc:
        print(f"bridge error [{exc.code}]: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
