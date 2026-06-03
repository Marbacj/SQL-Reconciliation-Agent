"""生成生产级模拟数据 — 直播电商对账扩展版

表结构 (5张表):
  - live_sessions:   直播场次主表 (200条)
  - live_gmv:        直播GMV记录 (200条)
  - order_amount:    订单金额记录 (~220条，含差异)
  - settlements:     结算记录 (~195条，含差异)
  - refunds:         退款记录 (~80条)
  - commissions:     分佣记录 (~200条，含差异)

故意设计的 8 种差异模式:
  D1 - 金额差异:      GMV与订单金额不一致（精度误差/人工调整）
  D2 - 数据缺失:      GMV表有记录，订单表无对应数据
  D3 - 幽灵订单:      订单表有记录，GMV表无对应直播场次
  D4 - 重复记录:      同一直播场次在订单表出现多条记录
  D5 - 时间差异:      结算日期晚于正常结算周期（T+7超时）
  D6 - 结算金额差:    结算金额 != GMV - 平台佣金（计算错误）
  D7 - 退款未扣除:    存在退款但结算金额未扣除
  D8 - 分佣比例异常:  分佣金额超出正常比例范围（>30% GMV）

用法:
    python data/generate_production_mock.py
    python data/generate_production_mock.py --db data/production_mock.db
"""

import sqlite3
import os
import random
import sys
import argparse
from datetime import date, timedelta

random.seed(2024)

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "production_mock.db")

# ==================== 基础配置 ====================

PLATFORMS = ['抖音', '快手', '淘宝直播', '视频号', '小红书']
CATEGORIES = ['美妆护肤', '服装服饰', '数码3C', '家居家电', '食品零食', '母婴宠物', '运动户外', '珠宝配饰']
ANCHORS = [
    ('李佳琦', '抖音', 0.15), ('薇娅', '抖音', 0.12), ('辛巴', '快手', 0.18),
    ('散打哥', '快手', 0.20), ('雪梨', '淘宝直播', 0.10), ('张大奕', '淘宝直播', 0.12),
    ('东方甄选', '抖音', 0.08), ('罗永浩', '抖音', 0.10), ('刘畊宏', '抖音', 0.08),
    ('冯提莫', '快手', 0.15), ('小伊伊', '快手', 0.12), ('宠物达人王哥', '抖音', 0.10),
    ('乡村小乔', '抖音', 0.08), ('茶颜阿涛', '淘宝直播', 0.10), ('虎哥说车', '抖音', 0.10),
    ('房琪kiki', '视频号', 0.12), ('樊登读书', '视频号', 0.08), ('奥利给大叔', '快手', 0.15),
    ('陈赫大厨', '抖音', 0.10), ('美食探店达人', '小红书', 0.12),
]

PAYMENT_CHANNELS = ['支付宝', '微信支付', '银行卡', '花呗', '京东白条']
SETTLEMENT_STATUS = ['pending', 'settled', 'disputed', 'failed']
REFUND_REASONS = ['商品质量问题', '发错货', '买家主动退款', '超时未发货', '价格异议', '重复下单']

BASE_DATE = date(2026, 4, 1)  # 从4月1日开始，覆盖两个月


def rand_date(offset_days_min=0, offset_days_max=60):
    return BASE_DATE + timedelta(days=random.randint(offset_days_min, offset_days_max))


def generate(db_path: str):
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # ==================== 1. live_sessions 主表 ====================
    c.execute('''CREATE TABLE live_sessions (
        session_id   INTEGER PRIMARY KEY,
        anchor_name  TEXT NOT NULL,
        platform     TEXT NOT NULL,
        category     TEXT NOT NULL,
        live_date    DATE NOT NULL,
        start_hour   INTEGER,
        duration_min INTEGER,
        viewer_count INTEGER,
        status       TEXT DEFAULT 'completed'
    )''')

    sessions = []
    for i in range(1, 201):
        anchor, platform, _ = random.choice(ANCHORS)
        category = random.choice(CATEGORIES)
        live_date = rand_date(0, 59)
        start_hour = random.randint(18, 23)
        duration = random.randint(60, 300)
        viewers = random.randint(5000, 500000)
        sessions.append((i, anchor, platform, category, str(live_date), start_hour, duration, viewers, 'completed'))

    c.executemany('INSERT INTO live_sessions VALUES (?,?,?,?,?,?,?,?,?)', sessions)
    session_ids = [s[0] for s in sessions]

    # ==================== 2. live_gmv 表 ====================
    c.execute('''CREATE TABLE live_gmv (
        id           INTEGER PRIMARY KEY,
        session_id   INTEGER NOT NULL,
        gmv          DECIMAL(14,2) NOT NULL,
        paid_gmv     DECIMAL(14,2),
        order_count  INTEGER,
        report_time  DATETIME,
        FOREIGN KEY (session_id) REFERENCES live_sessions(session_id)
    )''')

    # GMV数据 = 200条（与session一一对应）
    gmv_map = {}  # session_id -> gmv
    gmv_rows = []
    for idx, sid in enumerate(session_ids):
        session = sessions[idx]
        viewers = session[7]
        # GMV和观看人数正相关，加随机波动
        base_gmv = viewers * random.uniform(0.5, 3.0)
        gmv = round(base_gmv, 2)
        paid_gmv = round(gmv * random.uniform(0.85, 0.98), 2)
        order_cnt = random.randint(50, max(51, int(gmv / 200)))
        report_time = f"{session[4]} {session[5]:02d}:{random.randint(0,59):02d}:00"
        gmv_map[sid] = gmv
        gmv_rows.append((idx + 1, sid, gmv, paid_gmv, order_cnt, report_time))

    c.executemany('INSERT INTO live_gmv VALUES (?,?,?,?,?,?)', gmv_rows)

    # ==================== 3. order_amount 表 ====================
    c.execute('''CREATE TABLE order_amount (
        id             INTEGER PRIMARY KEY,
        order_no       TEXT NOT NULL UNIQUE,
        session_id     INTEGER,
        total_amount   DECIMAL(14,2) NOT NULL,
        refund_amount  DECIMAL(14,2) DEFAULT 0,
        net_amount     DECIMAL(14,2),
        order_date     DATE NOT NULL,
        pay_channel    TEXT,
        order_status   TEXT DEFAULT 'completed'
    )''')

    # 差异计划
    # D1金额差异: session_id 11,22,33,44,55 (GMV vs order偏差>5%)
    D1_sessions = random.sample(session_ids[:100], 5)
    # D2缺失: session_id某些在GMV有但order无
    D2_missing = random.sample(session_ids[100:150], 4)
    # D3幽灵订单: 不属于任何session的order
    GHOST_COUNT = 3
    # D4重复: 这些session会有2条order记录
    D4_dup = random.sample(session_ids[150:180], 3)

    order_rows = []
    oid = 1
    for sid in session_ids:
        if sid in D2_missing:
            continue  # 故意不生成订单

        gmv = gmv_map[sid]
        session = sessions[sid - 1]

        if sid in D1_sessions:
            # D1: 金额明显偏差（-8% ~ +12%）
            ratio = random.choice([random.uniform(0.88, 0.93), random.uniform(1.07, 1.12)])
            amount = round(gmv * ratio, 2)
        else:
            # 正常: 订单金额 ≈ GMV ±2%
            amount = round(gmv * random.uniform(0.98, 1.02), 2)

        refund_amt = 0.0
        net = round(amount - refund_amt, 2)
        order_date = session[4]
        channel = random.choice(PAYMENT_CHANNELS)

        order_rows.append((oid, f'ORD{100000+oid:06d}', sid, amount, refund_amt, net, order_date, channel, 'completed'))
        oid += 1

        if sid in D4_dup:
            # D4: 重复插入一条（金额相同，不同order_no）
            order_rows.append((oid, f'ORD{100000+oid:06d}', sid, amount, refund_amt, net, order_date, channel, 'completed'))
            oid += 1

    # D3: 幽灵订单（session_id=NULL或不存在的session）
    for g in range(GHOST_COUNT):
        ghost_amount = round(random.uniform(5000, 80000), 2)
        ghost_date = str(rand_date(0, 59))
        ghost_channel = random.choice(PAYMENT_CHANNELS)
        order_rows.append((oid, f'ORD{100000+oid:06d}', None, ghost_amount, 0, ghost_amount, ghost_date, ghost_channel, 'completed'))
        oid += 1

    c.executemany('INSERT INTO order_amount VALUES (?,?,?,?,?,?,?,?,?)', order_rows)
    # 构建 order_amount 映射: session_id -> [total_amount]
    order_map = {}
    for row in order_rows:
        sid = row[2]
        if sid:
            order_map.setdefault(sid, []).append(row[3])

    # ==================== 4. settlements 结算表 ====================
    c.execute('''CREATE TABLE settlements (
        id               INTEGER PRIMARY KEY,
        session_id       INTEGER NOT NULL,
        settle_amount    DECIMAL(14,2),
        platform_fee     DECIMAL(14,2),
        settle_date      DATE,
        expected_date    DATE,
        status           TEXT DEFAULT 'settled',
        note             TEXT,
        FOREIGN KEY (session_id) REFERENCES live_sessions(session_id)
    )''')

    # D5: 时间差异（超期结算）session
    D5_late = random.sample([s for s in session_ids if s not in D2_missing], 6)
    # D6: 结算金额错误
    D6_wrong = random.sample([s for s in session_ids if s not in D2_missing and s not in D5_late], 5)
    # D7: 有退款但结算未扣除（退款表会对应这些session）
    D7_no_deduct = random.sample([s for s in session_ids if s not in D2_missing and s not in D5_late and s not in D6_wrong], 4)

    settle_rows = []
    sid_settle_map = {}  # session_id -> settle_amount
    for idx, sid in enumerate(session_ids):
        if sid in D2_missing:
            continue
        gmv = gmv_map[sid]
        session = sessions[sid - 1]
        live_date = date.fromisoformat(session[4])

        anchor_name = session[1]
        # 找该主播的佣金比例
        commission_rate = next((a[2] for a in ANCHORS if a[0] == anchor_name), 0.10)
        platform_fee_rate = random.uniform(0.03, 0.06)
        platform_fee = round(gmv * platform_fee_rate, 2)

        if sid in D6_wrong:
            # D6: 结算金额计算错误（平台费用少扣）
            wrong_fee = round(platform_fee * random.uniform(0.3, 0.6), 2)
            settle_amt = round(gmv - wrong_fee, 2)
            note = 'D6:结算金额异常'
        elif sid in D7_no_deduct:
            # D7: 退款未扣除（正常金额，但应该减去退款）
            settle_amt = round(gmv - platform_fee, 2)
            note = 'D7:退款未扣除'
        else:
            settle_amt = round(gmv - platform_fee, 2)
            note = None

        expected_date = live_date + timedelta(days=7)

        if sid in D5_late:
            # D5: 超期结算（延迟12~30天）
            actual_settle = live_date + timedelta(days=random.randint(15, 35))
            status = 'settled'
            note = (note + ' D5:超期结算') if note else 'D5:超期结算'
        else:
            actual_settle = live_date + timedelta(days=random.randint(5, 8))
            status = 'settled'

        sid_settle_map[sid] = settle_amt
        settle_rows.append((idx + 1, sid, settle_amt, platform_fee, str(actual_settle), str(expected_date), status, note))

    c.executemany('INSERT INTO settlements VALUES (?,?,?,?,?,?,?,?)', settle_rows)

    # ==================== 5. refunds 退款表 ====================
    c.execute('''CREATE TABLE refunds (
        id             INTEGER PRIMARY KEY,
        order_no       TEXT NOT NULL,
        session_id     INTEGER,
        refund_amount  DECIMAL(12,2) NOT NULL,
        refund_date    DATE,
        refund_reason  TEXT,
        status         TEXT DEFAULT 'success'
    )''')

    refund_rows = []
    rid = 1

    # D7对应的session一定有退款
    d7_sessions_order = {row[2]: row[1] for row in order_rows if row[2] in D7_no_deduct}

    # 给约40个session生成退款记录（约20%比例）
    refund_sessions = D7_no_deduct + random.sample(
        [s for s in session_ids if s not in D2_missing and s not in D7_no_deduct],
        min(36, len([s for s in session_ids if s not in D2_missing and s not in D7_no_deduct]))
    )

    for sid in refund_sessions:
        gmv = gmv_map[sid]
        session = sessions[sid - 1]
        order_no = next((row[1] for row in order_rows if row[2] == sid), f'ORD_REF_{sid}')
        refund_amt = round(gmv * random.uniform(0.03, 0.15), 2)
        refund_date = date.fromisoformat(session[4]) + timedelta(days=random.randint(1, 14))
        reason = random.choice(REFUND_REASONS)
        refund_rows.append((rid, order_no, sid, refund_amt, str(refund_date), reason, 'success'))
        rid += 1

    c.executemany('INSERT INTO refunds VALUES (?,?,?,?,?,?,?)', refund_rows)

    # ==================== 6. commissions 分佣表 ====================
    c.execute('''CREATE TABLE commissions (
        id               INTEGER PRIMARY KEY,
        session_id       INTEGER NOT NULL,
        anchor_name      TEXT,
        commission_rate  DECIMAL(5,4),
        commission_amount DECIMAL(14,2),
        base_gmv         DECIMAL(14,2),
        pay_date         DATE,
        status           TEXT DEFAULT 'paid',
        FOREIGN KEY (session_id) REFERENCES live_sessions(session_id)
    )''')

    # D8: 分佣比例异常
    D8_abnormal = random.sample([s for s in session_ids if s not in D2_missing], 5)

    commission_rows = []
    cid = 1
    for sid in session_ids:
        if sid in D2_missing:
            continue
        gmv = gmv_map[sid]
        session = sessions[sid - 1]
        anchor = session[1]
        live_date = date.fromisoformat(session[4])

        anchor_rate = next((a[2] for a in ANCHORS if a[0] == anchor), 0.10)

        if sid in D8_abnormal:
            # D8: 分佣超出正常范围
            rate = round(random.uniform(0.32, 0.45), 4)
            note_status = 'disputed'
        else:
            rate = round(anchor_rate + random.uniform(-0.02, 0.02), 4)
            note_status = 'paid'

        commission_amt = round(gmv * rate, 2)
        pay_date = live_date + timedelta(days=random.randint(8, 15))

        commission_rows.append((cid, sid, anchor, rate, commission_amt, gmv, str(pay_date), note_status))
        cid += 1

    c.executemany('INSERT INTO commissions VALUES (?,?,?,?,?,?,?,?)', commission_rows)

    conn.commit()

    # ==================== 验证输出 ====================
    print("=" * 60)
    print("✅ 生产级模拟数据库已生成")
    print(f"   路径: {db_path}")
    print("=" * 60)

    tables = ['live_sessions', 'live_gmv', 'order_amount', 'settlements', 'refunds', 'commissions']
    for t in tables:
        c.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"  {t:20s}: {c.fetchone()[0]:>5} 行")

    print("\n🎯 故意设计的差异数据:")
    print(f"  D1 金额差异场次:   {D1_sessions}")
    print(f"  D2 GMV有/订单缺:   {D2_missing}")
    print(f"  D3 幽灵订单:       {GHOST_COUNT} 条 (session_id=NULL)")
    print(f"  D4 重复订单:       {D4_dup}")
    print(f"  D5 超期结算:       {D5_late}")
    print(f"  D6 结算金额错误:   {D6_wrong}")
    print(f"  D7 退款未扣除:     {D7_no_deduct}")
    print(f"  D8 分佣比例异常:   {D8_abnormal}")

    print("\n📊 快速核对查询:")
    # D1金额差
    c.execute("""
        SELECT g.session_id,
               g.gmv AS gmv,
               SUM(o.total_amount) AS order_total,
               ROUND(SUM(o.total_amount) - g.gmv, 2) AS diff
        FROM live_gmv g
        JOIN order_amount o ON g.session_id = o.session_id
        WHERE g.session_id IN ({})
        GROUP BY g.session_id
    """.format(','.join(str(s) for s in D1_sessions)))
    rows = c.fetchall()
    if rows:
        print("\n  D1 金额差异:")
        for r in rows:
            print(f"    session={r[0]}: GMV={r[1]:.0f}, Order={r[2]:.0f}, Diff={r[3]:.0f}")

    # D8分佣异常
    c.execute("""
        SELECT session_id, anchor_name, commission_rate, commission_amount, base_gmv,
               ROUND(commission_amount/base_gmv*100, 1) AS actual_pct
        FROM commissions WHERE session_id IN ({})
    """.format(','.join(str(s) for s in D8_abnormal)))
    rows = c.fetchall()
    if rows:
        print("\n  D8 分佣比例异常:")
        for r in rows:
            print(f"    session={r[0]}, {r[1]}: commission_rate={r[2]:.2%} (是正常上限30%的{r[2]/0.30:.1f}倍)")

    conn.close()
    print("\n✅ 数据生成完成！")
    print("\n使用建议:")
    print("  - Agent 可通过 sql_schema 查看任意表结构")
    print("  - 对账问题: 比较 live_gmv vs order_amount vs settlements")
    print("  - 退款核查: refunds 与 settlements 的 settle_amount 是否对应")
    print("  - 分佣审计: commissions.commission_rate 是否在正常区间")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='生成生产级直播电商对账模拟数据')
    parser.add_argument('--db', default=DEFAULT_DB, help='输出数据库路径')
    args = parser.parse_args()
    generate(args.db)
