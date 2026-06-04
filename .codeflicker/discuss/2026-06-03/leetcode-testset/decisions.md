/**
 * @author mabohui <mabohui@kuaishou.com>
 * Created on 2026-06-03
 *
 * 决策记录：LeetCode 50题测试集 + 方言后处理层
 *
 * ## 背景
 * 用户希望通过 LeetCode SQL 50题构建测试集，验证 Agent 的 Schema Linking 和 SQL 生成能力。
 * 50张表放入同一个 SQLite 库，让 AI 自动决定取哪张表的字段。
 *
 * ## 决策
 * 1. 数据来源：解析 LeetCode 题目文本，自动建 SQLite 库 + 评估集 + 知识库文档
 * 2. 方言处理：加后处理转换层（正则替换），把常见 MySQL 函数转成 SQLite 等价写法
 * 3. 知识库：题目解读转成知识库文档，供 RAG 检索
 * 4. Schema Linking Top-K 调为 8（50张表场景）
 *
 * ## 替代方案（已否决）
 * - A方案（内嵌经典题目）：不够灵活，后续扩展需要改脚本
 * - Prompt层方言处理：已作为补充方案，后处理层是主要防线
 */