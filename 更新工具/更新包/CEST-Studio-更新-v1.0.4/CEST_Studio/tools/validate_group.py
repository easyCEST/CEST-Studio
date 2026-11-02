# -*- coding: utf-8 -*-
"""tools/validate_group.py —— 一键自检核心层 `cest_group.py`（病例名自动分组）。

覆盖：
    · 猴子 c1..hp3    → 1 级、3 组、每组 3
    · 老鼠 ctrl_3H_小鼠1..model_5W_小鼠6 → 2 级、6 组、每组 6
    · 边界：无编号名 / 自定义分隔符 / 单组

只读，不写盘。用法（在 cest-gui 根目录）::

    runtime\\python.exe -X utf8 CEST_Studio\\tools\\validate_group.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
CG = HERE.parent.parent.parent            # cest-gui
sys.path.insert(0, str(CG / '03_Python_全链路' / '函数库'))

import cest_group as CG_  # noqa: E402


def _chk(tag, cond):
    print(f'  [{"OK" if cond else "✗ "}] {tag}')
    return bool(cond)


def main() -> int:
    ok = []

    monkey = ['c1', 'c2', 'c3', 'h1', 'h2', 'h3', 'hp1', 'hp2', 'hp3']
    rat = [f'{g}_{t}_小鼠{n}' for g in ('ctrl', 'model')
           for t in ('3H', '1W', '5W') for n in range(1, 7)]

    print('=== 猴子 ===')
    sc = CG_.scan(monkey)
    print(CG_.describe(sc))
    ok += [_chk('1 级', sc['n_levels'] == 1),
           _chk('3 组', len(sc['keys']) == 3),
           _chk('每组 3 个', all(len(v) == 3 for v in sc['groups'].values())),
           _chk('第1级 = c/h/hp', sc['levels'][0]['values'] == ['c', 'h', 'hp'])]
    pp = CG_.pair_plan(monkey)
    ok += [_chk('组间 3 对', pp['total'] == 3),
           _chk('c vs h 各 3 只', pp['pairs'][0]['membersA'] == ['c1', 'c2', 'c3'])]

    print('=== 老鼠 ===')
    sc = CG_.scan(rat)
    print(CG_.describe(sc))
    ok += [_chk('2 级', sc['n_levels'] == 2),
           _chk('6 组', len(sc['keys']) == 6),
           _chk('每组 6 个', all(len(v) == 6 for v in sc['groups'].values())),
           _chk('第1级 ctrl/model', sc['levels'][0]['values'] == ['ctrl', 'model']),
           _chk('第2级 3H/1W/5W', sc['levels'][1]['values'] == ['3H', '1W', '5W'])]
    pp = CG_.pair_plan(rat)
    ok += [_chk('组间 3 对（按时间）', pp['total'] == 3),
           _chk('每对 6 vs 6', all(len(p['membersA']) == 6 and len(p['membersB']) == 6
                                for p in pp['pairs']))]

    print('=== 边界 ===')
    ok += [_chk('无编号名原样', CG_.group_key('ctrl_base') == 'ctrl_base'),
           _chk('尾部下划线清理', CG_.group_key('c1_') == 'c'),
           _chk('自定义分隔符 -', CG_.group_key('ctrl-3H-1', sep='-') == 'ctrl-3H'),
           _chk('单组', len(CG_.scan(['x1', 'x2'])['keys']) == 1)]

    print('\n结论：' + ('核心层 cest_group 全部通过' if all(ok) else '有项未过'))
    return 0 if all(ok) else 1


if __name__ == '__main__':
    raise SystemExit(main())
