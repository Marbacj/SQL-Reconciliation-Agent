"""
SQL 对账 Agent — Streamlit Web UI

用法:
    streamlit run examples/reconciliation_ui.py

前置条件:
    - .env 中配置 LLM_API_KEY 等
    - data/mock_reconciliation.db 已生成
"""

import sys
import os
import io
import re
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from recon_core.core.llm import HelloAgentsLLM
from recon_core.core.config import Config
from recon_core.agents.reconciliation_agent import ReconciliationAgent
from recon_core.tools.builtin.case_store import CaseStore

# ── 页面配置 ──────────────────────────────────────────

st.set_page_config(
    page_title="SQL 对账 Agent",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 样式 ──────────────────────────────────────────────

st.markdown("""
<style>
    .step-card {
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 12px 16px;
        margin: 8px 0;
        background: #fafafa;
    }
    .step-thought { border-left: 4px solid #6c5ce7; }
    .step-action  { border-left: 4px solid #00b894; }
    .step-observe { border-left: 4px solid #0984e3; }
    .step-finish  { border-left: 4px solid #00cec9; }
    .step-error   { border-left: 4px solid #d63031; }
    .badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 12px;
        font-weight: 600;
    }
    .badge-thought { background: #dfe6e9; color: #6c5ce7; }
    .badge-action  { background: #dfe6e9; color: #00b894; }
    .badge-observe { background: #dfe6e9; color: #0984e3; }
    .badge-finish  { background: #dfe6e9; color: #00cec9; }
    .badge-error   { background: #ffeaa7; color: #d63031; }
    .diff-positive { color: #d63031; font-weight: bold; }
    .diff-negative { color: #00b894; font-weight: bold; }
    .metric-box {
        background: #f8f9fa;
        border-radius: 8px;
        padding: 16px;
        text-align: center;
        margin: 4px;
    }
    .metric-value { font-size: 28px; font-weight: 700; }
    .metric-label { font-size: 12px; color: #636e72; margin-top: 4px; }
</style>
""", unsafe_allow_html=True)

# ── 初始化 Agent ──────────────────────────────────────

@st.cache_resource
def get_agent():
    db_path = PROJECT_ROOT / "data" / "mock_reconciliation.db"
    output_dir = PROJECT_ROOT / "reports"
    case_dir = PROJECT_ROOT / "recon_cases"

    llm = HelloAgentsLLM()
    config = Config(
        skills_enabled=False,
        trace_enabled=True,
        subagent_enabled=False,
    )
    return ReconciliationAgent(
        name="对账分析师",
        llm=llm,
        db_path=str(db_path),
        config=config,
        max_steps=8,
        output_dir=str(output_dir),
        case_store=CaseStore(str(case_dir)),
    )


def parse_steps(raw_output: str) -> list[dict]:
    """解析 Agent 输出为结构化步骤列表"""
    steps = []
    lines = raw_output.split("\n")
    current_step = None

    for line in lines:
        # 检测步骤边界
        step_match = re.match(r"--- 第 (\d+) 步 ---", line)
        if step_match:
            if current_step:
                steps.append(current_step)
            current_step = {
                "step": int(step_match.group(1)),
                "events": [],
            }
            continue

        if current_step is None:
            current_step = {"step": 0, "events": []}

        # 分类事件
        if "推理:" in line or "Thought" in line:
            current_step["events"].append({"type": "thought", "text": line.strip()})
        elif "调用工具:" in line or "🎬" in line:
            current_step["events"].append({"type": "action", "text": line.strip()})
        elif "观察:" in line or "👀" in line:
            current_step["events"].append({"type": "observe", "text": line.strip()})
        elif "最终答案:" in line or "🎉" in line:
            current_step["events"].append({"type": "finish", "text": line.strip()})
        elif "❌" in line:
            current_step["events"].append({"type": "error", "text": line.strip()})
        elif line.strip():
            current_step["events"].append({"type": "info", "text": line.strip()})

    if current_step:
        steps.append(current_step)

    return steps


def render_step(step: dict):
    """渲染单个步骤"""
    with st.container():
        st.markdown(f"**Step {step['step']}**")
        for evt in step["events"]:
            css_class = f"step-{evt['type']}"
            badge_class = f"badge-{evt['type']}"
            labels = {
                "thought": "💭 推理",
                "action": "🔧 动作",
                "observe": "👀 观察",
                "finish": "✅ 完成",
                "error": "❌ 错误",
                "info": "ℹ️ 信息",
            }
            label = labels.get(evt["type"], "ℹ️")
            st.markdown(f"""
            <div class="step-card {css_class}">
                <span class="badge {badge_class}">{label}</span>
                <span style="margin-left: 8px; font-size: 14px;">{evt['text']}</span>
            </div>
            """, unsafe_allow_html=True)


def render_metrics(steps: list[dict], raw_output: str):
    """渲染指标卡片"""
    total_steps = len(steps)
    tool_calls = sum(1 for s in steps for e in s["events"] if e["type"] == "action")

    # 从输出中提取差异行数
    diff_match = re.search(r"存在差异:\s*(\d+)", raw_output)
    diff_count = int(diff_match.group(1)) if diff_match else 0

    # 从输出中提取一致行数
    match_match = re.search(r"完全一致:\s*(\d+)", raw_output)
    match_count = int(match_match.group(1)) if match_match else 0

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f"""
        <div class="metric-box">
            <div class="metric-value">{total_steps}</div>
            <div class="metric-label">推理步数</div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown(f"""
        <div class="metric-box">
            <div class="metric-value">{tool_calls}</div>
            <div class="metric-label">工具调用</div>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        st.markdown(f"""
        <div class="metric-box">
            <div class="metric-value" style="color:#00b894;">{match_count}</div>
            <div class="metric-label">一致行数</div>
        </div>
        """, unsafe_allow_html=True)
    with col4:
        color = "#d63031" if diff_count > 0 else "#00b894"
        st.markdown(f"""
        <div class="metric-box">
            <div class="metric-value" style="color:{color};">{diff_count}</div>
            <div class="metric-label">差异行数</div>
        </div>
        """, unsafe_allow_html=True)


def render_report(raw_output: str):
    """从输出中提取并渲染对账报告"""
    # 提取 diff_compare 输出的表格
    report_start = raw_output.find("## 对账结果")
    if report_start < 0:
        return

    report_text = raw_output[report_start:]

    # 解析差异行
    diff_lines = []
    in_diff_section = False
    current_key = None

    for line in report_text.split("\n"):
        if "数值差异" in line:
            in_diff_section = True
            continue
        if "仅左表存在" in line or "仅右表存在" in line:
            in_diff_section = True
            continue
        if in_diff_section and line.startswith("**key="):
            current_key = re.search(r"key=(\d+)", line)
            if current_key:
                current_key = current_key.group(1)
        if in_diff_section and "差异=" in line and current_key:
            parts = re.findall(r"左表=([\d.]+|N/A),\s*右表=([\d.]+|N/A),\s*差异=([\d.+\-N/A]+)", line)
            if parts:
                left, right, diff = parts[0]
                diff_lines.append({
                    "key": current_key,
                    "left": left,
                    "right": right,
                    "diff": diff,
                })

    if diff_lines:
        st.markdown("### 🔴 差异明细")
        for item in diff_lines:
            diff_str = item["diff"]
            diff_class = "diff-positive" if (
                diff_str not in ("N/A", "仅右表有", "仅左表有") and
                (isinstance(diff_str, str) and diff_str.startswith("+") or
                 (diff_str.lstrip("-").replace(".", "").isdigit() and float(diff_str) > 0))
            ) else "diff-negative"

            # Determine issue type
            if "仅右表" in str(diff_str) or item["left"] == "N/A":
                issue = "⚠️ 数据缺失"
            elif "仅左表" in str(diff_str) or item["right"] == "N/A":
                issue = "⚠️ 数据缺失"
            else:
                issue = "📊 数值差异"

            col1, col2, col3, col4, col5 = st.columns([1, 2, 2, 2, 2])
            with col1:
                st.markdown(f"**{item['key']}**")
            with col2:
                st.markdown(f"左表: `{item['left']}`")
            with col3:
                st.markdown(f"右表: `{item['right']}`")
            with col4:
                st.markdown(f"<span class='{diff_class}'>差额: {diff_str}</span>", unsafe_allow_html=True)
            with col5:
                st.markdown(issue)

    # Raw markdown report
    with st.expander("📄 完整报告（Markdown）", expanded=False):
        st.markdown(report_text)


# ── 侧边栏 ────────────────────────────────────────────

with st.sidebar:
    st.title("📊 SQL 对账 Agent")

    # 项目信息
    st.markdown("---")
    st.markdown("### ⚙️ 配置")

    db_path = PROJECT_ROOT / "data" / "mock_reconciliation.db"
    if db_path.exists():
        st.success(f"✅ 数据库就绪\n`{db_path}`")
    else:
        st.error(f"❌ 数据库未找到\n运行 `python data/generate_mock_data.py`")

    st.markdown(f"**LLM**: `{os.getenv('LLM_MODEL_ID', '未配置')}`")
    st.markdown(f"**Provider**: `{os.getenv('LLM_PROVIDER', 'deepseek')}`")

    st.markdown("---")
    st.markdown("### 📋 示例查询")
    examples = [
        "对比所有直播间的GMV和订单总金额，找出差异",
        "只看差异超过500元的直播间",
        "对比2026-05-27的GMV和订单数据",
    ]
    for ex in examples:
        if st.button(ex, use_container_width=True):
            st.session_state.query = ex

    st.markdown("---")
    st.markdown("### 📂 数据库 Schema")

    import sqlite3

    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        for (table,) in tables:
            cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
            count = conn.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0]
            with st.expander(f"📋 {table} ({count} 行)", expanded=False):
                for col in cols:
                    st.markdown(f"- `{col[1]}` ({col[2]})")
        conn.close()

    # ── 技能积累 ──
    st.markdown("---")
    st.markdown("### 🧠 技能积累")

    case_dir = PROJECT_ROOT / "recon_cases"
    case_store = CaseStore(str(case_dir))
    case_stats = case_store.stats()
    st.metric("累计案例", case_stats["total_cases"])

    if case_stats["total_cases"] > 0:
        all_cases = case_store.list_all()
        with st.expander(f"📋 历史案例 ({len(all_cases)})", expanded=False):
            for case in all_cases[:10]:
                st.markdown(
                    f"- `{case['id']}` {case['query'][:50]}..."
                )

# ── 主界面 ────────────────────────────────────────────

st.title("SQL 对账 Agent")
st.caption("自然语言驱动的自动化数据对账系统 — ReAct 推理 · NL2SQL · 跨表差异比对")

# 查询输入
col1, col2 = st.columns([5, 1])
with col1:
    query = st.text_area(
        "对账查询",
        value=st.session_state.get("query", ""),
        placeholder="例如：对比昨天各直播间的GMV和订单金额，找出差异超过100元的直播间",
        height=68,
        label_visibility="collapsed",
    )
with col2:
    st.markdown("<br>", unsafe_allow_html=True)
    run_btn = st.button("🚀 开始对账", type="primary", use_container_width=True)

# ── 执行对账 ──────────────────────────────────────────

if run_btn and query:
    # 清除旧查询
    st.session_state.query = ""

    # 捕获 stdout
    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured

    try:
        with st.spinner("🤖 Agent 正在执行对账推理..."):
            agent = get_agent()
            result = agent.run(query)
    finally:
        sys.stdout = old_stdout

    raw_output = captured.getvalue()

    # ── 展示结果 ──

    st.markdown("---")

    # 路由决策
    route = agent._last_route
    if route:
        intent_name = route.intent.name
        method = route.label.method
        conf = route.label.confidence
        tools_info = f"{route.tools_before}→{route.tools_after}"

        col_r1, col_r2, col_r3, col_r4 = st.columns(4)
        with col_r1:
            st.metric("路由意图", intent_name)
        with col_r2:
            st.metric("分类方式", method)
        with col_r3:
            st.metric("置信度", f"{conf:.0%}")
        with col_r4:
            st.metric("工具过滤", tools_info)
        if route.label.reasoning:
            st.caption(f"💡 {route.label.reasoning}")

    # 指标卡片
    steps = parse_steps(raw_output)
    render_metrics(steps, raw_output)

    st.markdown("---")

    # 推理步骤
    st.markdown("### 🧠 ReAct 推理轨迹")
    with st.expander("展开查看完整推理过程", expanded=True):
        for step in steps:
            if step["step"] > 0:
                render_step(step)

    # 对账报告
    st.markdown("---")
    st.markdown("### 📊 对账报告")
    render_report(raw_output)

    # 最终结果
    if result:
        st.markdown("---")
        st.success(result)

elif run_btn and not query:
    st.warning("请输入对账查询")

# ── 底部 ──────────────────────────────────────────────

st.markdown("---")
st.caption("SQL Reconciliation Agent · ReAct 推理引擎 · 跨表差异比对")
