#!/usr/bin/env python3
"""Вставить серверное правило: RU-домены и .ru/.su -> московский узел (p15).

Идемпотентно: если правило уже стоит, перезаписывает его. Правило ставится
после google-правила и ДО всех inboundTag-правил, иначе оно мертво:
Xray идёт сверху вниз, inboundTag-правило забирает запрос раньше.
"""
import json
import shutil
import sys
import time
from pathlib import Path

CFG = Path("/root/vpn-router/config.json")
BAK = Path("/root/vpn-router/backups")

RU_OUT = "p15"  # 🇷🇺 Москва - YouTube/ГосУслуги/Банки
RU_DOMAINS = [
    "domain:ru", "domain:su", "domain:рф",
    "domain:vk.com", "domain:vk.me", "domain:vk-cdn.net", "domain:userapi.com",
    "domain:mycdn.me", "domain:max.ru", "domain:ozon.com", "domain:wbstatic.net",
    "domain:sberbank.com", "domain:tinkoff.ru",
]


def main() -> int:
    cfg = json.loads(CFG.read_text())
    tags = {o.get("tag") for o in cfg["outbounds"]}
    if RU_OUT not in tags:
        print(f"FATAL: outbound {RU_OUT} отсутствует", file=sys.stderr)
        return 1

    rules = cfg["routing"]["rules"]
    rules = [r for r in rules if r.get("outboundTag") != RU_OUT or "domain" not in r or r.get("inboundTag")]
    rule = {"type": "field", "domain": RU_DOMAINS, "outboundTag": RU_OUT}

    # после google-правила (индекс 1), перед block-bittorrent и inboundTag-правилами
    pos = 1
    for i, r in enumerate(rules):
        if r.get("outboundTag") == "fr" and "domain" in r:
            pos = i + 1
            break
    rules.insert(pos, rule)
    cfg["routing"]["rules"] = rules

    BAK.mkdir(exist_ok=True)
    shutil.copy2(CFG, BAK / f"config-{time.strftime('%Y%m%d-%H%M%S')}.json")
    CFG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    print(f"правило вставлено на позицию {pos}; всего правил {len(rules)}")
    for i, r in enumerate(rules[:5]):
        print(i, r.get("outboundTag"), (r.get("domain") or r.get("ip") or r.get("inboundTag") or "")[:3])
    return 0


if __name__ == "__main__":
    sys.exit(main())
