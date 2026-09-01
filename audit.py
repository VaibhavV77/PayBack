"""
Audit trail. Every node in the graph calls log() on entry/exit. Kept
deliberately dumb (append-only, in-memory + JSONL file) so it's easy to
inspect after a batch run and easy to point a judge at.
"""

import json
import uuid
from pathlib import Path
from schemas import AuditEntry, CaseStatus

AUDIT_FILE = Path(__file__).parent / "audit_log.jsonl"

_log: list[AuditEntry] = []


def reset(path: Path = AUDIT_FILE):
    global _log
    _log = []
    if path.exists():
        path.unlink()


def log(transaction_id: str, node: str, status: CaseStatus, detail: str,
        path: Path = AUDIT_FILE) -> str:
    entry = AuditEntry(
        audit_id=str(uuid.uuid4())[:8],
        transaction_id=transaction_id,
        node=node,
        status=status,
        detail=detail,
    )
    _log.append(entry)
    with open(path, "a") as f:
        f.write(entry.model_dump_json() + "\n")
    return entry.audit_id


def all_entries() -> list[AuditEntry]:
    return list(_log)


def entries_for(transaction_id: str) -> list[AuditEntry]:
    return [e for e in _log if e.transaction_id == transaction_id]
