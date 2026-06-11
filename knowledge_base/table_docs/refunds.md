# refunds 表结构文档

## 表: `refunds`

## 业务含义
退款记录表，记录用户申请退款的处理结果。每条记录代表一笔退款事务。

## 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT | 主键，退款单号 |
| order_id | TEXT | 关联的原始订单 ID，指向 orders.id |
| amount | REAL | 退款金额；**始终为正数**，表示退还给用户的金额。此字段绝不会出现负值 |
| status | TEXT | 退款状态：success=退款成功 |
| created_at | TEXT | 退款创建时间 |

## 重要业务规则

- `amount` 字段**始终为正数**，永远不会出现负值
- 查询"负数金额"、"异常金额"应查 `orders` 表，而非此表
- 退款金额是退还给用户的实际金额，与订单金额相比通常偏小（部分退款场景）

## 常见查询示例

### 查询退款超过订单金额 50% 的记录
```sql
SELECT o.id, o.amount AS order_amt, r.amount AS refund_amt
FROM orders o JOIN refunds r ON o.id = r.order_id
WHERE r.amount > o.amount * 0.5;
```

### 按用户统计累计退款金额
```sql
SELECT o.user_id, SUM(r.amount) AS refund_sum
FROM orders o JOIN refunds r ON o.id = r.order_id
GROUP BY o.user_id ORDER BY refund_sum DESC;
```

## 关联关系
- `refunds.order_id` → `orders.id`（多对一，一个订单可有多笔退款）
