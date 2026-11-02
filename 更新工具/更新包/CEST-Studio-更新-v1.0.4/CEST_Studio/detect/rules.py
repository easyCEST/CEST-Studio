# -*- coding: utf-8 -*-
"""detect/rules.py —— 目录「指纹」判定规则（对应方案 §5.1 的表）。

只做**文件名/目录名**级别的判断，不读像素、不加载 mat（快）。
"""
from __future__ import annotations

import re
from pathlib import Path

# --- UIH 三序列：cest_tra_fast_10slice_0.7ut_{S0,Ssat,B0Ma}_<编号> ---
UIH_RE = re.compile(r'^cest_tra_fast_10slice_0\.7ut_(S0|Ssat|B0Ma)_(\d+)$', re.I)

# --- 项目（PROJ）固定子目录 ---
# PROJ 下的固定子目录。**写盘用中文名**（看不懂英文也能认）；
# 旧的英文名也照样识别（已跑过的老项目不用改）。
PROJ_SUBDIRS = ('00_roi', '01_recon', '02_display', '03_post',
                '04_ld', '05_prf_extract', '06_prf_analysis', 'logs')
SUBDIR_CN = {
    '00_roi':          '00_掩模',
    '01_recon':        '01_重建',
    '02_display':      '02_展示',
    '03_post':         '03_CEST后处理',
    '04_ld':           '04_逐体素LD',
    '05_prf_extract':  '05_PRF提取',
    '06_prf_analysis': '06_PRF分析',
    'logs':            '日志',
}


def cn_name(key: str) -> str:
    """ASCII 键 → 写盘用的中文目录名（不在表里的原样返回）。"""
    return SUBDIR_CN.get(str(key), str(key))


def names_of(key: str) -> tuple:
    """中文名在前、旧英文名在后（读取时先找中文，找不到再找英文）。"""
    cn = cn_name(key)
    return (cn,) if cn == key else (cn, key)


def find_subdir(root, key: str, make: bool = False):
    """在 PROJ 里找某个固定子目录；中文优先，兼容旧英文名。

    make=True 时即使不存在也返回「中文名」的路径（调用方自己 mkdir）。
    """
    root = Path(root)
    for n in names_of(key):
        d = root / n
        if d.is_dir():
            return d
    return (root / cn_name(key)) if make else None

# --- 各类指纹文件名 ---
RECON_FILES = ('s0.mat', 'ssat.mat', 'w_offset.mat')                 # ① 的产物
INTERP_FILES = ('0.7ut_img_interp.mat', '0p7P_interp.mat',           # 展示输入
                '0p7N_interp.mat', '2APT_interp.mat', '0.7ut_img_raw.mat')
ROI_FILES = ('Mask.mat', 'liverMask.mat', 'Thmask.mat', 'ROI_total.mat')
TRAIN_FILES = ('trainData.mat', 'trainLabel.mat', 'savepaths.mat')


def iter_dirs(d: Path):
    try:
        for e in d.iterdir():
            if e.is_dir():
                yield e
    except OSError:
        return


def iter_cases(d: Path):
    """病例目录：跳过 `_` / `.` 开头的元数据目录（`_频率` / `_缓存` / `_预设` …）。

    ① 会在 01_重建 下落 `_频率\<病例>.json`（频率协议留痕），它不是病例；
    `_` 的 ASCII 比字母小 → 不跳就会排在最前，被当成第一个病例（输入/病例名全错）。
    """
    for e in iter_dirs(d):
        if not e.name.startswith(('_', '.')):
            yield e


def has_all(d: Path, names) -> bool:
    return all((d / n).exists() for n in names)


def has_any(d: Path, names) -> bool:
    return any((d / n).exists() for n in names)


def is_project_root(d: Path) -> bool:
    """自身就含 00_roi / 01_recon … 这类固定子目录 → 是一个 PROJ。"""
    hits = sum(1 for s in PROJ_SUBDIRS if find_subdir(d, s) is not None)
    return hits >= 2


def count_dcm(d: Path) -> int:
    """只数**本层**的 *.dcm（递归会很慢，不需要）。"""
    try:
        return sum(1 for e in d.iterdir() if e.is_file() and e.suffix.lower() == '.dcm')
    except OSError:
        return 0


def _uih_tags(d: Path, depth: int = 2) -> dict:
    """在 d 下（深度≤depth）找 UIH 三序列目录，返回 {tag: (path, 编号)}。"""
    res: dict = {}
    if depth < 0:
        return res
    for e in iter_dirs(d):
        m = UIH_RE.match(e.name)
        if m:
            res.setdefault(m.group(1), (e, int(m.group(2))))
            continue
        if depth > 0:
            for k, v in _uih_tags(e, depth - 1).items():
                res.setdefault(k, v)
    return res


def _is_consecutive(tags: dict) -> bool:
    """编号是否相邻（方案要求 S0/Ssat/B0Ma 三组编号相邻）。"""
    if len(tags) < 3:
        return False
    nums = sorted(v[1] for v in tags.values())
    return nums == list(range(nums[0], nums[0] + len(nums)))


def find_uih_cases(root: Path, depth: int = 0) -> dict:
    """root 可能是「病例目录本身」或「病例的容器」，两种都支持。

    * root 自己直接含 S0/Ssat/B0Ma 三个目录 → 单病例（病例名 = root.name）
    * 否则把每个**直接子目录**当一个病例，看它里面有没有那三个目录

    返回 {病例名: {tag: path}}。
    """
    own = _uih_tags(root, depth=depth)
    if len(own) >= 3 and _is_consecutive(own):
        return {root.name: {k: v[0] for k, v in own.items()}}
    out = {}
    for e in iter_dirs(root):
        tags = _uih_tags(e, depth=depth)
        if len(tags) >= 3 and _is_consecutive(tags):
            out[e.name] = {k: v[0] for k, v in tags.items()}
    return out


def find_post_dirs(root: Path, depth: int = 1) -> list:
    """找 full_interp_* 且下面有 sliceN\\Self_corr\\V_exp.mat 的目录。

    depth 默认 1：即「root 的直接子目录里」或「root 下面一层」——
    避免从很高级的目录（如 2026\\09）一路递归把别处的后处理结果也捞出来。
    """
    out = []
    root = Path(root)
    if root.name.startswith('full_interp'):
        subs = list(iter_dirs(root))
        if any((s / 'Self_corr' / 'V_exp.mat').exists() for s in subs):
            out.append(root)
    stack = [(root, 0)]
    while stack:
        d, lvl = stack.pop()
        if lvl > depth:
            continue
        for e in iter_dirs(d):
            if e.name.startswith('full_interp'):
                subs = list(iter_dirs(e))
                if any((s / 'Self_corr' / 'V_exp.mat').exists() for s in subs):
                    out.append(e)
                continue
            stack.append((e, lvl + 1))
    return out


def find_recon_case_dirs(root: Path, depth: int = 0) -> list:
    """找含 s0/ssat/w_offset 的病例目录（① 的产物）。

    depth 默认 0：只认「root 的直接子目录就是病例」的情况（如 CEST_result\c1..hp3），
    避免从上级目录把多个数据集的病例混在一起。
    """
    out = []
    stack = [(root, 0)]
    while stack:
        d, lvl = stack.pop()
        if lvl > depth:
            continue
        for e in iter_dirs(d):
            if has_all(e, RECON_FILES):
                out.append(e)
            elif lvl < depth:
                stack.append((e, lvl + 1))
    return out
