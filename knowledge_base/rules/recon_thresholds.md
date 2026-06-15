# 对账规则与异常判断标准

## 金额差异容差
- 判断标准：`|orders.amount - payments.amount| > 0.01` 视为对账差异
- 容差范围：差值 ≤ 0.01 元忽略不计
- SQL 写法：`ABS(o.amount - p.amount) > 0.01`

## 异常类型定义

| 异常类型 | 判断条件 | 风险等级 |
|---------|---------|---------|
| 漏支付 | `orders.status='paid'` 但 `payments` 中无 `status='success'` 记录 | 高危 |
| 孤儿退款 | `refunds.order_id` 在 `orders.id` 中不存在 | 高危（脏数据） |
| 重复支付 | 同一 `order_id` 有多条 `payments.status='success'` | 高危 |
| 负金额订单 | `orders.amount < 0` | 需单独统计（冲销订单） |
| 金额不一致 | `|o.amount - p.amount| > 0.01` | 中危 |

## 时间窗口规则
- 默认按 `created_at` 字段聚合，粒度为天：`DATE(created_at)`
- 跨天对账：`GROUP BY DATE(created_at)`
- 月度对账：`GROUP BY strftime('%Y-%m', created_at)`

## 净收入计算口径
```
净收入 = SUM(orders.amount WHERE status='paid')
       - SUM(refunds.amount)
```
不含 pending / cancelled 状态订单。
