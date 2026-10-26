"""Додавання готівкової витрати з простої форми на телефоні.

Форма записує лише готівку: витрати з картки приходять з банківської виписки,
і ручний запис порахував би їх двічі.

Кожна витрата дописується в manual.beancount у правильному для Beancount вигляді:
    2026-10-26 * "Кава"
      Expenses:Марко:Кафе       12.50 PLN
      Assets:Марко:Готівка
Після запису облік перевіряється; якщо Beancount знаходить помилку, запис
відкочується. Потім файл комітиться в GitHub (storage.push), щоб пережити перезапуск.
"""

import threading
from datetime import date
from decimal import Decimal, InvalidOperation

from beancount import loader
from beancount.core import data

import storage

_lock = threading.Lock()
MAX_AMOUNT = Decimal("100000")
_seen_nonces = set()  # захист від подвійного натискання «Додати»


class CashError(ValueError):
    """Помилка, яку можна показати людині у формі."""


def expense_accounts(entries, person):
    """Відкриті рахунки витрат людини, крім «Без категорії» — для списку у формі."""
    prefix = f"Expenses:{person}:"
    return sorted(
        e.account for e in entries
        if isinstance(e, data.Open) and e.account.startswith(prefix) and not e.account.endswith("Без-Категорії")
    )


def parse_amount(text):
    text = (text or "").strip().replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise CashError("Сума має бути числом, наприклад 12,50.") from None
    if amount <= 0:
        raise CashError("Сума має бути більшою за нуль.")
    if amount > MAX_AMOUNT:
        raise CashError("Завелика сума для готівки.")
    if amount != amount.quantize(Decimal("0.01")):
        raise CashError("Не більше двох знаків після коми.")
    return amount.quantize(Decimal("0.01"))


def parse_date(text):
    try:
        day = date.fromisoformat((text or "").strip())
    except ValueError:
        raise CashError("Невірна дата.") from None
    if day < date(2026, 8, 1) or day > date.today():
        raise CashError("Дата має бути між 01.08.2026 і сьогоднішнім днем.")
    return day


def clean_text(text):
    text = " ".join((text or "").split())[:80]
    return text.replace("\\", "").replace('"', "'")


class Duplicate(Exception):
    """Ця форма вже була надіслана (повторне натискання)."""


def add_cash(ledger, person, day_text, amount_text, account, note, nonce=""):
    """Дописує готівкову витрату. Повертає (дата, сума, категорія) або кидає CashError."""
    day = parse_date(day_text)
    amount = parse_amount(amount_text)

    with _lock:
        if nonce:
            if nonce in _seen_nonces:
                raise Duplicate()
            _seen_nonces.add(nonce)
        entries, errors, _ = loader.load_file(str(ledger))
        if account not in expense_accounts(entries, person):
            _seen_nonces.discard(nonce)
            raise CashError("Оберіть категорію зі списку.")
        cash_account = f"Assets:{person}:Готівка"
        narration = clean_text(note) or account.split(":")[-1].replace("-", " ")

        block = (f'\n{day} * "{narration}"\n'
                 f"  {account:<40} {amount:>10} PLN\n"
                 f"  {cash_account}\n")
        manual = storage.MANUAL
        before = manual.read_text(encoding="utf-8")
        manual.write_text(before.rstrip("\n") + "\n" + block, encoding="utf-8")

        _, new_errors, _ = loader.load_file(str(ledger))
        if len(new_errors) > len(errors):
            manual.write_text(before, encoding="utf-8")  # відкат: облік має лишатися чистим
            _seen_nonces.discard(nonce)
            raise CashError("Облік не прийняв запис, нічого не збережено.")

    # Коміт у GitHub — у фоні, щоб людина не чекала відповіді від GitHub.
    threading.Thread(target=storage.push, daemon=True).start()
    return day, amount, account.split(":")[-1].replace("-", " ")
