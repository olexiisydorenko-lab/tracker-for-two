"""Перевірка обліку однією командою: python check.py

1. Перегенеровує облік з виписок (import.py).
2. Запускає bean-check — він має мовчати.
3. Друкує змінні витрати Олі й Марка за серпень і вересень
   і звіряє їх з контрольними цифрами з умови завдання.

Код виходу 0 — усе збігається, 1 — є розбіжність або помилка обліку.
"""

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from beancount import loader
from beanquery.query import run_query

ROOT = Path(__file__).parent
LEDGER = ROOT / "main.beancount"

CONTROL = {
    ("Оля", 8): Decimal("2195.21"),
    ("Оля", 9): Decimal("2582.42"),
    ("Марко", 8): Decimal("1855.57"),
    ("Марко", 9): Decimal("2432.83"),
}
MONTHS = {8: "серпень", 9: "вересень"}


def main():
    subprocess.run([sys.executable, str(ROOT / "import.py")], check=True)

    print("\n== bean-check ==")
    check = subprocess.run(["bean-check", str(LEDGER)], capture_output=True, text=True)
    output = (check.stdout + check.stderr).strip()
    print(output or "(тиша — помилок немає)")
    ok = check.returncode == 0 and not output

    entries, _, options = loader.load_file(str(LEDGER))
    _, rows = run_query(entries, options, (
        "SELECT month, root(account, 2) AS person, sum(number) "
        "WHERE account ~ '^Expenses' AND NOT ('fixed' IN tags) AND year = 2026 "
        "GROUP BY month, person"))
    actual = {(person.split(":")[1], month): total for month, person, total in rows}

    print("\n== Змінні витрати (без #fixed) ==")
    for (person, month), expected in CONTROL.items():
        got = actual.get((person, month), Decimal(0))
        mark = "OK" if got == expected else "НЕ ЗБІГАЄТЬСЯ"
        ok &= got == expected
        print(f"{person:<6} {MONTHS[month]:<9} {got:>9} зл  (контроль {expected})  {mark}")

    print("\nУсе збігається." if ok else "\nЄ розбіжності — див. вище.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
