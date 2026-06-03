"""生成超大规模多业务模拟数据库 — 生产噪音版

业务线覆盖 (30+ 张表):
  ① 直播电商核心 (6张):  live_sessions, live_gmv, order_amount, settlements, refunds, commissions
  ② 用户体系 (4张):      users, user_profiles, user_tags, user_login_logs
  ③ 商品中心 (4张):      products, product_categories, product_inventory, product_price_history
  ④ 营销活动 (4张):      campaigns, campaign_budgets, ad_spend, coupon_records
  ⑤ 供应链 (4张):        suppliers, purchase_orders, warehouses, logistics_records
  ⑥ 财务中心 (4张):      finance_bills, tax_records, bank_statements, cost_center_allocation
  ⑦ 客服系统 (3张):      complaints, complaint_followups, satisfaction_surveys
  ⑧ 平台运营 (3张):      anchor_contracts, anchor_performance, platform_rules
  ⑨ 风控 (2张):          risk_alerts, blacklist
  ⑩ 系统/审计 (2张):     operation_logs, data_sync_tasks

总计: 36 张表，约 8000+ 行数据

对账核心差异依然植入在直播电商核心6张表中，其余表是真实业务噪音。
"""

import sqlite3
import os
import random
import json
from datetime import date, timedelta, datetime

random.seed(2024)

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "enterprise_mock.db")

# ===== 基础数据常量 =====
PLATFORMS = ['抖音', '快手', '淘宝直播', '视频号', '小红书']
CATEGORIES = ['美妆护肤', '服装服饰', '数码3C', '家居家电', '食品零食', '母婴宠物', '运动户外', '珠宝配饰', '图书教育', '医疗健康']
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
REFUND_REASONS = ['商品质量问题', '发错货', '买家主动退款', '超时未发货', '价格异议', '重复下单']
PROVINCES = ['北京', '上海', '广东', '浙江', '江苏', '四川', '湖北', '湖南', '河南', '山东', '福建', '陕西']
CITIES = {
    '北京': ['北京市'], '上海': ['上海市'], '广东': ['广州', '深圳', '东莞', '佛山'],
    '浙江': ['杭州', '宁波', '温州'], '江苏': ['南京', '苏州', '无锡'],
    '四川': ['成都', '绵阳'], '湖北': ['武汉', '宜昌'], '湖南': ['长沙', '株洲'],
    '河南': ['郑州', '洛阳'], '山东': ['济南', '青岛'], '福建': ['福州', '厦门'],
    '陕西': ['西安', '咸阳'],
}
GENDERS = ['male', 'female', 'unknown']
AD_CHANNELS = ['搜索广告', '信息流', 'KOL合作', '短视频投流', '开屏广告', 'DSP', '联盟广告']
COMPLAINT_TYPES = ['商品质量', '物流问题', '售后服务', '虚假宣传', '账单异常', '平台规则', '主播行为']
RISK_LEVELS = ['low', 'medium', 'high', 'critical']
LOG_ACTIONS = ['login', 'logout', 'query', 'export', 'update', 'delete', 'approve', 'reject']
LOGISTICS_COMPANIES = ['顺丰', '京东快递', '中通', '圆通', '韵达', '极兔', '菜鸟']
WAREHOUSE_NAMES = ['华东仓-上海', '华南仓-广州', '华北仓-北京', '西南仓-成都', '华中仓-武汉']
BANKS = ['工商银行', '建设银行', '农业银行', '中国银行', '招商银行', '平安银行', '浦发银行']
TAX_TYPES = ['增值税', '企业所得税', '个人所得税', '印花税', '城建税']
COST_CENTERS = ['市场部', '技术部', '运营部', '商务部', '客服部', '供应链部', '财务部', '法务部']
SUPPLIER_CATEGORIES = ['美妆原料', '服装面料', '数码配件', '食品原料', '包装材料', '电子元件', '化工原料']

BASE_DATE = date(2026, 4, 1)

def rand_date(min_days=0, max_days=60):
    return BASE_DATE + timedelta(days=random.randint(min_days, max_days))

def rand_dt(min_days=0, max_days=60):
    d = rand_date(min_days, max_days)
    h, m, s = random.randint(0, 23), random.randint(0, 59), random.randint(0, 59)
    return f"{d} {h:02d}:{m:02d}:{s:02d}"

def rand_phone():
    return f"1{random.choice([3,4,5,6,7,8,9])}{random.randint(100000000, 999999999)}"

def rand_id_card():
    return f"{random.randint(100000, 999999)}{random.randint(19700101, 20051231)}{random.randint(1000, 9999)}"

def generate(db_path: str = DEFAULT_DB):
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # ================================================================
    # ① 用户体系
    # ================================================================

    # 1. users
    c.execute('''CREATE TABLE users (
        user_id      INTEGER PRIMARY KEY,
        username     TEXT NOT NULL,
        phone        TEXT,
        email        TEXT,
        register_date DATE,
        user_level   INTEGER DEFAULT 1,
        status       TEXT DEFAULT 'active',
        referrer_id  INTEGER
    )''')
    user_rows = []
    for i in range(1, 501):
        province = random.choice(PROVINCES)
        level = random.choices([1,2,3,4,5], weights=[40,25,15,12,8])[0]
        reg_date = rand_date(-365, 0)  # 过去一年注册
        status = random.choices(['active','inactive','banned'], weights=[85,12,3])[0]
        referrer = random.randint(1, i-1) if i > 1 and random.random() < 0.3 else None
        user_rows.append((i, f'user_{i:04d}', rand_phone(), f'user{i}@example.com',
                          str(reg_date), level, status, referrer))
    c.executemany('INSERT INTO users VALUES (?,?,?,?,?,?,?,?)', user_rows)

    # 2. user_profiles
    c.execute('''CREATE TABLE user_profiles (
        profile_id   INTEGER PRIMARY KEY,
        user_id      INTEGER NOT NULL,
        nickname     TEXT,
        gender       TEXT,
        province     TEXT,
        city         TEXT,
        age          INTEGER,
        vip_expire   DATE,
        total_spend  DECIMAL(14,2) DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )''')
    profile_rows = []
    for i, u in enumerate(user_rows):
        province = random.choice(PROVINCES)
        city = random.choice(CITIES[province])
        age = random.randint(18, 55)
        vip = str(rand_date(0, 365)) if random.random() < 0.4 else None
        spend = round(random.uniform(0, 50000), 2)
        gender = random.choice(GENDERS)
        profile_rows.append((i+1, u[0], f'昵称{i+1}', gender, province, city, age, vip, spend))
    c.executemany('INSERT INTO user_profiles VALUES (?,?,?,?,?,?,?,?,?)', profile_rows)

    # 3. user_tags
    c.execute('''CREATE TABLE user_tags (
        id       INTEGER PRIMARY KEY,
        user_id  INTEGER NOT NULL,
        tag      TEXT,
        score    DECIMAL(5,2),
        created_at DATE
    )''')
    TAGS = ['高价值', '价格敏感', '美妆控', '数码达人', '母婴用户', '运动爱好者', '高复购', '流失风险', '新用户', 'KOC']
    tag_rows = []
    tid = 1
    for u in user_rows[:200]:  # 给200个用户打标签
        tag_count = random.randint(1, 4)
        for tag in random.sample(TAGS, tag_count):
            tag_rows.append((tid, u[0], tag, round(random.uniform(0.6, 1.0), 2), str(rand_date(-30, 0))))
            tid += 1
    c.executemany('INSERT INTO user_tags VALUES (?,?,?,?,?)', tag_rows)

    # 4. user_login_logs
    c.execute('''CREATE TABLE user_login_logs (
        id          INTEGER PRIMARY KEY,
        user_id     INTEGER NOT NULL,
        login_time  DATETIME,
        ip_address  TEXT,
        device_type TEXT,
        app_version TEXT,
        success     INTEGER DEFAULT 1
    )''')
    login_rows = []
    lid = 1
    devices = ['iOS', 'Android', 'PC_Web', 'iPad', 'HarmonyOS']
    for u in random.sample(user_rows, 300):
        for _ in range(random.randint(1, 8)):
            ip = f"192.168.{random.randint(1,255)}.{random.randint(1,255)}"
            login_rows.append((lid, u[0], rand_dt(-30, 0), ip,
                               random.choice(devices), f"v{random.randint(8,12)}.{random.randint(0,9)}.0",
                               1 if random.random() > 0.05 else 0))
            lid += 1
    c.executemany('INSERT INTO user_login_logs VALUES (?,?,?,?,?,?,?)', login_rows)

    # ================================================================
    # ② 商品中心
    # ================================================================

    # 5. product_categories
    c.execute('''CREATE TABLE product_categories (
        category_id   INTEGER PRIMARY KEY,
        name          TEXT NOT NULL,
        parent_id     INTEGER,
        level         INTEGER DEFAULT 1,
        sort_order    INTEGER
    )''')
    cat_rows = [(i+1, cat, None, 1, i+1) for i, cat in enumerate(CATEGORIES)]
    sub_cats = ['口红', '粉底', '眼影', '防晒', 'T恤', '裤子', '外套', '连衣裙', '手机', '耳机',
                '平板', '充电器', '沙发', '床垫', '厨具', '灯具', '坚果', '零食', '饮料', '茶叶']
    for i, sub in enumerate(sub_cats):
        cat_rows.append((len(CATEGORIES)+i+1, sub, random.randint(1, len(CATEGORIES)), 2, i+1))
    c.executemany('INSERT INTO product_categories VALUES (?,?,?,?,?)', cat_rows)

    # 6. products
    c.execute('''CREATE TABLE products (
        product_id    INTEGER PRIMARY KEY,
        name          TEXT NOT NULL,
        category_id   INTEGER,
        brand         TEXT,
        cost_price    DECIMAL(12,2),
        sale_price    DECIMAL(12,2),
        status        TEXT DEFAULT 'active',
        create_time   DATE,
        supplier_id   INTEGER
    )''')
    BRANDS = ['华为', '小米', 'OPPO', '花西子', '完美日记', '优衣库', '耐克', '阿迪达斯', '三只松鼠', '元气森林', '自有品牌']
    prod_rows = []
    for i in range(1, 201):
        cat_id = random.randint(1, len(cat_rows))
        brand = random.choice(BRANDS)
        cost = round(random.uniform(10, 5000), 2)
        sale = round(cost * random.uniform(1.3, 3.5), 2)
        status = random.choices(['active','inactive','discontinued'], weights=[80,15,5])[0]
        prod_rows.append((i, f'商品{i:04d}-{brand}', cat_id, brand, cost, sale, status,
                          str(rand_date(-180, 0)), random.randint(1, 30)))
    c.executemany('INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?)', prod_rows)

    # 7. product_inventory
    c.execute('''CREATE TABLE product_inventory (
        id            INTEGER PRIMARY KEY,
        product_id    INTEGER NOT NULL,
        warehouse_id  INTEGER,
        quantity      INTEGER DEFAULT 0,
        reserved_qty  INTEGER DEFAULT 0,
        update_time   DATETIME,
        FOREIGN KEY (product_id) REFERENCES products(product_id)
    )''')
    inv_rows = []
    for i, p in enumerate(prod_rows):
        for wh in random.sample(range(1, 6), random.randint(1, 3)):
            qty = random.randint(0, 5000)
            inv_rows.append((len(inv_rows)+1, p[0], wh, qty, random.randint(0, min(qty, 100)), rand_dt(-7, 0)))
    c.executemany('INSERT INTO product_inventory VALUES (?,?,?,?,?,?)', inv_rows)

    # 8. product_price_history
    c.execute('''CREATE TABLE product_price_history (
        id            INTEGER PRIMARY KEY,
        product_id    INTEGER NOT NULL,
        old_price     DECIMAL(12,2),
        new_price     DECIMAL(12,2),
        change_reason TEXT,
        operator      TEXT,
        change_time   DATETIME
    )''')
    PRICE_REASONS = ['大促调价', '成本调整', '竞品跟价', '库存清仓', '节假日活动', '品牌授权调整']
    ph_rows = []
    for p in random.sample(prod_rows, 80):
        for _ in range(random.randint(1, 5)):
            old_p = round(random.uniform(20, 5000), 2)
            new_p = round(old_p * random.uniform(0.7, 1.3), 2)
            ph_rows.append((len(ph_rows)+1, p[0], old_p, new_p,
                            random.choice(PRICE_REASONS), f'operator_{random.randint(1,20)}', rand_dt(-60, 0)))
    c.executemany('INSERT INTO product_price_history VALUES (?,?,?,?,?,?,?)', ph_rows)

    # ================================================================
    # ③ 营销活动
    # ================================================================

    # 9. campaigns
    c.execute('''CREATE TABLE campaigns (
        campaign_id   INTEGER PRIMARY KEY,
        name          TEXT NOT NULL,
        type          TEXT,
        platform      TEXT,
        start_date    DATE,
        end_date      DATE,
        total_budget  DECIMAL(14,2),
        status        TEXT DEFAULT 'active'
    )''')
    CAMP_TYPES = ['618大促', '双十一', '品牌日', 'KOL合作', '开屏投放', 'DSP精投', '节日营销']
    camp_rows = []
    for i in range(1, 41):
        start = rand_date(0, 40)
        end = start + timedelta(days=random.randint(1, 30))
        budget = round(random.uniform(10000, 2000000), 2)
        status = random.choices(['planning','active','paused','ended'], weights=[10,50,15,25])[0]
        camp_rows.append((i, f'活动{i:02d}-{random.choice(CAMP_TYPES)}', random.choice(CAMP_TYPES),
                          random.choice(PLATFORMS), str(start), str(end), budget, status))
    c.executemany('INSERT INTO campaigns VALUES (?,?,?,?,?,?,?,?)', camp_rows)

    # 10. campaign_budgets
    c.execute('''CREATE TABLE campaign_budgets (
        id            INTEGER PRIMARY KEY,
        campaign_id   INTEGER NOT NULL,
        channel       TEXT,
        allocated     DECIMAL(14,2),
        spent         DECIMAL(14,2) DEFAULT 0,
        remaining     DECIMAL(14,2),
        update_time   DATETIME
    )''')
    cb_rows = []
    for camp in camp_rows:
        total = camp[6]
        channels = random.sample(AD_CHANNELS, random.randint(2, 5))
        per_ch = total / len(channels)
        for ch in channels:
            alloc = round(per_ch * random.uniform(0.7, 1.3), 2)
            spent = round(alloc * random.uniform(0.3, 1.0), 2)
            cb_rows.append((len(cb_rows)+1, camp[0], ch, alloc, spent, round(alloc-spent, 2), rand_dt(-10, 0)))
    c.executemany('INSERT INTO campaign_budgets VALUES (?,?,?,?,?,?,?)', cb_rows)

    # 11. ad_spend
    c.execute('''CREATE TABLE ad_spend (
        id            INTEGER PRIMARY KEY,
        campaign_id   INTEGER,
        channel       TEXT,
        spend_date    DATE,
        impressions   INTEGER,
        clicks        INTEGER,
        conversions   INTEGER,
        spend_amount  DECIMAL(12,2),
        cpm           DECIMAL(8,2),
        cpc           DECIMAL(8,2),
        roas          DECIMAL(6,2)
    )''')
    ad_rows = []
    for camp in camp_rows:
        start = date.fromisoformat(camp[4])
        end = date.fromisoformat(camp[5])
        days = (end - start).days + 1
        for _ in range(min(days, 30)):
            d = start + timedelta(days=random.randint(0, max(0, days-1)))
            impr = random.randint(10000, 5000000)
            clicks = int(impr * random.uniform(0.005, 0.08))
            conv = int(clicks * random.uniform(0.01, 0.15))
            spend = round(random.uniform(500, 100000), 2)
            cpm = round(spend / impr * 1000, 2) if impr > 0 else 0
            cpc = round(spend / clicks, 2) if clicks > 0 else 0
            roas = round(conv * random.uniform(100, 500) / spend, 2) if spend > 0 else 0
            ad_rows.append((len(ad_rows)+1, camp[0], random.choice(AD_CHANNELS),
                            str(d), impr, clicks, conv, spend, cpm, cpc, roas))
    c.executemany('INSERT INTO ad_spend VALUES (?,?,?,?,?,?,?,?,?,?,?)', ad_rows)

    # 12. coupon_records
    c.execute('''CREATE TABLE coupon_records (
        id            INTEGER PRIMARY KEY,
        coupon_code   TEXT NOT NULL,
        user_id       INTEGER,
        campaign_id   INTEGER,
        face_value    DECIMAL(8,2),
        min_purchase  DECIMAL(8,2),
        issue_date    DATE,
        expire_date   DATE,
        use_date      DATE,
        status        TEXT DEFAULT 'issued'
    )''')
    COUPON_STATUS = ['issued', 'used', 'expired', 'cancelled']
    coupon_rows = []
    for i in range(1, 301):
        uid = random.choice(user_rows)[0]
        cid = random.choice(camp_rows)[0]
        face = random.choice([5, 10, 20, 50, 100, 200])
        min_p = face * random.randint(3, 10)
        issue = rand_date(-20, 10)
        expire = issue + timedelta(days=random.randint(7, 30))
        status = random.choice(COUPON_STATUS)
        use_date = str(rand_date(0, 20)) if status == 'used' else None
        coupon_rows.append((i, f'CPN{i:06d}', uid, cid, face, min_p,
                            str(issue), str(expire), use_date, status))
    c.executemany('INSERT INTO coupon_records VALUES (?,?,?,?,?,?,?,?,?,?)', coupon_rows)

    # ================================================================
    # ④ 供应链
    # ================================================================

    # 13. suppliers
    c.execute('''CREATE TABLE suppliers (
        supplier_id   INTEGER PRIMARY KEY,
        name          TEXT NOT NULL,
        category      TEXT,
        contact_name  TEXT,
        contact_phone TEXT,
        province      TEXT,
        credit_level  TEXT,
        cooperation_since DATE,
        annual_quota  DECIMAL(14,2),
        status        TEXT DEFAULT 'active'
    )''')
    sup_rows = []
    CREDIT = ['A+', 'A', 'B+', 'B', 'C']
    for i in range(1, 31):
        prov = random.choice(PROVINCES)
        sup_rows.append((i, f'供应商{i:02d}有限公司', random.choice(SUPPLIER_CATEGORIES),
                         f'联系人{i}', rand_phone(), prov, random.choice(CREDIT),
                         str(rand_date(-730, -30)), round(random.uniform(100000, 50000000), 2),
                         random.choices(['active','inactive','blacklisted'], weights=[80,15,5])[0]))
    c.executemany('INSERT INTO suppliers VALUES (?,?,?,?,?,?,?,?,?,?)', sup_rows)

    # 14. purchase_orders
    c.execute('''CREATE TABLE purchase_orders (
        po_id         INTEGER PRIMARY KEY,
        po_no         TEXT NOT NULL,
        supplier_id   INTEGER NOT NULL,
        product_id    INTEGER,
        quantity      INTEGER,
        unit_price    DECIMAL(12,2),
        total_amount  DECIMAL(14,2),
        order_date    DATE,
        expect_date   DATE,
        actual_date   DATE,
        status        TEXT DEFAULT 'pending',
        FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id)
    )''')
    po_rows = []
    PO_STATUS = ['pending', 'confirmed', 'shipped', 'received', 'cancelled']
    for i in range(1, 151):
        sup = random.choice(sup_rows)
        prod = random.choice(prod_rows)
        qty = random.randint(100, 10000)
        unit_p = round(prod[4] * random.uniform(0.6, 0.9), 2)
        total = round(qty * unit_p, 2)
        order_d = rand_date(-30, 30)
        expect_d = order_d + timedelta(days=random.randint(7, 30))
        actual_d = expect_d + timedelta(days=random.randint(-3, 10)) if random.random() > 0.3 else None
        status = random.choice(PO_STATUS)
        po_rows.append((i, f'PO{2026000+i}', sup[0], prod[0], qty, unit_p, total,
                        str(order_d), str(expect_d), str(actual_d) if actual_d else None, status))
    c.executemany('INSERT INTO purchase_orders VALUES (?,?,?,?,?,?,?,?,?,?,?)', po_rows)

    # 15. warehouses
    c.execute('''CREATE TABLE warehouses (
        warehouse_id  INTEGER PRIMARY KEY,
        name          TEXT NOT NULL,
        province      TEXT,
        city          TEXT,
        area_sqm      DECIMAL(10,2),
        manager       TEXT,
        contact_phone TEXT,
        type          TEXT DEFAULT 'self_owned'
    )''')
    wh_rows = []
    for i, wh_name in enumerate(WAREHOUSE_NAMES):
        prov = PROVINCES[i % len(PROVINCES)]
        city = CITIES[prov][0]
        wh_rows.append((i+1, wh_name, prov, city, round(random.uniform(500, 50000), 2),
                        f'仓管{i+1}', rand_phone(),
                        random.choice(['self_owned', 'leased', 'third_party'])))
    c.executemany('INSERT INTO warehouses VALUES (?,?,?,?,?,?,?,?)', wh_rows)

    # 16. logistics_records
    c.execute('''CREATE TABLE logistics_records (
        id             INTEGER PRIMARY KEY,
        order_no       TEXT,
        waybill_no     TEXT NOT NULL,
        logistics_co   TEXT,
        send_date      DATE,
        expect_arrive  DATE,
        actual_arrive  DATE,
        sender_prov    TEXT,
        receiver_prov  TEXT,
        weight_kg      DECIMAL(6,2),
        freight        DECIMAL(8,2),
        status         TEXT DEFAULT 'in_transit'
    )''')
    LOG_STATUS = ['pending', 'picked_up', 'in_transit', 'delivered', 'failed', 'returned']
    lr_rows = []
    for i in range(1, 401):
        send_d = rand_date(-20, 40)
        expect_d = send_d + timedelta(days=random.randint(1, 5))
        actual_d = expect_d + timedelta(days=random.randint(-1, 3)) if random.random() > 0.2 else None
        sp = random.choice(PROVINCES)
        rp = random.choice(PROVINCES)
        wt = round(random.uniform(0.1, 50), 2)
        freight = round(wt * random.uniform(3, 15) + random.uniform(5, 30), 2)
        lr_rows.append((i, f'ORD{random.randint(100000, 200000):06d}',
                        f'SF{random.randint(1000000000, 9999999999)}',
                        random.choice(LOGISTICS_COMPANIES), str(send_d), str(expect_d),
                        str(actual_d) if actual_d else None, sp, rp, wt, freight,
                        random.choice(LOG_STATUS)))
    c.executemany('INSERT INTO logistics_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', lr_rows)

    # ================================================================
    # ⑤ 财务中心
    # ================================================================

    # 17. finance_bills
    c.execute('''CREATE TABLE finance_bills (
        bill_id       INTEGER PRIMARY KEY,
        bill_no       TEXT NOT NULL,
        bill_type     TEXT,
        amount        DECIMAL(14,2),
        currency      TEXT DEFAULT 'CNY',
        bill_date     DATE,
        due_date      DATE,
        paid_date     DATE,
        counterparty  TEXT,
        status        TEXT DEFAULT 'pending',
        remark        TEXT
    )''')
    BILL_TYPES = ['应收账款', '应付账款', '预收款', '预付款', '费用报销', '内部结算', '平台结算']
    fb_rows = []
    for i in range(1, 201):
        bill_d = rand_date(-30, 30)
        due_d = bill_d + timedelta(days=random.randint(7, 60))
        paid_d = due_d + timedelta(days=random.randint(-5, 15)) if random.random() > 0.3 else None
        amt = round(random.uniform(1000, 5000000), 2)
        status = random.choices(['pending','paid','overdue','cancelled','disputed'], weights=[20,50,15,10,5])[0]
        fb_rows.append((i, f'BILL{2026000+i}', random.choice(BILL_TYPES), amt, 'CNY',
                        str(bill_d), str(due_d), str(paid_d) if paid_d else None,
                        f'对方公司{random.randint(1,50)}', status,
                        random.choice(['正常业务', '大促结算', '合同款', '服务费', None])))
    c.executemany('INSERT INTO finance_bills VALUES (?,?,?,?,?,?,?,?,?,?,?)', fb_rows)

    # 18. tax_records
    c.execute('''CREATE TABLE tax_records (
        id            INTEGER PRIMARY KEY,
        period        TEXT NOT NULL,
        tax_type      TEXT,
        taxable_amount DECIMAL(14,2),
        tax_rate      DECIMAL(5,4),
        tax_amount    DECIMAL(14,2),
        declaration_date DATE,
        payment_date  DATE,
        status        TEXT DEFAULT 'declared'
    )''')
    tax_rows = []
    for i in range(1, 61):
        period = f"2026-{random.randint(1,6):02d}"
        tax_type = random.choice(TAX_TYPES)
        taxable = round(random.uniform(100000, 50000000), 2)
        rate = random.choice([0.03, 0.06, 0.09, 0.13, 0.20, 0.25])
        tax_amt = round(taxable * rate, 2)
        decl_d = rand_date(0, 30)
        pay_d = decl_d + timedelta(days=random.randint(1, 15)) if random.random() > 0.2 else None
        tax_rows.append((i, period, tax_type, taxable, rate, tax_amt,
                         str(decl_d), str(pay_d) if pay_d else None,
                         random.choice(['declared','paid','overdue'])))
    c.executemany('INSERT INTO tax_records VALUES (?,?,?,?,?,?,?,?,?)', tax_rows)

    # 19. bank_statements
    c.execute('''CREATE TABLE bank_statements (
        id             INTEGER PRIMARY KEY,
        account_no     TEXT NOT NULL,
        bank_name      TEXT,
        transaction_date DATE,
        transaction_type TEXT,
        amount         DECIMAL(14,2),
        balance        DECIMAL(14,2),
        counterparty   TEXT,
        remark         TEXT,
        status         TEXT DEFAULT 'confirmed'
    )''')
    TRANS_TYPES = ['收入', '支出', '转账', '退款', '手续费', '利息']
    bs_rows = []
    balance = round(random.uniform(1000000, 10000000), 2)
    for i in range(1, 301):
        bank = random.choice(BANKS)
        acc_no = f"6228{random.randint(100000000000, 999999999999)}"
        trans_d = rand_date(-30, 30)
        ttype = random.choice(TRANS_TYPES)
        amt = round(random.uniform(100, 2000000), 2)
        if ttype in ['收入', '转账收']:
            balance += amt
        else:
            balance -= amt
            amt = -amt
        balance = round(balance, 2)
        bs_rows.append((i, acc_no, bank, str(trans_d), ttype, amt, abs(balance),
                        f'对方账户{random.randint(1,100)}',
                        random.choice(['平台打款', '供应商付款', '广告费', '员工工资', '房租', '税款', None]),
                        'confirmed'))
    c.executemany('INSERT INTO bank_statements VALUES (?,?,?,?,?,?,?,?,?,?)', bs_rows)

    # 20. cost_center_allocation
    c.execute('''CREATE TABLE cost_center_allocation (
        id             INTEGER PRIMARY KEY,
        period         TEXT NOT NULL,
        cost_center    TEXT,
        cost_type      TEXT,
        amount         DECIMAL(12,2),
        allocation_ratio DECIMAL(5,4),
        allocated_by   TEXT,
        create_time    DATETIME
    )''')
    COST_TYPES = ['人力成本', '营销费用', '运营费用', '技术投入', '行政费用', '差旅费', '培训费']
    cc_rows = []
    for i in range(1, 121):
        period = f"2026-{random.randint(1,6):02d}"
        cc_rows.append((i, period, random.choice(COST_CENTERS), random.choice(COST_TYPES),
                        round(random.uniform(1000, 500000), 2),
                        round(random.uniform(0.01, 1.0), 4),
                        f'财务{random.randint(1,5)}号', rand_dt(-30, 0)))
    c.executemany('INSERT INTO cost_center_allocation VALUES (?,?,?,?,?,?,?,?)', cc_rows)

    # ================================================================
    # ⑥ 客服系统
    # ================================================================

    # 21. complaints
    c.execute('''CREATE TABLE complaints (
        complaint_id  INTEGER PRIMARY KEY,
        user_id       INTEGER,
        order_no      TEXT,
        type          TEXT,
        description   TEXT,
        channel       TEXT,
        priority      TEXT DEFAULT 'normal',
        status        TEXT DEFAULT 'open',
        create_time   DATETIME,
        resolve_time  DATETIME
    )''')
    COMP_CHANNELS = ['电话', '在线客服', 'App反馈', '社交媒体', '邮件', '12315']
    PRIORITIES = ['low', 'normal', 'high', 'urgent']
    comp_rows = []
    for i in range(1, 201):
        uid = random.choice(user_rows)[0]
        create_t = rand_dt(-30, 0)
        status = random.choices(['open','processing','resolved','closed'], weights=[15,25,40,20])[0]
        resolve_t = rand_dt(0, 15) if status in ['resolved','closed'] else None
        comp_rows.append((i, uid, f'ORD{random.randint(100000, 200000):06d}',
                          random.choice(COMPLAINT_TYPES),
                          f'投诉内容描述{i}，用户反映问题较为严重...',
                          random.choice(COMP_CHANNELS),
                          random.choice(PRIORITIES), status, create_t,
                          resolve_t))
    c.executemany('INSERT INTO complaints VALUES (?,?,?,?,?,?,?,?,?,?)', comp_rows)

    # 22. complaint_followups
    c.execute('''CREATE TABLE complaint_followups (
        id             INTEGER PRIMARY KEY,
        complaint_id   INTEGER NOT NULL,
        operator       TEXT,
        action         TEXT,
        note           TEXT,
        follow_time    DATETIME,
        FOREIGN KEY (complaint_id) REFERENCES complaints(complaint_id)
    )''')
    ACTIONS = ['首次响应', '问题确认', '内部协调', '给出方案', '用户确认', '补偿发放', '归档关闭']
    fu_rows = []
    for comp in comp_rows:
        for action in random.sample(ACTIONS, random.randint(1, 4)):
            fu_rows.append((len(fu_rows)+1, comp[0], f'客服{random.randint(1,20)}号',
                            action, f'{action}操作记录', rand_dt(-15, 0)))
    c.executemany('INSERT INTO complaint_followups VALUES (?,?,?,?,?,?)', fu_rows)

    # 23. satisfaction_surveys
    c.execute('''CREATE TABLE satisfaction_surveys (
        id             INTEGER PRIMARY KEY,
        user_id        INTEGER,
        order_no       TEXT,
        score          INTEGER,
        comment        TEXT,
        survey_date    DATE,
        category       TEXT
    )''')
    SURVEY_CATS = ['商品满意度', '物流体验', '客服体验', '整体体验', '直播体验']
    surv_rows = []
    for i in range(1, 251):
        uid = random.choice(user_rows)[0]
        score = random.choices([1,2,3,4,5], weights=[5,8,15,35,37])[0]
        surv_rows.append((i, uid, f'ORD{random.randint(100000, 200000):06d}',
                          score, f'{"好评" if score>=4 else "差评"}评价内容{i}',
                          str(rand_date(-30, 0)), random.choice(SURVEY_CATS)))
    c.executemany('INSERT INTO satisfaction_surveys VALUES (?,?,?,?,?,?,?)', surv_rows)

    # ================================================================
    # ⑦ 平台运营
    # ================================================================

    # 24. anchor_contracts
    c.execute('''CREATE TABLE anchor_contracts (
        contract_id    INTEGER PRIMARY KEY,
        anchor_name    TEXT NOT NULL,
        platform       TEXT,
        contract_no    TEXT,
        sign_date      DATE,
        start_date     DATE,
        end_date       DATE,
        base_salary    DECIMAL(10,2),
        commission_rate DECIMAL(5,4),
        exclusive      INTEGER DEFAULT 0,
        status         TEXT DEFAULT 'active'
    )''')
    ac_rows = []
    for i, (anchor, platform, rate) in enumerate(ANCHORS):
        sign_d = rand_date(-365, -30)
        start_d = sign_d + timedelta(days=random.randint(7, 30))
        end_d = start_d + timedelta(days=random.randint(90, 730))
        base_sal = round(random.uniform(0, 100000), 2)
        ac_rows.append((i+1, anchor, platform, f'CTR{2026000+i}', str(sign_d),
                        str(start_d), str(end_d), base_sal, rate,
                        1 if random.random() > 0.5 else 0,
                        random.choices(['active','expired','terminated'], weights=[70,20,10])[0]))
    c.executemany('INSERT INTO anchor_contracts VALUES (?,?,?,?,?,?,?,?,?,?,?)', ac_rows)

    # 25. anchor_performance
    c.execute('''CREATE TABLE anchor_performance (
        id             INTEGER PRIMARY KEY,
        anchor_name    TEXT,
        platform       TEXT,
        period         TEXT,
        live_count     INTEGER,
        total_gmv      DECIMAL(14,2),
        avg_viewers    INTEGER,
        fan_growth     INTEGER,
        conversion_rate DECIMAL(5,4),
        score          DECIMAL(4,2)
    )''')
    ap_rows = []
    for i, (anchor, platform, _) in enumerate(ANCHORS):
        for month in range(1, 7):
            gmv = round(random.uniform(100000, 50000000), 2)
            ap_rows.append((len(ap_rows)+1, anchor, platform, f"2026-{month:02d}",
                            random.randint(4, 30), gmv, random.randint(5000, 500000),
                            random.randint(-5000, 100000),
                            round(random.uniform(0.01, 0.15), 4),
                            round(random.uniform(60, 100), 2)))
    c.executemany('INSERT INTO anchor_performance VALUES (?,?,?,?,?,?,?,?,?,?)', ap_rows)

    # 26. platform_rules
    c.execute('''CREATE TABLE platform_rules (
        rule_id        INTEGER PRIMARY KEY,
        rule_code      TEXT NOT NULL,
        name           TEXT,
        category       TEXT,
        description    TEXT,
        effective_date DATE,
        status         TEXT DEFAULT 'active',
        penalty_level  TEXT
    )''')
    RULE_CATS = ['商品管理', '主播行为', '广告规范', '数据安全', '财务合规', '用户保护']
    pr_rows = []
    for i in range(1, 51):
        eff_d = rand_date(-180, 30)
        pr_rows.append((i, f'RULE{i:04d}', f'规则{i}:{random.choice(RULE_CATS)}相关',
                        random.choice(RULE_CATS), f'规则描述：{i}号规则详细说明内容...',
                        str(eff_d), random.choices(['active','deprecated'], weights=[85,15])[0],
                        random.choice(['warning','minor','major','critical'])))
    c.executemany('INSERT INTO platform_rules VALUES (?,?,?,?,?,?,?,?)', pr_rows)

    # ================================================================
    # ⑧ 风控
    # ================================================================

    # 27. risk_alerts
    c.execute('''CREATE TABLE risk_alerts (
        alert_id       INTEGER PRIMARY KEY,
        alert_time     DATETIME,
        alert_type     TEXT,
        target_type    TEXT,
        target_id      TEXT,
        risk_level     TEXT,
        description    TEXT,
        handler        TEXT,
        handle_time    DATETIME,
        status         TEXT DEFAULT 'pending'
    )''')
    ALERT_TYPES = ['刷单嫌疑', '异常退款', '账号盗用', '虚假GMV', '佣金欺诈', '敏感词违规', '异常登录', '资金风险']
    TARGET_TYPES = ['user', 'anchor', 'order', 'session', 'supplier']
    ra_rows = []
    for i in range(1, 101):
        alert_t = rand_dt(-30, 0)
        handle_t = rand_dt(0, 10) if random.random() > 0.4 else None
        ra_rows.append((i, alert_t, random.choice(ALERT_TYPES),
                        random.choice(TARGET_TYPES), str(random.randint(1, 200)),
                        random.choice(RISK_LEVELS), f'风控预警详情{i}',
                        f'风控{random.randint(1,10)}号' if handle_t else None,
                        handle_t, random.choices(['pending','handling','closed','false_alarm'], weights=[20,30,40,10])[0]))
    c.executemany('INSERT INTO risk_alerts VALUES (?,?,?,?,?,?,?,?,?,?)', ra_rows)

    # 28. blacklist
    c.execute('''CREATE TABLE blacklist (
        id             INTEGER PRIMARY KEY,
        target_type    TEXT,
        target_id      TEXT,
        reason         TEXT,
        add_time       DATETIME,
        expire_time    DATETIME,
        operator       TEXT,
        status         TEXT DEFAULT 'active'
    )''')
    BL_REASONS = ['多次刷单', '严重违规', '欺诈退款', '销售违禁品', '虚假宣传被投诉', '账号盗用']
    bl_rows = []
    for i in range(1, 41):
        add_t = rand_dt(-60, 0)
        exp_t_d = rand_date(10, 365)
        bl_rows.append((i, random.choice(TARGET_TYPES), str(random.randint(1, 500)),
                        random.choice(BL_REASONS), add_t, str(exp_t_d),
                        f'管理员{random.randint(1,5)}',
                        random.choices(['active','expired','removed'], weights=[70,20,10])[0]))
    c.executemany('INSERT INTO blacklist VALUES (?,?,?,?,?,?,?,?)', bl_rows)

    # ================================================================
    # ⑨ 系统/审计
    # ================================================================

    # 29. operation_logs
    c.execute('''CREATE TABLE operation_logs (
        id             INTEGER PRIMARY KEY,
        operator_id    TEXT,
        operator_type  TEXT,
        action         TEXT,
        resource_type  TEXT,
        resource_id    TEXT,
        ip_address     TEXT,
        user_agent     TEXT,
        request_params TEXT,
        result         TEXT,
        op_time        DATETIME
    )''')
    OP_TYPES = ['admin', 'finance', 'operation', 'cs', 'system']
    RESOURCES = ['order', 'settlement', 'user', 'product', 'campaign', 'anchor', 'refund', 'commission']
    ol_rows = []
    for i in range(1, 501):
        ip = f"{random.randint(10,192)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}"
        ol_rows.append((i, f'OP{random.randint(1,50):03d}', random.choice(OP_TYPES),
                        random.choice(LOG_ACTIONS), random.choice(RESOURCES),
                        str(random.randint(1, 1000)), ip,
                        'Mozilla/5.0 Chrome/120',
                        f'{{"id":{random.randint(1,1000)}}}',
                        random.choices(['success','failed','partial'], weights=[85,10,5])[0],
                        rand_dt(-30, 0)))
    c.executemany('INSERT INTO operation_logs VALUES (?,?,?,?,?,?,?,?,?,?,?)', ol_rows)

    # 30. data_sync_tasks
    c.execute('''CREATE TABLE data_sync_tasks (
        task_id        INTEGER PRIMARY KEY,
        task_name      TEXT,
        source_system  TEXT,
        target_table   TEXT,
        sync_type      TEXT,
        schedule       TEXT,
        last_run_time  DATETIME,
        next_run_time  DATETIME,
        last_status    TEXT,
        records_synced INTEGER,
        error_msg      TEXT
    )''')
    SOURCE_SYSTEMS = ['抖音开放平台', '快手开放平台', '淘宝开放平台', '支付宝', '微信支付', '银行系统', 'ERP', 'CRM']
    SYNC_TYPES = ['full', 'incremental', 'real_time']
    SCHEDULES = ['*/5 * * * *', '0 * * * *', '0 0 * * *', '0 0 * * 1', '0 2 1 * *']
    ds_rows = []
    for i in range(1, 36):
        last_run = rand_dt(-1, 0)
        next_run = rand_dt(0, 1)
        records = random.randint(0, 50000)
        status = random.choices(['success','failed','running','skipped'], weights=[75,10,5,10])[0]
        err = f'连接超时错误代码{random.randint(1000,9999)}' if status == 'failed' else None
        ds_rows.append((i, f'同步任务{i:02d}-{random.choice(SOURCE_SYSTEMS)}',
                        random.choice(SOURCE_SYSTEMS), random.choice(RESOURCES),
                        random.choice(SYNC_TYPES), random.choice(SCHEDULES),
                        last_run, next_run, status, records, err))
    c.executemany('INSERT INTO data_sync_tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)', ds_rows)

    # ================================================================
    # ⑩ 直播电商核心（含对账差异）— 沿用原有逻辑
    # ================================================================

    # 31. live_sessions
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
        sessions.append((i, anchor, platform, category, str(live_date),
                         random.randint(18, 23), random.randint(60, 300),
                         random.randint(5000, 500000), 'completed'))
    c.executemany('INSERT INTO live_sessions VALUES (?,?,?,?,?,?,?,?,?)', sessions)
    session_ids = [s[0] for s in sessions]

    # 32. live_gmv
    c.execute('''CREATE TABLE live_gmv (
        id           INTEGER PRIMARY KEY,
        session_id   INTEGER NOT NULL,
        gmv          DECIMAL(14,2) NOT NULL,
        paid_gmv     DECIMAL(14,2),
        order_count  INTEGER,
        report_time  DATETIME,
        FOREIGN KEY (session_id) REFERENCES live_sessions(session_id)
    )''')
    gmv_map = {}
    gmv_rows = []
    for idx, sid in enumerate(session_ids):
        viewers = sessions[idx][7]
        gmv = round(viewers * random.uniform(0.5, 3.0), 2)
        paid_gmv = round(gmv * random.uniform(0.85, 0.98), 2)
        order_cnt = random.randint(50, max(51, int(gmv / 200)))
        report_time = f"{sessions[idx][4]} {sessions[idx][5]:02d}:{random.randint(0,59):02d}:00"
        gmv_map[sid] = gmv
        gmv_rows.append((idx + 1, sid, gmv, paid_gmv, order_cnt, report_time))
    c.executemany('INSERT INTO live_gmv VALUES (?,?,?,?,?,?)', gmv_rows)

    # 差异设计
    D1_sessions = random.sample(session_ids[:100], 5)
    D2_missing   = random.sample(session_ids[100:150], 4)
    GHOST_COUNT  = 3
    D4_dup       = random.sample(session_ids[150:180], 3)
    D5_late      = random.sample([s for s in session_ids if s not in D2_missing], 6)
    D6_wrong     = random.sample([s for s in session_ids if s not in D2_missing and s not in D5_late], 5)
    D7_no_deduct = random.sample([s for s in session_ids if s not in D2_missing and s not in D5_late and s not in D6_wrong], 4)
    D8_abnormal  = random.sample([s for s in session_ids if s not in D2_missing], 5)

    # 33. order_amount
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
    order_rows = []
    oid = 1
    for sid in session_ids:
        if sid in D2_missing:
            continue
        gmv = gmv_map[sid]
        session = sessions[sid - 1]
        ratio = random.choice([random.uniform(0.88, 0.93), random.uniform(1.07, 1.12)]) if sid in D1_sessions else random.uniform(0.98, 1.02)
        amount = round(gmv * ratio, 2)
        net = round(amount, 2)
        order_rows.append((oid, f'ORD{100000+oid:06d}', sid, amount, 0, net, session[4], random.choice(PAYMENT_CHANNELS), 'completed'))
        oid += 1
        if sid in D4_dup:
            order_rows.append((oid, f'ORD{100000+oid:06d}', sid, amount, 0, net, session[4], random.choice(PAYMENT_CHANNELS), 'completed'))
            oid += 1
    for g in range(GHOST_COUNT):
        ghost_amount = round(random.uniform(5000, 80000), 2)
        order_rows.append((oid, f'ORD{100000+oid:06d}', None, ghost_amount, 0, ghost_amount,
                           str(rand_date(0, 59)), random.choice(PAYMENT_CHANNELS), 'completed'))
        oid += 1
    c.executemany('INSERT INTO order_amount VALUES (?,?,?,?,?,?,?,?,?)', order_rows)
    order_map = {row[2]: row[3] for row in order_rows if row[2]}

    # 34. settlements
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
    settle_rows = []
    for idx, sid in enumerate(session_ids):
        if sid in D2_missing:
            continue
        gmv = gmv_map[sid]
        session = sessions[sid - 1]
        live_date = date.fromisoformat(session[4])
        platform_fee = round(gmv * random.uniform(0.03, 0.06), 2)
        if sid in D6_wrong:
            settle_amt = round(gmv - platform_fee * random.uniform(0.3, 0.6), 2)
            note = 'D6:结算金额异常'
        elif sid in D7_no_deduct:
            settle_amt = round(gmv - platform_fee, 2)
            note = 'D7:退款未扣除'
        else:
            settle_amt = round(gmv - platform_fee, 2)
            note = None
        expected_date = live_date + timedelta(days=7)
        if sid in D5_late:
            actual_settle = live_date + timedelta(days=random.randint(15, 35))
            note = (note + ' D5:超期结算') if note else 'D5:超期结算'
        else:
            actual_settle = live_date + timedelta(days=random.randint(5, 8))
        settle_rows.append((idx+1, sid, settle_amt, platform_fee, str(actual_settle), str(expected_date), 'settled', note))
    c.executemany('INSERT INTO settlements VALUES (?,?,?,?,?,?,?,?)', settle_rows)

    # 35. refunds
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
    refund_sessions = D7_no_deduct + random.sample(
        [s for s in session_ids if s not in D2_missing and s not in D7_no_deduct], 36)
    for sid in refund_sessions:
        gmv = gmv_map[sid]
        session = sessions[sid - 1]
        order_no = next((row[1] for row in order_rows if row[2] == sid), f'ORD_REF_{sid}')
        refund_amt = round(gmv * random.uniform(0.03, 0.15), 2)
        refund_date = date.fromisoformat(session[4]) + timedelta(days=random.randint(1, 14))
        refund_rows.append((rid, order_no, sid, refund_amt, str(refund_date), random.choice(REFUND_REASONS), 'success'))
        rid += 1
    c.executemany('INSERT INTO refunds VALUES (?,?,?,?,?,?,?)', refund_rows)

    # 36. commissions
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
            rate = round(random.uniform(0.32, 0.45), 4)
            status = 'disputed'
        else:
            rate = round(anchor_rate + random.uniform(-0.02, 0.02), 4)
            status = 'paid'
        commission_rows.append((cid, sid, anchor, rate, round(gmv * rate, 2), gmv,
                                str(live_date + timedelta(days=random.randint(8, 15))), status))
        cid += 1
    c.executemany('INSERT INTO commissions VALUES (?,?,?,?,?,?,?,?)', commission_rows)

    conn.commit()

    # ================================================================
    # 统计输出
    # ================================================================
    print("=" * 70)
    print("✅ 企业级多业务模拟数据库已生成")
    print(f"   路径: {db_path}")
    print("=" * 70)

    c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    all_tables = [r[0] for r in c.fetchall()]
    total_rows = 0
    print(f"\n{'表名':<30} {'行数':>8}  {'业务域'}")
    print("-" * 60)

    domain_map = {
        'users': '用户体系', 'user_profiles': '用户体系', 'user_tags': '用户体系', 'user_login_logs': '用户体系',
        'products': '商品中心', 'product_categories': '商品中心', 'product_inventory': '商品中心', 'product_price_history': '商品中心',
        'campaigns': '营销活动', 'campaign_budgets': '营销活动', 'ad_spend': '营销活动', 'coupon_records': '营销活动',
        'suppliers': '供应链', 'purchase_orders': '供应链', 'warehouses': '供应链', 'logistics_records': '供应链',
        'finance_bills': '财务中心', 'tax_records': '财务中心', 'bank_statements': '财务中心', 'cost_center_allocation': '财务中心',
        'complaints': '客服系统', 'complaint_followups': '客服系统', 'satisfaction_surveys': '客服系统',
        'anchor_contracts': '平台运营', 'anchor_performance': '平台运营', 'platform_rules': '平台运营',
        'risk_alerts': '风控', 'blacklist': '风控',
        'operation_logs': '系统审计', 'data_sync_tasks': '系统审计',
        'live_sessions': '直播电商核心', 'live_gmv': '直播电商核心', 'order_amount': '直播电商核心',
        'settlements': '直播电商核心', 'refunds': '直播电商核心', 'commissions': '直播电商核心',
    }
    for t in all_tables:
        c.execute(f"SELECT COUNT(*) FROM {t}")
        cnt = c.fetchone()[0]
        total_rows += cnt
        domain = domain_map.get(t, '其他')
        print(f"  {t:<28} {cnt:>8}  {domain}")

    print("-" * 60)
    print(f"  {'合计':<28} {total_rows:>8}  ({len(all_tables)} 张表)")

    print(f"\n🎯 对账差异 (直播电商核心表):")
    print(f"  D1 金额差异(>5%): session {D1_sessions}")
    print(f"  D2 订单缺失:       session {D2_missing}")
    print(f"  D3 幽灵订单:       {GHOST_COUNT} 条 session_id=NULL")
    print(f"  D4 重复订单:       session {D4_dup}")
    print(f"  D5 超期结算:       session {D5_late}")
    print(f"  D6 结算金额错:     session {D6_wrong}")
    print(f"  D7 退款未扣除:     session {D7_no_deduct}")
    print(f"  D8 分佣比例异常:   session {D8_abnormal}")

    conn.close()

    # 生成 schema index
    schema = {
        "db_path": "data/enterprise_mock.db",
        "dialect": "sqlite",
        "description": "企业级多业务模拟数据库，36张表，覆盖直播电商、用户、商品、营销、供应链、财务、客服、风控等业务域",
        "built_at": 1780470000,
        "table_count": len(all_tables),
        "business_domains": {
            "直播电商核心": ["live_sessions","live_gmv","order_amount","settlements","refunds","commissions"],
            "用户体系": ["users","user_profiles","user_tags","user_login_logs"],
            "商品中心": ["products","product_categories","product_inventory","product_price_history"],
            "营销活动": ["campaigns","campaign_budgets","ad_spend","coupon_records"],
            "供应链": ["suppliers","purchase_orders","warehouses","logistics_records"],
            "财务中心": ["finance_bills","tax_records","bank_statements","cost_center_allocation"],
            "客服系统": ["complaints","complaint_followups","satisfaction_surveys"],
            "平台运营": ["anchor_contracts","anchor_performance","platform_rules"],
            "风控": ["risk_alerts","blacklist"],
            "系统审计": ["operation_logs","data_sync_tasks"]
        }
    }
    schema_path = os.path.join(os.path.dirname(db_path), "schema_index_enterprise.json")
    with open(schema_path, 'w', encoding='utf-8') as f:
        json.dump(schema, f, ensure_ascii=False, indent=2)
    print(f"\n✅ Schema 索引已生成: {schema_path}")
    print("\n💡 对 Agent 挑战建议:")
    print("  - '帮我找出直播GMV和订单金额不一致的场次' (需从36张表中定位正确的2张)")
    print("  - '哪些结算记录存在退款未扣除的问题？' (需关联settlements + refunds)")
    print("  - '统计各平台本月广告投放ROI和直播GMV的关联性' (需跨营销+直播域)")
    print("  - '找出被风控标记过的主播，其直播场次GMV异常情况' (需关联risk_alerts+live_gmv)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=DEFAULT_DB)
    args = parser.parse_args()
    generate(args.db)
