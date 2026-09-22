"""Small auditable state machine for actions that require a person."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import json
from pathlib import Path
import secrets
import time


class GateKind(str, Enum):
    AUTOMATABLE = "AUTOMATABLE"
    AUTHORIZED = "AUTHORIZED"
    HUMAN_GATE = "HUMAN_GATE"


class GateState(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class HumanGate:
    gate_id: str
    operation: str
    reason: str
    approval: str
    requested_permissions: list[str]
    user_action: str
    completion_check: str
    next_step: str
    kind: str = GateKind.HUMAN_GATE.value
    state: str = GateState.PENDING.value
    created_at: int = 0
    completed_at: int | None = None
    evidence: dict | None = None

    @classmethod
    def create(cls, operation, reason, approval, requested_permissions, user_action, completion_check, next_step):
        return cls(secrets.token_urlsafe(18), operation, reason, approval, requested_permissions,
                   user_action, completion_check, next_step, created_at=int(time.time()))

    def complete(self, evidence: dict):
        if self.state != GateState.PENDING.value:
            raise ValueError("Only a pending human gate can be completed.")
        self.state = GateState.COMPLETED.value
        self.completed_at = int(time.time())
        self.evidence = evidence

    def fail(self, reason: str):
        if self.state != GateState.PENDING.value:
            raise ValueError("Only a pending human gate can fail.")
        self.state = GateState.FAILED.value
        self.completed_at = int(time.time())
        self.evidence = {"reason": reason}

    def display(self) -> str:
        return (f"HUMAN GATE [{self.gate_id}]\nOperation: {self.operation}\nWhy: {self.reason}\n"
                f"Approval: {self.approval}\nRequested access: {', '.join(self.requested_permissions)}\n"
                f"Action: {self.user_action}\nCompletion: {self.completion_check}\nNext: {self.next_step}")


class GateStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)

    def save(self, gate: HumanGate):
        path = self.root / f"{gate.gate_id}.json"
        path.write_text(json.dumps(asdict(gate), indent=2) + "\n")
        path.chmod(0o600)
        return path

    def load(self, gate_id: str) -> HumanGate:
        data = json.loads((self.root / f"{gate_id}.json").read_text())
        return HumanGate(**data)

