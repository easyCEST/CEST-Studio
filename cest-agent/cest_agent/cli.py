# -*- coding: utf-8 -*-
"""cest_agent.cli —— 命令行入口（给脚本 / 外部智能体用；GUI 不走这里）。

    python -m cest_agent check   --proj <PROJ> [--case X] [--json]
    python -m cest_agent run     --proj <PROJ> [--case X | --all] [--stages 3,4]
    python -m cest_agent doctor  --text "<报错原文>" [--json]
    python -m cest_agent env                                查看找到的 GUI / 解释器
"""
from __future__ import annotations

import argparse
import json
import sys

from . import doctor, paths, plan, report, runner

STAGE_KEYS = {
    '1': 'm1_recon', 'recon': 'm1_recon',
    '8': 'm_roi', 'roi': 'm_roi',
    '3': 'm3_post', 'post': 'm3_post',
    '4': 'm4_ld', 'ld': 'm4_ld',
    '2': 'm2_display', 'display': 'm2_display',
}


def _stages(s: str) -> list:
    out = []
    for tok in str(s or '').replace('，', ',').split(','):
        t = tok.strip().lower()
        if not t:
            continue
        mid = STAGE_KEYS.get(t)
        if mid and mid not in out:
            out.append(mid)
    return out or ['m1_recon', 'm_roi', 'm3_post', 'm4_ld']


def _print_check(res):
    if res.get('error'):
        print('✗ ' + res['error'])
        return 2
    print(f"PROJ：{res.get('proj')}")
    for c in res.get('cases') or []:
        print(f"\n[{c['case']}] " + ('可跑' if c.get('runnable') else '需先处理'))
        for s in c.get('stages') or []:
            mark = {'done': '✓', 'todo': '○', 'blocked': '×'}.get(s['status'], '·')
            print(f"  {mark} {s['label']}")
            for p in s.get('problems') or []:
                print(f"      → {p['text']}")
        na = c.get('next_action') or {}
        if na.get('text'):
            print(f"  下一步：{na['text']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser('cest_agent', description='CEST Studio 自动编排智能体')
    ap.add_argument('cmd', choices=('check', 'run', 'doctor', 'env'))
    ap.add_argument('--proj', default='')
    ap.add_argument('--case', default='')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--stages', default='')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--text', default='')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)

    if a.cmd == 'env':
        info = paths.install()
        print(json.dumps(dict(info, python=paths.python_exe(),
                              state=str(paths.agent_state_dir())),
                         ensure_ascii=False, indent=2))
        return 0

    if a.cmd == 'doctor':
        d = doctor.diagnose(a.text)
        if a.json:
            print(json.dumps(d, ensure_ascii=False, indent=2))
        else:
            print(f"问题：{d['name']}")
            if d['cause']:
                print(f"原因：{d['cause']}")
            print(f"怎么办：{d['action']}")
            if d['evidence']:
                print(f"原文：{d['evidence']}")
        return 0

    if not a.proj:
        print('✗ 需要 --proj')
        return 1
    stages = _stages(a.stages)

    if a.cmd == 'check':
        res = plan.check(a.proj, '' if a.all else a.case, stages, force=a.force)
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        else:
            _print_check(res)
        return 0

    cases = plan.cases_of(a.proj)
    if not a.all and a.case:
        cases = [a.case]
    if not cases:
        print('✗ PROJ 里没有病例')
        return 1

    def ev(e):
        t = e.get('type')
        if t == 'log':
            print('  ' + str(e.get('text', '')))
        elif t == 'progress':
            tot = e.get('total') or 0
            if tot:
                print(f"  [{int(e.get('cur') or 0)}/{tot}] {e.get('text', '')}")
        elif t == 'fail':
            print(f"  × {e.get('stage')} {e.get('case')}: {e.get('text')}")
        elif t == 'need_human':
            print(f"  ! 需要人工：{e.get('case')} {e.get('text')}")

    def confirm_freq(case, info):
        print(f"\n[频率表] {case} 检索到 {info.get('n')} 个频偏"
              f"（来源 {info.get('source')}）")
        print('  前 12 个：' + ', '.join(f'{x:g}' for x in info.get('values', [])[:12]))
        ans = input('  已核对？回车=接受，输入 n=跳过这一例：').strip().lower()
        if ans in ('n', 'no', '不', '否'):
            return None
        return dict(values=info.get('values'), source=f"agent 检索：{info.get('source')}")

    res = runner.run_chain(a.proj, cases, stages, on_event=ev,
                           force=a.force, confirm_freq=confirm_freq)
    p = report.save(res)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    else:
        print('\n' + (res.get('summary') or ''))
        for d in res.get('diagnoses') or []:
            print(f"· {d.get('name')}：{d.get('action')}")
        print(f"\n报告：{p['markdown']}")
    return 0 if res.get('ok') else (2 if res.get('failures') else 0)


if __name__ == '__main__':
    sys.exit(main())
