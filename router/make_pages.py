#!/usr/bin/env python3
"""Landing pages: one per subscription token, buttons that deep-link into Happ / INCY.

A person opens https://<host>:<port>/u/<token> on a phone, taps a button and the
client imports the profile by itself — no copy-paste of base64 blobs.

    happ://add/<sub-url>     — Happ
    incy://add/<sub-url>     — INCY

Both formats are the clients' documented deeplinks; the sub URL carries no
reserved characters, so it goes in raw (no percent-encoding).

The page lives at /var/www/esmien-pages/<token>.html and shows the WHOLE pair
(main + LTE) whichever of the two tokens the person opened.

Usage: python3 make_pages.py
"""
import json
from pathlib import Path

BASE = Path("/root/vpn-router")
PAGES = Path("/var/www/esmien-pages")
BRAND = json.loads((BASE / "brand.json").read_text())
USERS = json.loads((BASE / "users.json").read_text()) if (BASE / "users.json").exists() else {}

TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0a0d12">
<title>Esmien VPN — {name}</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path d='M12 2l8 3.4v7c0 7.6-8 9.6-8 9.6S4 20 4 12.4v-7L12 2z' fill='%237c5cff'/></svg>">
<style>
  *,*::before,*::after{{box-sizing:border-box}}
  :root{{
    --bg:#0a0d12; --card:rgba(255,255,255,.055); --line:rgba(255,255,255,.09);
    --text:#eef2f8; --muted:#8d98a8; --accent:#7c5cff; --accent2:#22d3ee;
  }}
  html,body{{margin:0;padding:0;overflow-x:hidden}}
  body{{
    min-height:100vh; background:var(--bg); color:var(--text);
    font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,"Helvetica Neue",Arial,sans-serif;
    -webkit-font-smoothing:antialiased; padding:28px 18px calc(34px + env(safe-area-inset-bottom));
    display:flex; justify-content:center;
  }}
  body::before{{
    content:""; position:fixed; inset:-20% -20% auto -20%; height:70vh; pointer-events:none;
    background:
      radial-gradient(45% 55% at 20% 10%, rgba(124,92,255,.34), transparent 70%),
      radial-gradient(40% 50% at 85% 0%, rgba(34,211,238,.20), transparent 70%);
    filter:blur(12px);
  }}
  main{{width:100%; max-width:460px; position:relative}}
  .brand{{display:flex; align-items:center; gap:12px; margin-bottom:26px}}
  .logo{{
    width:46px; height:46px; border-radius:14px; display:grid; place-items:center; font-size:23px;
    background:linear-gradient(135deg,rgba(124,92,255,.95),rgba(34,211,238,.85));
    box-shadow:0 10px 30px rgba(124,92,255,.35);
  }}
  .brand h1{{font-size:17px; margin:0; letter-spacing:.2px}}
  .brand p{{margin:2px 0 0; font-size:13px; color:var(--muted)}}
  .hi{{font-size:26px; font-weight:700; letter-spacing:-.4px; margin:0 0 6px}}
  .hi span{{background:linear-gradient(90deg,#d3c6ff,#a8ecff); -webkit-background-clip:text; background-clip:text; color:transparent}}
  .sub{{margin:0 0 22px; color:var(--muted); font-size:14px}}
  .sub p{{margin:0 0 11px}}
  .sub ul{{margin:10px 0 12px; padding-left:20px}}
  .sub li{{margin:0 0 7px}}
  .sub b{{color:var(--text); font-weight:600}}
  .sub .hint{{opacity:.85; font-size:13px}}
  .card{{
    background:var(--card); border:1px solid var(--line); border-radius:20px; padding:16px;
    margin-bottom:14px; backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  }}
  .card h2{{display:flex; align-items:center; gap:9px; font-size:16px; margin:0 0 3px}}
  .ico{{flex:0 0 auto; width:20px; height:20px; display:block; opacity:.95}}
  .card .desc{{color:var(--muted); font-size:12.5px; margin:0 0 14px; line-height:1.4}}
  .tag{{font-size:10.5px; letter-spacing:.4px; text-transform:uppercase; color:#0a0d12; font-weight:800;
    background:linear-gradient(135deg,var(--accent2),#7ff0d7); padding:2px 7px; border-radius:999px}}
  .tag.warm{{background:linear-gradient(135deg,#ffd479,#ff9f6b)}}
  .row{{display:flex; gap:10px; min-width:0}}
  .btn{{
    flex:1 1 0; min-width:0; display:flex; align-items:center; justify-content:center; gap:8px; text-decoration:none;
    padding:13px 10px; border-radius:14px; font-size:14.5px; font-weight:650; color:var(--text);
    border:1px solid var(--line); background:rgba(255,255,255,.045); cursor:pointer;
    transition:transform .12s ease, background .2s ease; -webkit-tap-highlight-color:transparent;
  }}
  .btn:active{{transform:scale(.97)}}
  .btn.primary{{
    background:linear-gradient(135deg,var(--accent),#5b8cff); border-color:transparent;
    box-shadow:0 8px 22px rgba(92,110,255,.35);
  }}
  .copywrap{{display:flex; justify-content:center; margin-top:11px}}
  .copy{{
    display:inline-flex; align-items:center; justify-content:center; gap:7px; min-height:40px;
    padding:9px 14px; border-radius:999px; font-size:12.5px; color:#a9b5c6; cursor:pointer; font-family:inherit;
    background:rgba(255,255,255,.035); border:1px solid rgba(255,255,255,.07);
  }}
  .copy svg{{width:14px; height:14px; opacity:.75}}
  .copy:active{{color:var(--text)}}
  footer{{margin-top:26px; text-align:center; font-size:12.5px; color:var(--muted); line-height:1.7}}
  footer a{{color:#9fd8ff; text-decoration:none}}
  .toast{{
    position:fixed; left:50%; bottom:26px; transform:translate(-50%,26px); opacity:0;
    background:#e9eefc; color:#0a0d12; font-size:13px; font-weight:600; padding:10px 16px;
    border-radius:999px; transition:.22s ease; pointer-events:none;
  }}
  .toast.on{{opacity:1; transform:translate(-50%,0)}}
</style>
</head>
<body>
<main>
  <div class="brand">
    <div class="logo">
      <svg viewBox="0 0 24 24" width="23" height="23" fill="none" stroke="#0a0d12" stroke-width="2.2"
           stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.6l7.4 3.1v6.6c0 7-7.4 12.6-7.4 12.6S4.6 19.2 4.6 12.3V5.7L12 2.6z"/><path d="M8.7 12.2l2.3 2.3 4.4-4.6"/></svg>
    </div>
    <div><h1>Esmien VPN</h1><p>личный профиль</p></div>
  </div>

  <p class="hi">Привет, <span>{name}</span></p>
  <div class="sub">{intro}</div>

{cards}
  <footer>
    Приложение не установлено? <a href="https://www.happ.su">Happ</a> или <a href="https://incy.cc">INCY</a>.<br>
    Если кнопка не открывает приложение — скопируй ссылку и вставь её в приложении через «+».
  </footer>
</main>
<div class="toast" id="toast">Ссылка скопирована</div>
<script>
  function copyLink(url, el) {{
    const done = () => {{
      const t = document.getElementById('toast');
      t.classList.add('on'); setTimeout(() => t.classList.remove('on'), 1600);
      if (el) {{ const old = el.textContent; el.textContent = 'Скопировано ✓';
                setTimeout(() => el.textContent = old, 1600); }}
    }};
    if (navigator.clipboard && window.isSecureContext) {{
      navigator.clipboard.writeText(url).then(done).catch(fallback);
    }} else {{ fallback(); }}
    function fallback() {{
      const ta = document.createElement('textarea');
      ta.value = url; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try {{ document.execCommand('copy'); done(); }} catch (e) {{ prompt('Скопируй ссылку:', url); }}
      document.body.removeChild(ta);
    }}
  }}
</script>
</body>
</html>
"""

CARD = """  <section class="card">
    <h2>{icon}{label} <span class="tag {tagcls}">{badge}</span></h2>
    <p class="desc">{desc}</p>
    <div class="row">
      <a class="btn primary" href="happ://add/{url}">Happ</a>
      <a class="btn" href="incy://add/{url}">INCY</a>
    </div>
    <div class="copywrap"><button class="copy" type="button" onclick="copyLink('{url}', this)">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"
           stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2.5"/><path d="M6.5 15H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v.5"/></svg>
      скопировать ссылку
    </button></div>
  </section>"""

ICON_MAIN = """<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
      stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.6 2.6 2.6 15.4 0 18M12 3c-2.6 2.6-2.6 15.4 0 18"/></svg>"""

ICON_LTE = """<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
      stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 11.5a11 11 0 0 1 15 0"/><path d="M7.7 14.7a6.5 6.5 0 0 1 8.6 0"/><circle cx="12" cy="18.4" r="1.3" fill="currentColor" stroke="none"/></svg>"""


def sub_url(token: str) -> str:
    return f"https://{BRAND['host']}:{BRAND['port']}/sub/{token}"


def kind_of(token: str) -> str:
    f = Path(f"/var/www/esmien-sub/{token}/kind")
    return f.read_text().strip() if f.exists() else ""


INTRO_DEFAULT = "Выбери профиль и нажми кнопку — приложение подхватит настройки само."

INTRO_META = (
    "<p>Один узел — все сценарии внутри. Выбирать ничего не нужно:</p>"
    "<ul>"
    "<li>🤖 <b>Gemini</b> — работает как обычно</li>"
    "<li>🇷🇺 <b>Российские сайты</b> — без выключения VPN</li>"
    "<li>📺 <b>YouTube</b> — без рекламы</li>"
    "<li>💬 <b>TG, WhatsApp, Instagram</b> и прочее</li>"
    "</ul>"
    "<p>Нет списка моделей? Так задумано: маршруты решает сервер.</p>"
    "<p class=\"hint\">RU-сайты открываются чуть медленнее — запрос идёт через несколько "
    "серверов, это норма.</p>"
    "<p class=\"hint\">Если сценарий отвалится, переключение на Эстонию займёт до минуты.</p>"
)

META_DESC = "Один узел. Gemini, RU-сайты, YouTube без рекламы и заблокированные сервисы — "
META_DESC_TAIL = "сервер разводит их сам, переключать ничего не нужно."


def render(name: str, user: dict, token: str | None = None) -> str:
    token = token or user["token"]
    meta = kind_of(token) == "meta"
    main_desc = (META_DESC + META_DESC_TAIL) if meta else (
        "Наши маршруты: Эстония, Франция, Финляндия и пул релеев. "
        "RU-сервисы идут через Москву автоматически.")
    cards = [CARD.format(icon=ICON_MAIN, label="Основная", badge="main", tagcls="",
                         desc=main_desc, url=sub_url(user["token"]))]
    if user.get("token_lte"):
        cards.append(CARD.format(icon=ICON_LTE, label="LTE", badge="белые списки", tagcls="warm",
                                 desc="Прямые узлы провайдера: работают, когда провайдер режет всё, "
                                      "кроме российских адресов.",
                                 url=sub_url(user["token_lte"])))
    return TEMPLATE.format(name=name, cards="\n".join(cards),
                           intro=INTRO_META if meta else INTRO_DEFAULT)


def main() -> int:
    PAGES.mkdir(parents=True, exist_ok=True)
    PAGES.chmod(0o755)
    n = 0
    for name, user in USERS.items():
        for token in (user["token"], user.get("token_lte")):
            if not token:
                continue
            p = PAGES / f"{token}.html"
            p.write_text(render(name, user, token), encoding="utf-8")
            p.chmod(0o644)
            n += 1
    # какой токен кому принадлежит — только для root, в служебном файле
    index = {tok: name for name, u in USERS.items()
             for tok in (u["token"], u.get("token_lte")) if tok}
    ip = BASE / "pages_index.json"
    ip.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    ip.chmod(0o600)
    print(f"pages: {n} файлов в {PAGES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
