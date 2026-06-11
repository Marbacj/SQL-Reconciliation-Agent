# orders 表结构文档

## 表: `orders`

## 业务含义
主订单表，记录所有交易订单的详细信息。包含正常订单和异常冲销订单。

## 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT | 主键，订单编号（格式 OxxxxxxX） |
| user_id | TEXT | 下单用户 ID |
| amount | REAL | 订单金额；**正常订单为正数，冲销/异常订单可为负数**。负数金额代表冲销记录，不是退款。若要查询负数金额异常订单，应查此表 |
| status | TEXT | 订单状态：paid=已支付，pending=待支付，cancelled=已取消 |
| created_at | TEXT | 订单创建时间 |

## 重要业务规则

- `amount` 字段可以为负数，负值表示冲销/异常订单（如对账差异、系统冲正）
- 查询"金额为负数的异常订单"、"负数金额"、"冲销订单"应查此表的 `amount < 0`
- 退款金额不在此表，退款信息在 `refunds` 表（refunds.amount 始终为正数）

## 常见查询示例

### 查询异常（负数金额）订单
```sql
SELECT id, amount FROM orders WHERE amount < 0;
```

### 查询昨日已支付订单
```sql
SELECT * FROM orders WHERE status = 'paid' AND DATE(created_at) = DATE('now', '-1 day');
```

## 关联关系
- `orders.id` ← `payments.order_id`（一对多，一个订单可有多笔支付）
- `orders.id` ← `refunds.order_id`（一对多，一个订单可有多笔退款）
