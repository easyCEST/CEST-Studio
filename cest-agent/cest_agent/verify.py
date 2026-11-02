# -*- coding: utf-8 -*-
"""cest_agent.verify —— 产物校验：不把「没抛异常」当成功。

现成的坑：③ 单层失败只在日志里打一行 `slice 5 | 失败：...`，模块整体仍返回完成。
所以每个阶段跑完都要**回读产物**再说成功了没有。
"""
from __future__ import annotations

import json
from pathlib import Path

from detect import rules as R


def _sub(proj, key):
    return R.find_subdir(proj, key) if proj else None


def _load_json(p):
    try:
        return json.loads(Path(p).read_text(encoding='utf-8'))
    except Exception:                                    # noqa: BLE001
        return None


def post(proj, case, res=None) -> dict:
    """③ 后处理：post_meta.json 的 per_layer 里失败层必须为 0，且每层产物齐全。"""
    d = _sub(proj, '03_post')
    base = (d / case) if d else None
    if base is None or not base.is_dir():
        return dict(ok=False, reason='没找到 03_CEST后处理\\<病例> 目录', files=[])
    metas = sorted(base.glob('*_interp_*/post_meta.json')) + sorted(base.glob('post_meta.json'))
    if not metas:
        metas = sorted(base.rglob('post_meta.json'))
    if not metas:
        return dict(ok=False, reason='没有 post_meta.json（后处理没写收尾信息）', files=[])
    meta = _load_json(metas[-1]) or {}
    per = meta.get('per_layer') or []
    bad = [x for x in per if x.get('sec') is None]
    n_layer = int(meta.get('n_layer') or len(per) or 0)
    # 校验每层关键产物
    save_root = Path(meta.get('out_dir') or '') if meta.get('out_dir') else None
    if not (save_root and save_root.is_dir()):
        cands = sorted(p for p in base.iterdir() if p.is_dir() and '_interp_' in p.name)
        save_root = cands[-1] if cands else base
    tag = str(meta.get('b0_tag') or 'Self_corr')
    missing = []
    for x in per:
        if x.get('sec') is None:
            continue
        sp = save_root / f"slice{x['layer']}" / tag
        for nm in ('V_exp_mask.mat', 'V_as_mask.mat', 'V_exp.mat'):
            if not (sp / nm).exists():
                missing.append(f"slice{x['layer']}/{nm}")
    ok = bool(per) and not bad and not missing
    reason = ''
    if not per:
        reason = 'post_meta.json 里没有 per_layer（可能压根没进入逐层循环）'
    elif bad:
        reason = f"{len(bad)} 层失败：" + '、'.join(f"slice{x['layer']}" for x in bad[:6])
    elif missing:
        reason = f'{len(missing)} 个产物文件缺失，例如 ' + '、'.join(missing[:4])
    return dict(ok=ok, reason=reason, n_layer=n_layer, n_bad=len(bad),
                bad_layers=[x['layer'] for x in bad],
                n_missing=len(missing), missing=missing[:20],
                meta_file=str(metas[-1]), out_dir=str(save_root),
                sec=meta.get('sec'))


def ld(proj, case, res=None) -> dict:
    """④ 逐体素LD：voxel_LD.mat + ld_meta.json，且每层线宽中位数不离群。"""
    d = _sub(proj, '04_ld')
    base = (d / case) if d else None
    if base is None or not base.is_dir():
        return dict(ok=False, reason='没找到 04_逐体素LD\\<病例> 目录', files=[])
    need = ('voxel_LD.mat', 'voxel_LD_interp.mat')
    miss = [n for n in need if not (base / n).exists()]
    meta = _load_json(base / 'ld_meta.json') or {}
    per = meta.get('per_layer') or []
    bad = [x for x in per if x.get('sec') is None or x.get('error')]
    lws = [x.get('lw_median') for x in per if isinstance(x.get('lw_median'), (int, float))]
    outlier = []
    if len(lws) >= 4:
        s = sorted(lws)
        med = s[len(s) // 2]
        span = s[-1] - s[0]
        if med and span > 0.5 * med:
            outlier = [x['layer'] for x in per
                       if isinstance(x.get('lw_median'), (int, float))
                       and abs(x['lw_median'] - med) > 0.3 * med]
    ok = not miss and bool(per) and not bad
    reason = ''
    if miss:
        reason = '缺 ' + '、'.join(miss)
    elif not per:
        reason = 'ld_meta.json 里没有 per_layer'
    elif bad:
        reason = f'{len(bad)} 层失败：' + '、'.join(f"slice{x.get('layer')}" for x in bad[:6])
    return dict(ok=ok, reason=reason, n_layer=len(per), n_bad=len(bad),
                lw_median=(sorted(lws)[len(lws) // 2] if lws else None),
                outlier_layers=outlier, meta_file=str(base / 'ld_meta.json'),
                out_dir=str(base))


def display(proj, case, res=None) -> dict:
    d = _sub(proj, '02_display')
    base = (d / case) if d else None
    if base is None or not base.is_dir():
        return dict(ok=False, reason='没找到 02_展示\\<病例> 目录')
    pngs = list(base.rglob('*.png'))
    return dict(ok=bool(pngs), reason='' if pngs else '没有出图（*.png）',
                n_png=len(pngs), out_dir=str(base))


def recon(proj, case, res=None) -> dict:
    from detect import chain
    rd = chain.recon_dir(proj, case)
    if rd is None or not chain.has_recon(rd):
        return dict(ok=False, reason=f'缺 01_重建\\{case} 的 s0/ssat/w_offset')
    return dict(ok=True, reason='', out_dir=str(rd))


VERIFIERS = {
    'm1_recon': recon,
    'm3_post': post,
    'm4_ld': ld,
    'm2_display': display,
}


def run(stage_id, proj, case, res=None) -> dict:
    fn = VERIFIERS.get(stage_id)
    if fn is None:
        return dict(ok=True, reason='（该阶段无产物校验）')
    try:
        return fn(proj, case, res)
    except Exception as e:                               # noqa: BLE001
        return dict(ok=False, reason=f'校验本身出错：{type(e).__name__}: {e}')
