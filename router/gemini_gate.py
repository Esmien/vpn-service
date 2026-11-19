#!/usr/bin/env python3
"""Сторож NL-гейта: держит Google/Gemini на нашем втором адресе, падает на релей.

Раз в ~2.5 часа (таймер с джиттером) смотрит, какую страну Google отдаёт адресу
85.92.158.226:
  * страница живая и cc не из списка заблокированных  -> гейт здоров;
  * заглушка / ошибка / пустая страница               -> глобальное Google-правило
    переводится на bal-google (релей), клиенты продолжают работать.
Возврат на nl — только после RETURN_OK подряд здоровых проверок (без дребезга).

    gemini_gate.py             # обычный прогон (таймер)
    gemini_gate.py --dry-run   # показать решение, ничего не менять
    gemini_gate.py --pretend-cc=ru   # проверить логику переключения вручную
"""
import fcntl
import json
import random
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("/root/vpn-router")
CONFIG = BASE / "config.json"
STATE = BASE / "gemini_gate.json"
LOG = Path("/var/log/vpn-gemini-gate.log")
ENV_FILE = Path("/home/ai_service/notifications/.env")

NL_IP = "85.92.158.226"
NL_TAG = "nl"
FALLBACK_TAG = "bal-google"
RETURN_OK = 2                      # столько здоровых проверок подряд, чтобы вернуться
BLOCKED_CC = {"ru", "by", "ir", "kp", "sy", "cu", "cn", "mm", "sd", "tm", "ve", "af"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
CC_RE = re.compile(r'this\.gbar_=\{CONFIG:\[\[\[0,"www\.gstatic\.com","[^"]*","([a-z.]{2,7})"')
GOOGLE_MARK = "domain:google.com"


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {msg}"
    print(line)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def notify(text: str) -> None:
    """Сообщение владельцу в TG. Молча выходим, если .env недоступен."""
    try:
        token = chat = None
        for line in ENV_FILE.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "BOT_TOKEN":
                token = v.strip().strip('"\'')
            elif k.strip() == "BOT_OWNER":
                chat = v.strip().strip('"\'')
        if not (token and chat):
            return
        payload = json.dumps({"chat_id": chat, "text": text, "parse_mode": "HTML",
                              "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                     data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).close()
    except Exception as exc:  # noqa: BLE001 — уведомление не должно ломать сторож
        log(f"notify failed: {exc}")


def probe_gemini(pretend: str | None = None) -> tuple[str | None, int, int]:
    """(cc, http_code, bytes) для Gemini с NL-адреса. pretend подменяет cc (тест логики)."""
    if pretend:
        return pretend, 200, 900000
    cmd = ["curl", "-sS", "--interface", NL_IP, "--max-time", "30",
           "-H", "Accept-Language: en", "-A", UA,
           "-o", "/tmp/gemini-gate.html", "-w", "%{http_code}",
           "https://gemini.google.com/app"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        code = int(r.stdout.strip() or 0)
        body = Path("/tmp/gemini-gate.html").read_bytes() if Path("/tmp/gemini-gate.html").exists() else b""
    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        log(f"probe error: {exc}")
        return None, 0, 0
    m = CC_RE.search(body.decode(errors="ignore"))
    return (m.group(1) if m else None), code, len(body)


def google_rule_pos(cfg: dict) -> int:
    for i, r in enumerate(cfg["routing"]["rules"]):
        if not r.get("inboundTag") and GOOGLE_MARK in (r.get("domain") or []):
            return i
    raise SystemExit("не нашёл глобальное Google-правило в config.json")


def rule_target(rule: dict) -> str:
    return rule.get("outboundTag") or rule.get("balancerTag")


def set_target(rule: dict, tag: str) -> None:
    rule.pop("outboundTag", None)
    rule.pop("balancerTag", None)
    if tag == FALLBACK_TAG:
        rule["balancerTag"] = tag
    else:
        rule["outboundTag"] = tag


def main() -> int:
    dry = "--dry-run" in sys.argv
    pretend = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--pretend-cc=")), None)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    gate = state.get("gate", NL_TAG)
    streak = int(state.get("ok_streak", 0))

    cc, code, size = probe_gemini(pretend)
    healthy = code == 200 and size > 50000 and cc is not None and cc not in BLOCKED_CC
    desired = gate
    if healthy:
        streak += 1
        if gate != NL_TAG and streak >= RETURN_OK:
            desired = NL_TAG
    else:
        streak = 0
        if gate == NL_TAG:
            desired = FALLBACK_TAG
    log(f"probe: cc={cc} http={code} bytes={size} healthy={healthy} gate={gate} streak={streak}")

    cfg = json.loads(CONFIG.read_text())
    pos = google_rule_pos(cfg)
    current = rule_target(cfg["routing"]["rules"][pos])
    log(f"правило[{pos}] сейчас -> {current}; хотим -> {desired}")

    state.update({"gate": desired, "ok_streak": streak, "last_cc": cc,
                  "last_http": code, "updated": datetime.now(timezone.utc).isoformat()})
    if dry:
        STATE.write_text(json.dumps(state, indent=2))
        return 0

    if desired != current:
        lock = open("/var/lock/vpn-config.lock", "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            fresh = json.loads(CONFIG.read_text())
            rule = fresh["routing"]["rules"][google_rule_pos(fresh)]
            set_target(rule, desired)
            CONFIG.write_text(json.dumps(fresh, indent=2, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        r = subprocess.run(["vpn-cfg", "apply"], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            log(f"apply УПАЛ: {(r.stderr or r.stdout)[-300:]}")
            notify(f"🚨 Gemini-гейт: не смог применить переключение на {desired}")
            return 1
        log(f"apply ok: Google -> {desired}")
        if pretend:
            return 0
        if desired == FALLBACK_TAG:
            notify(f"⚠️ Gemini-гейт: NL-адрес отдаёт заглушку (cc={cc}). "
                   f"Google переведён на релей, проверка каждые 2–3 ч.")
        else:
            notify("✅ Gemini-гейт: NL-адрес снова отдаёт рабочую страницу "
                   f"(cc={cc}). Google вернулся на новый IP.")
    STATE.write_text(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
