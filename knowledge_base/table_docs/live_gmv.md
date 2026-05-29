# live_gmv 表结构文档

## 表名
`live_gmv` — 直播GMV记录表

## 业务含义
记录每个直播间每日的GMV（商品交易总额），数据来源于直播平台的实时统计系统。

## 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键，自增 |
| live_id | INTEGER | 直播间ID（业务主键，关联 order_amount.live_id） |
| live_title | TEXT | 直播间标题/主题名称 |
| gmv | DECIMAL(12,2) | 当日GMV（元），保留两位小数 |
| live_date | DATE | 直播日期（格式 YYYY-MM-DD） |
| platform | TEXT | 直播平台（抖音/快手/淘宝） |
| anchor_name | TEXT | 主播名称 |

## 关联关系
- 与 `order_amount` 表通过 `live_id` 字段关联
- 一个 live_id 在 live_gmv 中有一条记录（每日汇总）
- 一个 live_id 在 order_amount 中可能有多条记录（每笔订单一条）

## 常见查询示例

### 查询某日所有直播间GMV
```sql
SELECT live_id, live_title, gmv, platform, anchor_name
FROM live_gmv
WHERE live_date = '2026-05-27'
ORDER BY gmv DESC;
```

### 按平台汇总GMV
```sql
SELECT platform, SUM(gmv) as total_gmv, COUNT(*) as live_count
FROM live_gmv
WHERE live_date = '2026-05-27'
GROUP BY platform;
```

## 数据质量说明
- GMV 数据来源于平台官方统计，可能存在1-5%的统计误差
- 部分直播间可能因技术原因漏记 GMV（如 live_id=208 场景）
