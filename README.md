# VPN: VLESS over WebSocket + TLS (вход) -> внешние выходы

## Параллельный домен

`vpn.esmien.ru` и `share.esmien.com` работают одновременно. Старые страницы и подписки
не меняются; новые страницы имеют тот же `/u/<token>` на `share.esmien.com:443`.
Локальный `vpn-parallel-domain.service` читает исходные страницы и подписки и заменяет
имя при выдаче; токены и права пользователей не меняются, удалённые файлы сразу исчезают
и с нового домена. Сервис слушает только 127.0.0.1:18744.

Восстановление: выпустить DNS-01 сертификат `share-esmien-com` только на `share.esmien.com`
с учётными данными `/root/.secrets/cf-esmien-com.ini`; установить `router/parallel_domain.py`
в `/root/vpn-router/`, `systemd/vpn-parallel-domain.service` в `/etc/systemd/system/`,
выполнить `systemctl daemon-reload` и `systemctl enable --now vpn-parallel-domain`.
Hysteria через встроенный ACME DNS-01 выпускает отдельный сертификат на каждый домен
и выбирает его по SNI; настройка есть в `hysteria/config.example.yaml`. Токен Cloudflare
в живом `/etc/hysteria/config.yaml` — секрет и не должен попадать в репозиторий.
Генератор `apply_brand.py` создаёт оба HTTPS-входа и SNI-маршруты. После применения
проверить старые и новые VLESS и Hysteria2, страницы и три формата подписки.

Панель статистики работает только на `https://stats.esmien.com/` и использует один htpasswd, статику, API и историю
`/var/lib/vpn-stats/state.json`. Счётчики Xray привязаны к пользователям и выходам,
а не к DNS-имени входа, поэтому один коллектор учитывает трафик обоих доменов.
Панель закрыта на уровне nginx: обе площадки пускают только `85.92.158.238`
(наш edge, то есть клиентов внутри туннеля) и `127.0.0.1`, остальным — `444` без ответа.
Чтобы панель открывалась с любого устройства, в `config.json` есть правило
`domain:stats.esmien.{com,ru} -> direct`: маршрут через NL/Gemini сюда не пускают.
У `.com` отдельный сертификат `stats-esmien-com`; его vhost —
`nginx/stats.esmien.com.conf`, восстановить в `/etc/nginx/conf.d/`. Выдаваемые API,
CLI и панелью ссылки ведут на `https://share.esmien.com` (443).

Домашний провайдер режет 443 и fake-TLS/MTProto, но пропускает настоящий TLS на
нестандартных портах. Поэтому вход — VLESS+WS за nginx с сертификатом Let's Encrypt,
а внешние выходы подключаются как outbound'ы Xray.

## Схема

    клиент -> nginx (TLS, 8443 / 2087) -> xray vpn-router (VLESS+WS, 127.0.0.1:1008x)
           -> outbound: наш выход (direct) / внешний узел (p1..p23) / fr / fi

- 8443 — основной вход, 2087 — резервный.
- `vpn-router` — контейнер xray-core с `--network host`, конфиг `/root/vpn-router/config.json`.
- TLS терминирует nginx; Xray слушает только loopback.
- 3x-ui в схеме не участвует: панель не умеет описывать цепочки «вход → конкретный выход».

## Два пула

1. **Пул через наш сервер** (`/general`, `/france`, `/fi`, `/test`, `/p<i>`): клиент идёт
   к нам, мы выпускаем трафик через нужный узел. Это основная подписка.
2. **Белые списки** (`router/whitelist_links.json`): узлы с РФ-входом отдаются клиенту
   **напрямую**, минуя наш сервер, отдельной подпиской — иначе первый хоп (наш
   зарубежный IP) отваливается первым же при блокировках.

## Gemini

Работает только через IP, который Google считает поддерживаемой страной — не через
DNS и не через «правильную» локацию в названии тарифа. Проверка:

    python3 /root/vpn-router/gemini_geo_check.py [socks5-host:port]

`fr`, `fi`, `de` — ок; `ru` — заглушка. Наш эстонский сервер Google видит как `ru`.

## Правка конфига

    vpn-cfg status     # инбаунды/аутбаунды/дефолт + состояние контейнера
    vpn-cfg edit       # открыть config.json (бэкап автоматически)
    vpn-cfg apply      # валидация + перезапуск + health-check трёх маршрутов
    vpn-cfg rollback   # откат на предыдущий бэкап

## Развёртывание на новом сервере

    bash scripts/deploy.sh vpn.example.com mail@example.com
    python3 /root/vpn-router/import_sub.py <файл-подписки> [мёртвые индексы]

`deploy.sh` ставит зависимости, генерирует конфиг из `router/config.template.json`,
выпускает сертификат, поднимает контейнер, рендерит nginx и печатает ссылку подписки.

Шаблон конфига пересобирается из живого файла: `router/make_template.py` (маскирует
креды, выкидывает импортированные ретрансляторы).

## Реальный IP за stream

443-вход: `stream { listen 443; ssl_preread on; proxy_pass <backend>; proxy_protocol on; }`,
сайты слушают `127.0.0.1:9443 proxy_protocol ssl`. Лишний промежуточный хоп превращает
все адреса в логах в `127.0.0.1` — тогда fail2ban бесполезен.

## Напоминания о подписках

`vpn-sub-expiry.timer` (ежедневно 07:00 UTC) запускает `router/sub_expiry.py`:
берёт срок из заголовка `subscription-userinfo` каждой ссылки в
`/root/vpn-router/subs.json` и присылает в Telegram за 3, 2 и 1 день до конца,
плюс отдельное сообщение, если подписка уже истекла. Повторных сообщений за один
день нет — состояние в `sub_expiry_state.json`.

    python3 /root/vpn-router/sub_expiry.py --test    # проверка доставки
    python3 /root/vpn-router/sub_expiry.py --force   # отчёт сейчас, вне графика

Список подписок (`subs.json`) в репозиторий не попадает: там ссылки с ключами.

## Секреты

В репозиторий не попадают: UUID клиента, пароли внешних выходов, ссылки подписок
(`whitelist_links.json`, `relay_nodes.json` с кредами). Живут только на хосте.

## Legacy

`failover.py`, `provision_vpn_host.py`, `certbot_retry.py`, `systemd/vpn-failover.*` —
одноразовые сценарии прошлой схемы (авто-переключение выхода, отложенный выпуск
сертификата). В текущей схеме не используются: фолбэк отключён сознательно, чтобы
падение выхода было видно сразу.
