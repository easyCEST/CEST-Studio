# -*- coding: utf-8 -*-
"""detect/freq_doc.py —— 频率（ppm）的来源：解析 / 继承 / 存档 / 校验。

规则（已与用户确认）：
  * **不自动检索任何文档**：由用户在「频率表」对话框里**手动指定**一份文件；
  * 指定了就当场解析（UIH `.prot` / `.mat` / `.txt` / `.csv`），**读不出来就提示手动输入**；
  * 没指定就手动输入；
  * 4D 序列没有"确认过的频率表"一律**不许重建**（由 ① 的 validate 拦）。

频率表语义：
  * 填**频偏表**（唯一值；长度 = 每层频偏数）。若某频偏确实重复采集，可以写成重复值，
    但**位置只作提示**——多出来的帧由重建按物理判据自动判定并丢弃；
  * 核心层 `make_ssat` 取**唯一频偏**，并按 z 谱 ±k 镜像对称自动剔除「额外帧」→ `w_offset`。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

NUM_RE = re.compile(r'[-+]?\d+(?:\.\d+)?')
RANGE_A = re.compile(r'(-?\d+(?:\.\d+)?)\s*:\s*(-?\d+(?:\.\d+)?)\s*:\s*(-?\d+(?:\.\d+)?)')
RANGE_B = re.compile(r'(-?\d+(?:\.\d+)?)\s*(?:\.\.|~|至)\s*(-?\d+(?:\.\d+)?)')
STEP_RE = re.compile(r'步长|step|间隔', re.I)
PLUSMINUS = re.compile(r'±\s*(\d+(?:\.\d+)?)')
MAX_RANGE_LEN = 5000


@dataclass
class Candidate:
    values: list = field(default_factory=list)   # 采集顺序表（含重复）
    source: str = ''                             # 文件路径 / 说明
    line: int = 0
    raw: str = ''                                # 出处片段（给人看）
    confidence: str = 'high'                     # high / mid / low
    note: str = ''

    @property
    def n(self) -> int:
        return len(self.values)


# ----------------------------------------------------------------- 解析
def parse_numbers(s: str) -> list:
    out = []
    for m in NUM_RE.finditer(s or ''):
        try:
            out.append(float(m.group(0)))
        except ValueError:
            pass
    return out


def expand_range(start: float, step: float, stop: float) -> list:
    """起:步:止 → 列表（含端点）。"""
    if step == 0:
        return []
    n = int(round((stop - start) / step)) + 1
    if n <= 0 or n > MAX_RANGE_LEN:
        return []
    return [round(start + i * step, 10) for i in range(n)]


def parse_spec(text: str) -> list:
    """解析一段文本 → 频率列表。支持 `-12:0.1:12`、`-12..12 步长0.1`、±12、一串数字。"""
    t = (text or '').strip()
    if not t:
        return []
    m = RANGE_A.search(t)
    if m:
        return expand_range(float(m.group(1)), float(m.group(2)), float(m.group(3)))
    m2 = RANGE_B.search(t)
    if m2:
        a, b = float(m2.group(1)), float(m2.group(2))
        step = 0.1
        if STEP_RE.search(t):
            nums = [x for x in parse_numbers(t) if x not in (a, b) and 0 < abs(x) <= 5]
            if nums:
                step = nums[0]
        return expand_range(a, step, b)
    mpm = PLUSMINUS.search(t)
    if mpm and STEP_RE.search(t):
        r = float(mpm.group(1))
        nums = [x for x in parse_numbers(t) if 0 < abs(x) <= 5]
        step = nums[0] if nums else 0.1
        return expand_range(-r, step, r)
    vals = [v for v in parse_numbers(t) if -60 <= v <= 60]
    return vals if len(vals) >= 3 else []


def read_text(p: Path) -> str:
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'latin-1'):
        try:
            return p.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return ''


def parse_prot(p) -> list:
    """读 UIH 的 `.prot`（UProtocol XML）→ **频偏表（唯一值，列表顺序）**。

    字段：`FreqOffsetValue`（唯一频偏）/ `SsatScanNum`（每层采集帧数）/
    `TargetFreq` / `TargetOffsetAverage`。

    ★ **不再凭空补「重复的 3.5」**：每层 Ssat 帧数比频偏多出的那一帧是「额外扫描」，
      由重建时按谱线物理判据（±k 镜像对称）自动识别并丢弃
      （见 ``cest_recon.resolve_frame_offsets``）。
    """
    import xml.etree.ElementTree as ET

    p = Path(p)
    try:
        root = ET.fromstring(read_text(p))
    except Exception:                                            # noqa: BLE001
        return []

    def val(tag):
        for el in root.iter(tag):
            t = ''.join(el.itertext()).strip()
            if t:
                return t
        return ''

    uniq = parse_numbers(val('FreqOffsetValue'))
    if len(uniq) < 2:
        return []
    return uniq


# ----------------------------------------------------------------- 继承旧结果
def _flat(a):
    import numpy as np
    return np.asarray(a).ravel().tolist()


def load_freq_from_mat(p) -> list | None:
    """从 .mat 里读频率：优先 freq_seq（采集顺序），退化到 w_offset。"""
    try:
        from cest_io import load_mat
    except Exception:                                            # noqa: BLE001
        return None
    names = ('freq_seq', 'freq_sequence', 'freq_list', 'w_offset', 'w', 'freq')
    try:
        got = load_mat(Path(p), names)
    except Exception:                                            # noqa: BLE001
        return None
    for n in ('freq_seq', 'freq_sequence', 'freq_list'):
        if n in got:
            v = [float(x) for x in _flat(got[n])]
            if len(v) >= 3:
                return v
    for n in ('w_offset', 'w', 'freq'):
        if n in got:
            v = [float(x) for x in _flat(got[n])]
            if len(v) >= 3:
                return v
    return None


def inherit_freq(case_dir, proj=None, case='') -> Candidate | None:
    """从病例目录 / PROJ 里已有的结果继承频率（不递归、不检索）。"""
    case_dir = Path(case_dir)
    files = [case_dir / nm for nm in
             ('freq.mat', 'w_offset.mat', 'freq_seq.mat', 'freq_sequence.mat')]
    if proj and case:
        from .rules import find_subdir
        _rd = find_subdir(proj, '01_recon') or Path(proj) / '01_重建'
        files += [_rd / case / nm for nm in ('freq.mat', 'w_offset.mat')]
    try:
        for e in sorted(case_dir.iterdir()):
            if e.is_dir():
                files += [e / nm for nm in ('freq.mat', 'freq_sequence.mat')]
    except OSError:
        pass
    for f in files:
        if f.exists():
            v = load_freq_from_mat(f)
            if v:
                return Candidate(values=v, source=str(f), line=0, raw=f.name,
                                 confidence='high', note='继承已有结果')
    return None


# ----------------------------------------------------------------- 存档
def freq_dir(proj) -> Path:
    from .rules import find_subdir
    d = find_subdir(proj, '01_recon', make=True)
    return d / '_频率'


def saved_path(proj, case: str) -> Path:
    return freq_dir(proj) / f'{case}.json'


def load_saved(proj, case: str) -> Candidate | None:
    p = saved_path(proj, case) if (proj and case) else None
    if not (p and p.exists()) and proj and case:
        # 兼容老项目：老存档在 <01_recon>\_freq\<病例>.json
        from .rules import find_subdir
        old = find_subdir(proj, '01_recon')
        if old is not None and (old / '_freq' / f'{case}.json').exists():
            p = old / '_freq' / f'{case}.json'
    if not (p and p.exists()):
        return None
    try:
        d = json.loads(p.read_text(encoding='utf-8'))
        v = [float(x) for x in d.get('freq_seq', [])]
        if len(v) >= 3:
            return Candidate(values=v, source=str(p), line=0, raw='上次确认',
                             confidence='high',
                             note=f"上次确认：{d.get('confirmed_at', '')} {d.get('source', '')}")
    except Exception:                                            # noqa: BLE001
        return None
    return None


def save(proj, case: str, values, source: str = '', note: str = '') -> Path:
    import time
    d = freq_dir(proj)
    d.mkdir(parents=True, exist_ok=True)
    p = saved_path(proj, case)
    payload = dict(case=case, freq_seq=[float(x) for x in values],
                   w_offset=[float(x) for x in dedup(values)[0]],
                   source=source, note=note,
                   confirmed_at=time.strftime('%Y-%m-%d %H:%M:%S'))
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return p


# ----------------------------------------------------------------- 去重 / 校验
def dedup(values) -> tuple:
    """按首次出现顺序去重（与核心层 make_ssat 语义一致）→ (唯一值, 出现次数)。"""
    order, cnt = [], {}
    for v in values:
        v = float(v)
        if v not in cnt:
            cnt[v] = 0
            order.append(v)
        cnt[v] += 1
    return order, cnt


def summarize(values, need: int, exact: bool = False) -> dict:
    """校验频率表个数。

    exact=True  —— 必须**恰好** need 个（Philips：频偏数 = 帧数 − M0 帧数）；
    exact=False —— 允许 ≤ need（UIH：多出来的帧由重建自动识别并丢弃）。
    """
    vals = [float(x) for x in (values or [])]
    uniq, cnt = dedup(vals)
    msgs = []
    n_un = len(uniq)
    if exact:
        ok = (len(vals) == need) if need else len(vals) > 0
        if need:
            msgs.append(f'√ {len(vals)} 个 == 每层 {need} 帧' if ok
                        else f'× 个数 {len(vals)} ≠ 每层 {need} 帧（这份频率不能用于这个序列）')
    else:
        ok = (n_un >= 2) and (not need or n_un <= need)
        if need:
            if n_un == need:
                msgs.append(f'√ {n_un} 个频偏 == 每层 {need} 帧')
            elif n_un < need:
                msgs.append(f'√ {n_un} 个频偏 ＜ 每层 {need} 帧 → 多出的 {need - n_un} 帧'
                            f'将由重建按谱线物理判据自动识别为「额外帧」并丢弃')
            else:
                msgs.append(f'× 频偏 {n_un} 个 ＞ 每层 {need} 帧 —— 频偏数不能多于帧数')
    dup = {k: v for k, v in cnt.items() if v > 1}
    if dup:
        s = '、'.join(f'{k:g}×{v}' for k, v in dup.items())
        msgs.append(f'注意：表里有重复 {s} —— 位置只作提示，重建会按数据判定多余帧')
    return dict(n=len(vals), n_unique=n_un, unique=uniq, counts=cnt,
                ok=ok, msgs=msgs, dup=dup)
