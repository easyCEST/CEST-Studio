# -*- coding: utf-8 -*-
"""detect/scanner.py —— 输入总目录「探测器」：扫目录 → 认类型 → 列出可用模块。

用法（也能当命令行自测）：
    python detect/scanner.py "C:\\...\\20260909"
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import rules as R

KIND_LABEL = {
    'project_root':  'CEST 项目目录（PROJ）',
    'dicom_uih':     'UIH 三序列 CEST（原始 DICOM）',
    'dicom_philips': 'Philips 单序列 CEST（原始 DICOM）',
    'recon_root':    '已重建目录（多病例）',
    'recon_mat':     '已重建结果（单病例）',
    'img_mat':       '展示输入（img_interp）',
    'roi_mat':       'ROI / 掩模目录',
    'post_mat':      '后处理结果（PRF 输入）',
    'trainset':      'PRF 训练集',
    'container':     '上级目录（下面有多个数据集）',
    'unknown':       '未识别',
}

# 每种类型「天生能用」的模块 id
KIND_MODULES = {
    'dicom_uih':     ('m1_recon',),
    'dicom_philips': ('m1_recon',),
    'recon_root':    ('m_roi', 'm3_post', 'm4_ld', 'm2_display'),
    'recon_mat':     ('m_roi', 'm3_post', 'm4_ld', 'm2_display'),
    'img_mat':       ('m_roi', 'm3_post', 'm2_display'),
    'roi_mat':       ('m_roi', 'm3_post', 'm2_display'),
    'post_mat':      ('m_roi', 'm3_post', 'm5_prf', 'm6_prf_analysis'),
    'trainset':      ('m5_prf', 'm6_prf_analysis'),
    'proj_recon':    ('m_roi', 'm3_post', 'm4_ld', 'm2_display'),
    'project_root':  ('m_roi', 'm3_post', 'm4_ld', 'm5_prf', 'm6_prf_analysis',
                      'm2_display'),
}


@dataclass
class Detection:
    path: str = ''
    kind: str = 'unknown'
    label: str = ''
    lines: list = field(default_factory=list)      # 摘要（显示在界面上）
    cases: list = field(default_factory=list)      # 病例名（UIH / recon_root）
    meta: dict = field(default_factory=dict)       # 额外信息
    modules: list = field(default_factory=list)    # 可用模块 id
    candidates: list = field(default_factory=list)  # 多候选时列出

    @property
    def ok(self) -> bool:
        return self.kind != 'unknown'


def _mat_shapes(path: Path, names) -> dict:
    """用 whosmat 只读头信息拿尺寸（不加载数据）。"""
    try:
        from scipy.io import whosmat
        got = {n: tuple(s) for n, s, _ in whosmat(str(path))}
        return {n: got[n] for n in names if n in got}
    except Exception:                                        # noqa: BLE001
        return {}


def detect(path) -> Detection:
    p = Path(str(path)).expanduser()
    if not p.exists():
        return Detection(str(p), 'unknown', KIND_LABEL['unknown'], [f'路径不存在：{p}'])
    if p.is_file():
        p = p.parent

    det = detect_dir(p)

    # ---- 多候选提示（既有 dcm 又有 mat 之类） ----
    if det.candidates:
        det.lines.append('⚠ 多个候选，请确认：' + ' / '.join(det.candidates))
    return det


def _dirs(d: Path):
    try:
        return [e for e in sorted(d.iterdir(), key=lambda x: x.name) if e.is_dir()]
    except OSError:
        return []


def _classify_child(c: Path):
    """粗略判断一个子目录是什么（只看本层，不递归）。"""
    try:
        if R.is_project_root(c):
            return 'project_root'
        if R.find_uih_cases(c):
            return 'dicom_uih'
        if R.has_all(c, R.RECON_FILES):
            return 'recon_mat'
        if R.has_any(c, R.INTERP_FILES):
            return 'img_mat'
        if R.has_any(c, R.ROI_FILES):
            return 'roi_mat'
        if R.has_all(c, R.TRAIN_FILES):
            return 'trainset'
        if R.find_post_dirs(c, depth=1):
            return 'post_mat'
        if R.find_recon_case_dirs(c, depth=0):
            return 'recon_root'
        if R.count_dcm(c) >= 50:
            return 'dicom_philips'
    except OSError:
        pass
    return None


def _container_kids(p: Path):
    """直接子目录里能认出来的数据集；排掉「只是一堆已重建病例」那种（留给 recon_root）。"""
    kids = []
    for c in _dirs(p):
        k = _classify_child(c)
        if k:
            kids.append(dict(name=c.name, path=str(c), kind=k,
                             label=KIND_LABEL.get(k, k)))
    return kids


def detect_dir(p: Path) -> Detection:
    d = Detection(path=str(p))

    # ---------- 1) 本体就直接命中的类型 ----------
    if R.is_project_root(p):
        d.kind = 'project_root'
        return _fill_project(p, d)

    uih = R.find_uih_cases(p)
    if uih:
        d.kind = 'dicom_uih'
        d.cases = sorted(uih)
        d.meta['seq'] = {c: {k: str(v) for k, v in t.items()} for c, t in uih.items()}
        d.lines.append(f'UIH 三序列 CEST · {len(uih)} 个病例（{" ".join(d.cases)}）')
        d.lines.append('Ssat 620 帧 → 20 层 × 31 时相；采集嵌套由 ① 读 DICOM 标签自动判定')
        return _finish(p, d)

    if R.has_all(p, R.RECON_FILES):
        d.kind = 'recon_mat'
        sh = _mat_shapes(p / 's0.mat', ('s0', 'S0'))
        if sh:
            d.lines.append(f'已重建：s0 {sh.get("s0") or sh.get("S0")}')
        d.lines.append('缺展示用的 img_interp → ② 会先自动跑轻量 make_img_interp（不做 LD 拟合）')
        d.lines.append('④ 逐体素LD 可直接在这上面跑（需要 00_掩模\\<病例>\\Mask.mat）')
        d.cases = [p.name]
        return _finish(p, d)

    if R.has_any(p, R.INTERP_FILES):
        d.kind = 'img_mat'
        d.lines.append('展示输入：可直接出多层拼接图')
        d.cases = [p.name]
        return _finish(p, d)

    if R.has_any(p, R.ROI_FILES):
        d.kind = 'roi_mat'
        d.lines.append('ROI / 掩模目录（⑧ 的产物；②③④ 会从这里取）')
        return _finish(p, d)

    if R.has_all(p, R.TRAIN_FILES):
        d.kind = 'trainset'
        d.lines.append('发现 trainData / trainLabel / savepaths.mat')
        return _finish(p, d)

    # ---------- 2) 后处理（只在「本层」或「下一层」，不从很高级的目录乱搜） ----------
    post = R.find_post_dirs(p, depth=1)
    if post:
        d.kind = 'post_mat'
        d.lines.append(f'后处理结果 {len(post)} 个：'
                       + '、'.join(f'{x.parent.name}/{x.name}' for x in post[:4]))
        return _finish(p, d)

    # ---------- 3) Philips 单序列 DICOM ----------
    n_dcm = R.count_dcm(p)
    if n_dcm >= 50:
        d.kind = 'dicom_philips'
        d.lines.append(f'Philips 单序列：本层 {n_dcm} 个 .dcm（= 一个序列）')
        d.lines.append('单序列重建：需要层数 n_layer 与 M0 时相（默认 10 层 / TPI 1,2,33）')
        d.meta['n_dcm'] = n_dcm
        return _finish(p, d)

    # ---------- 4) 上级目录：下面有别的数据集 → 让用户选一个往下走 ----------
    kids = _container_kids(p)
    if kids and any(k['kind'] != 'recon_mat' for k in kids):
        d.kind = 'container'
        d.meta['children'] = kids
        d.lines.append(f'这是上级目录，下面有 {len(kids)} 个数据集：')
        for c in kids[:8]:
            d.lines.append(f'　　{c["name"]} → {c["label"]}')
        d.lines.append('请在下面「下级数据集」里选一个。')
        return _finish(p, d)

    # ---------- 5) 多病例的已重建目录 ----------
    recon_cases = R.find_recon_case_dirs(p, depth=0)
    if recon_cases:
        d.kind = 'recon_root'
        d.cases = sorted(x.name for x in recon_cases)
        sh = _mat_shapes(recon_cases[0] / 's0.mat', ('s0', 'S0'))
        d.lines.append(f'已重建（多病例）{len(recon_cases)} 例：{" ".join(d.cases)}')
        if sh:
            d.lines.append(f'样例 s0 形状 {sh.get("s0") or sh.get("S0")}')
        return _finish(p, d)

    if kids:
        d.kind = 'container'
        d.meta['children'] = kids
        d.lines.append(f'这是上级目录，下面有 {len(kids)} 个数据集：')
        for c in kids[:8]:
            d.lines.append(f'　　{c["name"]} → {c["label"]}')
        return _finish(p, d)

    # ---------- 6) 都没命中 ----------
    d.kind = 'unknown'
    d.lines.append('没认出已知结构。可试着选上一级目录，或确认里面有 DICOM / .mat。')
    return _finish(p, d)


def _fill_project(p: Path, d: Detection) -> Detection:
    done, allcases = [], []
    for sub in R.PROJ_SUBDIRS:
        sp = R.find_subdir(p, sub)
        if sp is None:
            continue
        # ★ 用 iter_cases：`_频率` / `_缓存` / `_预设` 这类元数据目录不算病例
        cases = sorted(x.name for x in R.iter_cases(sp))
        allcases += cases
        done.append(f'{sp.name}（{len(cases)} 例）' if cases else sp.name)
    d.lines.append('项目目录；已完成：' + ('、'.join(done) if done else '（空）'))
    d.cases = sorted(set(allcases))

    # ② 有活干：01_recon 或 02_display 里有病例
    _rc = R.find_subdir(p, '01_recon')
    if _rc is not None and any(R.iter_cases(_rc)):
        d.modules.append('m2_display')
    _rd2 = R.find_subdir(p, '02_display')
    if _rd2 is not None and any(R.iter_cases(_rd2)):
            # （已并入上一行）
        d.lines.append('下一步建议：② 换个频偏/mode 继续出图，或 ④ 逐体素LD')
    d.lines.append('说明：ⓐ①⑧③④⑤⑥② 已接入（⑧ 去噪掩模 + 器官 ROI、② 多层图谱展示都在）；'
                   '⑥ PRF 分析（组间/组内左右，吃 ⑤ 的 savepaths.mat）已接入')
    return _finish(p, d)


def _finish(p: Path, d: Detection) -> Detection:
    if not d.label:
        d.label = KIND_LABEL.get(d.kind, d.kind)
    mods = list(KIND_MODULES.get(d.kind, ()))
    if d.kind in ('recon_mat', 'img_mat', 'project_root'):
        mods.append('m0_check')
    else:
        mods.append('m0_check')
    # 去重保序
    seen, out = set(), []
    for m in mods:
        if m not in seen:
            seen.add(m)
            out.append(m)
    d.modules = out
    d.meta.setdefault('n_files', 0)
    return d


def describe(det: Detection) -> str:
    """格式化识别结果（界面 + 命令行共用）。"""
    s = [f'识别：{det.label}']
    s += ['　' + x for x in det.lines]
    s.append(f'路径：{det.path}')
    return '\n'.join(s)


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:                                  # 无参数就自测几个已知目录
        args = [r'C:\Users\Administrator\Desktop\2026\09\20260909',
                r'C:\Users\Administrator\Desktop\ZHAO LEI\APT_0.7UT',
                r'C:\Users\Administrator\Desktop\2026\09\CEST_result',
                r'C:\Users\Administrator\Desktop\2026\09\CEST_result\c1']
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                                        # noqa: BLE001
        pass
    for a in args:
        print('=' * 70)
        print(describe(detect(a)))
        print('可用模块：', detect(a).modules)
