# -*- coding: utf-8 -*-
"""detect/series.py —— 序列扫描器。

干两件事：
  1. 把病例目录下**每个含 DICOM 的文件夹**摸清：帧数 / 层数 / 每层帧数 / 采集序 / 维度(3D|4D)；
  2. 按「同前缀 + 编号相邻」匹配出 S0 / Ssat / B0Ma 一组，并把**缺哪些、还有哪些候选**报出来。

慢操作（读 DICOM 标签）只在需要时对**匹配到的几个文件夹**做，且带缓存。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 名字拆三段：前缀 / 标签 / 编号
NAME_RE = re.compile(r'^(?P<prefix>.+?)_(?P<tag>[A-Za-z0-9.]+?)_(?P<num>\d+)$')

DEFAULT_TAGS = ('S0', 'Ssat', 'B0Ma')
DEFAULT_SEQ_BASE = 'cest_tra_fast_10slice_0.7ut'

# 别名 → 规范标签（老一套命名用 B0Map，新一套用 B0Ma，指的是同一个东西）。
# 键一律小写，查表时把 tag 也转小写，保证大小写混写也能认出来。
TAG_ALIAS = {
    's0': 'S0',
    'ssat': 'Ssat',
    'b0ma': 'B0Ma',
    'b0map': 'B0Ma',
    'b0maps': 'B0Ma',
    'b0': 'B0Ma',
}


def canon_tag(tag: str) -> str:
    """把命名里的标签归一到规范标签；不认识的原样返回。"""
    return TAG_ALIAS.get((tag or '').lower(), tag)


_CACHE: dict = {}


@dataclass
class Dim:
    n_frames: int = 0
    n_slices: int = 0
    fps: int = 0                 # 每层帧数
    order: str = 'unknown'       # 采集序
    error: str = ''

    @property
    def ndim(self) -> str:
        if self.error or not self.fps:
            return '?'
        return '3D' if self.fps == 1 else '4D'

    @property
    def text(self) -> str:
        if self.error:
            return '读取失败'
        return f'{self.ndim}（{self.n_frames}帧 / {self.n_slices}层 = 每层 {self.fps} 帧）'


@dataclass
class Seq:
    name: str
    path: str
    n_dcm: int = 0
    tag: str = ''
    num: int = -1
    dim: Dim = field(default_factory=Dim)

    @property
    def is4d(self) -> bool:
        return self.dim.ndim == '4D'


@dataclass
class Group:
    case: str = ''
    case_dir: str = ''
    prefix: str = ''
    seqs: dict = field(default_factory=dict)        # tag -> Seq（已匹配到的）
    missing: list = field(default_factory=list)     # 缺哪些 tag
    candidates: list = field(default_factory=list)  # 其它可选组合（编号列表）
    others: list = field(default_factory=list)      # 不参与匹配的文件夹
    error: str = ''


# ---------------------------------------------------------------- 便宜的操作
def count_dcm(d: Path) -> int:
    try:
        return sum(1 for e in d.iterdir() if e.is_file() and e.suffix.lower() == '.dcm')
    except OSError:
        return 0


def parse_name(name: str):
    m = NAME_RE.match(name)
    if not m:
        return None
    return m.group('prefix'), canon_tag(m.group('tag')), int(m.group('num'))


def list_dicom_folders(case_dir) -> list:
    """列出**直接子目录**里含 .dcm 的（只数文件名，很快）。"""
    d = Path(case_dir)
    out = []
    try:
        it = list(d.iterdir())
    except OSError:
        return out
    for e in sorted(it, key=lambda p: p.name):
        if not e.is_dir():
            continue
        n = count_dcm(e)
        if n:
            p = parse_name(e.name)
            out.append(dict(name=e.name, path=str(e), n_dcm=n,
                            prefix=p[0] if p else '', tag=p[1] if p else '',
                            num=p[2] if p else -1))
    return out


# ---------------------------------------------------------------- 慢操作（带缓存）
def scan_dims(folder, slice_max=None) -> Dim:
    """读 DICOM 标签 → 帧数/层数/每层帧数/采集序。带缓存。"""
    folder = Path(folder)
    try:
        key = (str(folder), folder.stat().st_mtime_ns, slice_max)
    except OSError as e:
        return Dim(error=f'读不到目录：{e}')
    if key in _CACHE:
        return _CACHE[key]
    dim = Dim()
    try:
        from dicom_order import detect_nesting, read_series_meta
        meta = read_series_meta(folder)
        r = detect_nesting(meta, slice_max=slice_max)
        dim = Dim(n_frames=r['n_frames'], n_slices=r['n_slices'],
                  fps=r['frames_per_slice'], order=r['order'])
    except Exception as e:                                       # noqa: BLE001
        dim = Dim(error=f'{type(e).__name__}: {e}')
    _CACHE[key] = dim
    return dim


# ---------------------------------------------------------------- 组合匹配
def _combos(nums_by_tag, tags):
    """在 (标签→编号列表) 里找「编号相邻」的组合，返回 [ [num per tag] ... ]。"""
    per_tag = [sorted(set(nums_by_tag.get(t, []))) for t in tags]
    if any(not x for x in per_tag):
        return [], [t for t, x in zip(tags, per_tag) if not x]
    out = []
    for a in per_tag[0]:
        for b in per_tag[1]:
            for c in per_tag[2]:
                ns = sorted([a, b, c])
                if len(set(ns)) == 3 and ns[1] - ns[0] == 1 and ns[2] - ns[1] == 1:
                    out.append([a, b, c])
    # 严格按 tags 顺序递增的排前面
    out.sort(key=lambda ns: (0 if (ns[1] == ns[0] + 1 and ns[2] == ns[1] + 1) else 1,
                             -max(ns)))
    return out, []


def match_group(case_dir, seq_base=None, tags=DEFAULT_TAGS,
                slice_max=None, scan=True) -> Group:
    """在病例目录里匹配一组序列（同前缀 + 编号相邻）。"""
    case_dir = Path(case_dir)
    g = Group(case=case_dir.name, case_dir=str(case_dir))
    folders = list_dicom_folders(case_dir)
    if not folders:
        g.error = '这个目录下没有含 .dcm 的子文件夹'
        return g

    # 按前缀归类
    by_prefix: dict = {}
    for f in folders:
        if not f['prefix']:
            g.others.append(f['name'])
            continue
        by_prefix.setdefault(f['prefix'], []).append(f)

    # 选前缀：优先完全等于 seq_base；否则含 tags 最多；再否则编号最大
    def score(pfx):
        fs = by_prefix[pfx]
        have = len({f['tag'] for f in fs} & set(tags))
        mx = max((f['num'] for f in fs), default=-1)
        return (0 if (seq_base and pfx == seq_base) else 1, -have, -mx)

    pfx = sorted(by_prefix, key=score)[0] if by_prefix else ''
    if not pfx:
        g.error = '这些文件夹的名字都不是「前缀_标签_编号」的形式'
        g.others = [f['name'] for f in folders]
        return g
    g.prefix = pfx

    fs = by_prefix[pfx]
    nums_by_tag: dict = {}
    path_by: dict = {}
    for f in fs:
        nums_by_tag.setdefault(f['tag'], []).append(f['num'])
        path_by[(f['tag'], f['num'])] = f

    combos, missing = _combos(nums_by_tag, tags)
    if not combos:
        g.missing = missing or list(tags)
        g.others = [f['name'] for f in folders if f['prefix'] != pfx]
        g.error = (f'前缀「{pfx}」下凑不齐编号相邻的 ' + ' / '.join(tags) +
                   f'；缺：{g.missing or "无（编号不相邻）"}')
        return g

    main = combos[0]
    g.candidates = sorted({tuple(ns) for ns in combos[1:]})
    g.others = [f['name'] for f in folders if f['prefix'] != pfx]

    for t, n in zip(tags, main):
        f = path_by.get((t, n))
        if f is None:
            g.missing.append(t)
            continue
        s = Seq(name=f['name'], path=f['path'], n_dcm=f['n_dcm'], tag=t, num=n)
        if scan:
            s.dim = scan_dims(s.path, slice_max=slice_max)
        g.seqs[t] = s
    for t in tags:
        if t not in g.seqs and t not in g.missing:
            g.missing.append(t)
    return g
