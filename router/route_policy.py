"""Route policy: build a candidate from an admin-owned choice, without I/O.

This module does not start Xray or write a live configuration. Its caller must
perform atomic persistence, validation, blue/green apply, and rollback.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

GROUPS = {"gemini": "bal-google", "ru": "bal-ru"}


class PolicyError(ValueError):
    pass


def validate_policy(policy: dict[str, Any], cfg: dict[str, Any]) -> None:
    if not isinstance(policy, dict) or set(policy) != set(GROUPS):
        raise PolicyError("нужны оба сценария: gemini и ru")
    outbounds = {o.get("tag"): o for o in cfg.get("outbounds", [])}
    for scenario, selected in policy.items():
        if not isinstance(selected, list) or not selected:
            raise PolicyError(f"{scenario}: выберите хотя бы один выход")
        if not all(isinstance(tag, str) and tag for tag in selected):
            raise PolicyError(f"{scenario}: некорректный идентификатор")
        if len(selected) != len(set(selected)):
            raise PolicyError(f"{scenario}: повторяющийся выход")
        for tag in selected:
            item = outbounds.get(tag)
            if not item or item.get("protocol") not in {"freedom", "trojan", "vless", "socks"}:
                raise PolicyError(f"{scenario}: неизвестный или неподходящий выход: {tag}")
            if item.get('protocol') == 'socks':
                from hy2_bridge import outbound
                if not tag.startswith('wl-') or item != outbound(tag):
                    raise PolicyError(f"{scenario}: некорректный локальный выход")


def render_policy(cfg: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """Keep all unrelated config intact; never use an unselected fallback.

    Multiple selected nodes use Xray's leastPing, not ordered priority. For
    a single node, a direct rule avoids accidentally selecting a prefix match.
    For several nodes, exact selector semantics must be verified on the Xray
    version in use before deploying.
    """
    validate_policy(policy, cfg)
    result = deepcopy(cfg)
    routing = result.get("routing", {})
    rules = routing.get("rules", [])
    matches = {
        "gemini": [r for r in rules if not r.get("inboundTag") and "domain:google.com" in r.get("domain", [])],
        "ru": [r for r in rules if not r.get("inboundTag") and "domain:ru" in r.get("domain", [])],
    }
    for scenario, matching in matches.items():
        if len(matching) != 1:
            raise PolicyError(f"{scenario}: ожидается ровно одно глобальное правило")
    balancers = routing.get("balancers", [])
    if not isinstance(balancers, list):
        raise PolicyError("неверный список балансировщиков")
    for scenario, group in GROUPS.items():
        rule = matches[scenario][0]
        selected = policy[scenario]
        rule.pop("outboundTag", None)
        rule.pop("balancerTag", None)
        if len(selected) == 1:
            rule["outboundTag"] = selected[0]
        else:
            # Xray selectors are prefixes. Refuse ambiguous tags; otherwise a
            # choice 'p1' could silently include an unselected 'p15'.
            tags = [o.get("tag", "") for o in result.get("outbounds", [])]
            for tag in selected:
                if any(other != tag and other.startswith(tag) for other in tags):
                    raise PolicyError(f"неоднозначный префикс выхода: {tag}")
            found = [b for b in balancers if b.get("tag") == group]
            if len(found) != 1:
                raise PolicyError(f"не найден балансировщик {group}")
            found[0]["selector"] = list(selected)
            found[0]["fallbackTag"] = selected[0]
            found[0]["strategy"] = {"type": "leastPing"}
            observatory = result.get("observatory")
            if not isinstance(observatory, dict):
                raise PolicyError("для нескольких выходов нужна проверка Xray observatory")
            observatory["subjectSelector"] = sorted(set(observatory.get("subjectSelector", [])) | set(selected))
            rule["balancerTag"] = group
    return result
