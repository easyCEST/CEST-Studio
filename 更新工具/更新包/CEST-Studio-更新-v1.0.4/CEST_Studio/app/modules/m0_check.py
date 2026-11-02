# -*- coding: utf-8 -*-
"""app/modules/m0_check.py —— Ⓞ 诊断 / 自检：只读，不改任何数据。

做三件事：① 复述识别结果；② 打印用到的核心层版本（md5）；③ 把输入里能读到的
.mat 变量与形状列出来（whosmat 只读头，不加载数据）。
"""
from __future__ import annotations

from pathlib import Path

from .base import Field, Module


class CheckModule(Module):
    id = 'm0_check'
    badge = '⑦'                # 方案文档里写成 Ⓞ；圈字母字体不支持，界面用 ⑦（辅助模块）
    title = '诊断 / 自检'
    input_label = '要检查的目录'
    out_subdir = 'logs'
    accepts = ()
    produces = ()
    schema = (
        Field('src', '要检查的目录（空=用当前输入总目录）', 'dir', ''),
    )

    def suggest(self, det, proj):
        return {'src': det.path if det else ''}

    def preview_files(self, p, proj):
        return [str(Path(proj) / '日志')] if proj else []

    def preview_image(self, res):
        return None

    def run(self, p, ctx):
        src = str(p.get('src') or '').strip() or (ctx.det.path if ctx.det else '')
        if not src or not Path(src).exists():
            raise RuntimeError(f'目录不存在：{src}')

        from app.core_path import CORE_LIB, fingerprint
        d = Path(src)

        ctx.log('— 识别结果 —')
        if ctx.det:
            ctx.log(f'  类型：{ctx.det.label}  ({ctx.det.kind})')
            for ln in ctx.det.lines:
                ctx.log('  ' + ln)
        ctx.log('— 核心层版本（md5 前 8 位）—')
        ctx.log('  ' + fingerprint())
        ctx.log(f'  位置：{CORE_LIB}')

        ctx.log('— 目录内容（本层）—')
        dirs = sorted(x.name for x in d.iterdir() if x.is_dir())
        mats = sorted(x.name for x in d.iterdir() if x.suffix == '.mat')
        dcms = sum(1 for x in d.iterdir() if x.suffix.lower() == '.dcm')
        ctx.log(f'  子目录 {len(dirs)} 个' + (f'：{" ".join(dirs[:12])}' if dirs else ''))
        ctx.log(f'  .mat {len(mats)} 个：{" ".join(mats[:12])}')
        ctx.log(f'  .dcm（本层）{dcms} 个')

        for f in mats[:6]:
            try:
                from scipy.io import whosmat
                got = [(n, tuple(s)) for n, s, _c in whosmat(str(d / f))]
                ctx.log(f'  {f}: ' + ', '.join(f'{n}{list(s)}' for n, s in got[:6]))
            except Exception as e:                                # noqa: BLE001
                ctx.log(f'  {f}: 读头失败 {type(e).__name__}')
        return dict(src=src, n_mat=len(mats), n_dcm=dcms)
