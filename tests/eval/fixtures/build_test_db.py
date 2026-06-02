"""测试数据生成器：在 SQLite 中构建 orders/refunds/payments 三张表 + 7 天种子数据。

使用：
    python -m tests.eval.fixtures.build_test_db --db data/eval_data.sqlite --seed 42
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS orders (
    id          TEXT PRIMARY KEY,
    user_id     TEXT,
    amount      REAL NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refunds (
    id          TEXT PRIMARY KEY,
    order_id    TEXT,
    amount      REAL NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id          TEXT PRIMARY KEY,
    order_id    TEXT,
    amount      REAL NOT NULL,
    channel     TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_payments_oid ON payments(order_id);
CREATE INDEX IF NOT EXISTS idx_refunds_oid ON refunds(order_id);
"""


def build(db_path: str, seed: int = 42, days: int = 14, daily_orders: int = 60) -> dict:
    """生成确定性的测试数据集。

    返回统计信息：{orders, payments, refunds, anomalies, ...}。
    """
    Path(os.path.dirname(db_path) or ".").mkdir(parents=True, exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    rng = random.Random(seed)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript(DDL)

    channels = ["wechat", "alipay", "card", "wallet"]
    statuses_order = ["paid", "paid", "paid", "pending", "cancelled"]
    statuses_pay = ["success", "success", "success", "success", "failed"]
    user_pool = [f"U{i:03d}" for i in range(1, 31)]

    today = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)

    orders: list = []
    payments: list = []
    refunds: list = []

    anomalies = {"mismatch": 0, "orphan_refund": 0, "missing_payment": 0, "neg_amount": 0}

    order_seq = 0
    pay_seq = 0
    refund_seq = 0

    for day_offset in range(days, 0, -1):
        day = today - timedelta(days=day_offset - 1)
        # 当天订单数有自然抖动
        n = max(1, int(rng.gauss(daily_orders, 10)))
        for _ in range(n):
            order_seq += 1
            oid = f"O{order_seq:06d}"
            uid = rng.choice(user_pool)
            amt = round(rng.uniform(10, 500), 2)
            # 引入少量负金额异常（5%）
            if rng.random() < 0.05:
                amt = -abs(amt)
                anomalies["neg_amount"] += 1
            ostatus = rng.choice(statuses_order)
            ts = (day + timedelta(seconds=rng.randint(0, 86399))).strftime("%Y-%m-%d %H:%M:%S")
            orders.append((oid, uid, amt, ostatus, ts))

            # 大多数 paid 订单有支付记录；少量缺失 → missing_payment
            if ostatus == "paid":
                if rng.random() < 0.95:
                    pay_seq += 1
                    # 5% 概率金额不一致
                    pay_amt = amt if rng.random() > 0.05 else round(amt + rng.uniform(-2, 2), 2)
                    if pay_amt != amt:
                        anomalies["mismatch"] += 1
                    channel = rng.choice(channels)
                    pstatus = rng.choice(statuses_pay)
                    pts = (day + timedelta(seconds=rng.randint(0, 86399))).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    payments.append((f"P{pay_seq:06d}", oid, pay_amt, channel, pstatus, pts))
                else:
                    anomalies["missing_payment"] += 1

            # 8% 订单产生退款
            if ostatus == "paid" and rng.random() < 0.08:
                refund_seq += 1
                refund_amt = round(min(abs(amt), rng.uniform(5, abs(amt))), 2)
                rts = (day + timedelta(seconds=rng.randint(0, 86399))).strftime("%Y-%m-%d %H:%M:%S")
                refunds.append((f"R{refund_seq:06d}", oid, refund_amt, "success", rts))

        # 每天注入 1 条孤儿退款（找不到 order）
        if day_offset <= 3:
            refund_seq += 1
            refunds.append(
                (
                    f"R{refund_seq:06d}",
                    f"O999999_orphan_{day_offset}",
                    50.0,
                    "success",
                    (day + timedelta(seconds=43200)).strftime("%Y-%m-%d %H:%M:%S"),
                )
            )
            anomalies["orphan_refund"] += 1

    cur.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", orders)
    cur.executemany("INSERT INTO payments VALUES (?,?,?,?,?,?)", payments)
    cur.executemany("INSERT INTO refunds VALUES (?,?,?,?,?)", refunds)
    conn.commit()
    conn.close()

    return {
        "db": db_path,
        "orders": len(orders),
        "payments": len(payments),
        "refunds": len(refunds),
        "anomalies": anomalies,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/eval_data.sqlite")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()

    stat = build(args.db, seed=args.seed, days=args.days)
    print("Test DB built:")
    for k, v in stat.items():
        print(f"  {k}: {v}")
