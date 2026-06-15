<!--
此文件由 scripts/gen_relationship_chunks.py 自动生成。
如需添加自定义关联，请在运行脚本时通过 --extra 参数传入，
或在此文件末尾手动追加（重新生成时会覆盖）。
-->

# 关联关系：refunds ↔ orders

## 连接键
- `refunds.order_id` = `orders.id`
- 来源：字段命名推断
- 基数：refunds 中多条记录对应 orders 中一条（N:1）

## 业务含义
一笔订单可对应多条退款记录（分批退款、部分退款等）。

## 标准 JOIN 写法
```sql
SELECT * FROM refunds f
JOIN orders t ON f.order_id = t.id
-- 或 LEFT JOIN 保留无匹配的 refunds 行
```

## 常见查询场景
### 孤儿退款
```sql
SELECT r.id, r.order_id FROM refunds r
LEFT JOIN orders o ON r.order_id = o.id
WHERE o.id IS NULL
```

### 净收入
```sql
SELECT SUM(o.amount) - COALESCE(SUM(r.amount),0) AS net
FROM orders o
LEFT JOIN refunds r ON o.id = r.order_id
WHERE o.status='paid'
```


---

# 关联关系：payments ↔ orders

## 连接键
- `payments.order_id` = `orders.id`
- 来源：字段命名推断
- 基数：payments 中多条记录对应 orders 中一条（N:1）

## 业务含义
一笔订单可对应多条支付记录（正常支付、重复支付、补单等）。

## 标准 JOIN 写法
```sql
SELECT * FROM payments f
JOIN orders t ON f.order_id = t.id
-- 或 LEFT JOIN 保留无匹配的 payments 行
```

## 常见查询场景
### 对账差异
```sql
SELECT o.id, o.amount, p.amount AS paid, ABS(o.amount-p.amount) AS diff
FROM orders o
JOIN payments p ON o.id = p.order_id
WHERE ABS(o.amount - p.amount) > 0.01
```

### 漏支付检测
```sql
SELECT o.id, o.amount FROM orders o
LEFT JOIN payments p ON o.id = p.order_id AND p.status='success'
WHERE o.status='paid' AND p.order_id IS NULL
```

### 重复支付检测
```sql
SELECT order_id, COUNT(*) AS cnt FROM payments
WHERE status='success'
GROUP BY order_id HAVING cnt > 1
```


---

# 数据库 Schema 总览

## 表清单
- `orders`：id, user_id, amount, status, created_at
- `payments`：id, order_id, amount, channel, status, created_at
- `refunds`：id, order_id, amount, status, created_at

## 表关联关系
- `refunds.order_id` → `orders.id`
- `payments.order_id` → `orders.id`
