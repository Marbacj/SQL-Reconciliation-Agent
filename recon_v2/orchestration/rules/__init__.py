# @author mabohui <mabohui@kuaishou.com>
# Created on 2026-06-07
#
# orchestration/rules 包
#
# 职责分层：
#   intent_rules.py  — 意图分类所需的所有静态知识（纯数据）
#   recon_guard.py   — 对账路径守卫（纯函数，无副作用）
#
# 使用示例：
#   from recon_v2.orchestration.rules import (
#       VALID_INTENTS, FAST_PATH_RULES,
#       INTENT_DEFINITIONS, STATIC_FEW_SHOTS,
#       RECON_INTENTS, should_enter_recon,
#   )

from recon_v2.orchestration.rules.intent_rules import (
    VALID_INTENTS,
    FAST_PATH_RULES,
    INTENT_DEFINITIONS,
    STATIC_FEW_SHOTS,
)
from recon_v2.orchestration.rules.recon_guard import (
    RECON_INTENTS,
    SINGLE_METRIC_SIGNALS,
    RECON_SIGNAL_WORDS,
    GuardResult,
    should_enter_recon,
    build_recon_planner_prompt_prefix,
)

__all__ = [
    # intent_rules
    "VALID_INTENTS",
    "FAST_PATH_RULES",
    "INTENT_DEFINITIONS",
    "STATIC_FEW_SHOTS",
    # recon_guard
    "RECON_INTENTS",
    "SINGLE_METRIC_SIGNALS",
    "RECON_SIGNAL_WORDS",
    "GuardResult",
    "should_enter_recon",
    "build_recon_planner_prompt_prefix",
]
