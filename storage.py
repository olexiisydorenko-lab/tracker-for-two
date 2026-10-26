"""Збереження manual.beancount між перезапусками сервера.

Безкоштовний Render не має постійного диска: після засинання файли повертаються
до стану з репозиторію. Тому ручні операції (готівка з телефона) живуть у самому
репозиторії на GitHub:
  - pull() під час старту забирає останню версію manual.beancount;
  - push() після кожного збереження у Fava комітить її назад.

Потрібні змінні середовища (лише на сервері):
  GITHUB_TOKEN   fine-grained токен з доступом лише до цього репозиторію (Contents: Read and write)
  GITHUB_REPO    власник/назва, наприклад olexiisydorenko-lab/tracker-for-two
  GITHUB_BRANCH  гілка, за замовчуванням main

Якщо змінних немає (локальний запуск, сервер з постійним диском), модуль нічого не робить.
"""

import base64
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
MANUAL = ROOT / "manual.beancount"
REMOTE_PATH = "manual.beancount"
API = "https://api.github.com"
# [skip render] — щоб коміт з ручною операцією не запускав повторний деплой.
COMMIT_MESSAGE = "Ручна операція, додана з телефона [skip render]"

log = logging.getLogger("tracker.storage")
_lock = threading.Lock()
_state = {"sha": None, "synced": None}


def enabled():
    return bool(os.environ.get("GITHUB_TOKEN") and os.environ.get("GITHUB_REPO"))


def _request(method, body=None):
    repo = os.environ["GITHUB_REPO"]
    branch = os.environ.get("GITHUB_BRANCH", "main")
    url = f"{API}/repos/{repo}/contents/{REMOTE_PATH}"
    if method == "GET":
        url += f"?ref={branch}"
    data = json.dumps({**body, "branch": branch}).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "tracker-for-two",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def pull():
    """Забирає manual.beancount з GitHub і записує локально."""
    if not enabled():
        return False
    with _lock:
        try:
            meta = _request("GET")
        except urllib.error.HTTPError as err:
            log.error("Не вдалося прочитати %s з GitHub: %s", REMOTE_PATH, err)
            return False
        text = base64.b64decode(meta["content"]).decode("utf-8")
        MANUAL.write_text(text, encoding="utf-8")
        _state.update(sha=meta["sha"], synced=text)
        log.info("manual.beancount отримано з GitHub (%s)", meta["sha"][:7])
        return True


def push():
    """Комітить manual.beancount у GitHub, якщо файл змінився з останньої синхронізації."""
    if not enabled():
        return False
    with _lock:
        text = MANUAL.read_text(encoding="utf-8")
        if text == _state["synced"]:
            return False
        for attempt in range(2):
            if _state["sha"] is None:
                try:
                    _state["sha"] = _request("GET")["sha"]
                except urllib.error.HTTPError:
                    _state["sha"] = None  # файлу ще немає — створимо
            body = {"message": COMMIT_MESSAGE,
                    "content": base64.b64encode(text.encode("utf-8")).decode()}
            if _state["sha"]:
                body["sha"] = _state["sha"]
            try:
                result = _request("PUT", body)
            except urllib.error.HTTPError as err:
                if err.code in (409, 422) and attempt == 0:
                    _state["sha"] = None  # хтось змінив файл — беремо свіжий sha і пробуємо ще раз
                    continue
                log.error("Не вдалося зберегти %s у GitHub: %s", REMOTE_PATH, err)
                return False
            _state.update(sha=result["content"]["sha"], synced=text)
            log.info("manual.beancount збережено в GitHub (%s)", _state["sha"][:7])
            return True
        return False


class SyncAfterWrite:
    """WSGI-обгортка над Fava: після успішного запису (PUT/POST/DELETE) — push() у GitHub."""

    WRITE_METHODS = {"PUT", "POST", "DELETE"}

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if environ.get("REQUEST_METHOD") not in self.WRITE_METHODS or not enabled():
            return self.app(environ, start_response)

        status_holder = {}

        def start(status, headers, exc_info=None):
            status_holder["code"] = int(status.split()[0])
            return start_response(status, headers, exc_info)

        body = list(self.app(environ, start))  # дочитуємо відповідь, щоб запис точно завершився
        if status_holder.get("code", 500) < 400:
            push()
        return body
