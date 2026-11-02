# -*- coding: utf-8 -*-
"""cest_agent.plan —— 预检门禁：这条链现在能跑到哪，缺什么，谁来解决。

★ 复用 GUI 自己的判断（detect/chain + detect/rules），**不另写一套**——
  否则两边会打架：agent 说能跑、GUI 说不能跑。

每个问题都带 fix_kind：
    human_gui   必须用户到某个页面手做（掩模 / ROI / 频率表确认）
    manual_arg  需要用户给一个具体数值（B0 比例系数等）
    blocked     被上游卡住，不是它本身的问题
    auto        agent 可以自己处理
"""
from __future__ import annotations

from detect import chain
from detect import rules as R

# 链条顺序（② 展示放最后；⑧ 只检查、不执行）
STAGES = ('m1_recon', 'm_roi', 'm3_post', 'm4_ld', 'm2_display')
STAGE_LABEL = {
    'm1_recon': '① 重建',
    'm_roi': '⑧ 掩模/ROI',
    'm3_post': '③ 后处理',
    'm4_ld': '④ 逐体素LD',
    'm2_display': '② 展示',
    'm5_prf': '⑤ PRF提取',
    'm6_prf_analysis': '⑥ PRF分析',
}


def cases_of(proj) -> list:
    return chain.list_cases(proj) if proj else []


def mask_and_roi(proj, case) -> tuple:
    """⑧ 的两个产物：去噪掩模（③④ 都要）+ ROI_total（只有 ③ 要）。"""
    rd = chain.roi_dir(proj, case)
    if rd is None:
        return False, False
    return (chain.mask_file(proj, case) is not None,
            (rd / 'ROI_total.mat').exists())


def _done(proj, case, mid) -> bool:
    d = R.find_subdir(proj, mid) if proj else None
    c = (d / case) if d else None
    try:
        if c is None or not c.is_dir():
            return False
        if mid == '03_post':
            return any(c.rglob('post_meta.json'))
        if mid == '04_ld':
            return any(c.rglob('voxel_LD.mat'))
        if mid == '02_display':
            return any(c.rglob('*.png'))
    except OSError:
        return False
    return False


def _blocked_by(stage_map, ids):
    return [s for s in ids if stage_map.get(s, {}).get('status') != 'done']


def _blk_problem(ids, case):
    return dict(kind='blocked', fix_kind='blocked',
                text='等 ' + '、'.join(STAGE_LABEL[b] for b in ids) + ' 完成后才能跑',
                goto=dict(mid=ids[0], case=case))


def roi_stage(proj, case) -> dict:
    """⑧ 的状态。注意：⑧ 是**人工阶段**，agent 只检查，永远不代跑。

    状态以「去噪掩模 Mask.mat」为准（与 GUI 的数据链自检一致）；
    缺 ROI_total 单独作为 ③ 的前置问题列出，不影响 ④（④ 只要掩模）。
    """
    if not proj:
        return dict(id='m_roi', label=STAGE_LABEL['m_roi'], status='blocked',
                    problems=[dict(kind='no_proj', fix_kind='human_gui',
                                   text='还没选输出总目录（PROJ）',
                                   goto=dict(mid='m_roi', case=case or ''))])
    recon = chain.recon_dir(proj, case)
    if not chain.has_recon(recon):
        return dict(id='m_roi', label=STAGE_LABEL['m_roi'], status='blocked',
                    problems=[dict(kind='blocked', fix_kind='blocked',
                                   text='等 ① 重建出数据后才能画掩模',
                                   goto=dict(mid='m1_recon', case=case or ''))])
    has_mask, has_roi = mask_and_roi(proj, case)
    probs = []
    if not has_mask:
        probs.append(dict(
            kind='mask_missing', fix_kind='human_gui',
            text=f'缺 00_掩模\\{case}\\Mask.mat（去噪掩膜）—— 必须手画，我不代生成',
            goto=dict(mid='m_roi', case=case)))
    if not has_roi:
        probs.append(dict(
            kind='roi_missing', fix_kind='human_gui',
            text=f'缺 00_掩模\\{case}\\ROI_total.mat（器官 ROI）—— ③ 后处理要它，需手画',
            goto=dict(mid='m_roi', case=case)))
    return dict(id='m_roi', label=STAGE_LABEL['m_roi'],
                status='done' if has_mask else 'todo',
                has_mask=has_mask, has_roi=has_roi, problems=probs)


def check_case(proj, case, stages=None, force=False) -> dict:
    stages = list(stages or STAGES)
    smap = {}

    # ① 重建
    if 'm1_recon' in stages:
        ok = chain.has_recon(chain.recon_dir(proj, case)) and not force
        smap['m1_recon'] = dict(
            id='m1_recon', label=STAGE_LABEL['m1_recon'],
            status='done' if ok else 'todo',
            problems=[] if ok else [dict(
                kind='recon_missing', fix_kind='manual_arg',
                text=f'缺 ① 重建数据（01_重建\\{case}\\ 下要 s0 / ssat / w_offset）'
                     f'—— 需要原始 DICOM 或先导入',
                goto=dict(mid='m1_recon', case=case))])

    # ⑧ 掩模 / ROI（人工阶段）
    roi = None
    if 'm_roi' in stages:
        roi = roi_stage(proj, case)
        smap['m_roi'] = roi

    # ③ 后处理：需要 ① + 掩模 + ROI_total
    if 'm3_post' in stages:
        ok = _done(proj, case, '03_post') and not force
        blk = _blocked_by(smap, ['m1_recon', 'm_roi'])
        probs = []
        if roi is not None and roi.get('has_roi') is False:
            blk = sorted(set(blk + ['m_roi']))
            probs.append([p for p in roi['problems']
                          if p['kind'] == 'roi_missing'][0]
                         if any(p['kind'] == 'roi_missing' for p in roi['problems'])
                         else _blk_problem(['m_roi'], case))
        if ok:
            status = 'done'
        elif blk:
            status = 'blocked'
            if not probs:
                probs.append(_blk_problem(blk, case))
        else:
            status = 'todo'
        smap['m3_post'] = dict(id='m3_post', label=STAGE_LABEL['m3_post'],
                               status=status, problems=probs)

    # ④ 逐体素LD：需要 ① + 掩模（不吃 ③、不吃 ROI_total）
    if 'm4_ld' in stages:
        ok = _done(proj, case, '04_ld') and not force
        needs = ['m1_recon']
        if roi is not None and roi.get('has_mask') is False:
            needs.append('m_roi')
        blk = _blocked_by(smap, needs)
        if ok:
            status, probs = 'done', []
        elif blk:
            status = 'blocked'
            probs = [next((p for p in (roi or {}).get('problems') or []
                           if p['kind'] == 'mask_missing'), None)
                     or _blk_problem(blk, case)]
        else:
            status, probs = 'todo', []
        smap['m4_ld'] = dict(id='m4_ld', label=STAGE_LABEL['m4_ld'],
                             status=status, problems=probs)

    # ② 展示：需要 ① + 掩模
    if 'm2_display' in stages:
        ok = _done(proj, case, '02_display') and not force
        needs = ['m1_recon']
        if roi is not None and roi.get('has_mask') is False:
            needs.append('m_roi')
        blk = _blocked_by(smap, needs)
        if ok:
            status, probs = 'done', []
        elif blk:
            status = 'blocked'
            probs = [next((p for p in (roi or {}).get('problems') or []
                           if p['kind'] == 'mask_missing'), None)
                     or _blk_problem(blk, case)]
        else:
            status, probs = 'todo', []
        smap['m2_display'] = dict(id='m2_display', label=STAGE_LABEL['m2_display'],
                                  status=status, problems=probs)

    out = dict(case=case, proj=proj, stages=[], can_run=[], blocked=[],
               needs_human=[], questions=[])
    for s in stages:
        item = smap.get(s)
        if item is None:
            continue
        out['stages'].append(item)
        # ⑧ 是人工阶段：它的 todo 不是「agent 能跑」，绝不能算进 can_run
        if item['status'] == 'todo' and s != 'm_roi':
            out['can_run'].append(s)
        elif item['status'] == 'blocked':
            out['blocked'].append(s)
    seen = set()
    for s in out['stages']:
        for p in s['problems']:
            # 同一件事（比如缺 ROI_total）会在 ⑧ 和 ③ 各报一次 → 去重
            key = (p['kind'], p.get('text', ''))
            if key in seen:
                continue
            seen.add(key)
            if p['fix_kind'] == 'human_gui':
                out['needs_human'].append(p)
            elif p['fix_kind'] == 'manual_arg':
                out['questions'].append(p)
    out['runnable'] = bool(out['can_run'])
    out['next_action'] = _next_action(out)
    return out


def _next_action(res) -> dict:
    if res['needs_human']:
        p = res['needs_human'][0]
        return dict(kind='human', text=p['text'], goto=p.get('goto') or {})
    if res['questions']:
        p = res['questions'][0]
        return dict(kind='question', text=p['text'], goto=p.get('goto') or {})
    if res['can_run']:
        return dict(kind='run',
                    text='可以跑：' + '、'.join(STAGE_LABEL[s] for s in res['can_run']))
    return dict(kind='done', text='这条链已经全跑完了。')


def check(proj, case='', stages=None, force=False) -> dict:
    stages = list(stages or STAGES)
    if not proj:
        return dict(proj='', cases=[], error='还没选输出总目录（PROJ）',
                    next_action=dict(kind='human',
                                     text='先选一个输出总目录（PROJ）',
                                     goto=dict(mid='m1_recon', case='')))
    cases = cases_of(proj)
    if case and case not in cases:
        return dict(proj=proj, cases=[], error=f'PROJ 里没有这个病例：{case}',
                    known=cases)
    targets = [case] if case else cases
    if not targets:
        return dict(proj=proj, cases=[],
                    error='PROJ 里还没有病例（01_重建 下没有病例子目录）',
                    next_action=dict(kind='human', text='先跑 ① 重建',
                                     goto=dict(mid='m1_recon', case='')))
    res = dict(proj=proj, cases=[], stages=stages,
               summary=dict(done=0, todo=0, blocked=0, needs_human=0))
    for c in targets:
        r = check_case(proj, c, stages, force=force)
        res['cases'].append(r)
        for s in r['stages']:
            if s['status'] == 'done':
                res['summary']['done'] += 1
            elif s['status'] == 'todo':
                res['summary']['todo'] += 1
            else:
                res['summary']['blocked'] += 1
        res['summary']['needs_human'] += len(r['needs_human'])
    res['ready_cases'] = [r['case'] for r in res['cases'] if r['runnable']]
    res['stuck_cases'] = [r['case'] for r in res['cases'] if not r['runnable']]
    return res
