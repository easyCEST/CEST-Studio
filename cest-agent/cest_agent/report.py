# -*- coding: utf-8 -*-
"""cest_agent.report —— 运行报告：一份 JSON（机器读）+ 一份 Markdown（人读）。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import paths

STAGE_NAME = {
    'm1_recon': '① 重建', 'm_roi': '⑧ 掩模/ROI', 'm3_post': '③ 后处理',
    'm4_ld': '④ 逐体素LD', 'm2_display': '② 展示',
    'm5_prf': '⑤ PRF提取', 'm6_prf_analysis': '⑥ PRF分析',
}
STATUS_CN = {'ok': '全部成功', 'partial': '产物不全', 'fail': '失败',
             'blocked': '卡在前置', 'cancelled': '已取消', 'done': '已完成'}


def save(result: dict) -> dict:
    """写 report.json + report.md，返回两个路径。"""
    d = paths.agent_state_dir() / 'reports'
    d.mkdir(parents=True, exist_ok=True)
    rid = result.get('run_id') or time.strftime('%Y%m%d_%H%M%S')
    pj = d / f'{rid}.json'
    pm = d / f'{rid}.md'
    pj.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                  encoding='utf-8')
    pm.write_text(to_markdown(result), encoding='utf-8')
    return dict(json=str(pj), markdown=str(pm))


def to_markdown(result: dict) -> str:
    L = []
    L.append(f"# CEST 处理报告 · {result.get('run_id', '')}")
    L.append('')
    L.append(f"- 项目目录：`{result.get('proj', '')}`")
    L.append(f"- 耗时：{result.get('sec', 0)} 秒")
    L.append(f"- 结论：{result.get('summary', '')}")
    L.append('')

    L.append('## 各病例')
    L.append('')
    L.append('| 病例 | 结果 | 阶段 |')
    L.append('|:--|:--|:--|')
    for c in result.get('cases') or []:
        st = STATUS_CN.get(c.get('status'), c.get('status', ''))
        bits = []
        for s in c.get('stages') or []:
            nm = STAGE_NAME.get(s['id'], s['id'])
            mark = {'done': '✓', 'partial': '△', 'fail': '×',
                    'cancelled': '■'}.get(s.get('status'), '·')
            extra = '（跳过）' if s.get('skipped') else ''
            bits.append(f'{mark}{nm}{extra}')
        L.append(f"| {c.get('case', '')} | {st} | {' '.join(bits)} |")
    L.append('')

    pend = result.get('pending_human') or []
    if pend:
        L.append('## 需要你手动处理')
        L.append('')
        for p in pend:
            nm = STAGE_NAME.get(p.get('stage', ''), p.get('stage', ''))
            L.append(f"- **{p.get('case', '')}** · {nm}：{p.get('text', '')}")
        L.append('')

    diags = result.get('diagnoses') or []
    if diags:
        L.append('## 失败诊断')
        L.append('')
        for d in diags:
            nm = STAGE_NAME.get(d.get('stage', ''), d.get('stage', ''))
            L.append(f"### {d.get('case', '')} · {nm} · {d.get('name', '')}")
            L.append('')
            if d.get('cause'):
                L.append(f"- 原因：{d['cause']}")
            L.append(f"- 处置：{d.get('action', '')}")
            if d.get('evidence'):
                L.append(f"- 原文：`{str(d['evidence'])[:300]}`")
            L.append('')

    fails = result.get('failures') or []
    if fails:
        L.append('## 失败明细')
        L.append('')
        for f in fails:
            nm = STAGE_NAME.get(f.get('stage', ''), f.get('stage', ''))
            L.append(f"- {f.get('case', '')} · {nm}：`{str(f.get('error', ''))[:400]}`")
        L.append('')

    L.append('---')
    L.append('')
    L.append('由 CEST 智能体生成。掩模 / ROI / 频率表这些必须人工决定的步骤，'
             '智能体只负责拦下并指路，不会代生成。')
    return '\n'.join(L)
