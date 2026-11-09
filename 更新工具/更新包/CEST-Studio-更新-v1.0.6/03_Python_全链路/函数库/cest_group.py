# -*- coding: utf-8 -*-
"""cest_group.py —— 病例名自动分组（供 ⑤ PRF 提取 / ⑥ 通用，不写死任何名字）。

一条规则同时吃各种命名
----------------------
1. **组名 `group_key`**：去掉病例名末尾的“个体编号”（尾部数字；顺带去掉残留分隔符）
     c1            -> c
     hp1           -> hp
     ctrl_3H_小鼠1 -> ctrl_3H_小鼠
     ctrl_3H_小鼠  -> ctrl_3H_小鼠（本来就无编号，不变）
2. **级 `levels`**：把组名按分隔符（默认 `_`）**切段**，只保留“在全体病例里取值 ≥2 种”的段
     c / h / hp                    -> 段 [c|h|hp]                         = 1 级
     ctrl_3H_小鼠 / model_5W_小鼠  -> [ctrl|model][3H|1W|5W]             = 2 级
     （尾巴 `小鼠` 每只都一样 → 恒定 → 不算一级）

于是：
* **组内·按组** = 每个 `group_key` 一份；
* **组内·单个** = 每个病例一份；
* **组间** = 按「最后一个有变化的级」配对（老鼠=时间 3H/1W/5W；猴子只有 1 级则直接在两个组名间对比），
  在该级的每个取值里按「第 1 级」对比（老鼠=ctrl vs model）。

API
---
    group_key(name, sep='_')            -> str
    scan(names, sep='_')                -> dict(groups, keys, segments, levels, n_levels, sep)
    pair_plan(names, sep='_', bucket_level='last') -> 组间配对方案（见下）
    describe(scan_result)               -> 多行字符串（给日志/界面用）
"""
from __future__ import annotations

import re
from collections import OrderedDict

__all__ = ['group_key', 'scan', 'pair_plan', 'describe', 'members_of', 'label_of']


def group_key(name: str, sep: str = '_') -> str:
    """去掉末尾的个体编号 → 组名（先剥分隔符、再剥数字，反复直到稳定）。"""
    s = str(name).strip()
    while True:
        before = s
        if sep:
            while s.endswith(sep):
                s = s[:-len(sep)]
        s = re.sub(r'\d+$', '', s)
        if s == before:
            break
    return s.rstrip()


def scan(names, sep: str = '_') -> dict:
    """扫一遍病例名，给出分组 + 级。

    返回 dict：
      groups    : OrderedDict{组名: [成员名…]}      （按出现顺序）
      keys      : [组名…]
      segments  : [[段…]…]                          与 keys 对应
      levels    : [{'pos': 段序号, 'values': [取值…]}…]   取值只保留 >1 种的段
      n_levels  : 级数
      sep       : 用的分隔符
    """
    names = [str(n) for n in names]
    groups: "OrderedDict[str, list]" = OrderedDict()
    for n in names:
        groups.setdefault(group_key(n, sep), []).append(n)
    keys = list(groups.keys())
    segments = [(k.split(sep) if sep else [k]) for k in keys]
    nseg = max((len(s) for s in segments), default=0)
    levels = []
    for i in range(nseg):
        vals = []
        for s in segments:
            if i < len(s) and s[i] not in vals:
                vals.append(s[i])
        if len(vals) > 1:                          # 恒定段不算一级
            levels.append({'pos': i, 'values': vals})
    # 干净标签：只取“有变化的级”拼起来（ctrl_3H_小鼠 -> ctrl_3H；c -> c）
    poss = [lv['pos'] for lv in levels]
    labels = OrderedDict()
    for k, s in zip(keys, segments):
        labels[k] = '_'.join(s[p] for p in poss if p < len(s)) if poss else k
    return {'groups': groups, 'keys': keys, 'segments': segments,
            'levels': levels, 'n_levels': len(levels), 'sep': sep,
            'labels': labels}


def members_of(scan_result: dict, key: str) -> list:
    return list(scan_result['groups'].get(key, []))


def label_of(scan_result: dict, key: str) -> str:
    """组的“干净标签”（只拼有变化的级）：ctrl_3H_小鼠 -> ctrl_3H。"""
    return scan_result.get('labels', {}).get(key, key)


def pair_plan(names, sep: str = '_', bucket_level: str = 'last') -> dict:
    """组间配对方案（不写死）。

    * 若有 ≥2 级：用最后一个有变化的级当“配对级”（老鼠=时间），
      在该级每个取值里，拿“第 1 级”的不同取值做对比 → 得到若干对
      ``(配对级取值, 组A名, 组B名)``，每组A/B 由该级取值+第1级取值拼回原名。
    * 若只有 1 级（猴子 c/h/hp）：没有配对级，返回 ``pairs`` 为空，
      由调用方从 ``levels[0]['values']`` 里挑两个组名对比。
    """
    sc = scan(names, sep)
    keys = sc['keys']
    segs = sc['segments']
    grp = sc['groups']

    def _keys_at(pos, val):
        return [k for k, s in zip(keys, segs) if len(s) > pos and s[pos] == val]

    def _members(ks):
        out = []
        for k in ks:
            out += list(grp.get(k, []))
        return out

    lvl1 = sc['levels'][0] if sc['levels'] else None
    if sc['n_levels'] >= 2:
        lvl2 = sc['levels'][-1]          # 配对级（时间）
        out = []
        for b in lvl2['values']:
            in_b = []                     # [(组别取值, [组名…]), …]
            for a in lvl1['values']:
                ks = [k for k in _keys_at(lvl2['pos'], b)
                      if segs[keys.index(k)][lvl1['pos']] == a]
                in_b.append((a, ks))
            for i in range(len(in_b)):
                for j in range(i + 1, len(in_b)):
                    (a1, k1), (a2, k2) = in_b[i], in_b[j]
                    out.append({'bucket': b, 'A': a1, 'B': a2,
                                'groupA': k1, 'groupB': k2,
                                'membersA': _members(k1), 'membersB': _members(k2)})
        return {'mode': 'bucket', 'pair_level': lvl2, 'level1': lvl1,
                'pairs': out, 'total': len(out)}
    # 只有 1 级（猴子 c/h/hp）：直接在取值间两两对比
    vals = lvl1['values'] if lvl1 else []
    pos = lvl1['pos'] if lvl1 else 0
    out = []
    for i in range(len(vals)):
        for j in range(i + 1, len(vals)):
            ka, kb = _keys_at(pos, vals[i]), _keys_at(pos, vals[j])
            out.append({'bucket': None, 'A': vals[i], 'B': vals[j],
                        'groupA': ka, 'groupB': kb,
                        'membersA': _members(ka), 'membersB': _members(kb)})
    return {'mode': 'direct', 'pair_level': None, 'level1': lvl1,
            'pairs': out, 'total': len(out)}


def describe(sc: dict) -> str:
    """把 scan 结果写成给人看的一段（日志/界面直接用）。"""
    lines = [f'识别到 {sc["n_levels"]} 级分组、共 {len(sc["keys"])} 组（分隔符 {sc["sep"]!r}）']
    for i, lv in enumerate(sc['levels'], 1):
        lines.append(f'  第{i}级（第{lv["pos"]+1}段）取值：{", ".join(lv["values"])}')
    for k in sc['keys']:
        mem = sc['groups'][k]
        lab = sc.get('labels', {}).get(k, k)
        lines.append(f'  组 {lab}：{len(mem)} 个 → {", ".join(mem[:6])}'
                     + (' …' if len(mem) > 6 else ''))
    return '\n'.join(lines)
