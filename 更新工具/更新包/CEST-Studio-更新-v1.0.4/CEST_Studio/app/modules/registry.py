# -*- coding: utf-8 -*-
"""app/modules/registry.py —— 模块注册表（界面左侧导航就按这个顺序排）。

以后接 ③ 后处理 / ④ 逐体素LD / ⑤ 提取 / ⑥ 分析 / Ⓡ 画布，
只要在这里 import 一下、加进 ALL 就行。
"""
from __future__ import annotations

from .m0_check import CheckModule
from .m1_recon import ReconModule
from .m2_display import DisplayModule
from .m3_post import PostModule
from .m4_ld import LDModule
from .m5_prf import PrfExtractModule
from .m6_prf_analysis import PrfAnalysisModule
from .m_import import ImportModule
from .m_roi import ROIModule

# 顺序 = 界面导航顺序（⓪ 导入重建 → ① 重建 → ⑧ 掩模/ROI → ③ CEST 后处理
#              → ④ 逐体素LD → ⑤ PRF 提取 → ② 多层图谱 → ⑦ 自检）
# 「ROI 定量曲线」不单独占导航 —— 它是 ③ CEST 后处理里的【ROI 定量曲线…】按钮。
ALL = (
    ImportModule(),
    ReconModule(),
    ROIModule(),
    PostModule(),
    LDModule(),
    PrfExtractModule(),
    PrfAnalysisModule(),
    DisplayModule(),
    CheckModule(),
)

BY_ID = {m.id: m for m in ALL}


def get(mid: str):
    return BY_ID.get(mid)


def for_kinds(kinds) -> list:
    """按识别类型筛出可用模块。"""
    kinds = set(kinds or ())
    return [m for m in ALL if (not m.accepts) or (kinds & set(m.accepts))]
