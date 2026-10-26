"""Імпорт банківських виписок Олі й Марка у Beancount.

Кожна людина має свою виписку (різні банки, різні формати) і свої правила:
  data/olya-statement.csv  + rules-olya.csv   → ledger/olya.beancount
  data/marko-statement.csv + rules-marko.csv  → ledger/marko.beancount

Згенеровані файли перезаписуються при кожному запуску.
Готівку, додану з телефона, Fava пише в manual.beancount — його імпорт не чіпає.

Запуск: python import.py
"""

import csv
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
LEDGER = ROOT / "ledger"


def load_rules(path):
    """Правила в порядку файлу: перемагає перше, що підійшло."""
    with path.open(encoding="utf-8") as f:
        return [
            (row["шаблон"].lower(), row["рахунок"], row["фіксована"].strip().lower() == "так")
            for row in csv.DictReader(f)
        ]


def categorize(description, amount, rules, person):
    text = description.lower()
    for pattern, account, fixed in rules:
        if pattern in text:
            return account, fixed
    if amount > 0:
        return f"Income:{person}:Інше", False
    return f"Expenses:{person}:Без-Категорії", False


# ---------- Формати банків ----------

def read_olya(path):
    """Банк Олі: CSV з комами, ISO-дати, нові операції внизу."""
    rows = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["State"] != "COMPLETED":
                continue
            rows.append({
                "date": date.fromisoformat(row["Completed Date"][:10]),
                "description": row["Description"],
                "amount": Decimal(row["Amount"]) - Decimal(row["Fee"]),
                "balance": Decimal(row["Balance"]),
            })
    return rows


def pl_number(text):
    """«-1 234,56» → Decimal('-1234.56')"""
    return Decimal(text.replace(" ", "").replace(" ", "").replace(",", "."))


def read_marko(path):
    """Банк Марка: «;», коми в сумах, пробіли в тисячах, дати ДД.ММ.РРРР,
    шапка з 7 рядків, нові операції вгорі."""
    lines = path.read_text(encoding="utf-8").splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("Data operacji;"))
    rows = []
    for line in lines[header + 1:]:
        if not line.strip():
            continue
        op_date, _booked, _kind, description, amount, balance = line.split(";")
        rows.append({
            "date": datetime.strptime(op_date, "%d.%m.%Y").date(),
            "description": description,
            "amount": pl_number(amount),
            "balance": pl_number(balance),
        })
    rows.reverse()  # старі операції першими, щоб залишок ішов по порядку
    return rows


# ---------- Запис ----------

def quote(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def write_ledger(person, slug, rows, rules):
    bank = f"Assets:{person}:Рахунок"
    lines = [f"; Згенеровано import.py з виписки {person} — не редагуйте руками.", ""]
    day_end_balance = {}
    for row in rows:
        account, fixed = categorize(row["description"], row["amount"], rules, person)
        tag = " #fixed" if fixed else ""
        lines.append(f'{row["date"]} * "{quote(row["description"])}"{tag}')
        lines.append(f"  {bank:<40} {row['amount']:>10} PLN")
        lines.append(f"  {account}")
        lines.append("")
        day_end_balance[row["date"]] = row["balance"]

    # Beancount перевіряє залишок на початок дня, тому перевірка — на наступний день.
    for day, balance in day_end_balance.items():
        lines.append(f"{day + timedelta(days=1)} balance {bank} {balance} PLN")

    out = LEDGER / f"{slug}.beancount"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{person}: {len(rows)} операцій, {len(day_end_balance)} перевірок залишку → {out.relative_to(ROOT)}")


def main():
    LEDGER.mkdir(exist_ok=True)
    write_ledger("Оля", "olya", read_olya(DATA / "olya-statement.csv"), load_rules(ROOT / "rules-olya.csv"))
    write_ledger("Марко", "marko", read_marko(DATA / "marko-statement.csv"), load_rules(ROOT / "rules-marko.csv"))


if __name__ == "__main__":
    main()
