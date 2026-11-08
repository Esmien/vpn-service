#!/usr/bin/env python3
"""Blue/green for the VPN router: two xray containers, switch by nginx reload.

Canonical config is `/root/vpn-router/config.json` and it always carries the
color-A port block, because the container `vpn-router` mounts it directly. The
color-B variant (`config.b.json`) is generated from it with every inbound port
shifted by OFFSET and is mounted by `vpn-router-b`. nginx always proxies to the
ACTIVE color; `/root/vpn-router/vpn-color` holds which one that is.

Why it is downtime-free: an apply never restarts the container that carries
traffic. It validates the new config, restarts only the idle color, waits until
the idle color answers on every inbound, then rewrites the nginx upstreams and
reloads nginx — a graceful reload keeps established sockets and sends only new
connections to the new color. Established sessions on the drained color keep
working; they are replaced at the next maintenance, on a container that already
stopped accepting traffic.

    vpn-bluegreen.py status
    vpn-bluegreen.py build              # config.json -> config.b.json (+ validate both)
    vpn-bluegreen.py apply              # build, warm the idle color, flip
    vpn-bluegreen.py flip [a|b]         # switch traffic only
    vpn-bluegreen.py probe [a|b]        # direct inbound probe of a color
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

BASE = Path("/root/vpn-router")
CONFIG = BASE / "config.json"
CONFIG_B = BASE / "config.b.json"
COLOR = BASE / "vpn-color"
APPLIED = BASE / ".applied"          # hash of the config last handed to each color
IMAGE = "ghcr.io/xtls/xray-core:latest"
OFFSET = 1000
CONTAINERS = {"a": "vpn-router", "b": "vpn-router-b"}


def other(color: str) -> str:
    return "b" if color == "a" else "a"


def active() -> str:
    return COLOR.read_text().strip() if COLOR.exists() else "a"


def cfg_for(color: str) -> Path:
    return CONFIG if color == "a" else CONFIG_B


def load(color: str) -> dict:
    return json.loads(cfg_for(color).read_text())


def derive_b(cfg: dict) -> dict:
    b = json.loads(json.dumps(cfg))
    for ib in b.get("inbounds", []):
        ib["port"] = int(ib["port"]) + OFFSET
    return b


def path_of(ib: dict) -> str:
    return ib.get("streamSettings", {}).get("wsSettings", {}).get("path", "/")


def validate(path: Path) -> tuple[bool, str]:
    r = subprocess.run(
        ["docker", "run", "--rm", "--user", "0:0", "--entrypoint", "/usr/local/bin/xray",
         "-v", f"{path}:/etc/xray/config.json:ro", IMAGE, "run", "-test", "-c", "/etc/xray/config.json"],
        capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[-800:]


def lint(cfg: dict) -> list[str]:
    """Checks `xray -test` silently accepts: dangling tags, published inbounds, bad ports.

    A routing rule pointing at a non-existent outbound passes `-test` and then
    quietly drops that traffic — the one class of typo that used to survive a
    restart unnoticed.
    """
    errs: list[str] = []
    in_tags = [i.get("tag") for i in cfg.get("inbounds", [])]
    out_tags = [o.get("tag") for o in cfg.get("outbounds", [])]
    if len(set(in_tags)) != len(in_tags):
        errs.append("дублирующиеся tag у инбаундов")
    if len(set(out_tags)) != len(out_tags):
        errs.append("дублирующиеся tag у аутбаундов")
    ports: set[int] = set()
    for ib in cfg.get("inbounds", []):
        try:
            port = int(ib.get("port"))
        except (TypeError, ValueError):
            errs.append(f"{ib.get('tag')}: порт не число")
            continue
        if not 0 < port < 65536:
            errs.append(f"{ib.get('tag')}: порт {port} вне диапазона")
        if port in ports:
            errs.append(f"{ib.get('tag')}: порт {port} занят другим инбаундом")
        ports.add(port)
        if ib.get("listen") not in ("127.0.0.1", "::1"):
            errs.append(f"{ib.get('tag')}: listen={ib.get('listen')} — инбаунд опубликован в интернет")
        if ib.get("protocol") != "dokodemo-door" and not (ib.get("settings", {}).get("clients") or []):
            errs.append(f"{ib.get('tag')}: нет ни одного клиента")
    bal_tags = [b.get("tag") for b in (cfg.get("routing", {}).get("balancers") or [])]
    for n, rule in enumerate(cfg.get("routing", {}).get("rules", []), 1):
        ot = rule.get("outboundTag")
        if ot and ot not in out_tags and ot != (cfg.get("api") or {}).get("tag"):
            errs.append(f"правило #{n}: outboundTag «{ot}» не существует")
        bt = rule.get("balancerTag")
        if bt and bt not in bal_tags:
            errs.append(f"правило #{n}: balancerTag «{bt}» не существует")
        for it in rule.get("inboundTag") or []:
            if it not in in_tags:
                errs.append(f"правило #{n}: inboundTag «{it}» не существует")
    default_out = (cfg.get("routing", {}).get("rules") or [{}])[-1].get("outboundTag")
    if default_out not in out_tags:
        errs.append(f"последнее правило ссылается на несуществующий outbound «{default_out}»")
    return errs


def build(write: bool = True) -> int:
    cfg = load("a")
    problems = lint(cfg)
    if problems:
        print("lint конфига не пройден:")
        for p in problems:
            print(f"  · {p}")
        return 1
    b = derive_b(cfg)
    CONFIG_B.write_text(json.dumps(b, ensure_ascii=False, indent=2) + "\n")
    CONFIG_B.chmod(0o600)
    ok, out = validate(CONFIG)
    if not ok:
        print("config.json НЕ прошёл xray -test:")
        print(out)
        return 1
    ok, out = validate(CONFIG_B)
    if not ok:
        print("config.b.json НЕ прошёл xray -test:")
        print(out)
        return 1
    print(f"build: config.b.json готов, порты +{OFFSET}, оба варианта валидны "
          f"({len(cfg.get('inbounds', []))} инбаундов)")
    return 0


def probe_direct(color: str, timeout: float = 1.5) -> tuple[int, int]:
    """GET every WS inbound directly; a healthy xray answers 400 (not 502/000)."""
    cfg = load(color)
    good = total = 0
    for ib in cfg.get("inbounds", []):
        if ib.get("protocol") == "dokodemo-door":
            continue  # api-инбаунд не говорит по HTTP: пробовать его бессмысленно
        total += 1
        url = f"http://127.0.0.1:{ib['port']}{path_of(ib)}"
        r = subprocess.run(["curl", "-sS", "-m", str(timeout), "-o", "/dev/null",
                            "-w", "%{http_code}", url], capture_output=True, text=True)
        if r.stdout.strip() == "400":
            good += 1
    return good, total


def probe_nginx() -> bool:
    brand = json.loads((BASE / "brand.json").read_text())
    ok = True
    for path in ("/general", "/ws"):
        r = subprocess.run(["curl", "-sS", "-m", "8", "--resolve",
                            f"{brand['host']}:{brand['port']}:127.0.0.1", "-o", "/dev/null",
                            "-w", "%{http_code}", f"https://{brand['host']}:{brand['port']}{path}"],
                           capture_output=True, text=True)
        if r.stdout.strip() != "400":
            ok = False
            print(f"  {path}: {r.stdout.strip() or 'нет ответа'}")
    return ok


def container_running(color: str) -> bool:
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINERS[color]],
                       capture_output=True, text=True)
    return r.stdout.strip() == "true"


def ensure_container(color: str) -> int:
    name = CONTAINERS[color]
    exists = subprocess.run(["docker", "inspect", name], capture_output=True).returncode == 0
    if not exists:
        print(f"контейнера {name} нет — создайте его один раз (см. README blue/green)")
        return 1
    return 0


def set_color(color: str) -> None:
    COLOR.write_text(color + "\n")
    COLOR.chmod(0o644)


def applied_hash(color: str) -> str:
    if not APPLIED.exists():
        return ""
    try:
        return json.loads(APPLIED.read_text()).get(color, "")
    except Exception:  # noqa: BLE001
        return ""


def mark_applied(color: str) -> None:
    data = {}
    if APPLIED.exists():
        try:
            data = json.loads(APPLIED.read_text())
        except Exception:  # noqa: BLE001
            data = {}
    data[color] = hashlib.sha256(cfg_for(color).read_bytes()).hexdigest()
    APPLIED.write_text(json.dumps(data, indent=2) + "\n")


def restart_container(color: str) -> int:
    name = CONTAINERS[color]
    r = subprocess.run(["docker", "restart", name], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"docker restart {name} упал: {(r.stderr or r.stdout).strip()[:200]}")
        return 1
    return 0


def flip(color: str, verify: bool = True) -> int:
    prev = active()
    if color == prev:
        print(f"цвет {color} уже активен")
        return 0
    set_color(color)
    r = subprocess.run(["python3", str(BASE / "apply_brand.py")], capture_output=True, text=True)
    if r.returncode != 0:
        set_color(prev)
        print("apply_brand.py упал, откат цвета")
        print((r.stderr or r.stdout)[-400:])
        return 1
    t = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    if t.returncode != 0:
        set_color(prev)
        subprocess.run(["python3", str(BASE / "apply_brand.py")], capture_output=True)
        print("nginx -t не прошёл, откат цвета")
        print((t.stderr or t.stdout)[-400:])
        return 1
    subprocess.run(["systemctl", "reload", "nginx"], check=False)
    if verify and not probe_nginx():
        set_color(prev)
        subprocess.run(["python3", str(BASE / "apply_brand.py")], capture_output=True)
        subprocess.run(["systemctl", "reload", "nginx"], check=False)
        print(f"цвет {color} не отвечает через nginx — вернул {prev}")
        return 1
    return 0


def cmd_apply() -> int:
    if build() != 0:
        print("остановлено: конфиг не валиден, трафик не тронут")
        return 1
    cur = active()
    tgt = other(cur)
    if ensure_container(tgt) != 0:
        return 1
    h = hashlib.sha256(cfg_for(tgt).read_bytes()).hexdigest()
    if applied_hash(tgt) != h or not container_running(tgt):
        print(f"поднимаю неактивный {tgt} ({CONTAINERS[tgt]}) с новым конфигом…")
        if restart_container(tgt) != 0:
            return 1
    good = total = 0
    for _ in range(12):
        good, total = probe_direct(tgt)
        if good == total:
            break
        subprocess.run(["sleep", "2"], check=False)
    if good != total:
        print(f"{tgt} ответил {good}/{total} инбаундов — не переключаю, трафик на {cur}")
        return 1
    mark_applied(tgt)
    print(f"{tgt}: {good}/{total} инбаундов отвечают, переключаю nginx…")
    if flip(tgt) != 0:
        return 1
    print(f"готово: трафик на {tgt}, {cur} ({CONTAINERS[cur]}) остаётся живым до следующего apply")
    return 0


def cmd_status() -> int:
    cur = active()
    print(f"активный цвет: {cur}  ({CONTAINERS[cur]})")
    for c in ("a", "b"):
        cfg = load(c)
        ports = f"{cfg['inbounds'][0]['port']}…{cfg['inbounds'][-1]['port']}"
        good, total = probe_direct(c)
        run = "running" if container_running(c) else "stopped"
        mark = "← трафик" if c == cur else ""
        print(f"  {c}: {CONTAINERS[c]:<13} {run:<8} порты {ports:<12} инбаунды {good}/{total} {mark}")
    return 0


def cmd_probe(color: str | None = None) -> int:
    c = color or active()
    good, total = probe_direct(c)
    print(f"{c}: {good}/{total} инбаундов отвечают 400")
    return 0 if good == total else 1


def main() -> int:
    args = sys.argv[1:]
    cmd = args[0] if args else "status"
    if cmd == "status":
        return cmd_status()
    if cmd == "build":
        return build()
    if cmd == "apply":
        return cmd_apply()
    if cmd == "flip":
        return flip(args[1] if len(args) > 1 else other(active()))
    if cmd == "probe":
        return cmd_probe(args[1] if len(args) > 1 else None)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
