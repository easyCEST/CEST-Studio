# -*- coding: utf-8 -*-
"""cest_agent.doctor —— 错误诊断：把报错翻成「这是什么问题 + 你该做什么」。

两级来源，都不靠猜：
  1. 种子规则（下面 RULES）：从 CEST Studio 代码里 `raise RuntimeError('…')` /
     `errs.append('…')` 的原文抽出来的，天然是人话；
  2. 兜底：没命中就给最小复现信息（末行 + 上下文），不做无根据推测。

处置三类（fix_kind）：
  auto        agent 能自己修（目录不存在、层号越界留空等）
  human_gui   必须用户到某个页面手做（掩模 / ROI / 频率表）
  manual_arg  需要用户给一个具体数值（B0 比例系数等）
  bug         像是真 bug / 环境问题 → 归档 traceback
"""
from __future__ import annotations

import re

# 每条：(正则, 名称, 一句话原因, 处置, fix_kind, goto)
RULES = [
    (r'缺前置条件.*掩模|需要去噪掩膜|没有可用的掩模|缺 ⑧ 去噪掩膜',
     '缺去噪掩模',
     '③④ 都把 00_掩模\\<病例>\\Mask.mat 当硬门禁；没有掩模就没法限定拟合范围。',
     '到 ⑧ 去噪掩模页，在底图上圈出组织范围并保存（agent 不会代生成）。',
     'human_gui', 'm_roi'),

    (r'ROI_total\.mat|ROI_total',
     '缺 ROI_total',
     '③ 后处理需要器官 ROI（ROI_total.mat）才能出定量曲线。',
     '在 ⑧ 页画好 ROI 并保存；ROI 与掩模是同一次绘制流程。',
     'human_gui', 'm_roi'),

    (r'缺少已确认的频率表|频率表 .*≠|频率表未设定|4D 数据没有频率',
     '频率表缺失或个数不符',
     '4D 序列每层帧数固定，频偏表个数必须对得上；没有确认过的表不许重建。',
     '先在 ⑨ 里让它检索（存档 → 已有结果 → .prot → 文本），检索不到再手写；'
     '无论如何都要你自己核对一遍再确认。',
     'human_gui', 'm1_recon'),

    (r'「B0 图谱校正」需要 b0_scale|B0 图谱校正需要',
     'B0 图谱校正缺标定系数',
     'b0.mat 是满量程整数图，必须先给比例系数与零点才能换算成 ppm。',
     '要么在 ③ 高级参数里填 B0 比例系数，要么改用「自校正」（默认口径）。',
     'manual_arg', 'm3_post'),

    (r'掩模形状 .*与数据|掩模维度不认识|形状 .* ≠ 底图|前两维 ≠ 底图',
     '掩模/ROI 与数据尺寸不匹配',
     '掩模是给另一个病例或另一套重建尺寸画的，形状对不上。',
     '在 ⑧ 页按当前病例重新画；或确认 01_重建 下这份数据是不是被换过。',
     'human_gui', 'm_roi'),

    (r'层号超出范围',
     '层号越界',
     '「层」参数里填了超出实际层数的编号。',
     '把「层」留空（= 全部层），或改成 1~实际层数 范围内的编号。',
     'auto', ''),

    (r'normZ 维度|normZ 形状不认识|normZ 频偏维|总层数 .* 与 normZ 层数',
     '重建数据维度不对',
     '① 的产物与 ③ 期望的维度口径不一致（可能没有确认频率表就跑过、或数据被替换）。',
     '确认 ① 是否完整跑过；必要时重跑 ①（会先备份旧产物）。',
     'manual_arg', 'm1_recon'),

    (r'ssat 形状|s0 .*尺寸不一致|数据不是 4D',
     '数据与频偏维不匹配',
     'ssat 的帧数与 w_offset 的频偏数对不上，通常是频率表个数不对。',
     '回到 ① 核对频率表（个数要 = 每层帧数，或按 UIH 规则可少不可多）。',
     'human_gui', 'm1_recon'),

    (r'目录不存在：',
     '输入目录不存在',
     '参数里记着一个已经不存在的路径（目录被移走/改名）。',
     'agent 会按 PROJ 重新定位输入目录；定位不到就需要你确认数据放哪了。',
     'auto', ''),

    (r'PROJ\\\\01_重建 下没有病例|PROJ 里还没有病例',
     'PROJ 里没有病例',
     '输出总目录里还没有任何重建结果。',
     '先跑 ① DICOM 重建，或把已有重建结果放进 01_重建。',
     'human_gui', 'm1_recon'),

    (r'搜索网格是空的|网格',
     'LD 拟合网格为空',
     '中心/线宽网格的上下界设反了，或步长太大导致没有可取的点。',
     '在 ④ 高级参数里恢复默认（中心 -2~?、步长 0.05），或点「预设」。',
     'auto', 'm4_ld'),

    (r'噪声区是空的|没有 liverMask',
     '掩模底图不完整',
     '去噪掩模 = liverMask × snrMask，缺了组织多边形或噪声区就构不成掩模。',
     '在 ⑧ 页把「组织范围」和「纯噪声区」两块都圈出来再保存。',
     'human_gui', 'm_roi'),

    (r'⑤ 需要 V_exp|⑥ 需要 ⑤ 的地址清单|先去 ⑤',
     'PRF 上游产物缺失',
     '⑤ 需要 ③/④ 的逐层产物，⑥ 需要 ⑤ 的 savepaths。',
     '先把 ③ 或 ④ 跑完，再跑 ⑤，最后 ⑥。',
     'human_gui', 'm5_prf'),

    (r'没有输出目录|需要先选输出总目录|没有输出根',
     '没有输出总目录（PROJ）',
     '所有产物都写在 PROJ 下面，没有它就无法落盘。',
     '在顶部选一个输出总目录。',
     'human_gui', 'm1_recon'),

    (r'找不到核心层|core_path|函数库',
     '找不到核心层',
     'agent 是按 cest-gui 的目录布局找 03_Python_全链路\\函数库 的。',
     '确认 cest-agent 与 cest-gui 平级，或设置环境变量 CEST_GUI_HOME。',
     'bug', ''),

    (r'ModuleNotFoundError|ImportError|No module named',
     '缺少依赖或路径不对',
     '当前解释器里缺某个包，或 sys.path 没挂对。',
     '确认用的是 cest-gui\\runtime\\python.exe（同源解释器，自带全部依赖）。',
     'bug', ''),

    (r'MemoryError|Unable to allocate|数组太大',
     '内存不足',
     '③ 的 *_interp.mat 单个数百 MB，逐层读入会吃掉大量内存。',
     '减少「层」的数量分批跑；或关掉「写 V_as 全量」省内存与磁盘。',
     'manual_arg', 'm3_post'),

    (r'PermissionError|拒绝访问|being used by another process',
     '文件被占用',
     '产物正被别的程序（Excel / MATLAB / 另一个 CEST Studio 窗口）打开着。',
     '关掉占用它的程序再重试。',
     'auto', ''),

    (r'No space left|磁盘空间不足|Errno 28',
     '磁盘空间不足',
     '③ 的中间产物很大，一盘可能装不下。',
     '清理 PROJ 下的旧 .bak 与历史 interp 目录，或换一个更大的盘做 PROJ。',
     'manual_arg', ''),

    (r"KeyError.*nClipLow|KeyError.*nClipHigh",
     '归一化退化输入（旧版 GUI 的 bug）',
     'Z 谱是常数（全 1 / 全同值）时，normalize_spect0916 走 degenerate 分支，'
     '旧版返回的 info 缺 nClipLow/nClipHigh，④ 打日志时 KeyError。',
     '已在本版函数库修好（degenerate 分支补齐两键）。若仍报错，说明用的是旧'
     ' cest-gui 副本，更新 03_Python_全链路\\函数库\\cest_ld.py。',
     'bug', 'm4_ld'),

    (r'degenerate|归一化后.*全为 0|Z 谱是常数|输入全是 NaN/Inf',
     '数据退化，④ 不可用',
     'Z = ssat/s0 在全脑范围没有动态范围（多为合成/占位数据，或 s0 与 ssat 是同一张图）。',
     '换真实采集的 ssat/s0 再跑 ④；合成数据只适合验流程，不适合算 LD。',
     'blocked', 'm1_recon'),
]

_COMPILED = [(re.compile(p), n, c, a, k, g) for p, n, c, a, k, g in RULES]


def diagnose(text: str, stage: str = '') -> dict:
    """给一段报错文本（异常消息 / 日志 / traceback），返回诊断结果。"""
    text = str(text or '')
    for rx, name, cause, action, kind, goto in _COMPILED:
        if rx.search(text):
            return dict(matched=True, name=name, cause=cause, action=action,
                        fix_kind=kind, goto_mid=goto, stage=stage,
                        evidence=_first_match_line(rx, text))
    return dict(matched=False, name='未登记的报错', cause='',
                action='把这个错误原文发我，我登记成一条新规则；'
                       '同时可以先用「导出报告」把上下文一起带走。',
                fix_kind='bug', goto_mid='', stage=stage,
                evidence=_tail(text))


def _first_match_line(rx, text) -> str:
    for ln in text.splitlines():
        if rx.search(ln):
            return ln.strip()[:200]
    return ''


def _tail(text: str) -> str:
    lines = [x for x in text.strip().splitlines() if x.strip()]
    return '\n'.join(lines[-6:])[:600]


def explain_failures(failures) -> list:
    """把 runner 收集到的失败逐条翻译。"""
    out = []
    for f in failures or []:
        d = diagnose(f.get('error') or f.get('message') or '', f.get('stage', ''))
        d['case'] = f.get('case', '')
        d['stage'] = f.get('stage', '')
        d['raw'] = (f.get('error') or '')[:400]
        out.append(d)
    return out


def summary(diags) -> str:
    if not diags:
        return '没有失败。'
    parts = []
    for d in diags:
        parts.append(f"· {d.get('stage', '')} {d.get('name')}：{d.get('action')}")
    return '\n'.join(parts)
