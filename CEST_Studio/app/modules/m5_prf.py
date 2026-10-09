# -*- coding: utf-8 -*-
"""app/modules/m5_prf.py —— ⑤ PRF 提取（把 CEST 数据变成随机森林能吃的训练集）。

**按用户原代码口径复刻，两个模式，点一次全出：**

  组内
    · 按组   → 每个「组」（如 ctrl_3H）一份训练集      （老鼠 6 份）
    · 单个   → 每个病例一份                            （老鼠 36 份）
  组间
    · 按时间 → 每个配对级取值一份：左/右分开（奇对奇、偶对偶）
               （老鼠 3H/1W/5W 各一份，每份含 左.dataLeft / 右.dataRight）

每份都同时出「左 / 右」两套（ROI 奇=左、偶=右）：
    组内：trainDataLeft_ROI.mat(var totalTrainDataLeft) + trainDataRight_ROI.mat(var totalTrainDataRight)
    单个：trainDataLeft_ROI<k>.mat(var trainDataLeft) + trainDataRight_ROI<j>.mat(var trainDataRight)
    组间：trainDataLeft_ROI.mat(var dataLeft) + trainLabelLeft.mat(var labelLeft)  + 右同

**分组不写死名字**：走核心层 `cest_group`（按病例名前缀自动识别几级分组）。
hier 结构（大鼠原始）则按目录层级 g1_g2 分组。
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import numpy as np

from .base import Field, Module
from detect import rules as R

MODE_CHOICES = ('cestpost（③ 后处理产物）', 'hier（大鼠原始结构）', 'flat（临床 结果N/…）')
TASK_IN = '组内（同一组内部对比）'
TASK_BET = '组间（不同组之间对比）'
UNIT_GROUP = '按组（同一组的老鼠合并成一份）'
UNIT_ONE = '单个（每只一份）'
NORM_CHOICES = ('不除 S0（原始信号）', '除 S0（÷S0+eps）')


def _mode_key(p) -> str:
    v = str(p.get('mode') or '')
    return 'hier' if v.startswith('hier') else ('flat' if v.startswith('flat') else 'cestpost')


def _i(p, k, d=0):
    try:
        return int(float(str(p.get(k)).strip()))
    except (TypeError, ValueError):
        return d


def _f(p, k, d=0.0):
    try:
        return float(str(p.get(k)).strip())
    except (TypeError, ValueError):
        return d


def _parse_region(txt):
    """'1,3,5;2,4,6' → [[1,3,5],[2,4,6]]；空 → None。"""
    s = str(txt or '').strip()
    if not s:
        return None
    out = []
    for grp in s.split(';'):
        nums = [int(x) for x in grp.replace('，', ',').split(',') if x.strip().isdigit()]
        if nums:
            out.append(nums)
    return out or None


class PrfExtractModule(Module):
    id = 'm5_prf'
    badge = '⑤'
    title = 'PRF 提取（训练集）'
    out_subdir = '05_prf_extract'
    case_in_out = False
    accepts = ('post_mat', 'project_root', 'recon_mat', 'recon_root')
    produces = ('trainset',)
    chain_proj = False               # 跨病例组织（自解析），不挂单病例数据链
    schema = (
        Field('src', '数据根', 'dir', '',
              tip='放“后处理结果”的目录。\ncestpost：给 PROJ 根也行（会自动下钻到 03_CEST后处理）。\n'
                  'hier：大鼠原始目录（含 ctrl/model）。'),
        Field('out_dir', '输出根（留空=PROJ\\05_PRF提取）', 'dir', ''),
        Field('mode', '目录结构', 'choice', MODE_CHOICES[0], MODE_CHOICES,
              tip='cestpost=③ 后处理产物；hier=大鼠原始结构；flat=临床 结果N/…'),
        Field('task', '分析模式', 'choice', TASK_IN, (TASK_IN, TASK_BET),
              tip=f'{TASK_IN}：组内比（左 vs 右），按「粒度」出全部组或全部病例。\n'
                  f'{TASK_BET}：按“配对级”自动两两对比（老鼠=按时间 3H/1W/5W，ctrl vs model），\n'
                  f'每份都分 左/右 两套（奇对奇、偶对偶）。'),
        Field('unit', '粒度（仅组内）', 'choice', UNIT_GROUP, (UNIT_GROUP, UNIT_ONE),
              tip=f'{UNIT_GROUP} = 同一组的老鼠合并成一份；{UNIT_ONE} = 每个病例一份。',
              show_if=('task', (TASK_IN,))),
        Field('sep', '病例名分隔符', 'text', '_',
              tip='用来把病例名切段识别“几级分组”。默认 _（如 ctrl_3H_小鼠1 → ctrl / 3H / 小鼠1）。'),
        Field('region_roi_idx', '部位划分（左;右）', 'text', '',
              tip='留空 = 按 ROI 奇偶自动分（奇=左、偶=右）。也可写 1,3,5;2,4,6。'),
        # ---------------- 高级 ----------------
        Field('norm', '信号归一', 'choice', NORM_CHOICES[0], NORM_CHOICES,
              advanced=True, group='数值'),
        Field('epsilon', 'epsilon（分母保护）', 'float', 1e-8, advanced=True, group='数值'),
        Field('level1', 'hier 第1级（组别）', 'text', '', advanced=True, group='hier 结构'),
        Field('level2', 'hier 第2级（时间）', 'text', '', advanced=True, group='hier 结构'),
        Field('max_animal', 'hier 每组取前 N 只（0=全部）', 'int', 0, advanced=True, group='hier 结构'),
        Field('pick_rule', 'hier 取只规则（min/max）', 'choice', 'min', ('min', 'max'),
              advanced=True, group='hier 结构'),
        Field('filename', '结果文件名过滤（留空=自动）', 'text', '', advanced=True, group='高级'),
        Field('stage_prefix', '结果目录前缀', 'text', 'full_interp', advanced=True, group='高级'),
        Field('sub_path', 'flat 子路径', 'text', '', advanced=True, group='高级'),
        Field('ratio_list', 'flat 结果目录名单', 'text', '', advanced=True, group='高级'),
        Field('reset_savepaths', '重跑前重置 savepaths（旧的备份）', 'bool', False,
              advanced=True, group='高级'),
    )

    def stage_overrides(self, p, proj) -> dict:
        """按「分析模式 + 粒度」判断 ⑤ 该不该亮：只认该模式对应的那份产物。"""
        if not proj:
            return {}
        task = str(p.get('task') or TASK_IN)
        if task == TASK_BET:
            sub, name = '组间', 'newGroupPaths.mat'
        elif str(p.get('unit') or '').startswith('按组'):
            sub, name = '组内_按组', 'groupPaths.mat'
        else:
            sub, name = '组内_单个', 'resultPaths.mat'
        d5 = R.find_subdir(proj, '05_prf_extract')
        done5 = bool(d5 and ((d5 / sub / name).exists() or (d5 / name).exists()))
        return {'m5_prf': done5}

    # ---------------------------------------------------------------- 路径
    def out_dir_for(self, p, proj):
        od = str(p.get('out_dir') or '').strip()
        if od:
            return Path(od)
        if not proj:
            return None
        return Path(proj) / R.cn_name(self.out_subdir)

    # ---------------------------------------------------------------- 扫描
    def _data_root(self, src, mode):
        srcp = Path(str(src))
        if mode == 'cestpost':
            d = R.find_subdir(srcp, '03_post')
            if d is not None and Path(d) != srcp:
                return Path(d)
        return srcp

    def _gen_args(self, p, src):
        mode = _mode_key(p)
        l1 = [x for x in str(p.get('level1') or '').replace('，', ',').split(',') if x.strip()]
        l2 = [x for x in str(p.get('level2') or '').replace('，', ',').split(',') if x.strip()]
        rl = [x for x in str(p.get('ratio_list') or '').replace('，', ',').split(',') if x.strip()]
        return dict(root_dir=str(self._data_root(src, mode)), mode=mode,
                    sub_path=str(p.get('sub_path') or '').strip(),
                    ratio_list=rl or None,
                    level1=l1 or None, level2=l2 or None,
                    max_animal=_i(p, 'max_animal', 0) or None,
                    pick_rule=str(p.get('pick_rule') or 'min'),
                    filename=str(p.get('filename') or '').strip(),
                    stage_prefix=str(p.get('stage_prefix') or 'full_interp').strip(),
                    verbose=False)

    def _scan(self, p, src):
        import prf_extract as PE
        return PE.gen_paths(**self._gen_args(p, src))

    def _grouping(self, p, recs):
        """返回 (names, name_of_rec, group_of_name, label_of_name, extra)。

        * cestpost/flat：病例名 = rec['g1']；分组走 cest_group（按名字前缀）。
        * hier：分组走目录层级 g1_g2。
        """
        import cest_group as CGRP
        mode = _mode_key(p)
        if mode == 'hier':
            namemap = {}
            for r in recs:
                k = str(r.get('name') or r.get('g1'))
                grp = f"{r.get('g1')}_{r.get('g2')}" if r.get('g2') else str(r.get('g1'))
                namemap.setdefault(k, grp)
            groups = {}
            for k, g in namemap.items():
                groups.setdefault(g, []).append(k)
            return namemap, groups, None
        names = []
        for r in recs:
            n = str(r.get('g1'))
            if n not in names:
                names.append(n)
        sc = CGRP.scan(names, sep=str(p.get('sep') or '_'))
        return names, sc, CGRP

    # ---------------------------------------------------------------- 校验
    def validate(self, p, ctx=None):
        errs = []
        src = str(p.get('src') or '').strip()
        proj = getattr(ctx, 'proj', None)
        if not src and not proj:
            errs.append('⑤ 需要「数据根」或「输出总目录（PROJ）」')
        elif src and not Path(src).exists():
            errs.append(f'数据根不存在：{src}')
        if not self.out_dir_for(p, proj):
            errs.append('⑤ 需要「输出根」，或先选「输出总目录（PROJ）」')
        return errs

    def preview_files(self, p, proj):
        d = self.out_dir_for(p, proj)
        if not d:
            return []
        t = str(p.get('task') or '')
        if t == TASK_BET:
            b = d / '组间'
            return [str(b), str(b / '训练集最终版' / '<配对级>\\'),
                    str(b / '训练集最终版' / '<配对级>\\trainDataLeft_ROI.mat + trainLabelLeft.mat'),
                    str(b / '训练集最终版' / '<配对级>\\trainDataRight_ROI.mat + trainLabelRight.mat'),
                    str(b / 'newGroupPaths.mat')]
        sub = '组内_按组' if str(p.get('unit') or '').startswith('按组') else '组内_单个'
        b = d / sub
        return [str(b), str(b / '<组名 或 病例名>\\trainData.mat + trainLabel.mat + w_offset.mat'),
                str(b / '<组名 或 病例名>\\trainDataLeft_ROI.mat + trainDataRight_ROI.mat'),
                str(b / ('groupPaths.mat' if sub == '组内_按组' else 'resultPaths.mat'))]

    # ---------------------------------------------------------------- 运行
    def _fresh(self, p: Path, stamp: str):
        """同名重跑：已有产物先改名成 <名>.bak_<时间戳>（不删，遵循项目铁律）。"""
        try:
            if p.exists():
                bak = p.with_name(f'{p.name}.bak_{stamp}')
                k = 1
                while bak.exists():
                    bak = p.with_name(f'{p.name}.bak_{stamp}_{k}')
                    k += 1
                p.rename(bak)
        except OSError:
            pass

    def _dirs_of(self, recs, names, want):
        ds, rs = [], []
        ws = set(want)
        for r in recs:
            key = str(r.get('g1'))
            if key in ws:
                ds.append(r['dir'])
                rs.append(r.get('roi_dir'))
        return ds, rs

    def run(self, p, ctx):
        import prf_extract as PE
        from scipy.io import savemat
        from cest_io import load_mat

        src = str(p.get('src') or '').strip() or str(getattr(ctx, 'proj', '') or '')
        base = self.out_dir_for(p, ctx.proj)
        if base is None:
            raise RuntimeError('⑤ 需要输出根（或先选 PROJ）。')
        base.mkdir(parents=True, exist_ok=True)
        _mode_dir = ('组内_按组' if (str(p.get('task') or '') == TASK_IN
                                    and str(p.get('unit') or '').startswith('按组'))
                     else ('组内_单个' if str(p.get('task') or '') == TASK_IN else '组间'))
        base = base / _mode_dir          # 三个模式各占一个子文件夹，互不干扰
        base.mkdir(parents=True, exist_ok=True)
        droot = self._data_root(src, _mode_key(p))
        task = str(p.get('task') or TASK_IN)
        unit_group = str(p.get('unit') or '').startswith('按组')
        norm = 0 if str(p.get('norm') or '').startswith('不除') else 1
        eps = _f(p, 'epsilon', 1e-8)
        rroi = _parse_region(p.get('region_roi_idx'))
        sep = str(p.get('sep') or '_')

        ctx.log(f'⑤ PRF 提取：{task}　{"按组" if unit_group else "单个"}　数据根 {droot}')
        recs = self._scan(p, src)
        if not recs:
            raise RuntimeError(
                f'在 {droot} 里一条都没扫到（结构={_mode_key(p)}）。\n'
                f'cestpost 需要 <病例>\\full_interp_*\\sliceN\\Self_corr\\V_exp.mat（先跑 ③）。')

        # ---- 预检：每个记录要同时有 V_exp(或V_as)/S0/ROI_total/w_offset ----
        from cest_io import find_files
        bad = []
        for r in recs:
            ff = find_files(r['dir'])
            if not ff.get('ok'):
                lack = [lbl for k, lbl in (('data', 'V_exp/V_as'), ('s0', 'S0'),
                                           ('roi', 'ROI_total'), ('w', 'w_offset'))
                        if not ff.get(k)]
                bad.append((r.get('name') or r['dir'], lack))
        ctx.log(f'   预检：扫到 {len(recs)} 个记录 → 完整 {len(recs) - len(bad)}、缺文件 {len(bad)}')
        if bad:
            for nm, lack in bad[:20]:
                ctx.log(f'      · {nm}：缺 {"、".join(lack)}')
            raise RuntimeError(f'⑤ 需要 V_exp(或V_as)/S0/ROI_total/w_offset，'
                               f'上面 {len(bad)} 个缺 → 先去 ③ / ⑧ 补齐。')

        sp_file = base / ('groupPaths.mat' if (task == TASK_IN and unit_group)
                          else ('resultPaths.mat' if task == TASK_IN else 'newGroupPaths.mat'))
        sp_key = sp_file.stem
        if str(p.get('reset_savepaths') or '').lower() in ('true', '1') and sp_file.exists():
            bak = sp_file.with_suffix('.bak_%s.mat' % time.strftime('%Y%m%d_%H%M%S'))
            shutil.copy2(sp_file, bak)
            sp_file.unlink()

        results = []
        stamp = time.strftime('%Y%m%d_%H%M%S')
        # ================= 组内 =================
        if task == TASK_IN:
            names, sc, CGRP = self._grouping(p, recs)
            if isinstance(sc, dict):                       # cestpost/flat：名字分组
                ctx.log('   ' + CGRP.describe(sc).replace('\n', '\n   '))
                plan = [(CGRP.label_of(sc, k), CGRP.members_of(sc, k)) for k in sc['keys']] \
                    if unit_group else None
            else:                                          # hier：目录层级分组
                plan = None
            if not unit_group:
                plan = [(n, [n]) for n in names]
            total = len(plan)
            ctx.log(f'   → 将产出 {total} 份训练集')
            for i, (label, members) in enumerate(plan, 1):
                ctx.check_cancel()
                ctx.progress(int(i * 100 / total), 100, f'[{i}/{total}] {label}')
                ds, rs = self._dirs_of(recs, names, members)
                out = base / label
                self._fresh(out, stamp)
                rec, X = PE.build_regions(ds, out, region_roi_idx=rroi, norm=norm,
                                          epsilon=eps, roi_dir=rs,
                                          add_to_path_list=False, verbose=False)
                self._write_lr(out, X, unit_group, savemat)
                ctx.log(f'   [{i}/{total}] {label}：{rec.get("n_voxel", "?")} 体素　{out}')
                results.append(str(out))
        # ================= 组间 =================
        else:
            names, sc, CGRP = self._grouping(p, recs)
            if not isinstance(sc, dict):
                raise RuntimeError('组间模式需要“按病例名前缀分组”的结构（cestpost/flat）；'
                                   'hier 请用组内模式。')
            pp = CGRP.pair_plan(names, sep=sep)
            ctx.log('   ' + CGRP.describe(sc).replace('\n', '\n   '))
            ctx.log(f'   组间配对：{pp["total"]} 对（{pp["mode"]}）')
            bdir = base / '训练集最终版'
            total = max(1, pp['total'])
            for i, pr in enumerate(pp['pairs'], 1):
                ctx.check_cancel()
                tag = pr['bucket'] or f'{pr["A"]}_vs_{pr["B"]}'
                ctx.progress(int(i * 100 / total), 100, f'[{i}/{total}] {tag} {pr["A"]} vs {pr["B"]}')
                ad, ar = self._dirs_of(recs, names, pr['membersA'])
                bd, br = self._dirs_of(recs, names, pr['membersB'])
                out = bdir / tag
                self._fresh(out, stamp)
                out.mkdir(parents=True, exist_ok=True)
                lr = self._roi_sets(recs, names, rroi)
                self._subjects_side(PE, ad, bd, ar, br, out, 'Left', lr[0], norm, eps,
                                    pr, CGRP, savemat, load_mat)
                self._subjects_side(PE, ad, bd, ar, br, out, 'Right', lr[1], norm, eps,
                                    pr, CGRP, savemat, load_mat)
                ctx.log(f'   [{i}/{total}] {tag}  {pr["A"]} vs {pr["B"]}　{out}')
                results.append(str(out))
            # newGroupPaths
        # savepaths（最后一次性写：变量名/形状对齐原代码 groupPaths / resultPaths / newGroupPaths）
        cells = np.empty((1, len(results)), dtype=object)
        for _i2, _s in enumerate(results):
            cells[0, _i2] = str(_s)
        savemat(str(sp_file), {sp_key: cells, 'pathInfo': cells})
        meta = dict(task=task, unit='group' if unit_group else 'single', mode=_mode_key(p),
                    sep=sep, norm=norm, epsilon=eps, region_roi_idx=rroi,
                    data_root=str(droot), n_record=len(recs), outputs=results,
                    sec=round(time.time() - _T0, 1) if '_T0' in globals() else None)
        import json
        (base / 'prf_extract_meta.json').write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        return dict(out_dir=str(base), case=f'{task}', n_ok=len(results),
                    files=[str(base), str(sp_file)], results=results)

    # ------------------------------------------------------------ 小工具
    def _write_lr(self, out, X, unit_group, savemat):
        """写 左/右（组内用 totalTrainDataLeft/Right；单个用 trainDataLeft/Right + ROI 号）。"""
        if not isinstance(X, (list, tuple)) or len(X) < 2:
            return
        L, R = np.asarray(X[0], float), np.asarray(X[1], float)
        if unit_group:
            savemat(str(out / 'trainDataLeft_ROI.mat'), {'totalTrainDataLeft': L})
            savemat(str(out / 'trainDataRight_ROI.mat'), {'totalTrainDataRight': R})
        else:
            savemat(str(out / 'trainDataLeft_ROI1.mat'), {'trainDataLeft': L})
            savemat(str(out / 'trainDataRight_ROI2.mat'), {'trainDataRight': R})

    def _roi_sets(self, recs, names, rroi):
        """返回 (左ROI号, 右ROI号)。rroi 给了就用，否则按 ROI 总数定奇偶。"""
        if rroi and len(rroi) >= 2:
            return list(rroi[0]), list(rroi[1])
        n = 2
        try:
            from cest_io import load_first
            from pathlib import Path as _P
            r = recs[0]
            f = _P(r['roi_dir']) / 'ROI_total.mat' if r.get('roi_dir') else None
            if f and f.exists():
                v = load_first(f)
                a = np.asarray(v, dtype=object)
                n = int(a.ravel().size) if a.dtype == object else 1
        except Exception:
            n = 2
        left = [i + 1 for i in range(n) if (i + 1) % 2 == 1]
        right = [i + 1 for i in range(n) if (i + 1) % 2 == 0]
        return left or [1], right or [1]

    def _subjects_side(self, PE, ad, bd, ar, br, out, side, rois, norm, eps,
                       pr, CGRP, savemat, load_mat):
        tmp = out / ('_tmp' + side)
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        rec, (X1, X2) = PE.build_subjects(ad, bd, tmp, roi_idx=rois, norm=norm, epsilon=eps,
                                          roi_dir1=ar, roi_dir2=br,
                                          add_to_path_list=False, verbose=False)
        X1, X2 = np.asarray(X1, float), np.asarray(X2, float)
        data = np.concatenate([X1, X2], axis=0) if X1.size and X2.size else \
            (X1 if X1.size else X2)
        lab = np.concatenate([np.ones((len(X1), 1)), np.zeros((len(X2), 1))]) \
            if X1.size and X2.size else (np.ones((len(X1), 1)) if X1.size else np.zeros((len(X2), 1)))
        savemat(str(out / f'trainData{side}_ROI.mat'), {f'data{side}': data})
        savemat(str(out / f'trainLabel{side}.mat'), {f'label{side}': lab})
        w = load_mat(tmp / 'w_offset.mat') if (tmp / 'w_offset.mat').exists() else None
        if w is not None:
            for k, v in (w.items() if isinstance(w, dict) else []):
                if not str(k).startswith('__'):
                    savemat(str(out / 'w_offset.mat'), {k: np.asarray(v)})
                    break
        shutil.rmtree(tmp, ignore_errors=True)

    def _append_path(self, sp_file, d):
        from scipy.io import loadmat, savemat
        arr = []
        if sp_file.exists():
            try:
                v = loadmat(str(sp_file))
                for k, vv in v.items():
                    if not str(k).startswith('__'):
                        arr = [str(x) for x in np.asarray(vv, dtype=object).ravel()]
                        break
            except Exception:
                arr = []
        arr.append(str(d))
        arr = list(dict.fromkeys(arr))
        key = sp_file.stem
        savemat(str(sp_file), {key: np.array(arr, dtype=object).reshape(-1, 1)})


_T0 = time.time()
