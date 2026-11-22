# -*- coding: utf-8 -*-
"""app/modules/m7_compare.py —— ⑩ 图像对比（两个病例并排比）。

两件事，一次跑完：

1) 多层对比：A、B 各自出一张多层图，再出一张 |A−B| 多层图（三张独立落盘）。
   出图口径与 ② 多层图谱展示逐字一致（同一个 gen_mosaic / 同一套色标策略）。

2) 单层对比：指定层，ROI1 / ROI2 各出一张图（每张上下两个子图）：
      上：Z 谱（A、B 两条实测曲线）
      下：LD 残差（A、B 两条）
   曲线直接读 ③ 后处理已经算好的 Zspc_filter / w_offset_filter / LD.mat，
   **不重算、不画拟合**。该层没跑过 ③ → 直接拦住，提示先去跑 ③。

输出：PROJ\\10_对比\\<A>_vs_<B>\\（本模块独占，不碰其它模块目录）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from .base import Field, Module

from detect import rules as R

from .m2_display import (DEFAULT_CAXIS, _backup_combined, build_mode_and_caxis,
                         probe_dims, dims_text)


# ----------------------------------------------------------------- 小工具
def _cases(proj) -> list:
    """PROJ 里有结果的病例（重建 / 展示 / 后处理 / LD 任一存在即算）。"""
    proj = str(proj or '').strip()
    if not proj:
        return []
    out, seen = [], set()
    for key in ('01_recon', '02_display', '03_post', '04_ld'):
        d = R.find_subdir(proj, key)
        if d is None:
            continue
        try:
            ents = sorted(p for p in d.iterdir() if p.is_dir()
                          and not p.name.startswith(('_', '.')))
        except OSError:
            continue
        for e in ents:
            if e.name not in seen:
                seen.add(e.name)
                out.append(e.name)
    return out


def _mat(path):
    from scipy.io import loadmat
    return loadmat(str(path), squeeze_me=True)


def _num(v):
    """参数值 → float；空/None/非法 → None（注意 0 是合法值，不能被 or 吃掉）。"""
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _cells(v):
    """matlab cell（object 数组）→ list[ndarray]。"""
    a = np.atleast_1d(np.asarray(v, dtype=object))
    return [np.asarray(x, float).ravel() for x in a]


class CompareModule(Module):
    id = 'm7_compare'
    badge = '⑩'
    title = '图像对比'
    out_subdir = '10_compare'
    case_in_out = False
    accepts = ('project_root',)
    produces = ()
    chain_proj = False            # 自己按 PROJ 枚举病例，不走单病例数据链
    proj_direct = True

    schema = (
        # ---------------- 共同 ----------------
        Field('case_a', '病例 A', 'choice', '', (), dyn=True,
              tip='对比的第一例（默认 GS）'),
        Field('case_b', '病例 B', 'choice', '', (), dyn=True,
              tip='对比的第二例（默认 SSE 那例）'),

        # ---------------- 多层对比 ----------------
        Field('mode', '【多层】图类型', 'choice', 'ST', ('raw', 'ST', 'APTW'),
              tip='与 ② 多层图谱展示同口径：raw / ST / APTW'),
        Field('w', '【多层】目标频偏 ppm', 'float', 3.5),
        Field('layers', '【多层】层', 'text', '',
              tip='留空=全部层；也可写 1,3,5 或 1-10'),
        Field('cols', '【多层】每行子图数', 'int', 6),
        Field('caxis_mode', '【多层】色标策略', 'choice', 'manual',
              ('manual', 'per_layer_5_95', 'global_5_95'),
              tip='manual=用下面的固定上下限（三张图共用，可横向比）；\n'
                  'per_layer_5_95 / global_5_95 = 自动算（各图按自己的数据算 5~95%）'),
        Field('caxis_lo', '【多层】色标下限', 'float', 0.0,
              tip='默认 0：A / B / diff 三张图统一用这组上下限，能直接横向比；留空=按图类型常用口径'),
        Field('caxis_hi', '【多层】色标上限', 'float', 0.5,
              tip='默认 0.5：与「统一 0–0.5」一致；留空=按图类型常用口径'),

        # ---------------- 单层对比 ----------------
        Field('layer', '【单层】层号', 'int', 16,
              tip='该层必须已经跑过 ③ 后处理的 ROI 定量曲线'),
        Field('roi', '【单层】要哪个 ROI', 'choice', '两个都出',
              ('两个都出', '只要 ROI1', '只要 ROI2')),

        # ---------------- 输出 ----------------
        Field('dpi', '保存分辨率', 'int', 300),
    )

    # ================================================================ 参数
    def suggest(self, det, proj):
        cs = _cases(proj)
        a = next((c for c in cs if 'gs' == c.strip().lower()), '')
        if not a:
            a = next((c for c in cs if c.strip().lower().startswith('gs')), '')
        if not a and cs:
            a = cs[0]
        b = ''
        for kw in ('1112', 'interp', 'cest'):
            b = next((c for c in cs if c != a and kw in c.lower()), '')
            if b:
                break
        if not b:
            b = next((c for c in cs if c != a and 'sse' in c.lower()), '')
        if not b:
            b = next((c for c in cs if c != a), '')
        return {'case_a': a, 'case_b': b, 'mode': 'ST', 'w': 3.5, 'layers': '',
                'cols': 6, 'caxis_mode': 'manual', 'caxis_lo': 0.0, 'caxis_hi': 0.5,
                'layer': 16, 'roi': '两个都出', 'dpi': 300}

    def dyn_default(self, key, opts, proj):
        v = str((self.suggest(None, proj) or {}).get(key, '') or '')
        return v if v in [str(o) for o in opts] else ''

    def dynamic_choices(self, key, get, proj):
        if key in ('case_a', 'case_b'):
            return _cases(proj)
        return []

    # ================================================================ 目录
    def _pair_name(self, p) -> str:
        a = str(p.get('case_a') or '').strip() or 'A'
        b = str(p.get('case_b') or '').strip() or 'B'
        return f'{a}_vs_{b}'

    MULTI_SUB = '多层'
    SINGLE_SUB = '单层'

    def out_dir_for(self, p, proj) -> Path | None:
        if not proj:
            return None
        return Path(proj) / R.cn_name(self.out_subdir) / self._pair_name(p)

    def preview_files(self, p, proj):
        d = self.out_dir_for(p, proj)
        if not d:
            return []
        a = str(p.get('case_a') or 'A').strip()
        b = str(p.get('case_b') or 'B').strip()
        mode = str(p.get('mode') or 'ST').upper()
        w = _num(p.get('w'))
        w = 3.5 if w is None else w
        stem = f'{mode} {w}ppm'.replace('.', '_') + '_combined'
        lay = int(p.get('layer') or 16)
        multi = d / self.MULTI_SUB
        one = d / self.SINGLE_SUB / f'slice{lay}'
        return [
            str(d),
            str(multi / f'{a} {stem}.png'),
            str(multi / f'{b} {stem}.png'),
            str(multi / f'diff_abs {stem}.png'),
            str(one / 'ROI1.png'),
            str(one / 'ROI2.png'),
        ]

    def preview_image(self, res):
        p = (res or {}).get('png')
        return p if (p and Path(p).exists()) else None

    # ================================================================ 数据定位
    def _interp(self, proj, case, out_root, log=print, make=True):
        """A/B 的展示数据（0.7ut_img_interp.mat）。没有就从 ① 重建结果生成一次到 _cache。"""
        cands = []
        d02 = R.find_subdir(proj, '02_display')
        if d02:
            cands.append(Path(d02) / case / '_cache' / '0.7ut_img_interp.mat')
            cands.append(Path(d02) / case / '0.7ut_img_interp.mat')
            cands += sorted(Path(d02).glob(f'{case}/**/0.7ut_img_interp.mat'))
        d04 = R.find_subdir(proj, '04_ld')
        if d04:
            cands.append(Path(d04) / case / '0.7ut_img_interp.mat')
        for f in cands:
            if f.is_file():
                return str(f)
        recon = R.find_subdir(proj, '01_recon')
        if make and recon is not None:
            rd = Path(recon) / case
            if (rd / 's0.mat').exists() and (rd / 'ssat.mat').exists():
                import cest_ld as L
                cache = Path(out_root) / '_cache' / case
                log(f'  {case}：没有 img_interp → 由重建结果生成一次到 '
                    f'{cache}（之后复用）')
                mf = self._mask(proj, case)
                L.make_img_interp(str(rd), str(cache),
                                  mask_file=mf, verbose=False)
                f = cache / '0.7ut_img_interp.mat'
                if f.is_file():
                    return str(f)
        return ''

    def _mask(self, proj, case):
        d = R.find_subdir(proj, '00_roi')
        if d is None:
            return None
        for n in ('Mask.mat', 'mask.mat'):
            f = Path(d) / case / n
            if f.is_file():
                return str(f)
        return None

    def _roi_dir(self, proj, case, layer):
        """③ 后处理里该层的 ROI 曲线目录（slice<L>/<tag>/ROI_*）→ Path 或 None。"""
        post = R.find_subdir(proj, '03_post')
        if post is None:
            return None
        base = Path(post) / case
        if not base.is_dir():
            return None
        found = []
        for fd in sorted(base.glob('full_interp_*')):
            sp = fd / f'slice{int(layer)}'
            if not sp.is_dir():
                continue
            for cand in sp.glob('*/ROI_*'):
                if (cand / 'LD.mat').is_file() and (cand / 'w_offset_filter.mat').is_file():
                    found.append(cand)
        if not found:
            return None
        return max(found, key=lambda x: x.stat().st_mtime)

    # ================================================================ 校验
    def validate(self, p, ctx=None):
        errs = []
        proj = getattr(ctx, 'proj', None)
        a = str(p.get('case_a') or '').strip()
        b = str(p.get('case_b') or '').strip()
        if not proj:
            errs.append('需要先选「输出总目录（PROJ）」')
            return errs
        if not a or not b:
            errs.append('⑩ 要选两个病例：病例 A、病例 B')
            return errs
        if a == b:
            errs.append('病例 A 和病例 B 是同一个，没有可比的')
            return errs

        out = self.out_dir_for(p, proj)
        for c in (a, b):
            dp = self._interp(proj, c, out, log=lambda *_: None, make=False)
            if not dp:
                errs.append(f'{c}：找不到 img_interp（先在 ② 多层图谱展示里跑一次，'
                            f'或确认 01_重建/{c} 已重建）')
            elif not self._mask(proj, c):
                errs.append(f'{c}：找不到 00_掩模\\{c}\\Mask.mat（先在 ⑧ 画好掩模）')

        lay = int(p.get('layer') or 16)
        for c in (a, b):
            if self._roi_dir(proj, c, lay) is None:
                errs.append(f'{c}：第 {lay} 层还没有 ③ 后处理的 ROI 定量曲线\n'
                            f'　→ 先去「③ CEST 后处理」把第 {lay} 层跑完（并勾上'
                            f'「顺带出 ROI 定量曲线」），再来这里对比')
        return errs

    # ================================================================ 执行
    def run(self, p, ctx):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import cest_display as D

        proj = ctx.proj
        log = ctx.log
        a = str(p.get('case_a') or '').strip()
        b = str(p.get('case_b') or '').strip()
        out = self.out_dir_for(p, proj)
        out_multi = out / self.MULTI_SUB
        lay = int(p.get('layer') or 16)
        out_one = out / self.SINGLE_SUB / f'slice{lay}'
        out_multi.mkdir(parents=True, exist_ok=True)
        out_one.mkdir(parents=True, exist_ok=True)

        mode = str(p.get('mode') or 'ST').upper()
        w = _num(p.get('w'))
        w = 3.5 if w is None else w
        cols = int(p.get('cols') or 6)
        dpi = int(p.get('dpi') or 300)
        layers = self._layers(p)
        caxis_mode = str(p.get('caxis_mode') or 'manual')

        caxis = None
        if caxis_mode == 'manual':
            lo, hi = _num(p.get('caxis_lo')), _num(p.get('caxis_hi'))
            caxis = (lo, hi) if (lo is not None and hi is not None) else DEFAULT_CAXIS.get(mode)

        log(f'⑩ 图像对比：{a}  vs  {b}　mode={mode} {w}ppm　→  {out}')
        log(f'  多层 → {out_multi}')
        log(f'  单层 → {out_one}')
        files = []

        # ---------------- 1) 多层：A / B / |A−B| ----------------
        dA, dB = {}, {}
        for tag, case, store in (('A', a, dA), ('B', b, dB)):
            dp = self._interp(proj, case, out, log=log)
            mf = self._mask(proj, case)
            log(f'  {tag} = {case}：{Path(dp).name if dp else "×"}'
                + (f'　掩模 {Path(mf).name}' if mf else ''))
            store.update(D.load_display_input(dp, verbose=False, mask_file=mf))

        img = {}
        cax = {}
        for tag, case, store in (('A', a, dA), ('B', b, dB)):
            i3, _c, _q = build_mode_and_caxis(
                store, mode, w, use_mask=True, caxis_mode=caxis_mode,
                caxis=caxis, layers=layers, verbose=False)
            if _c is None:
                _c = self._auto_caxis(_q, layers, caxis_mode)
            log(f'  {tag} 色标（{caxis_mode}）= {_c}')
            img[tag], cax[tag] = i3, _c
            files += self._save_mosaic(i3, f'{case} {self._stem(mode, w)}', mode, w,
                                       _c, cols, dpi, out_multi, dp, layers,
                                       pre=f'{case} {mode} {w}ppm', log=log)
            plt.close('all')

        diff, cdiff = self._diff(img['A'], img['B'], dA.get('mask'), dB.get('mask'),
                                 caxis_mode, caxis, layers)
        if caxis_mode != 'manual':
            _c = self._auto_caxis(diff, layers, caxis_mode, skip_neg=True)
            cdiff = _c
            log(f'  diff 自适应色标（{caxis_mode}）= {_c}')
        log(f'  diff 色标 = {cdiff}　最大差 {np.nanmax(diff[np.isfinite(diff)]):.4f}')
        files += self._save_mosaic(diff, f'diff_abs {self._stem(mode, w)}', mode, w,
                                   cdiff, cols, dpi, out_multi, dp,
                                   layers, pre=f'|{a} − {b}|　{mode} {w}ppm', log=log)
        plt.close('all')

        # ---------------- 2) 单层：ROI1 / ROI2 ----------------
        want = str(p.get('roi') or '两个都出')
        rois = [1, 2] if want == '两个都出' else ([1] if want == '只要 ROI1' else [2])
        pngs = []
        for k in rois:
            png = out_one / f'ROI{k}.png'
            self._plot_roi(png, proj, a, b, lay, k, dpi, log=log)
            files.append(str(png))
            files.append(str(png.with_suffix('.json')))
            pngs.append(str(png))
        ctx.log(f'  已写 {len(files)} 个文件')
        return dict(png=pngs[0] if pngs else str(out), out_dir=str(out),
                    case=f'{a} vs {b}', files=files)

    # ---------------------------------------------------------------- 内部
    @staticmethod
    def _stem(mode, w) -> str:
        return f'{mode} {w}ppm'.replace('.', '_') + '_combined'

    @staticmethod
    def _layers(p):
        s = str(p.get('layers') or '').strip()
        if not s:
            return None
        out = []
        for part in s.replace('，', ',').split(','):
            part = part.strip()
            if not part:
                continue
            if '-' in part:
                lo, hi = part.split('-', 1)
                out += list(range(int(lo), int(hi) + 1))
            else:
                out.append(int(part))
        return out or None

    @staticmethod
    def _auto_caxis(qsrc, layers, mode, skip_neg=False):
        """与 ② 同口径的自适应色标（per_layer_5_95 / global_5_95）。"""
        v = np.asarray(qsrc, float)
        lay = list(layers or range(1, v.shape[2] + 1))
        idx = [int(x) - 1 for x in lay if 1 <= int(x) <= v.shape[2]]
        if mode == 'per_layer_5_95':
            lo, hi = [], []
            for k in idx:
                c = v[:, :, k]
                c = c[np.isfinite(c) & (c > 0 if skip_neg else c != 0)]
                if c.size:
                    lo.append(float(np.percentile(c, 5)))
                    hi.append(float(np.percentile(c, 95)))
            return (min(lo), max(hi)) if lo else (0.0, 1.0)
        g = v[:, :, idx] if idx else v
        g = g[np.isfinite(g) & (g > 0 if skip_neg else g != 0)]
        return ((float(np.percentile(g, 5)), float(np.percentile(g, 95)))
                if g.size else (0.0, 1.0))

    @staticmethod
    def _diff(iA, iB, mA, mB, caxis_mode, caxis, layers):
        """|A−B|；两例掩模的交集之外强制 −1（与 ② 的背景口径一致 → 显示为黑）。"""
        a = np.asarray(iA, float)
        b = np.asarray(iB, float)
        if a.shape != b.shape:
            raise RuntimeError(f'两例数据形状不一致：{a.shape} vs {b.shape}')
        d = np.abs(a - b)
        m = None
        if mA is not None and mB is not None:
            ma = np.asarray(mA, float)
            mb = np.asarray(mB, float)
            m = (ma > 0) & (mb > 0)
        elif mA is not None:
            m = np.asarray(mA, float) > 0
        if m is not None:
            if m.ndim == 2:
                m = np.repeat(m[:, :, None], d.shape[2], axis=2)
            d = np.where(m, d, -1.0)
        else:
            d = np.where((a > 0) & (b > 0), d, -1.0)
        return d, caxis

    def _save_mosaic(self, img3d, name, mode, w, caxis, cols, dpi, out, source,
                     layers, pre, log=print):
        """出多层图：**子图标题只留层号**，上面统一一个大标题写「谁 · 什么模式 · 多少 ppm」。

        gen_mosaic 只负责排版（save_dir 传空 → 只返回 Figure），
        大标题 / 落盘 / .mat / .json 在这里补，核心层一行不改。
        """
        import cest_display as D
        import matplotlib.pyplot as plt
        from scipy.io import savemat

        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        _backup_combined(out, name, log)
        lay = list(layers or range(1, int(img3d.shape[2]) + 1))
        opts = dict(layers=lay, cols=int(cols), caxis=caxis, cmap_n_black=1,
                    cbar_pos='bottom', dpi=int(dpi),
                    title_fmt='slice {val:.0f}', title_prefix='')
        fig = D.gen_mosaic(img3d, opts)

        cax = caxis if caxis is not None else (
            float(np.nanmin(img3d[np.isfinite(img3d)])),
            float(np.nanmax(img3d[np.isfinite(img3d)])))
        fig.suptitle(pre, fontsize=17, y=0.985)
        try:
            fig.tight_layout(rect=(0, 0.09, 1, 0.955))
        except Exception:                                        # noqa: BLE001
            pass
        png = out / f'{name}.png'
        fig.savefig(str(png), dpi=int(dpi), facecolor='w')
        plt.close(fig)

        savemat(str(out / f'{name}.mat'),
                {'data': np.asarray(img3d, dtype=float),
                 'layers': np.asarray(lay, dtype=float).reshape(1, -1),
                 'caxis_range': np.asarray(cax, dtype=float).reshape(1, -1)})
        info = dict(title=f'{pre}', mode=mode, w=w, layers=lay, cols=int(cols),
                    caxis=list(map(float, cax)), cmap_n_black=1,
                    title_fmt='slice {val:.0f}', cbar_pos='bottom', dpi=int(dpi),
                    source=str(source))
        (out / f'{name}.json').write_text(
            json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        log(f'  已写 {name}.png')
        return [str(out / f'{name}{e}') for e in ('.png', '.mat', '.json')]

    # ---------------------------------------------------------------- 单层曲线
    def _plot_roi(self, png, proj, a, b, lay, k, dpi=300, log=print):
        import matplotlib.pyplot as plt
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial']
        plt.rcParams['axes.unicode_minus'] = False

        cur = {}
        for c in (a, b):
            d = self._roi_dir(proj, c, lay)
            if d is None:
                raise RuntimeError(f'{c}：第 {lay} 层没有 ROI 定量曲线')
            try:
                off = _cells(_mat(d / 'w_offset_filter.mat')['w_offset_trimmed'])
                z = _cells(_mat(d / 'Zspc_filter.mat')['mSigE_trimmed'])
                ld = _cells(_mat(d / 'LD.mat')['LD'])
            except KeyError as e:
                raise RuntimeError(f'{c}：{d.name} 里缺变量 {e}') from e
            j = k - 1
            if j >= len(z) or j >= len(ld):
                raise RuntimeError(f'{c}：{d.name} 只有 {len(z)} 个 ROI，没有 ROI{k}')
            cur[c] = dict(off=off[j], z=z[j], ld=ld[j], dir=str(d))

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(7.2, 7.2), dpi=int(dpi),
            gridspec_kw=dict(height_ratios=[3, 2], hspace=0.3))
        style = ((a, '#1f4e79', '-'), (b, '#c0392b', '-'))
        for c, col, ls in style:
            ax1.plot(cur[c]['off'], cur[c]['z'], ls, color=col, lw=1.5, label=c)
            ax2.plot(cur[c]['off'], cur[c]['ld'], ls, color=col, lw=1.5, label=c)

        ax1.set_xlabel('频偏 (ppm)')
        ax1.set_ylabel('归一化 Z（裁剪后 0–1）')
        ax1.set_title(f'ROI{k}  Z 谱对比（slice {lay}）')
        ax1.grid(alpha=0.3)
        ax1.legend(fontsize=9)

        ax2.axhline(0, color='#888', lw=0.8, ls='--')
        ax2.set_xlabel('频偏 (ppm)')
        ax2.set_ylabel('LD = 拟合 − Z（残差）')
        ax2.set_title(f'ROI{k}  LD 残差对比')
        ax2.grid(alpha=0.3)
        ax2.legend(fontsize=9)

        fig.tight_layout()
        png = Path(png)
        png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(png), dpi=int(dpi))
        plt.close(fig)
        meta = dict(slice=int(lay), roi=int(k), png=str(png),
                    curves={c: dict(dir=cur[c]['dir'],
                                    n_points=int(cur[c]['off'].size),
                                    ld_min=float(np.nanmin(cur[c]['ld'])),
                                    ld_max=float(np.nanmax(cur[c]['ld'])))
                            for c in cur})
        png.with_suffix('.json').write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        log(f'  已写 {png.name}（ROI{k}）')
