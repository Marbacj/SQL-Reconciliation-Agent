# order_amount 表结构文档

## 表名
`order_amount` — 订单金额记录表

## 业务含义
记录每笔订单的实付金额，数据来源于订单系统。与 live_gmv 表通过 live_id 关联，用于GMV对账。

## 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键，自增 |
| order_id | TEXT | 订单编号（业务主键，格式 ORDxxxxx） |
| live_id | INTEGER | 关联的直播间ID |
| total_amount | DECIMAL(12,2) | 订单实付金额（元），保留两位小数 |
| order_date | DATE | 下单日期（格式 YYYY-MM-DD） |
| order_status | TEXT | 订单状态（completed/pending/refunded） |

## 关联关系
- 与 `live_gmv` 表通过 `live_id` 字段关联
- 一个直播间的订单金额汇总后应与 live_gmv.gmv 基本一致
- 差异可能来源：退款订单、统计口径不同、数据漏记

## 常见查询示例

### 按直播间汇总订单金额
```sql
SELECT live_id, SUM(total_amount) as total_order, COUNT(*) as order_count
FROM order_amount
WHERE order_date = '2026-05-27'
GROUP BY live_id
ORDER BY total_order DESC;
```

### 只统计已完成订单
```sql
SELECT live_id, SUM(total_amount) as completed_amount
FROM order_amount
WHERE order_date = '2026-05-27' AND order_status = 'completed'
GROUP BY live_id;
```

## 数据质量说明
- 订单金额为实付金额，不含优惠券抵扣
- 部分订单可能因退款导致金额为0
- 可能存在直播间有订单但无GMV记录的情况（数据漏记）
