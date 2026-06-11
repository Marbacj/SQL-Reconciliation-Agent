# payments 表结构文档

## 表: `payments`

## 业务含义
支付流水表，记录每笔订单的支付明细。一个订单可能有多条支付记录（重试、分期等场景）。

## 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT | 主键，支付流水号 |
| order_id | TEXT | 关联的订单 ID，指向 orders.id |
| amount | REAL | 实际支付金额；与对应 orders.amount 的差异即为对账异常 |
| channel | TEXT | 支付渠道：wechat=微信支付，alipay=支付宝，card=银行卡，wallet=钱包 |
| status | TEXT | 支付状态：success=支付成功，failed=支付失败 |
| created_at | TEXT | 支付时间 |

## 重要业务规则

- `payments.amount` 应与 `orders.amount` 一致，差异代表对账异常
- 对账场景：orders 状态为 paid 但 payments 无 success 记录 → 订单状态异常
- 对账场景：payments 有 success 记录但 orders 状态不是 paid → 支付成功但订单未更新

## 常见查询示例

### 对账：支付成功但订单状态未更新的记录
```sql
SELECT o.id FROM orders o JOIN payments p ON o.id = p.order_id
WHERE p.status = 'success' AND o.status != 'paid'
AND DATE(p.created_at) = DATE('now', '-1 day');
```

### 微信渠道最近 7 天退款金额合计
```sql
SELECT SUM(r.amount) AS refund_total
FROM refunds r JOIN payments p ON r.order_id = p.order_id
WHERE p.channel = 'wechat' AND r.created_at >= DATE('now', '-7 days');
```

## 关联关系
- `payments.order_id` → `orders.id`（多对一）
- 通过 `order_id` 可与 `refunds` 间接关联
