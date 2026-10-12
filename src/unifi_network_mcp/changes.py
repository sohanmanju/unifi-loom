import asyncio
import copy
import hashlib
import json
import os
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal

from .client import UniFiRejected, redact

_UNSET = object()


@dataclass(frozen=True)
class StateRead:
    """Explicit read route and projection for writes whose item route is not readable."""
    request: dict
    selected_id: str
    fields: tuple[str, ...]
    id_field: str = "_id"

    def extract(self, response):
        records = response.get("data", response) if isinstance(response, dict) else response
        if not isinstance(records, list):
            records = [records]
        matches = [r for r in records if isinstance(r, dict) and r.get(self.id_field) == self.selected_id]
        if len(matches) != 1:
            raise ValueError("State read did not return exactly one matching resource")
        return {key: copy.deepcopy(matches[0][key]) for key in self.fields if key in matches[0]}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class MutationOutcome:
    status: Literal["submitting", "accepted", "rejected", "unknown"]
    result: object = None
    read_back: object = None
    error: str | None = None
    audit_error: str | None = None
    verification_error: str | None = None
    recorded_at: float = field(default_factory=time.monotonic)

    def public(self):
        return {key: value for key, value in asdict(self).items() if key != "recorded_at"}


class Changes:
    """Single-use plans with separately inspectable submission outcomes."""

    def __init__(self, client, catalog):
        self.client, self.catalog = client, catalog
        self.plans = {}
        self.outcomes: OrderedDict[str, MutationOutcome] = OrderedDict()
        self.lock = asyncio.Lock()

    def audit(self, plan_id, request, outcome):
        directory = self.client.config.state_dir
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        record = {"at": datetime.now(timezone.utc).isoformat(), "plan_id": plan_id,
                  "adapter": request["adapter"], "method": request["method"],
                  "path": request["path"], "request_hash": digest(request), "outcome": outcome}
        fd = os.open(directory / "audit.jsonl", os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as stream:
            stream.write(json.dumps(record) + "\n")

    def record(self, token, outcome):
        self.outcomes[token] = outcome
        while len(self.outcomes) > 256:
            self.outcomes.popitem(last=False)

    def audit_outcome(self, token, request, outcome):
        """Secondary persistence must never alter the submission result."""
        try:
            self.audit(token, request, outcome.status)
        except Exception:
            outcome.audit_error = "Outcome retained in memory; audit append failed."

    async def snapshot(self, request, state_read=None):
        if state_read is not None:
            return state_read.extract(await self.client.request(**state_read.request))
        if request["method"] not in ("PUT", "PATCH", "DELETE"):
            return None
        if request["adapter"] == "official":
            if not any(op["method"] == "GET" and self.path_matches(op["path"], request["path"])
                       for op in self.catalog.operations.values()):
                return None
        elif "/rest/" not in request["path"]:
            return None
        return await self.client.request(request["adapter"], "GET", request["path"])

    @staticmethod
    def path_matches(template, path):
        return bool(re.fullmatch(re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(template)), path))

    async def prepare(self, operation_id, path_params, query, body, reason, *, baseline=_UNSET, state_read=None):
        if not reason.strip():
            raise ValueError("A reason is required")
        request = self.catalog.prepare_request(operation_id, path_params, query, body)
        if request["method"] == "GET":
            raise ValueError("Use read_operation for GET operations")
        # Trusted services can bind a read/modify/write plan to the exact state
        # used to construct it, rather than silently accepting a newer baseline.
        before = await self.snapshot(request, state_read) if baseline is _UNSET else (
            state_read.extract(baseline) if state_read is not None else copy.deepcopy(baseline))
        token = secrets.token_urlsafe(24)
        async with self.lock:
            self.plans = {k: v for k, v in self.plans.items() if v["expires"] > time.monotonic()}
            if len(self.plans) >= 100:
                raise ValueError("Too many pending plans")
            self.audit(token, request, "prepared")
            self.plans[token] = {"request": copy.deepcopy(request), "before": before,
                                 "reason": reason, "expires": time.monotonic() + 600,
                                 "operation_id": operation_id, "state_read": state_read}
        return {"plan_id": token, "operation_id": operation_id, "expires_in_seconds": 600,
                "request": redact(request), "before": redact(before), "reason": reason,
                "writes_enabled": self.client.config.allow_writes,
                "rollback": "Previous state held in memory when available; no automatic rollback or console backup.",
                "approval": "apply_change requires the MCP host's human elicitation; unsupported hosts fail closed."}

    def inspect(self, token):
        plan = self.plans.get(token)
        if not plan or plan["expires"] <= time.monotonic():
            raise ValueError("Unknown, expired, or consumed plan; use change_status for a submitted plan")
        preview = {k: v for k, v in plan.items() if k not in ("expires", "state_read")}
        if plan["state_read"] is not None:
            preview["state_read"] = asdict(plan["state_read"])
        return redact(copy.deepcopy(preview))

    def status(self, token):
        if token in self.outcomes:
            outcome = self.outcomes[token]
            if outcome.recorded_at + 3600 <= time.monotonic():
                raise ValueError("Outcome retention expired")
            return outcome.public()
        if token in self.plans:
            return {"status": "prepared" if self.plans[token]["expires"] > time.monotonic() else "expired"}
        raise ValueError("Unknown plan")

    async def apply(self, token):
        if not self.client.config.allow_writes:
            raise ValueError("Writes disabled; set UNIFI_ALLOW_WRITES=true locally")
        async with self.lock:
            self.inspect(token)
            plan = self.plans.pop(token)  # Consume before any attempt; ambiguous writes cannot replay.
            request = plan["request"]
            try:
                if plan["before"] is not None:
                    current = await self.snapshot(request, plan["state_read"])
                    if digest(current) != digest(plan["before"]):
                        raise ValueError("Resource changed since preview; prepare a new plan")
                self.audit(token, request, "submitting")  # Required before submission.
            except BaseException:
                outcome = MutationOutcome("rejected", error="Pre-submission check or audit failed; no mutation submitted.")
                self.record(token, outcome)
                self.audit_outcome(token, request, outcome)
                raise

            self.record(token, MutationOutcome("submitting"))
            try:
                result = await self.client.request(**request)
            except BaseException as error:
                outcome = MutationOutcome("rejected" if isinstance(error, UniFiRejected) else "unknown",
                                          error="Submission rejected." if isinstance(error, UniFiRejected)
                                          else "Submission outcome unknown; inspect console state before retrying.")
                self.record(token, outcome)
                self.audit_outcome(token, request, outcome)
                raise  # Preserve the original transport error/cancellation even if audit fails.

            outcome = MutationOutcome("accepted", result=redact(result))
            self.record(token, outcome)  # Acceptance is established before secondary work.
            self.audit_outcome(token, request, outcome)
            if request["method"] in ("PUT", "PATCH") and plan["before"] is not None:
                try:
                    outcome.read_back = redact(await self.snapshot(request, plan["state_read"]))
                except asyncio.CancelledError:
                    outcome.verification_error = "Write accepted; read-back cancelled."
                    raise
                except Exception:
                    outcome.verification_error = "Write accepted; read-back failed. Inspect console state."
            return {**outcome.public(),
                    "note": "API acceptance/read-back does not prove downstream provisioning or connectivity."}
