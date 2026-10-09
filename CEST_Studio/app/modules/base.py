# -*- coding: utf-8 -*-
"""app/modules/base.py —— 统一模块协议。

只要继承 Module、填好 schema / suggest / validate / run，
界面层（base_page）就自动生成「输入 / 参数 / 输出 / 运行 / 结果」五段。
改参数 = 改 schema；换算法 = 改 run 里的调用。随时可改。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:                                    # ASCII 键 → 中文目录名
    from detect.rules import cn_name
except Exception:                                        # noqa: BLE001
    def cn_name(x):
        return x


@dataclass
class Field:
    """一个参数。kind 决定界面控件；kinds / modes 控制何时显示。"""
    key: str
    label: str = ''
    kind: str = 'text'            # text / int / float / bool / choice / dir / file / cases
    default: object = None
    choices: tuple = ()
    tip: str = ''
    kinds: tuple = ()             # 只在识别类型命中时显示（空=总是显示）
    modes: tuple = ()             # 只在 p['mode'] 命中时显示（用于 ② 的 LD 等）
    advanced: bool = False        # True → 不占主表单，收进「高级参数…」对话框
    group: str = ''               # 高级对话框里的分组名
    # 动态下拉框（kind='choice'）：选项由模块的 dynamic_choices(key, get, proj) 提供，
    # 页面刷新时重新填充（如 ⑤ 的「组名 / 病例名」）。
    dyn: bool = False
    # 只在 p[show_if[0]] 的值 ∈ show_if[1] 时显示（如 ⑤ 的「粒度」只在 组内 显示）。
    show_if: tuple = ()

    def __post_init__(self):
        if not self.label:
            self.label = self.key


class Module:
    id: str = 'm0'
    badge: str = ''
    title: str = ''
    out_subdir: str = ''          # PROJ 下的固定子目录名
    input_label: str = ''         # 拖拽区显示的「本模块输入是什么」；空=按 chain 推断
    case_in_out: bool = False     # 输出是否按 <子目录>/<病例> 再分一层
    accepts: tuple = ()           # 能吃的输入类型（detect.kind）
    produces: tuple = ()
    schema: tuple = ()            # tuple[Field]

    # ---- 数据链（detect/chain.py）----
    # chain_proj=True：输入根默认 = 输出总目录 PROJ；本页会显示「输入根 → 解析结果」
    #   两行 + 病例下拉框，并在输入是 PROJ 时用 chain 自动填下面这些参数。
    chain_proj: bool = False
    chain_fill: tuple = ()        # 自动填的参数字段名（'case' / 'src' / 'mask_file'）

    # ---- 批量模式（可选）----
    # 当 chain_batch_field 这个参数的值以 chain_batch_values 里任一前缀开头时，
    # 数据链门禁放宽为「只要 PROJ 里还有病例就允许跑」——缺前置的病例由模块在
    # run 里逐个提醒并跳过（用于 ③ 的「全部病例（自动）」）。
    chain_batch_field: str = ''
    chain_batch_values: tuple = ()

    # ---- 参数 ----
    def suggest(self, det, proj) -> dict:
        """根据识别结果自动填参；det/proj 可能为 None。"""
        return {}

    def validate(self, p, ctx=None) -> list:
        """返回中文错误列表；空 = 可以跑。"""
        return []

    # ---- 目录预览 ----
    def out_dir_for(self, p, proj) -> Path | None:
        if not proj:
            return None
        d = Path(proj) / cn_name(self.out_subdir)
        case = str(p.get('case') or '').strip()
        return (d / case) if (case and self.case_in_out) else d

    def preview_files(self, p, proj) -> list:
        """界面「将生成的文件」预览；默认只给输出目录。"""
        d = self.out_dir_for(p, proj)
        return [str(d)] if d else []

    def preview_image(self, res) -> str | None:
        """跑完后的预览图路径；默认没有。"""
        return None

    # ---- 自定义面板（默认无；① 用它放「序列与频率」） ----
    def extra_panel(self, page):
        return None

    def refresh_extra(self, panel, det, proj, params):
        pass

    def presets(self) -> dict:
        """预设参数（名字 → {key: 值}），供「高级参数…」对话框一键套用。"""
        return {}

    # ---- 数据链自检覆盖（可选）----
    def stage_overrides(self, p, proj) -> dict:
        """模块按当前参数给出某些数据链阶段的「是否完成」（覆盖通用判断）。

        返回 {stage_id: bool}；只覆盖关心的 key——例如 ⑤/⑥ 按「分析模式」
        判断该模式对应的产物在不在，避免"别的模式跑过就当这模式也完了"。
        """
        return {}

    # ---- 执行 ----
    def run(self, p, ctx) -> dict:
        raise NotImplementedError(f'{self.id} 还没实现 run()')
