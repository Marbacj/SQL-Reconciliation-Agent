# 业务术语与计算口径

## 核心指标定义

| 指标 | 计算公式 | 数据来源 |
|------|---------|---------|
| GMV | `SUM(total_amount WHERE order_status='completed')` | `order_amount` 表 |
| 支付成功率 | `COUNT(status='success') / COUNT(*)` | `payments` 表，按 `order_id` 关联 |
| 订单完成率 | `COUNT(status='paid') / COUNT(*)` | `orders` 表 |
| 退款率 | `COUNT(DISTINCT refunds.order_id) / COUNT(DISTINCT orders.id WHERE status='paid')` | 跨 `orders` + `refunds` |
| 平均订单金额 (AOV) | `AVG(amount) WHERE status='paid'` | `orders` 表，排除 pending/cancelled |
| 对账差异金额 | `ABS(o.amount - p.amount) WHERE 差值 > 0.01` | `orders` JOIN `payments` |

## 术语映射（用户口语 → SQL 字段）

| 用户说 | 实际含义 | SQL 字段 |
|-------|---------|---------|
| 销售额 / 营收 | GMV | `order_amount.total_amount` |
| 订单量 | 订单数 | `COUNT(*) FROM orders` |
| 支付成功的 | status='success' | `payments.status` |
| 已完成的订单 | status='paid' | `orders.status` |
| 退款 | 退款记录 | `refunds` 表 |
| 直播间收入 | 直播 GMV | `live_gmv.gmv` 或 `order_amount` GROUP BY `live_id` |
