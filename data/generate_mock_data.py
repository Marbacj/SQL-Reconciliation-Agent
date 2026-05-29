"""生成对账 Demo 模拟数据

用法:
    python data/generate_mock_data.py

生成两张表:
  - live_gmv: 直播GMV记录（26条，含 live_id=312 差异行）
  - order_amount: 订单金额记录（27条，含 3 处故意差异）

故意设计的 3 处差异:
  - live_id=105: GMV=12500, Order=11800, diff=+700（GMV虚高）
  - live_id=208: 仅订单表有3500元, GMV表无记录（数据缺失）
  - live_id=312: GMV=8900, Order=9200, diff=-300（订单虚高）
"""

import sqlite3
import os
import random
import sys

random.seed(42)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock_reconciliation.db")


def generate():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # ==================== live_gmv 表 ====================
    c.execute('''CREATE TABLE live_gmv (
        id INTEGER PRIMARY KEY, live_id INTEGER NOT NULL, live_title TEXT,
        gmv DECIMAL(12,2), live_date DATE, platform TEXT, anchor_name TEXT)''')

    live_data = [
        (1, 101, '618大促预热专场', 15800, '2026-05-27', '抖音', '李佳琦'),
        (2, 102, '美妆护肤专场', 23400, '2026-05-27', '抖音', '薇娅'),
        (3, 103, '数码3C专场', 45600, '2026-05-27', '快手', '辛巴'),
        (4, 104, '服装搭配专场', 12300, '2026-05-27', '抖音', '张大奕'),
        (5, 105, '零食专场', 12500, '2026-05-27', '抖音', '烈儿'),
        (6, 106, '家居好物专场', 8900, '2026-05-27', '快手', '散打哥'),
        (7, 107, '珠宝首饰专场', 67800, '2026-05-27', '淘宝', '雪梨'),
        (8, 108, '运动户外专场', 15600, '2026-05-27', '抖音', '刘畊宏'),
        (9, 109, '图书文具专场', 3200, '2026-05-27', '抖音', '樊登'),
        (10, 110, '母婴用品专场', 18900, '2026-05-27', '快手', '小伊伊'),
        (11, 111, '生鲜水果专场', 9800, '2026-05-27', '抖音', '东方甄选'),
        (12, 112, '汽车用品专场', 34500, '2026-05-27', '抖音', '虎哥'),
        (13, 113, '宠物用品专场', 5600, '2026-05-27', '快手', '宠物达人'),
        (14, 114, '跨境美妆专场', 27800, '2026-05-27', '淘宝', '虫虫'),
        (15, 115, '地方特产专场', 11200, '2026-05-27', '抖音', '乡村小乔'),
        (16, 116, '潮牌服饰专场', 22300, '2026-05-27', '抖音', '陈赫'),
        (17, 117, '家电专场', 56700, '2026-05-27', '快手', '二驴'),
        (18, 118, '个护清洁专场', 7800, '2026-05-27', '抖音', '多余和毛毛姐'),
        (19, 119, '酒水专场', 42100, '2026-05-27', '抖音', '罗永浩'),
        (20, 120, '玩具专场', 4500, '2026-05-27', '快手', '玩具达人'),
        (21, 121, '医美专场', 89000, '2026-05-27', '淘宝', '韩安冉'),
        (22, 122, '办公用品专场', 6700, '2026-05-27', '抖音', '秋叶大叔'),
        (23, 123, '乐器专场', 13400, '2026-05-27', '快手', '冯提莫'),
        (24, 124, '户外露营专场', 19800, '2026-05-27', '抖音', '房琪'),
        (25, 125, '茶叶专场', 8700, '2026-05-27', '抖音', '茶颜悦色'),
        (26, 312, '虚拟直播间312', 8900, '2026-05-27', '抖音', '测试主播'),
    ]
    c.executemany('INSERT INTO live_gmv VALUES (?,?,?,?,?,?,?)', live_data)

    # ==================== order_amount 表 ====================
    c.execute('''CREATE TABLE order_amount (
        id INTEGER PRIMARY KEY, order_id TEXT NOT NULL, live_id INTEGER NOT NULL,
        total_amount DECIMAL(12,2), order_date DATE, order_status TEXT)''')

    # 3 处故意差异
    DISCREPANCIES = {
        105: 11800.00,  # GMV=12500 → diff=+700
        312: 9200.00,   # GMV=8900 → diff=-300
    }

    order_data = []
    oid = 1
    for row in live_data:
        live_id = row[1]
        gmv = row[3]
        if live_id in DISCREPANCIES:
            amount = DISCREPANCIES[live_id]
        else:
            amount = round(gmv * (1 + random.uniform(-0.03, 0.03)), 2)
        order_data.append((oid, f'ORD{10000+oid}', live_id, amount, '2026-05-27', 'completed'))
        oid += 1

    # live_id=208: 仅订单表存在，GMV表无记录（数据缺失）
    order_data.append((oid, 'ORD20001', 208, 3500.00, '2026-05-27', 'completed'))

    c.executemany('INSERT INTO order_amount VALUES (?,?,?,?,?,?)', order_data)
    conn.commit()

    # 验证
    c.execute("SELECT COUNT(*) FROM live_gmv")
    print(f"✅ live_gmv: {c.fetchone()[0]} rows")
    c.execute("SELECT COUNT(*) FROM order_amount")
    print(f"✅ order_amount: {c.fetchone()[0]} rows")

    c.execute("""
        SELECT l.live_id, l.gmv AS gmv, COALESCE(SUM(o.total_amount), 0) AS orders,
               l.gmv - COALESCE(SUM(o.total_amount), 0) AS diff
        FROM live_gmv l LEFT JOIN order_amount o ON l.live_id = o.live_id
        WHERE l.live_id IN (105, 208, 312) GROUP BY l.live_id
    """)
    print("\n🎯 故意差异:")
    for row in c.fetchall():
        print(f"  live_id={row[0]}: GMV={row[1]:.0f}, Order={row[2]:.0f}, Diff={row[3]:.0f}")

    c.execute("SELECT live_id, SUM(total_amount) FROM order_amount WHERE live_id=208 GROUP BY live_id")
    r = c.fetchone()
    if r:
        print(f"  live_id=208: ⚠️ 仅订单表存在 (Order={r[1]:.0f})，GMV表无记录")

    conn.close()
    print(f"\n✅ 数据库已生成: {DB_PATH}")


if __name__ == "__main__":
    generate()
