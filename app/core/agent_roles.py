"""Versioned agent roles, migration, and deny-first capability checks."""
from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

SCHEMA_VERSION = 2
DEFAULT_ROLE_ID = "assistant"
DEFAULT_CAPABILITIES = ("read", "write", "edit", "search", "delegate")
DELEGATED_CAPABILITIES = frozenset(DEFAULT_CAPABILITIES)


def default_role():
    return {"role_id": DEFAULT_ROLE_ID, "role_version": 1, "name": "Ассистент",
            "prompt_revision": 1, "policy_version": 1,
            "capabilities": list(DEFAULT_CAPABILITIES), "delegated_capabilities": list(DELEGATED_CAPABILITIES)}


def validate_role(role):
    if not isinstance(role, dict) or not isinstance(role.get("role_id"), str) or not role["role_id"]:
        raise ValueError("Некорректная роль")
    if not isinstance(role.get("role_version"), int) or role["role_version"] < 1:
        raise ValueError("Некорректная версия роли")
    for key in ("prompt_revision", "policy_version"):
        if not isinstance(role.get(key), int) or role[key] < 1:
            raise ValueError("Некорректная версия политики")
    caps = role.get("capabilities", [])
    delegated = role.get("delegated_capabilities", [])
    if not isinstance(caps, list) or not all(isinstance(x, str) and x for x in caps):
        raise ValueError("Некорректные capabilities")
    if not isinstance(delegated, list) or not all(isinstance(x, str) and x for x in delegated):
        raise ValueError("Некорректные delegated capabilities")
    return role


def child_capabilities(parent, delegated, requested=None):
    """Deny first: no identity or title can expand this intersection."""
    parent_set = set(parent.get("capabilities", ())) if isinstance(parent, dict) else set()
    delegated_set = set(delegated or ())
    requested_set = set(requested) if requested is not None else delegated_set
    return sorted(parent_set & delegated_set & requested_set)


def can_delegate(parent, target, requested=None):
    if not isinstance(parent, dict) or not isinstance(target, dict):
        return False
    if target.get("parent_chat_id") != parent.get("chat_id"):
        return False
    return bool(child_capabilities(parent.get("role", {}), parent.get("delegated_capabilities", ()), requested))


def _v2(data):
    role = copy.deepcopy(data.get("role") or default_role())
    validate_role(role)
    chats = data.get("chats", [])
    if not isinstance(chats, list):
        raise ValueError("Invalid chat list")
    for chat in chats:
        if (not isinstance(chat, dict) or
                not all(isinstance(chat.get(field), str) and chat[field]
                        for field in ("id", "title", "folder", "session")) or
                not isinstance(chat.get("messages", []), list)):
            raise ValueError("Invalid chat")
        chat.setdefault("role_id", role["role_id"])
        chat.setdefault("role_version", role["role_version"])
        chat.setdefault("prompt_revision", role["prompt_revision"])
        chat.setdefault("policy_version", role["policy_version"])
        chat.setdefault("role_snapshot", copy.deepcopy(role))
    data["schema_version"] = SCHEMA_VERSION
    data["roles"] = data.get("roles") or {role["role_id"]: role}
    data["role"] = role
    return data


def migrate_file(path: Path):
    """Migrate v1/legacy state with backup and atomic replace; source stays intact on failure."""
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    version = raw.get("schema_version", 1)
    if version == SCHEMA_VERSION:
        return raw, None
    if version != 1:
        raise ValueError("Неподдерживаемая версия схемы ролей")
    backup = path.with_name(path.name + f".v1-backup-{time.strftime('%Y%m%d%H%M%S')}")
    shutil.copy2(path, backup)
    try:
        migrated = _v2(raw)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(migrated, out, ensure_ascii=False, indent=2)
                out.flush(); os.fsync(out.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        return migrated, backup
    except Exception:
        shutil.copy2(backup, path)
        raise
