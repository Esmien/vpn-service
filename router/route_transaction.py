"""Testable file transaction for VPN route policy; NOT wired to live VPN.

The caller supplies validation and apply functions. Uses a single advisory
lock and a pending marker to fail closed after an interrupted transaction.
External writers must honor the same lock and policy; this module cannot stop
old watchdogs. Do not use on production until recovery and writers are wired.
"""
from __future__ import annotations
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Callable
from route_policy import render_policy


class TransactionError(RuntimeError):
    pass


def write_atomic(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=".route-txn-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_config_in_place(path: Path, data: bytes) -> None:
    """Keep bind-mounted inode stable; pending record is required for crash recovery."""
    with path.open("r+b", buffering=0) as f:
        f.seek(0)
        f.write(data)
        f.truncate()
        os.fsync(f.fileno())


def recover(directory: Path, apply: Callable[[dict], bool], lock_path: Path | None = None) -> bool:
    """Restore the prior version after interruption; retain marker on failure."""
    pending = directory / "pending.json"
    with (lock_path or directory / "policy.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not pending.exists():
            return False
        record = json.loads(pending.read_text())
        config = record['old_config'].encode()
        old_cfg = json.loads(config)
        write_config_in_place(directory / 'config.json', config)
        if not apply(old_cfg):
            raise TransactionError('восстановление не подтверждено')
        policy = directory / 'policy.json'
        if record['old_policy'] is None:
            policy.unlink(missing_ok=True)
        else:
            write_atomic(policy, record['old_policy'].encode())
        pending.unlink()
        return True


def set_policy(directory: Path, policy: dict, validate: Callable[[dict], bool], apply: Callable[[dict], bool], lock_path: Path | None = None, prepare: Callable[[dict], dict] = lambda cfg: cfg) -> None:
    config = directory / "config.json"
    policy_file = directory / "policy.json"
    pending = directory / "pending.json"
    with (lock_path or directory / "policy.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if pending.exists():
            raise TransactionError("незавершённая операция: требуется восстановление")
        old_config = config.read_bytes()
        old_policy = policy_file.read_bytes() if policy_file.exists() else None
        candidate = render_policy(prepare(json.loads(old_config)), policy)
        if not validate(candidate):
            raise TransactionError("новая конфигурация не прошла проверку")
        record = {"old_config": old_config.decode(), "old_policy": old_policy.decode() if old_policy else None}
        write_atomic(pending, json.dumps(record).encode())
        try:
            write_config_in_place(config, (json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode())
            if not apply(candidate):
                raise TransactionError("не удалось применить")
            write_atomic(policy_file, (json.dumps(policy, ensure_ascii=False, indent=2) + "\n").encode())
        except Exception as exc:
            write_config_in_place(config, old_config)
            # Reapply old config if new apply may have switched traffic.
            try:
                rolled_back = apply(json.loads(old_config))
            except Exception:
                rolled_back = False
            if not rolled_back:
                raise TransactionError("откат не подтверждён; сохранён файл восстановления") from exc
            if old_policy is not None:
                write_atomic(policy_file, old_policy)
            elif policy_file.exists():
                policy_file.unlink()
            pending.unlink()
            raise TransactionError("применение не удалось; прежний конфиг восстановлен") from exc
        pending.unlink()
