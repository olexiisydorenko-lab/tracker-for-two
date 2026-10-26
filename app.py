"""Вебзастосунок трекера: вхід за паролем, звіт за місяць і Fava для додавання витрат.

  /login   форма входу
  /        звіт за місяць (?who=olya або ?who=marko)
  /fava/   Fava: перегляд обліку і кнопка «+» для готівки (пише в manual.beancount)

Без входу не відкривається жоден шлях, крім /login: перевірку робить
AuthMiddleware над усім застосунком, тож вона захищає й Fava.

Секрети беруться лише зі змінних середовища (див. .env.example):
  TRACKER_USER            логін (за замовчуванням marko)
  TRACKER_PASSWORD_HASH   хеш пароля з werkzeug (див. README) — або
  TRACKER_PASSWORD        пароль у змінній середовища хостингу (не в коді)
  SECRET_KEY              ключ для підпису cookie входу
  GITHUB_TOKEN, GITHUB_REPO  збереження ручних операцій між перезапусками (storage.py)
  TRACKER_DEV=1           лише для локального запуску без HTTPS
"""

import hmac
import html
import os
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from fava.application import create_app as create_fava
from flask import Flask, redirect, request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash
from werkzeug.wrappers import Request, Response

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")  # секрети з .env поруч (він у .gitignore), не з коду

import cash  # noqa: E402
import report  # noqa: E402
import storage  # noqa: E402
LEDGER = ROOT / "main.beancount"
COOKIE = "tracker_auth"
SESSION_DAYS = 30


def require_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Не задано змінну середовища {name} — див. .env.example")
    return value


USER = os.environ.get("TRACKER_USER", "marko")
# Пароль задається або хешем (краще), або відкритим значенням у змінній середовища хостингу.
PASSWORD_HASH = os.environ.get("TRACKER_PASSWORD_HASH")
PASSWORD = os.environ.get("TRACKER_PASSWORD")
if not (PASSWORD_HASH or PASSWORD):
    raise RuntimeError("Не задано TRACKER_PASSWORD_HASH або TRACKER_PASSWORD — див. .env.example")
SECRET_KEY = require_env("SECRET_KEY")
DEV = os.environ.get("TRACKER_DEV") == "1"

signer = URLSafeTimedSerializer(SECRET_KEY, salt="tracker-login")


# ---------- Сторінки ----------

web = Flask(__name__)

LOGIN_PAGE = """<!doctype html>
<html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Вхід — трекер</title>
<style>
body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:#fcfcfb; color:#0b0b0b;
  font:16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
@media (prefers-color-scheme: dark) {{ body {{ background:#1a1a19; color:#f0efec; }}
  input {{ background:#232321; color:#f0efec; border-color:#3a3936 !important; }} }}
form {{ width:min(340px, calc(100vw - 32px)); display:grid; gap:12px; }}
h1 {{ font-size:22px; margin:0 0 4px; }}
input {{ font:inherit; padding:10px 12px; border:1px solid #d6d5d0; border-radius:8px; }}
button {{ font:inherit; padding:10px; border:0; border-radius:8px; background:#2a78d6; color:#fff; }}
.err {{ color:#c4372f; margin:0; }}
</style></head>
<body><form method="post" action="login">
<h1>Трекер Олі й Марка</h1>
{error}
<input name="username" autocomplete="username" placeholder="Логін" required autofocus>
<input name="password" type="password" autocomplete="current-password" placeholder="Пароль" required>
<input type="hidden" name="next" value="{next}">
<button type="submit">Увійти</button>
</form></body></html>"""


def safe_next(target):
    """Повертаємо лише на свій сайт, ніколи на чужий домен."""
    target = target or "/"
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


@web.get("/login")
def login_form():
    return LOGIN_PAGE.format(error="", next=html.escape(safe_next(request.args.get("next"))))


@web.post("/login")
def login_submit():
    user_ok = hmac.compare_digest(request.form.get("username", ""), USER)
    given = request.form.get("password", "")
    if PASSWORD_HASH:
        pass_ok = check_password_hash(PASSWORD_HASH, given)
    else:
        pass_ok = hmac.compare_digest(given.encode(), PASSWORD.encode())
    if not (user_ok and pass_ok):
        time.sleep(1)  # гальмуємо підбір пароля
        return LOGIN_PAGE.format(error='<p class="err">Невірний логін або пароль.</p>',
                                 next=html.escape(safe_next(request.form.get("next")))), 401
    response = redirect(request.script_root + safe_next(request.form.get("next")))
    response.set_cookie(COOKIE, signer.dumps(USER), max_age=SESSION_DAYS * 86400,
                        secure=not DEV, httponly=True, samesite="Lax")
    return response


@web.get("/logout")
def logout():
    response = redirect(request.script_root + "/login")
    response.delete_cookie(COOKIE)
    return response


@web.get("/")
def home():
    who = request.args.get("who", "marko")
    if who not in report.PEOPLE:
        who = "marko"
    notice = None
    if request.args.get("added"):
        notice = ("ok", f"Додано: {request.args['added']}")
    return report.render(who, prefix=request.script_root, ledger=LEDGER, notice=notice)


@web.post("/add")
def add():
    """Готівкова витрата з форми на головній сторінці."""
    who = request.form.get("who", "marko")
    if who not in report.PEOPLE:
        who = "marko"
    try:
        day, amount, category = cash.add_cash(
            LEDGER, report.PEOPLE[who], request.form.get("date"), request.form.get("amount"),
            request.form.get("account"), request.form.get("note"))
    except cash.CashError as err:
        return report.render(who, prefix=request.script_root, ledger=LEDGER, notice=("err", str(err))), 400
    added = f"{amount} зл, {category}, {day:%d.%m}".replace(".", ",", 1)
    # Post/Redirect/Get: перезавантаження сторінки не додасть витрату вдруге.
    return redirect(f"{request.script_root}/?who={who}&added={quote(added)}#add", 303)


# ---------- Захист усього застосунку ----------

class AuthMiddleware:
    """Пускає далі лише з дійсною cookie входу; без неї — 302 на /login.

    HTTP → HTTPS перенаправляє сам хостинг (Render робить це завжди),
    а цей шар додає HSTS, щоб браузер більше не пробував HTTP.
    """

    OPEN_PATHS = ("/login",)

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        req = Request(environ)
        if req.path not in self.OPEN_PATHS and not self.logged_in(req):
            target = req.full_path if req.query_string else req.path
            resp = redirect(f"{req.script_root}/login?next={quote(target, safe='/')}")
            return self.with_headers(resp)(environ, start_response)

        def start(status, headers, exc_info=None):
            headers.extend(self.security_headers())
            return start_response(status, headers, exc_info)

        return self.app(environ, start)

    @staticmethod
    def logged_in(req):
        token = req.cookies.get(COOKIE)
        if not token:
            return False
        try:
            return signer.loads(token, max_age=SESSION_DAYS * 86400) == USER
        except BadSignature:
            return False

    @staticmethod
    def security_headers():
        headers = [("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "same-origin"),
                   ("Cache-Control", "no-store")]
        if not DEV:
            headers.append(("Strict-Transport-Security", "max-age=31536000"))
        return headers

    def with_headers(self, resp: Response):
        for name, value in self.security_headers():
            resp.headers[name] = value
        return resp


# На Render диск тимчасовий: спершу забираємо ручні операції з GitHub, потім вантажимо облік.
storage.pull()
fava = create_fava([LEDGER], load=True, poll_watcher=True)

application = ProxyFix(  # Render віддає HTTPS через проксі
    AuthMiddleware(DispatcherMiddleware(web, {"/fava": storage.SyncAfterWrite(fava)})),
    x_proto=1, x_host=1,
)


if __name__ == "__main__":
    from werkzeug.serving import run_simple

    run_simple("127.0.0.1", 5000, application, use_reloader=False)
