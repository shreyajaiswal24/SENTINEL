"""Generate sample sales files for the dashboard's "Refine your data" panel.

Each file exercises one check (or all of them). Deterministic (seeded).
Run: python test_uploads/make_samples.py
"""
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

OUT = Path(__file__).parent
rng = random.Random(42)

CATEGORIES = {"Electronics": (40, 900), "Grocery": (2, 40), "Toys": (8, 120),
              "Clothing": (10, 150), "Home": (15, 400)}
REGIONS = ["North", "South", "East", "West"]
FIRST = ["Aarav", "Priya", "Rohan", "Ananya", "Vikram", "Meera", "Kabir", "Isha",
         "Arjun", "Diya", "Sam", "Olivia", "Liam", "Emma", "Noah", "Zara"]
LAST = ["Sharma", "Patel", "Singh", "Iyer", "Khan", "Reddy", "Smith", "Brown", "Jones"]


def make_sales(n: int, start: int = 1) -> pd.DataFrame:
    rows = []
    d0 = date(2026, 9, 1)
    for i in range(start, start + n):
        cat = rng.choice(list(CATEGORIES))
        lo, hi = CATEGORIES[cat]
        qty = rng.randint(1, 10)
        price = round(rng.uniform(lo, hi), 2)
        rows.append({
            "order_id": f"ORD{i:05d}",
            "customer": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
            "product": f"{cat[:4].upper()}-{rng.randint(100, 999)}",
            "category": cat,
            "quantity": qty,
            "unit_price": price,
            "amount": round(qty * price, 2),
            "region": rng.choice(REGIONS),
            "sale_date": (d0 + timedelta(days=rng.randint(0, 29))).isoformat(),
        })
    return pd.DataFrame(rows)


def blank(df, col, frac):
    idx = df.sample(frac=frac, random_state=rng.randint(0, 10**6)).index
    df.loc[idx, col] = None
    return df


def add_dupes(df, n):
    dupes = df.sample(n=n, random_state=7)
    return pd.concat([df, dupes]).sample(frac=1, random_state=8).reset_index(drop=True)


files = {}

# 1. Clean baseline — should come back with no issues.
files["01_clean_sales.csv"] = make_sales(1000)

# 2. Duplicates — 150 exact duplicate rows mixed in.
files["02_duplicate_sales.csv"] = add_dupes(make_sales(1000), 150)

# 3. Nulls — missing order_id / amount (critical -> dropped) + missing region/customer (kept).
df = make_sales(1000)
df = blank(df, "order_id", 0.03); df = blank(df, "amount", 0.04)
df = blank(df, "region", 0.06); df = blank(df, "customer", 0.02)
files["03_null_values_sales.csv"] = df

# 4. Schema drift — amount renamed to total_amount, plus an extra column.
df = make_sales(1000).rename(columns={"amount": "total_amount"})
df["promo_code"] = [rng.choice(["", "DIWALI10", "FEST20", "NEWUSER"]) for _ in range(len(df))]
files["04_schema_drift_sales.csv"] = df

# 5. Missing column — region dropped entirely (flagged, never fabricated).
files["05_missing_column_sales.csv"] = make_sales(1000).drop(columns=["region"])

# 6. Low volume — only 320 rows (< 50% of expected 1000).
files["06_low_volume_sales.csv"] = make_sales(320)

# 7. Everything broken, as Excel — drift + dupes + nulls + missing column + low volume.
df = make_sales(420).rename(columns={"amount": "total_amount"}).drop(columns=["sale_date"])
df = blank(df, "order_id", 0.05); df = blank(df, "category", 0.05)
df = add_dupes(df, 60)
files["07_everything_broken_sales.xlsx"] = df

for name, frame in files.items():
    path = OUT / name
    if name.endswith(".xlsx"):
        frame.to_excel(path, index=False)
    else:
        frame.to_csv(path, index=False)
    print(f"{name:34} {len(frame):>5} rows  {len(frame.columns)} cols")
