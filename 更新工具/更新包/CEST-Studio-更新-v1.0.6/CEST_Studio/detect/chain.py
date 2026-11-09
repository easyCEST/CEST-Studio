# -*- coding: utf-8 -*-
"""detect/chain.py —— 数据链解析器（壳内；核心层 03_Python_全链路\\函数库 一行不动）。

用户要求「输入目录按模块独立」：
    ① DICOM 重建 只吃**原始 DICOM**；
    ⑧ 去噪掩模 / ④ 逐体素LD / ② 多层图谱 / ③ 后处理 … 的**输入目录 = 输出目录 = 项目目录 PROJ**。
把某个数据处理模块的输入根设成 PROJ 后，本模块负责**自动解析**出它需要的东西：

    病例 + 数据目录（01_重建\\<病例>）+ 掩模（00_掩模\\<病例>\\Mask.mat）

并把「缺哪一步、去哪补」讲清楚（missing / hint），供界面红字 + 【开始】置灰。

用法（只在壳里用）：
    from detect import chain
    r = chain.resolve(proj, 'm4_ld')        # 病例自动选
    r = chain.resolve(proj, 'm4_ld', 'c1')  # 指定病例
    r.ready / r.case / r.src_dir / r.mask_file / r.missing / r.hint
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import rules as R

# 每个模块需要 PROJ 里的哪些「上游产物」（空 = 不吃 PROJ，只吃原始 DICOM）
NEEDS = {
    'm1_recon':   (),                       # 只吃原始 DICOM
    'm_import':   (),                       # ⓪ 导入重建：吃任意 .mat 目录，不吃 PROJ
    'm_roi':      ('01_recon',),            # 底图 S0 + 手画 ROI
    'm3_post':    ('01_recon', '00_roi'),   # ③ 后处理：重建数据 + 去噪掩模
    'm4_ld':      ('01_recon', '00_roi'),   # 重建数据 + 去噪掩模（两个都是硬门禁）
    'm5_prf':     ('03_post', '00_roi'),    # PRF 提取：跨病例组织，自己解析（chain_proj=False）
    'm6_prf_analysis': (),                  # ⑥ PRF 分析：吃 ⑤ 的 savepaths.mat，自解析
    'm2_display': ('01_recon', '00_roi'),   # 也可复用 04_ld / 02_display 的 img_interp
    'm0_check':   (),
}

# 输入根默认 = PROJ 的数据处理模块（拖拽区「应用到所有数据处理模块」也按这个列表）
DATA_MODULES = tuple(k for k, v in NEEDS.items() if v)

# 数据链自检顺序（界面上 ① → ⑧ → ③ → ④ → ⑤ → ②）
STAGE_ORDER = ('m1_recon', 'm_roi', 'm3_post', 'm4_ld', 'm5_prf',
               'm6_prf_analysis', 'm2_display')
STAGE_NAME = {
    'm1_recon':   '① 重建',
    'm_roi':      '⑧ 掩模',
    'm3_post':    '③ 后处理',
    'm4_ld':      '④ 逐体素LD',
    'm5_prf':     '⑤ PRF提取',
    'm6_prf_analysis': '⑥ PRF分析',
    'm2_display': '② 展示',
}


# ----------------------------------------------------------------- 小工具
def _sub(proj, key):
    """PROJ 下的固定子目录（中文优先，兼容旧英文名）。"""
    return R.find_subdir(proj, key) if proj else None


def has_recon(d) -> bool:
    """是不是 ① 的重建结果目录（s0 / ssat / w_offset 齐全）。"""
    return bool(d) and R.has_all(Path(d), R.RECON_FILES)


def list_cases(proj) -> list:
    """PROJ 里的病例（01_重建 ∪ 04_逐体素LD）。

    ★ 一律走 R.iter_cases()：跳过 `_`/`.` 开头的元数据目录
      （如 01_重建\\_频率，它是频率协议 json，不是病例）。
    """
    proj = str(proj or '').strip()
    if not proj:
        return []
    seen, out = set(), []
    for key in ('01_recon', '04_ld'):
        d = _sub(proj, key)
        if d is None:
            continue
        for e in R.iter_cases(d):
            if e.name not in seen:
                seen.add(e.name)
                out.append(e.name)
    out.sort()
    return out


def _first_case(d):
    if d is None:
        return ''
    for e in R.iter_cases(d):
        return e.name
    return ''


def pick_case(proj, case=None) -> str:
    """病例优先级：用户给的 → 04_逐体素LD 里已有的 → 01_重建 里第一个。"""
    c = str(case or '').strip()
    if c and c in list_cases(proj):
        return c
    return (_first_case(_sub(proj, '04_ld'))
            or _first_case(_sub(proj, '01_recon')) or c or '')


def recon_dir(proj, case):
    d = _sub(proj, '01_recon')
    return (d / str(case)) if (d and case) else None


def roi_dir(proj, case):
    d = _sub(proj, '00_roi')
    return (d / str(case)) if (d and case) else None


def mask_file(proj, case):
    """00_掩模\\<病例>\\Mask.mat；找不到再退 Mask/mask/liverMask/Thmask。"""
    d = roi_dir(proj, case)
    if d is None:
        return None
    for n in ('Mask.mat', 'mask.mat', 'liverMask.mat', 'Thmask.mat'):
        f = d / n
        if f.exists():
            return f
    return None


def interp_file(proj, case):
    """② 的展示数据：优先 04_逐体素LD\\<病例>\\，再 02_展示\\<病例>\\_cache\\。"""
    for key, rels in (('04_ld', (Path('0.7ut_img_interp.mat'),)),
                      ('02_display', (Path('_cache') / '0.7ut_img_interp.mat',
                                      Path('0.7ut_img_interp.mat')))):
        d = _sub(proj, key)
        if d is None:
            continue
        for rel in rels:
            f = d / str(case) / rel
            if f.exists():
                return f
    return None


def relpath(proj, p) -> str:
    """显示用短路径：PROJ 内显示相对路径，PROJ 外显示原名。"""
    if p is None:
        return ''
    p = Path(p)
    try:
        return str(p.relative_to(Path(proj)))
    except Exception:                                        # noqa: BLE001
        return str(p)


# ----------------------------------------------------------------- 解析结果
@dataclass
class Resolved:
    proj: str = ''
    module: str = ''
    case: str = ''
    cases: list = field(default_factory=list)
    src_dir: str = ''          # ① 重建数据目录（PROJ\01_重建\<病例>）
    mask_file: str = ''        # 去噪掩模（PROJ\00_掩模\<病例>\Mask.mat）
    missing: list = field(default_factory=list)
    ready: bool = False
    hint: str = ''


def resolve(proj, module, case=None) -> Resolved:
    """解析某模块在 PROJ 上的数据链。proj 为空 / 不适用时也返回一个（missing 里说明）。"""
    proj = str(proj or '').strip()
    res = Resolved(proj=proj, module=str(module or ''))
    needs = NEEDS.get(module, ())

    # ⑤ PRF 提取 / ⑥ PRF 分析：跨病例组织数据（自解析），不走单病例数据链
    if module in ('m5_prf', 'm6_prf_analysis'):
        res.ready = True
        return res

    # ① / ⑦：不吃 PROJ 的数据链（⑦ 只读自检）
    if not needs:
        res.ready = True
        return res

    if not proj:
        res.missing.append('还没选「输出总目录（PROJ）」')
        res.hint = '⑧④② 的输入目录默认就是输出总目录（PROJ）；先在顶部选一个。'
        return res

    res.cases = list_cases(proj)
    if not res.cases:
        where = relpath(proj, Path(proj) / R.cn_name('01_recon'))
        res.missing.append(f'PROJ 里还没有病例（{where} 下没有病例子目录）')
        res.hint = '先跑 ① DICOM 重建（或把已有的重建结果放进 01_重建），再来这一页。'
        return res

    case = pick_case(proj, case)
    res.case = case
    rd = recon_dir(proj, case)
    if has_recon(rd):
        res.src_dir = str(rd)

    # ---- ① 重建数据（m2_display 允许用 ④/② 的 img_interp 顶替） ----
    if '01_recon' in needs and not res.src_dir:
        if module == 'm2_display' and interp_file(proj, case) is not None:
            pass
        else:
            p = relpath(proj, rd) if rd is not None else \
                str(Path(R.cn_name('01_recon')) / case)
            res.missing.append(f'缺 ① 重建数据：{p}（要 s0 / ssat / w_offset）'
                               f' → 先去 ① DICOM 重建')
    # ---- ⑧ 去噪掩模 ----
    if '00_roi' in needs:
        mf = mask_file(proj, case)
        if mf is not None:
            res.mask_file = str(mf)
        else:
            p = str(Path(R.cn_name('00_roi')) / case / 'Mask.mat')
            res.missing.append(f'缺 ⑧ 去噪掩膜：{p} → 先去 ⑧ 去噪掩模画好/载入')

    res.ready = not res.missing
    res.hint = ('可以跑了。' if res.ready
                else '补齐上面的缺项后【开始】才会亮（本软件不会自动生成掩模）。')
    return res


def _p5_done(proj) -> bool:
    """⑤ PRF 提取：PROJ\\05_PRF提取 下有没有任何地址清单（三个子文件夹都算）。"""
    p5 = _sub(proj, '05_prf_extract')
    if not p5:
        return False
    try:
        for pat in ('*Paths.mat', 'savepaths.mat'):
            if next(p5.rglob(pat), None) is not None:
                return True
    except OSError:
        return False
    return False


def _p6_done(proj) -> bool:
    """⑥ PRF 分析：PROJ\\06_PRF分析 下有没有 prf_analysis_meta.json。"""
    p6 = _sub(proj, '06_prf_analysis')
    return bool(p6 and (p6 / 'prf_analysis_meta.json').exists())


def _any_case(proj, key) -> bool:
    """PROJ 的某个固定子目录下有没有 ≥1 个病例目录。"""
    d = _sub(proj, key)
    if not d:
        return False
    try:
        return next(R.iter_cases(d), None) is not None
    except OSError:
        return False


def stage_status_proj(proj) -> dict:
    """**项目级**自检（没选病例 / 跨病例页时用）：某阶段在 PROJ 里到底做没做过。

    ⑤⑥ 本来就是跨病例的；①⑧③④② 只问「有没有 ≥1 个病例做过」，
    这样即使当前页没绑定病例，也不会一片「待跑」误导人。
    """
    proj = str(proj or '').strip()
    st = {k: False for k in STAGE_ORDER}
    if not proj:
        return st
    st['m1_recon'] = _any_case(proj, '01_recon')
    st['m_roi'] = _any_case(proj, '00_roi')
    st['m3_post'] = _any_case(proj, '03_post')
    st['m4_ld'] = _any_case(proj, '04_ld')
    st['m5_prf'] = _p5_done(proj)
    st['m6_prf_analysis'] = _p6_done(proj)
    st['m2_display'] = _any_case(proj, '02_display')
    return st


def stage_status(proj, case) -> dict:
    """数据链自检：每个模块「做完了 / 还没跑」。

    * 给了 <病例> → ①⑧③④② 按该病例逐个查；⑤⑥ 仍按项目级（它们跨病例组织）。
    * 没给病例（跨病例页 ⑤⑥，或还没选）→ 整条退回**项目级** `stage_status_proj`，
      避免全都误报「待跑」。
    """
    proj = str(proj or '').strip()
    st = {k: False for k in STAGE_ORDER}
    if not proj:
        return st
    if not case:
        return stage_status_proj(proj)
    st['m1_recon'] = has_recon(recon_dir(proj, case))
    st['m_roi'] = mask_file(proj, case) is not None
    p3 = _sub(proj, '03_post')
    c3 = (p3 / str(case)) if p3 else None
    try:
        st['m3_post'] = bool(c3 is not None and c3.is_dir()
                             and any(c3.rglob('post_meta.json')))
    except OSError:
        st['m3_post'] = False
    ld = _sub(proj, '04_ld')
    c4 = (ld / str(case)) if ld else None
    try:
        st['m4_ld'] = bool(c4 is not None and c4.is_dir()
                           and any(c4.rglob('voxel_LD.mat')))
    except OSError:
        st['m4_ld'] = False
    st['m5_prf'] = _p5_done(proj)             # ⑤ 是跨病例的（项目级）
    st['m6_prf_analysis'] = _p6_done(proj)    # ⑥ 是跨病例的（项目级）
    d2 = _sub(proj, '02_display')
    cd = (d2 / str(case)) if d2 else None
    try:
        st['m2_display'] = bool(cd is not None and cd.is_dir()
                                and any(cd.rglob('*.png')))
    except OSError:
        st['m2_display'] = False
    return st


def describe_stages(proj, case) -> str:
    """一行文字：① ✓ → ⑧ ✓ → ④ 待跑 → ② 待跑。"""
    st = stage_status(proj, case)
    return ' → '.join(f'{STAGE_NAME[k]} {"✓" if st[k] else "待跑"}' for k in STAGE_ORDER)
