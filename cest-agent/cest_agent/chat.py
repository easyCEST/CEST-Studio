# -*- coding: utf-8 -*-
"""cest_agent.chat —— 本地对话层：把一次运行结果说成人话，也能回答追问。

★ 不依赖任何大模型：全部由**真实运行结果**拼出来，所以不会瞎编。
  接了大模型时，把它当「措辞美化 + 自由追问」，事实部分仍从 result 里取。
"""
from __future__ import annotations

import re

from .intent import STAGE_ALIAS
from .report import STAGE_NAME

CN = {'ok': '全部跑成功了', 'partial': '跑完了但产物不完整',
      'fail': '中途失败', 'blocked': '被前置条件挡住了',
      'cancelled': '被取消了'}


def describe_case(res: dict) -> str:
    """单病例预检 → 一段人话。"""
    if res.get('error'):
        return res['error']
    lines = [f"{res.get('case', '')}：" + ('能直接开跑。' if res.get('runnable')
                                          else '还不能跑。')]
    for s in res.get('stages') or []:
        nm = STAGE_NAME.get(s['id'], s['id'])
        st = {'done': '已完成', 'todo': '待跑', 'blocked': '被卡住'}.get(
            s.get('status'), s.get('status', ''))
        lines.append(f"  · {nm}：{st}")
        for p in s.get('problems') or []:
            lines.append(f"      {p.get('text', '')}")
    na = res.get('next_action') or {}
    if na.get('text'):
        lines.append('')
        lines.append(na['text'])
    return '\n'.join(lines)


def describe_check(res: dict) -> str:
    """预检结果 → 一段人话。"""
    if res.get('error'):
        return res['error']
    cases = res.get('cases') or []
    if not cases:
        return '没找到任何病例。'
    s = res.get('summary') or {}
    lines = [f"共 {len(cases)} 个病例：{s.get('done', 0)} 个阶段已完成、"
             f"{s.get('todo', 0)} 个待跑、{s.get('blocked', 0)} 个被卡住。"]

    ready = res.get('ready_cases') or []
    stuck = res.get('stuck_cases') or []
    if ready:
        lines.append(f"能直接开跑：{'、'.join(ready[:6])}"
                     + ('…' if len(ready) > 6 else ''))
    if stuck:
        lines.append(f"需要先处理：{'、'.join(stuck[:6])}"
                     + ('…' if len(stuck) > 6 else ''))

    # 把人需要做的事归类说清楚
    humans = []
    for c in cases:
        for p in (c.get('needs_human') or []):
            humans.append((c['case'], p))
    if humans:
        by_kind = {}
        for case, p in humans:
            by_kind.setdefault(p.get('kind', 'other'), []).append(case)
        lines.append('')
        if 'mask_missing' in by_kind:
            lines.append(f"缺去噪掩模 {len(by_kind['mask_missing'])} 例"
                         f"（{'、'.join(by_kind['mask_missing'][:5])}）—— 这个必须你在 ⑧ 页手画，"
                         f"我不会代生成。")
        if 'roi_missing' in by_kind:
            lines.append(f"缺器官 ROI {len(by_kind['roi_missing'])} 例 —— 同样在 ⑧ 页画，"
                         f"③ 后处理要它。")
        others = [v for k, v in by_kind.items()
                  if k not in ('mask_missing', 'roi_missing')]
        if others:
            lines.append(f"另外还有 {sum(len(v) for v in others)} 项需要你确认。")
    return '\n'.join(lines)


def describe_run(res: dict) -> str:
    """运行结果 → 一段人话。"""
    lines = [res.get('summary') or '']
    sec = res.get('sec')
    if sec:
        lines.append(f'耗时 {sec} 秒。')

    bad = [c for c in (res.get('cases') or [])
           if c.get('status') in ('fail', 'partial', 'blocked')]
    if bad:
        lines.append('')
        for c in bad[:8]:
            lines.append(f"· {c['case']}：{CN.get(c['status'], c['status'])}")
            for s in c.get('stages') or []:
                if s.get('status') == 'fail':
                    lines.append(f"    {STAGE_NAME.get(s['id'], s['id'])}："
                                 f"{str(s.get('error', ''))[:160]}")
                elif s.get('status') == 'partial':
                    v = s.get('verify') or {}
                    lines.append(f"    {STAGE_NAME.get(s['id'], s['id'])}："
                                 f"{v.get('reason', '产物不全')}")

    pend = res.get('pending_human') or []
    if pend:
        lines.append('')
        lines.append('需要你做：')
        seen = set()
        for p in pend:
            k = (p.get('case'), p.get('text'))
            if k in seen:
                continue
            seen.add(k)
            lines.append(f"  · {p.get('case', '')}：{p.get('text', '')}")

    diags = res.get('diagnoses') or []
    if diags:
        lines.append('')
        lines.append('我的判断：')
        for d in diags[:5]:
            lines.append(f"  · {d.get('name', '')} —— {d.get('cause', '')}")
            lines.append(f"    怎么办：{d.get('action', '')}")
    return '\n'.join([x for x in lines if x is not None])


def answer(question: str, result: dict | None, cfg_llm: dict | None = None) -> str:
    """回答追问。优先用**真实结果**里的字段；实在答不了才落到 LLM 或提示。"""
    q = str(question or '').strip()
    if not q:
        return '你想问什么？'
    if result is None:
        return '还没有跑过任务。先说一句「跑到 LD」之类的，我先跑一遍再回答你。'

    low = q.lower()

    # 问某一层为什么失败
    m = re.search(r'第\s*(\d+)\s*层', q)
    if m and re.search(r'为什么|怎么|失败|挂', q):
        layer = int(m.group(1))
        for c in result.get('cases') or []:
            for s in c.get('stages') or []:
                v = s.get('verify') or {}
                if layer in (v.get('bad_layers') or []):
                    return (f"{c['case']} 的 {STAGE_NAME.get(s['id'], s['id'])} "
                            f"第 {layer} 层没跑成。原因写在「失败明细」里，"
                            f"通常是该层掩模为空或数据维度异常。"
                            f"要不要我重跑这一例？")
        return f'这次运行里没有第 {layer} 层的失败记录。可能它跑成功了，或者你说的不是这一轮。'

    # 问缺什么
    if re.search(r'缺什么|还要|下一步|怎么办|怎么弄', q):
        pend = result.get('pending_human') or []
        if pend:
            return '还需要你做：\n' + '\n'.join(
                f"  · {p.get('case', '')}：{p.get('text', '')}" for p in pend[:8])
        diags = result.get('diagnoses') or []
        if diags:
            return '\n'.join(f"· {d.get('name', '')}：{d.get('action', '')}"
                             for d in diags[:6])
        return '按这次的结果，没有需要你额外处理的东西。'

    # 问某个阶段
    for mid, pat in STAGE_ALIAS:
        if re.search(pat, low):
            rows = []
            for c in result.get('cases') or []:
                for s in c.get('stages') or []:
                    if s['id'] == mid:
                        v = s.get('verify') or {}
                        rows.append(f"  · {c['case']}：{s.get('status')}"
                                    + (f"（{v.get('reason')}）" if v.get('reason') else ''))
            if rows:
                return f"{STAGE_NAME.get(mid, mid)} 的状态：\n" + '\n'.join(rows[:10])
            return f'这次运行没有涉及 {STAGE_NAME.get(mid, mid)}。'

    # 落到 LLM（如果把 fact 一起给它，也不会编）
    if cfg_llm:
        got = _llm_answer(q, result, cfg_llm)
        if got:
            return got
    return ('我只能按这次真实运行结果回答。可以问：缺什么、哪一层失败、'
            '某个阶段什么状态；或者直接说「重跑 ③」。')


def _llm_answer(q: str, result: dict, cfg: dict) -> str | None:
    """可选：把结果摘要一起喂给大模型，让它组织语言。事实仍来自 result。"""
    try:
        import json
        import urllib.request
        fact = json.dumps(dict(summary=result.get('summary'),
                               cases=[dict(case=c['case'], status=c['status'],
                                           stages=[s['id'] + ':' + s['status']
                                                   for s in c.get('stages') or []])
                                      for c in result.get('cases') or []],
                               pending_human=result.get('pending_human'),
                               diagnoses=[d.get('name') for d in
                                          result.get('diagnoses') or []]),
                          ensure_ascii=False)[:4000]
        body = json.dumps({
            'model': cfg.get('model'),
            'messages': [
                dict(role='system', content=
                     '你是 CEST 数据处理助手。只能依据给定的运行事实回答，'
                     '不要编造任何数值或结论。掩模和频率表必须由人决定，'
                     '不能说"已自动生成"。回答简短、直接给下一步。'),
                dict(role='user', content=f'事实：{fact}\n\n问题：{q}'),
            ],
            'temperature': 0.2,
        }).encode('utf-8')
        req = urllib.request.Request(
            str(cfg.get('base_url')).rstrip('/') + '/chat/completions', data=body,
            headers={'Content-Type': 'application/json',
                     'Authorization': 'Bearer ' + str(cfg.get('api_key'))})
        with urllib.request.urlopen(req, timeout=float(cfg.get('timeout') or 20)) as r:
            data = json.loads(r.read().decode('utf-8'))
        return data['choices'][0]['message']['content'].strip()
    except Exception:                                    # noqa: BLE001
        return None
