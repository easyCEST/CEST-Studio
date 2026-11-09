# -*- coding: utf-8 -*-
"""app/modules/m6_prf_analysis.py —— ⑥ PRF 分析（随机森林特征重要性 → 图 / Excel）。

三个模式，各对应 ⑤ PRF 提取的一类产物，各写一个子文件夹：

    组内      ← 05_PRF提取\\组内_按组\\groupPaths.mat
                每个「组别」一张图，图内 = 1W / 3H / 5W 三根并排柱
    单个对比  ← 05_PRF提取\\组内_单个\\resultPaths.mat
                每个「组别」一张图，图内 = 该组每只鼠一根并排柱
    组间      ← 05_PRF提取\\组间\\newGroupPaths.mat
                左脑一张 + 右脑一张，图内 = 1W / 3H / 5W 三根并排柱

两种出图样式（下拉切）：
    仅均值    = 1 个随机森林（n_permute 次置换）取均值，不画误差棒
    均值±SD   = 重训 n_repeats 次（random_state 逐次换），取 Mean±SD，画误差棒

频偏筛选 = **去水峰**（挖空 0ppm 附近）+ **边缘裁剪**（两端上限），左右分开填。

★ 核心层 prf_analysis.py 一行不改：只用它的
  rf_importance / merge_peaks / plot_multi / RANGE_PRESETS。
"""
from __future__ import annotations

import contextlib
import json
import time
from collections import OrderedDict
from pathlib import Path


@contextlib.contextmanager
def _threading_backend():
    """强制 sklearn/joblib 用「线程」后端。

    核心层 prf_analysis.py 用 n_jobs=-1；在 Windows 上 n_jobs=-1 会走 joblib/loky
    **spawn 子进程**（每个弹一个黑框），均值±SD 反复重训时就会"一直弹黑框"。
    套上 threading 后端后 n_jobs=-1 走线程，既不出黑框、也不重入启动。
    """
    try:
        from joblib import parallel_backend
        cm = parallel_backend('threading')
    except Exception:                                        # noqa: BLE001
        cm = contextlib.nullcontext()
    with cm:
        yield

import numpy as np

from .base import Field, Module

from detect import rules as R

# ---- 模式 ----
MODE_GROUP = '组内'
MODE_SINGLE = '单个对比'
MODE_BETWEEN = '组间'
# 兼容旧常量名（别处只有 validate 脚本引用）
MODE_COMPARE = MODE_BETWEEN
MODE_LEFTRIGHT = MODE_GROUP
MODE_CHOICES = (MODE_GROUP, MODE_SINGLE, MODE_BETWEEN)
MODE_FOLDER = {MODE_GROUP: '组内', MODE_SINGLE: '单个对比', MODE_BETWEEN: '组间'}
MODE_SP = {MODE_GROUP: ('组内_按组', 'groupPaths.mat'),
           MODE_SINGLE: ('组内_单个', 'resultPaths.mat'),
           MODE_BETWEEN: ('组间', 'newGroupPaths.mat')}

# ---- 出图样式 ----
STYLE_MEAN = '仅均值'
STYLE_SD = '均值±SD'
STYLE_CHOICES = (STYLE_MEAN, STYLE_SD)

# ---- 频偏范围 ----
RANGE_CLIN = 'clinical（3T：±1.2~4.5）'
RANGE_RAT = 'rat7t（7T：±0.5~3.0）'
RANGE_CUSTOM = '自定义（用下面 4 个边界）'
RANGE_CHOICES = (RANGE_CLIN, RANGE_RAT, RANGE_CUSTOM)

FMT_CHOICES = ('png', 'svg')
COLORS = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#ffd700']


def _f(p, k, d=None):
    v = p.get(k)
    if v is None or (isinstance(v, str) and not v.strip()):
        return d
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _i(p, k, d=0):
    v = _f(p, k, None)
    return int(v) if v is not None else d


def _range_key(p) -> str:
    v = str(p.get('range') or '')
    if v.startswith('rat7t'):
        return 'rat7t'
    if v.startswith('自定义'):
        return 'custom'
    return 'clinical'


class PrfAnalysisModule(Module):
    id = 'm6_prf_analysis'
    badge = '⑥'
    title = 'PRF 分析'
    out_subdir = '06_prf_analysis'          # → 06_PRF分析
    case_in_out = False
    accepts = ('trainset', 'project_root', 'post_mat')
    produces = ('prf_analysis',)
    chain_proj = False                      # 跨病例组织数据，自解析
    input_label = '⑤ PRF 提取的地址清单（留空 = 按模式自动找）'
    schema = (
        # ---------------- 常用（主表单） ----------------
        Field('savepaths', '输入地址清单（⑤ 产物）', 'file', '',
              tip='留空 = 按模式自动找 05_PRF提取\\{组内_按组|组内_单个|组间}\\{...Paths.mat}\n'
                  '（也可直接给 .mat 文件或它所在的目录）'),
        Field('mode', '分析模式', 'choice', MODE_GROUP, MODE_CHOICES,
              tip=f'{MODE_GROUP}：吃 ⑤「组内_按组」→ 每个组别一张图（图内 1W/3H/5W 三柱）\n'
                  f'{MODE_SINGLE}：吃 ⑤「组内_单个」→ 每个组一张图（图内该组每只鼠一根柱）\n'
                  f'{MODE_BETWEEN}：吃 ⑤「组间」→ 左脑一张、右脑一张（图内 1W/3H/5W 三柱）'),
        Field('style', '出图样式', 'choice', STYLE_MEAN, STYLE_CHOICES,
              tip=f'{STYLE_MEAN}：1 个随机森林 + n_permute 次置换，取均值（无误差棒）\n'
                  f'{STYLE_SD}：重训 n_repeats 次，取 Mean±SD（画误差棒）'),
        Field('n_repeats', '重复次数（仅 均值±SD）', 'int', 10,
              tip='仅「均值±SD」用：重训几次取 Mean±SD（原代码默认 10）。'),
        Field('out_dir', '输出根（留空=PROJ\\06_PRF分析）', 'dir', '',
              tip='三个模式各写一个子文件夹（组内 / 单个对比 / 组间）。'),
        Field('dpi', '图分辨率', 'int', 300),

        # ---------------- 高级：频偏 ----------------
        Field('range', '频偏范围', 'choice', RANGE_CHOICES[0], RANGE_CHOICES,
              advanced=True, group='频偏',
              tip='clinical=3T（±1.2~4.5）；rat7t=7T 大鼠（±0.5~3.0）；\n'
                  '自定义 = 用下面「去水峰 / 边缘」4 个边界。'),
        Field('water_left', '去水峰·左 ppm', 'float', -0.5, advanced=True, group='频偏',
              tip='挖空 0ppm 水峰：左边界（默认 -0.5，即剔除 -0.5~0.5）。'),
        Field('water_right', '去水峰·右 ppm', 'float', 0.5, advanced=True, group='频偏',
              tip='挖空 0ppm 水峰：右边界。'),
        Field('edge_left', '边缘·左 ppm', 'float', -3.7, advanced=True, group='频偏',
              tip='左端裁剪（默认 -3.7）。'),
        Field('edge_right', '边缘·右 ppm', 'float', 3.6, advanced=True, group='频偏',
              tip='右端裁剪（默认 3.6，可与左端不对称）。'),
        Field('merge', '合并 5 个 CEST 峰区间', 'bool', True, advanced=True, group='频偏',
              tip='NOE/G-Am/PCr/Amine/APT 区间内频偏合并成一根柱（原代码口径）。'),
        Field('how', '区间合并方式', 'choice', 'sum', ('sum', 'mean'), advanced=True,
              group='频偏', tip='sum=原 Python 脚本；mean=MATLAB 柱状图。'),

        # ---------------- 高级：随机森林 ----------------
        Field('n_permute', '置换次数（仅 仅均值 / SD 内层）', 'int', 10, advanced=True,
              group='随机森林'),
        Field('n_estimators', '树的数量', 'int', 100, advanced=True, group='随机森林'),
        Field('max_depth', '最大深度', 'int', 8, advanced=True, group='随机森林'),
        Field('min_samples_leaf', '叶最小样本数', 'int', 3, advanced=True, group='随机森林'),
        Field('random_state', '随机种子', 'int', 42, advanced=True, group='随机森林',
              tip='固定种子 → 可复现；「均值±SD」时每次自动 +run。'),

        # ---------------- 高级：绘图 ----------------
        Field('fmt', '图格式', 'choice', 'png', FMT_CHOICES, advanced=True, group='绘图',
              tip='png / svg。'),
    )

    def presets(self):
        return {
            '组内·仅均值': {'mode': MODE_GROUP, 'style': STYLE_MEAN},
            '组内·误差棒': {'mode': MODE_GROUP, 'style': STYLE_SD},
            '单个对比': {'mode': MODE_SINGLE},
            '组间·误差棒': {'mode': MODE_BETWEEN, 'style': STYLE_SD},
            '7T 大鼠范围': {'range': RANGE_CHOICES[1]},
            '自定义水峰/边缘': {'range': RANGE_CHOICES[2], 'water_left': -0.5,
                               'water_right': 0.5, 'edge_left': -3.7, 'edge_right': 3.6},
        }

    def stage_overrides(self, p, proj) -> dict:
        """按「分析模式」判断 ⑤ / ⑥ 该不该亮：只认该模式对应的那份产物。"""
        if not proj:
            return {}
        mode = str(p.get('mode') or MODE_GROUP)
        sub, name = MODE_SP.get(mode, ('', 'groupPaths.mat'))
        d5 = R.find_subdir(proj, '05_prf_extract')
        done5 = bool(d5 and ((d5 / sub / name).exists() or (d5 / name).exists()))
        d6 = R.find_subdir(proj, '06_prf_analysis')
        folder = MODE_FOLDER.get(mode, '组内')
        done6 = bool(d6 and (d6 / folder / 'prf_analysis_meta.json').exists())
        return {'m5_prf': done5, 'm6_prf_analysis': done6}

    # ================================================================ 目录
    def out_dir_for(self, p, proj):
        od = str(p.get('out_dir') or '').strip()
        if od:
            return Path(od)
        if not proj:
            return None
        return Path(proj) / R.cn_name(self.out_subdir)

    def _sp_file(self, p, proj):
        """定位要读的地址清单（按模式）。"""
        mode = str(p.get('mode') or MODE_GROUP)
        sub, name = MODE_SP.get(mode, ('', 'savepaths.mat'))
        sp = str(p.get('savepaths') or '').strip()
        if sp:
            pp = Path(sp)
            if pp.is_dir():
                cand = [pp / name, pp / 'savepaths.mat']
                for s in ('组内_按组', '组内_单个', '组间'):
                    cand.append(pp / s / name)
                for c in cand:
                    if c.exists():
                        return c
                return pp / name
            return pp
        if not proj:
            return None
        d5 = R.find_subdir(proj, '05_prf_extract')
        if d5 is None:
            return None
        cand = [d5 / sub / name, d5 / name, d5 / 'savepaths.mat']
        for c in cand:
            if c.exists():
                return c
        return d5 / sub / name

    def preview_files(self, p, proj) -> list:
        d = self.out_dir_for(p, proj)
        if not d:
            return []
        mode = str(p.get('mode') or MODE_GROUP)
        folder = MODE_FOLDER.get(mode, '组内')
        fmt = str(p.get('fmt') or 'png')
        if mode == MODE_BETWEEN:
            return [str(d / folder), str(d / folder / ('左脑_频率重要性.' + fmt)),
                    str(d / folder / ('右脑_频率重要性.' + fmt)),
                    str(d / folder / '左脑_频率重要性.xlsx'),
                    str(d / folder / '右脑_频率重要性.xlsx'),
                    str(d / 'prf_analysis_meta.json')]
        return [str(d / folder), str(d / folder / ('<组别>_频率重要性.' + fmt)),
                str(d / folder / '<组别>_频率重要性.xlsx'),
                str(d / 'prf_analysis_meta.json')]

    def preview_image(self, res):
        return res.get('png') if isinstance(res, dict) else None

    # ================================================================ 校验
    def validate(self, p, ctx=None) -> list:
        errs = []
        proj = getattr(ctx, 'proj', None)
        sp = self._sp_file(p, proj)
        if sp is None:
            errs.append('没有输入：请填「输入地址清单」，或先在顶部选「输出总目录（PROJ）」。')
            return errs
        if not sp.exists():
            errs.append('输入地址清单不存在：' + str(sp)
                        + '\n（先去 ⑤ PRF 提取 跑对应模式，它会生成这个清单）')
            return errs
        try:
            from cest_io import read_path_info
            dirs = read_path_info(sp)
        except Exception as e:                                   # noqa: BLE001
            errs.append(f'读地址清单失败：{e}')
            return errs
        if not dirs:
            errs.append(sp.name + ' 里没有地址（还没提取过训练集）→ 先去 ⑤。')
        if self.out_dir_for(p, proj) is None:
            errs.append('没有输出目录：请填「输出根」或先选输出总目录（PROJ）。')
        return errs

    # ================================================================ 执行
    def _cfg(self, p) -> dict:
        return dict(
            style=str(p.get('style') or STYLE_MEAN),
            n_repeats=max(1, _i(p, 'n_repeats', 10)),
            n_permute=max(1, _i(p, 'n_permute', 10)),
            ne=_i(p, 'n_estimators', 100), md=_i(p, 'max_depth', 8),
            msl=_i(p, 'min_samples_leaf', 3), rs=_i(p, 'random_state', 42),
            range=_range_key(p),
            wlo=_f(p, 'water_left', -0.5), whi=_f(p, 'water_right', 0.5),
            elo=_f(p, 'edge_left', -3.7), ehi=_f(p, 'edge_right', 3.6),
            merge=bool(p.get('merge', True)), how=str(p.get('how') or 'sum'),
            dpi=max(30, _i(p, 'dpi', 300)), fmt=str(p.get('fmt') or 'png'),
        )

    def run(self, p, ctx) -> dict:
        from cest_io import read_path_info

        t0 = time.time()
        sp = self._sp_file(p, ctx.proj)
        out_root = self.out_dir_for(p, ctx.proj)
        if sp is None or not sp.exists():
            raise RuntimeError(f'⑥ 需要 ⑤ 的地址清单：{sp}（先去 ⑤ PRF 提取）')
        if out_root is None:
            raise RuntimeError('没有输出目录：请填「输出根」或先选输出总目录（PROJ）。')
        dirs = read_path_info(sp)
        if not dirs:
            raise RuntimeError(f'{sp} 里没有地址（还没提取过训练集）→ 先去 ⑤。')

        mode = str(p.get('mode') or MODE_GROUP)
        folder = MODE_FOLDER.get(mode, '组内')
        cfg = self._cfg(p)

        out_root.mkdir(parents=True, exist_ok=True)
        mdir = out_root / folder
        stamp = time.strftime('%Y%m%d_%H%M%S')
        self._backup(mdir, stamp)          # 同名重跑先备份（不删）
        mdir.mkdir(parents=True, exist_ok=True)

        ctx.log(f'⑥ PRF 分析：模式「{mode}」　样式「{cfg["style"]}」')
        ctx.log(f'   输入 {sp}')
        rng_txt = {'clinical': 'clinical(±1.2~4.5)', 'rat7t': 'rat7t(±0.5~3.0)',
                   'custom': f'自定义 水峰 {cfg["wlo"]}~{cfg["whi"]} / 边缘 {cfg["elo"]}~{cfg["ehi"]}'}
        ctx.log(f'   地址 {len(dirs)} 个　频偏 {rng_txt.get(cfg["range"], cfg["range"])}　'
                f'合并 {"开" if cfg["merge"] else "关"}（{cfg["how"]}）')
        ctx.progress(2, 100, '读地址清单…')
        ctx.check_cancel()

        if mode == MODE_BETWEEN:
            meta = self._run_between(dirs, mdir, cfg, ctx)
        else:
            meta = self._run_ingroup(dirs, mdir, cfg, ctx,
                                     single=(mode == MODE_SINGLE))

        meta.update(mode=mode, style=cfg['style'], savepaths=str(sp),
                    out_dir=str(mdir), range=cfg['range'], n_repeats=cfg['n_repeats'],
                    n_permute=cfg['n_permute'], merge=cfg['merge'], how=cfg['how'],
                    n_dirs=len(dirs), sec=round(time.time() - t0, 2))
        (mdir / 'prf_analysis_meta.json').write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        self._rollup_meta(out_root, folder, meta)

        ctx.progress(100, 100, f'⑥ 完成（{time.time() - t0:.1f}s）')
        ctx.log(f'   产物目录：{mdir}')
        return dict(out_dir=str(mdir), case=f'PRF 分析（{mode}）', mode=mode,
                    n=meta.get('n_charts', 0), meta=meta, png=meta.get('png'),
                    files=[str(mdir), str(mdir / 'prf_analysis_meta.json')])

    # ---------------------------------------------------------------- 组内 / 单个对比
    def _run_ingroup(self, dirs, mdir, cfg, ctx, single) -> dict:
        import cest_group as CGRP
        from cest_io import load_mat

        names = [Path(d).name for d in dirs]
        dmap = {Path(d).name: Path(d) for d in dirs}
        sc = CGRP.scan(names, sep='_')
        ctx.log('   ' + CGRP.describe(sc).replace('\n', '\n   '))

        charts = OrderedDict()          # 图标签 -> [(柱子标签, 目录)]
        if single:
            for key in sc['keys']:
                lab = CGRP.label_of(sc, key)
                for nm in sc['groups'][key]:
                    charts.setdefault(lab, []).append((nm, dmap[nm]))
        else:
            lv = sc['levels']
            if len(lv) >= 2:
                split, bar = lv[0], lv[-1]
                for key in sc['keys']:
                    seg = sc['segments'][sc['keys'].index(key)]
                    sv = seg[split['pos']] if split['pos'] < len(seg) else key
                    bv = seg[bar['pos']] if bar['pos'] < len(seg) else key
                    charts.setdefault(sv, []).append((bv, dmap[sc['groups'][key][0]]))
            else:
                for key in sc['keys']:
                    charts.setdefault(MODE_GROUP, []).append(
                        (key, dmap[sc['groups'][key][0]]))

        for sv in charts:
            charts[sv].sort(key=lambda x: x[0])
        keys = sorted(charts.keys())
        n_charts = max(1, len(keys))
        ctx.log(f'   → 将出 {len(keys)} 张图')

        recs = []
        for i, cl in enumerate(keys, 1):
            ctx.check_cancel()
            ctx.progress(int(5 + 88 * i / n_charts), 100, f'[{i}/{len(keys)}] {cl}')
            results, labels, sheets = [], [], []
            for sl, d in charts[cl]:
                try:
                    X, y, wf = self._read_ingroup(load_mat, d)
                except Exception as e:                           # noqa: BLE001
                    ctx.log(f'   [跳过] {d.name}：{e}')
                    continue
                r = self._analyze(X, y, wf, cfg)
                if r is None:
                    ctx.log(f'   [跳过] {d.name}：频偏范围内没有有效频点')
                    continue
                results.append(r)
                labels.append(sl)
                sheets.append((sl, r))
            if not results:
                ctx.log(f'   [跳过] 图「{cl}」：没有可用数据')
                continue
            img = mdir / f'{cl}_频率重要性.{cfg["fmt"]}'
            self._plot(results, labels, img, cfg, f'{cl} 频率重要性')
            xls = mdir / f'{cl}_频率重要性.xlsx'
            self._write_xlsx(xls, sheets)
            recs.append(dict(chart=cl, png=str(img), xlsx=str(xls),
                             n_series=len(results),
                             n=[int(r['n']) for r in results],
                             r2=[round(float(r['r2']), 4) for r in results]))
            ctx.log(f'   {cl}：{len(results)} 组 → {img.name}')

        if not recs:
            raise RuntimeError('没有任何可分析的训练集：确认 ⑤ 对应产物齐全、'
                               '且频偏范围内有有效频点。')
        return dict(kind=('single' if single else 'ingroup'),
                    n_charts=len(recs), charts=recs, png=recs[0]['png'])

    # ---------------------------------------------------------------- 组间
    def _run_between(self, dirs, mdir, cfg, ctx) -> dict:
        from cest_io import load_mat

        sides = (('左脑', 'left'), ('右脑', 'right'))
        n_steps = max(1, len(dirs) * len(sides))
        recs = []
        step = 0
        for side_cn, side in sides:
            results, labels, sheets = [], [], []
            for d in dirs:
                ctx.check_cancel()
                step += 1
                ctx.progress(int(4 + 90 * step / n_steps), 100,
                             f'{side_cn}：{Path(d).name}')
                try:
                    X, y, wf = self._read_lr(load_mat, Path(d), side)
                except Exception as e:                           # noqa: BLE001
                    ctx.log(f'   [跳过] {side_cn} {Path(d).name}：{e}')
                    continue
                r = self._analyze(X, y, wf, cfg)
                if r is None:
                    ctx.log(f'   [跳过] {side_cn} {Path(d).name}：频偏范围内没有有效频点')
                    continue
                results.append(r)
                labels.append(Path(d).name)
                sheets.append((Path(d).name, r))
            if not results:
                ctx.log(f'   [跳过] {side_cn}：没有可用数据（缺 data/标签/w_offset？）')
                continue
            img = mdir / f'{side_cn}_频率重要性.{cfg["fmt"]}'
            self._plot(results, labels, img, cfg, f'{side_cn} 频率重要性')
            xls = mdir / f'{side_cn}_频率重要性.xlsx'
            self._write_xlsx(xls, sheets)
            recs.append(dict(chart=side_cn, png=str(img), xlsx=str(xls),
                             n_series=len(results),
                             n=[int(r['n']) for r in results],
                             r2=[round(float(r['r2']), 4) for r in results]))
            ctx.log(f'   {side_cn}：{len(results)} 组 → {img.name}')

        if not recs:
            raise RuntimeError('组间没有可用训练集：确认 ⑤ 组间产物里每个配对级目录都有 '
                               'trainDataLeft_ROI(dataLeft)/trainLabelLeft + w_offset.mat。')
        return dict(kind='between', n_charts=len(recs), charts=recs, png=recs[0]['png'])

    # ---------------------------------------------------------------- 读数据
    @staticmethod
    def _ld(load_mat, path, names):
        if not Path(path).exists():
            return None
        try:
            got = load_mat(path, list(names))
        except Exception:                                        # noqa: BLE001
            return None
        for n in names:
            if n in got:
                return np.asarray(got[n])
        return None

    def _read_ingroup(self, load_mat, d):
        d = Path(d)
        X = self._ld(load_mat, d / 'trainData.mat',
                     ['trainData', 'data', 'X', 'trainDataL'])
        y = self._ld(load_mat, d / 'trainLabel.mat', ['trainLabel', 'label', 'y'])
        w = self._ld(load_mat, d / 'w_offset.mat', ['w_offset', 'w', 'offset'])
        if X is None or y is None or w is None:
            raise FileNotFoundError('缺 trainData/trainLabel/w_offset')
        return X, y, w

    def _read_lr(self, load_mat, d, side):
        if side == 'left':
            X = self._ld(load_mat, d / 'trainDataLeft_ROI.mat',
                         ['dataLeft', 'trainDataLeft', 'totalTrainDataLeft', 'trainData'])
            y = self._ld(load_mat, d / 'trainLabelLeft.mat',
                         ['labelLeft', 'trainLabelLeft', 'trainLabel'])
        else:
            X = self._ld(load_mat, d / 'trainDataRight_ROI.mat',
                         ['dataRight', 'trainDataRight', 'totalTrainDataRight', 'trainData'])
            y = self._ld(load_mat, d / 'trainLabelRight.mat',
                         ['labelRight', 'trainLabelRight', 'trainLabel'])
        w = self._ld(load_mat, d / 'w_offset.mat', ['w_offset', 'w', 'offset'])
        if X is None or y is None or w is None:
            raise FileNotFoundError('缺 ' + side + ' 的 data/标签/w_offset')
        return X, y, w

    # ---------------------------------------------------------------- 频偏掩码
    def _mask(self, w_full, cfg):
        import prf_analysis as PA
        w = np.round(np.asarray(w_full).ravel().astype(float), 1)
        if cfg['range'] == 'rat7t':
            presets = PA.RANGE_PRESETS['rat7t']
        elif cfg['range'] == 'custom':
            presets = ((cfg['elo'], cfg['wlo']), (cfg['whi'], cfg['ehi']))
        else:
            presets = PA.RANGE_PRESETS['clinical']
        m = np.zeros(w.size, bool)
        for lo, hi in presets:
            m |= (w > lo) & (w < hi)
        return m, w

    # ---------------------------------------------------------------- 分析
    def _analyze(self, X, y, w_full, cfg):
        import prf_analysis as PA
        m, wfull = self._mask(w_full, cfg)
        if not m.any():
            return None
        w = wfull[m]                       # ★ 掩码后要一起切，作为特征名
        Xs = np.asarray(X, float)[:, m]
        ys = np.asarray(y).ravel()
        if Xs.shape[1] == 0 or ys.size == 0:
            return None

        kw = dict(n_permute=cfg['n_permute'], n_estimators=cfg['ne'],
                  max_depth=cfg['md'], min_samples_leaf=cfg['msl'])
        with _threading_backend():        # ★ 核心 n_jobs=-1 → 线程，避免 spawn 黑框
            if cfg['style'] == STYLE_SD:
                runs, r2s, oobs = [], [], []
                for k in range(cfg['n_repeats']):
                    r = PA.rf_importance(Xs, ys, w, random_state=cfg['rs'] + k,
                                         verbose=False, **kw)
                    runs.append(np.asarray(r['mean']).ravel())
                    r2s.append(r['train_r2'])
                    oobs.append(r['oob'])
                raw = np.asarray(runs)
                mean, std = raw.mean(axis=0), raw.std(axis=0)
                r2 = float(np.mean(r2s))
                oob = float(np.nanmean(oobs))
                n = int(len(ys))
            else:
                r = PA.rf_importance(Xs, ys, w, random_state=cfg['rs'],
                                     verbose=False, **kw)
                raw = np.asarray(r['raw'])
                mean = np.asarray(r['mean']).ravel()
                std = np.asarray(r['std']).ravel()
                r2, oob, n = float(r['train_r2']), float(r['oob']), int(r['n_sample'])

        wv, mv, sv, rv = w, mean, std, raw
        if cfg['merge']:
            wv, mv, sv, rv = PA.merge_peaks(w, mean, std, raw, how=cfg['how'],
                                            verbose=False)
        return dict(w=wv, mean=mv, std=sv, raw=rv, n=n, r2=r2, oob=oob)

    # ---------------------------------------------------------------- 出图
    def _plot(self, results, labels, path, cfg, title):
        import prf_analysis as PA
        if cfg['style'] == STYLE_SD:
            self._plot_errbar(results, labels, path, cfg, title)
        else:
            PA.plot_multi(results, labels, str(path), colors=COLORS,
                          title=title, n_permute=cfg['n_permute'], dpi=cfg['dpi'])

    def _plot_errbar(self, results, labels, path, cfg, title):
        import matplotlib.pyplot as plt
        w = np.asarray(results[0]['w']).ravel()
        nf, ng = w.size, len(results)
        bw = 0.5 / max(ng, 1)
        x = np.arange(nf)
        fig, ax = plt.subplots(figsize=(max(14, nf * 0.3), 7))
        for i, res in enumerate(results):
            off = (i - (ng - 1) / 2.0) * bw
            ax.bar(x + off, res['mean'], width=bw, color=COLORS[i % len(COLORS)],
                   label=f'{labels[i]}（n={res["n"]}）')
            ax.errorbar(x + off, res['mean'], yerr=res['std'], fmt='none',
                        ecolor='black', capsize=2, alpha=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels([f'{round(float(v), 2):g}' for v in w],
                           rotation=45, ha='right')
        ax.set_xlabel('Saturation Frequency Offset (ppm)', fontsize=14)
        ax.set_ylabel('Feature Importance (R² Drop)', fontsize=14)
        ax.set_title(title or '随机森林特征重要性', fontsize=15, pad=14)
        ax.grid(axis='y', alpha=0.3)
        ax.set_ylim(bottom=0)
        ax.legend(loc='upper right')
        fig.tight_layout()
        fig.savefig(str(path), dpi=cfg['dpi'], bbox_inches='tight')
        plt.close(fig)

    def _write_xlsx(self, path, sheets):
        import pandas as pd
        with pd.ExcelWriter(str(path), engine='openpyxl') as xw:
            for nm, r in sheets:
                w = np.asarray(r['w']).ravel()
                raw = np.asarray(r['raw'])
                cols = {'频率(ppm)': np.round(w, 2)}
                for k in range(min(10, raw.shape[0])):
                    cols[f'第{k + 1}次'] = raw[k, :]
                cols['均值'] = np.asarray(r['mean']).ravel()
                cols['标准差'] = np.asarray(r['std']).ravel()
                cols['样本量'] = [int(r['n'])] * w.size
                safe = ''.join(ch for ch in str(nm) if ch not in '[]:*?/\\')[:31] or 'sheet'
                pd.DataFrame(cols).to_excel(xw, sheet_name=safe, index=False)

    # ---------------------------------------------------------------- 小工具
    def _backup(self, d: Path, stamp: str):
        try:
            if d.exists():
                bak = d.with_name(f'{d.name}.bak_{stamp}')
                k = 1
                while bak.exists():
                    bak = d.with_name(f'{d.name}.bak_{stamp}_{k}')
                    k += 1
                d.rename(bak)
        except OSError:
            pass

    def _rollup_meta(self, out_root: Path, folder: str, meta: dict):
        """顶层 06_PRF分析\\prf_analysis_meta.json（给数据链自检看；各模式合并）。"""
        tp = out_root / 'prf_analysis_meta.json'
        roll = {'modes': {}}
        try:
            if tp.exists():
                old = json.loads(tp.read_text(encoding='utf-8'))
                if isinstance(old, dict) and isinstance(old.get('modes'), dict):
                    roll = old
        except Exception:                                        # noqa: BLE001
            pass
        roll['modes'][folder] = meta
        try:
            tp.write_text(json.dumps(roll, ensure_ascii=False, indent=2),
                          encoding='utf-8')
        except OSError:
            pass
