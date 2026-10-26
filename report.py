"""Звіт за місяць для Олі або Марка.

render(person) повертає HTML-сторінку: витрати за категоріями, норма заощаджень,
фіксовані й змінні витрати, подушка безпеки. Вебзастосунок (app.py) викликає
render() на кожен запит, тож щойно додана з телефона витрата одразу у звіті.

Запуск окремо: python report.py [olya|marko]  → report.html
"""

import calendar
import html
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from beancount import loader
from beancount.core import data
from beanquery.query import run_query

ROOT = Path(__file__).parent
LEDGER = ROOT / "main.beancount"
OUTPUT = ROOT / "report.html"

PEOPLE = {"olya": "Оля", "marko": "Марко"}
GROCERIES = "Продукти"

CUSHION_MONTHS = 6
MONTH_NAMES = [
    "", "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
    "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень",
]


# ---------- Форматування ----------

def zl(value):
    """Decimal → «12 345,67 зл»."""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " зл"


def pct(part, whole):
    return f"{part / whole * 100:.1f}%".replace(".", ",") if whole else "—"


def esc(text):
    return html.escape(str(text))


# ---------- Дані ----------

def q(entries, options, sql):
    _, rows = run_query(entries, options, sql)
    return rows


def by_month(rows):
    """[(year, month, value)] → {(year, month): value}"""
    return {(y, m): v for y, m, v in rows}


def collect(entries, options, person):
    exp = f"'^Expenses:{person}:'"
    inc = f"'^Income:{person}:'"
    group = "GROUP BY year, month ORDER BY year, month"

    categories = defaultdict(list)
    for y, m, account, total in q(entries, options,
            f"SELECT year, month, account, sum(number) AS total WHERE account ~ {exp} "
            f"GROUP BY year, month, account ORDER BY year, month, total DESC"):
        categories[(y, m)].append((account.split(":")[-1].replace("-", " "), total))

    income = by_month(q(entries, options, f"SELECT year, month, -sum(number) WHERE account ~ {inc} {group}"))
    expenses = by_month(q(entries, options, f"SELECT year, month, sum(number) WHERE account ~ {exp} {group}"))
    fixed = by_month(q(entries, options,
            f"SELECT year, month, sum(number) WHERE account ~ {exp} AND 'fixed' IN tags {group}"))
    groceries = by_month(q(entries, options,
            f"SELECT year, month, sum(number) WHERE account = 'Expenses:{person}:{GROCERIES}' "
            f"AND NOT ('fixed' IN tags) {group}"))
    zero = Decimal(0)
    variable = {k: expenses[k] - fixed.get(k, zero) for k in expenses}
    saved = {k: income.get(k, zero) - expenses[k] for k in expenses}
    mandatory = {k: fixed.get(k, zero) + groceries.get(k, zero) for k in expenses}

    # Для подушки: гроші людини (рахунок + готівка) на кінець кожного місяця.
    asset_rows = q(entries, options,
            f"SELECT year, month, sum(number) WHERE account ~ '^Assets:{person}:' {group}")
    balance_end, running = {}, Decimal(0)
    for y, m, change in asset_rows:
        running += change
        balance_end[(y, m)] = running

    # Операції, додані вручну через Fava (готівка), — окремим списком.
    manual = defaultdict(list)
    for e in entries:
        if isinstance(e, data.Transaction) and str(e.meta.get("filename", "")).endswith("manual.beancount"):
            for p in e.postings:
                if p.account.startswith(f"Expenses:{person}:") and p.units is not None:
                    manual[(e.date.year, e.date.month)].append(
                        (e.date, e.narration or e.payee or "", p.account.split(":")[-1], p.units.number))

    last_date = max(e.date for e in entries if isinstance(e, data.Transaction))
    months = sorted(expenses)
    for k in months:  # місяць без операцій з активами успадковує попередній залишок
        balance_end.setdefault(k, running)
    return {
        "person": person, "months": months, "categories": categories, "income": income,
        "expenses": expenses, "saved": saved, "fixed": fixed, "variable": variable,
        "groceries": groceries, "mandatory": mandatory, "balance_end": balance_end,
        "last_date": last_date, "manual": manual,
    }


def is_complete(key, last_date):
    y, m = key
    return (y, m) < (last_date.year, last_date.month) or last_date.day == calendar.monthrange(y, m)[1]


def month_label(key):
    return f"{MONTH_NAMES[key[1]]} {key[0]}"


# ---------- Блоки сторінки ----------

def block(number, question, headline, sub, chart, conclusion):
    return f"""
  <section class="card">
    <div class="q">Питання {number}</div>
    <h2>{esc(question)}</h2>
    <div class="headline">{headline}</div>
    <div class="sub">{sub}</div>
    {chart}
    <p class="conclusion">{conclusion}</p>
  </section>"""


def block_categories(d, key):
    cats = d["categories"][key]
    total = d["expenses"][key]
    top = cats[0][1]
    rows = "".join(
        f'<div class="hrow" title="{esc(name)}: {zl(v)} ({pct(v, total)})">'
        f'<span class="hlabel">{esc(name)}</span>'
        f'<span class="htrack"><span class="hbar" style="width:{v / top * 100:.1f}%"></span></span>'
        f'<span class="hval">{zl(v)}</span></div>'
        for name, v in cats
    )
    first, second = cats[0], cats[1] if len(cats) > 1 else None
    conclusion = f"Найбільше пішло на «{esc(first[0])}» — {pct(first[1], total)} усіх витрат"
    conclusion += f", на другому місці «{esc(second[0])}» ({pct(second[1], total)})." if second else "."
    return block(1, "Скільки грошей іде на кожну категорію витрат?",
                 zl(total), "усього витрат за місяць", f'<div class="hchart">{rows}</div>', conclusion)


def block_savings(d, key):
    zero = Decimal(0)
    inc = lambda k: d["income"].get(k, zero)
    income, saved = inc(key), d["saved"][key]
    rates = {k: (d["saved"][k] / inc(k) * 100 if inc(k) else zero) for k in d["months"]}
    peak = max(max(rates.values()), Decimal(1))

    cols = []
    for k in d["months"]:
        h = max(float(rates[k] / peak * 100), 0)
        mark = " current" if k == key else ""
        partial = "" if is_complete(k, d["last_date"]) else "*"
        cols.append(
            f'<div class="col{mark}" title="{month_label(k)}: {pct(d["saved"][k], inc(k))}">'
            f'<span class="colval">{pct(d["saved"][k], inc(k))}</span>'
            f'<span class="colarea"><span class="colbar" style="height:{h:.1f}%"></span></span>'
            f'<span class="collabel">{MONTH_NAMES[k[1]][:3]}{partial}</span></div>'
        )
    chart = f'<div class="cols">{"".join(cols)}</div>'
    if not all(is_complete(k, d["last_date"]) for k in d["months"]):
        chart += '<div class="note">* місяць неповний</div>'

    if income:
        conclusion = f"Відкладено {zl(saved)} з {zl(income)} доходу"
    else:
        conclusion = f"Цього місяця доходу не було, витрати {zl(d['expenses'][key])} покрито з залишку"
    idx = d["months"].index(key)
    if idx > 0 and income and inc(d["months"][idx - 1]):
        diff = rates[key] - rates[d["months"][idx - 1]]
        word = "більше" if diff >= 0 else "менше"
        conclusion += f" — на {abs(diff):.1f} п.п. {word}, ніж минулого місяця".replace(".", ",", 1)
    if not is_complete(key, d["last_date"]):
        conclusion += ", але місяць ще не закінчився"
    conclusion += "."
    return block(2, "Яку частку доходу відкладено?",
                 pct(saved, income), "норма заощаджень", chart, conclusion)


def block_groups(d, key):
    total = d["expenses"][key]
    fixed, variable = d["fixed"].get(key, Decimal(0)), d["variable"][key]
    parts = [("Фіксовані", fixed, "s1"), ("Змінні", variable, "s3")]
    segs = "".join(
        f'<span class="seg {c}" style="width:{v / total * 100:.2f}%" title="{n}: {zl(v)} ({pct(v, total)})"></span>'
        for n, v, c in parts
    )
    legend = "".join(
        f'<div class="lg"><span class="key {c}"></span>{n}<span class="lgv">{zl(v)} · {pct(v, total)}</span></div>'
        for n, v, c in parts
    )
    chart = f'<div class="stack">{segs}</div><div class="legend">{legend}</div>'
    conclusion = (f"Фіксовані платежі — {zl(fixed)}; решту, {zl(variable)} ({pct(variable, total)}), "
                  f"можна скоротити.")
    return block(3, "Скільки витрат фіксовані, а скільки змінні?",
                 zl(variable), "змінні витрати — ними можна керувати", chart, conclusion)


def block_manual(d, key):
    items = sorted(d["manual"].get(key, []), reverse=True)
    if not items:
        return ""
    rows = "".join(
        f'<div class="lg">{day:%d.%m} · {esc(text)} <small>({esc(cat.replace("-", " "))})</small>'
        f'<span class="lgv">{zl(amount)}</span></div>'
        for day, text, cat, amount in items)
    return f"""
  <section class="card">
    <div class="q">Додано з телефона</div>
    <h2>Готівка та інші ручні операції</h2>
    <div class="legend">{rows}</div>
    <p class="conclusion">Ці витрати вже враховано в категоріях і сумах вище.</p>
  </section>"""


def block_cushion(d, key):
    full = [k for k in d["months"] if k <= key and is_complete(k, d["last_date"])]
    balance = d["balance_end"][key]
    if not full:
        return ""
    avg = sum(d["mandatory"][k] for k in full) / len(full)
    target = avg * CUSHION_MONTHS
    share = min(balance / target, Decimal(1)) * 100
    months_cov = f"{balance / avg:.1f}".replace(".", ",")
    return f"""
  <section class="card">
    <div class="q">Додатково</div>
    <h2>Подушка безпеки</h2>
    <div class="headline">{zl(balance)}</div>
    <div class="sub">накопичено на кінець місяця · ціль {zl(target)}</div>
    <div class="htrack big" title="{pct(balance, target)} цілі"><span class="hbar" style="width:{share:.1f}%"></span></div>
    <p class="conclusion">Це {pct(balance, target)} цілі: грошей вистачить на {months_cov} міс. обовʼязкових витрат
    (у середньому {zl(avg)} на місяць), а ціль — {CUSHION_MONTHS}.</p>
  </section>"""


# ---------- Сторінка ----------

STYLE = """
:root {
  --surface: #fcfcfb; --card: #ffffff; --border: #e4e3df; --track: #efeeea;
  --text: #0b0b0b; --text-2: #52514e;
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --muted-bar: #b7d3f6;
  --badge-bg: #fdf0d5; --badge-text: #7a5200;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface: #1a1a19; --card: #232321; --border: #3a3936; --track: #2e2d2b;
    --text: #f0efec; --text-2: #c3c2b7;
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --muted-bar: #184f95;
    --badge-bg: #4a3600; --badge-text: #ffd27a;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface); color: var(--text);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 24px; margin: 0; }
h2 { font-size: 17px; margin: 2px 0 12px; }
.top { position: sticky; top: 0; background: var(--surface); padding: 12px 0; z-index: 1;
  border-bottom: 1px solid var(--border); }
.tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.tabs button { font: inherit; padding: 6px 14px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--border); background: var(--card); color: var(--text); }
.tabs button[aria-selected="true"] { background: var(--s1); border-color: var(--s1); color: #fff; }
.tabs small { opacity: .8; }
.badge { display: inline-block; margin-top: 16px; font-size: 13px; font-weight: 600;
  background: var(--badge-bg); color: var(--badge-text); padding: 3px 10px; border-radius: 999px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-top: 16px; }
.q { font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: var(--text-2); }
.headline { font-size: 34px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1.1; }
.sub { color: var(--text-2); margin-bottom: 16px; }
.conclusion { margin: 16px 0 0; padding-top: 12px; border-top: 1px solid var(--border); }
.note { font-size: 12px; color: var(--text-2); margin-top: 4px; }
.hchart { display: grid; gap: 6px; }
.hrow { display: grid; grid-template-columns: 120px 1fr 100px; gap: 10px; align-items: center; }
.hlabel { font-size: 14px; }
.hval { text-align: right; font-size: 14px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.htrack { display: block; height: 10px; background: var(--track); border-radius: 4px; overflow: hidden; }
.htrack.big { height: 14px; }
.hbar { display: block; height: 100%; background: var(--s1); border-radius: 4px; }
.cols { display: flex; gap: 16px; }
.col { flex: 1; max-width: 90px; display: flex; flex-direction: column; align-items: center; gap: 4px; }
.colarea { display: flex; align-items: flex-end; width: 100%; height: 120px; }
.colbar { display: block; width: 100%; background: var(--muted-bar); border-radius: 4px 4px 0 0; min-height: 2px; }
.col.current .colbar { background: var(--s1); }
.colval { font-size: 13px; color: var(--text-2); font-variant-numeric: tabular-nums; }
.col.current .colval { color: var(--text); font-weight: 600; }
.collabel { font-size: 13px; color: var(--text-2); }
.stack { display: flex; gap: 2px; height: 24px; border-radius: 4px; overflow: hidden; }
.seg { display: block; height: 100%; }
.s1 { background: var(--s1); } .s2 { background: var(--s2); } .s3 { background: var(--s3); }
.legend { display: grid; gap: 4px; margin-top: 10px; }
.lg { display: flex; align-items: center; font-size: 14px; }
.lgv { margin-left: auto; font-variant-numeric: tabular-nums; color: var(--text-2); }
.key { width: 10px; height: 10px; border-radius: 2px; margin-right: 8px; }
.nav { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; font-size: 14px; margin-bottom: 8px; }
.nav a { color: var(--s1); text-decoration: none; }
.people { display: inline-flex; gap: 4px; }
.people a { padding: 4px 12px; border: 1px solid var(--border); border-radius: 999px; color: var(--text); }
.people a.on { background: var(--text); color: var(--surface); border-color: var(--text); }
.logout { margin-left: auto; color: var(--text-2) !important; }
@media (max-width: 480px) {
  .hrow { grid-template-columns: 90px 1fr 84px; }
  .headline { font-size: 28px; }
}
"""

SCRIPT = """
const buttons = document.querySelectorAll('.tabs button');
function show(id) {
  document.querySelectorAll('.month').forEach(s => s.hidden = s.id !== id);
  buttons.forEach(b => b.setAttribute('aria-selected', b.dataset.target === id));
  history.replaceState(null, '', '#' + id);
}
buttons.forEach(b => b.addEventListener('click', () => show(b.dataset.target)));
const start = location.hash.slice(1);
show(document.getElementById(start) ? start : buttons[buttons.length - 1].dataset.target);
"""


LOGOUT = '<a class="logout" href="{prefix}/logout">Вийти</a>'


def render(slug, prefix="", ledger=LEDGER, with_nav=True):
    """HTML-сторінка звіту для людини slug ('olya' або 'marko')."""
    person = PEOPLE[slug]
    entries, errors, options = loader.load_file(str(ledger))
    if errors:
        return (f"<p>У обліку {len(errors)} помилок, звіт не показано. "
                f"Запустіть <code>python check.py</code>.</p>")

    d = collect(entries, options, person)
    last = d["last_date"]
    tabs, sections = [], []
    for key in d["months"]:
        mid = f"m{key[0]}-{key[1]:02d}"
        latest = key == d["months"][-1]  # видно одразу, навіть без JavaScript
        complete = is_complete(key, last)
        note = "" if complete else f" <small>до {last:%d.%m}</small>"
        tabs.append(f'<button type="button" role="tab" data-target="{mid}" '
                    f'aria-selected="{str(latest).lower()}">{month_label(key)}{note}</button>')
        badge = "" if complete else f'<div class="badge">Місяць неповний: дані до {last:%d.%m.%Y}</div>'
        sections.append(
            f'<div class="month" id="{mid}"{"" if latest else " hidden"}>{badge}'
            f'{block_categories(d, key)}{block_savings(d, key)}{block_groups(d, key)}{block_manual(d, key)}{block_cushion(d, key)}</div>'
        )

    nav = ""
    if with_nav:
        people = "".join(
            f'<a href="{prefix}/?who={s_}" class="{"on" if s_ == slug else ""}">{p}</a>'
            for s_, p in PEOPLE.items())
        nav = (f'<nav class="nav"><span class="people">{people}</span>'
               f'<a href="{prefix}/fava/">Додати витрату (Fava)</a>'
               f'{LOGOUT.format(prefix=prefix)}</nav>')

    return f"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Фінанси: {person}</title>
<style>{STYLE}</style>
</head>
<body>
<main>
  <div class="top">
    {nav}
    <h1>Фінанси: {person}</h1>
    <div class="tabs" role="tablist">{"".join(tabs)}</div>
  </div>
  {"".join(sections)}
</main>
<script>{SCRIPT}</script>
</body>
</html>
"""


def main():
    slug = sys.argv[1] if len(sys.argv) > 1 else "olya"
    OUTPUT.write_text(render(slug, with_nav=False), encoding="utf-8")
    print(f"Звіт ({PEOPLE[slug]}) записано в {OUTPUT.name}")


if __name__ == "__main__":
    main()
