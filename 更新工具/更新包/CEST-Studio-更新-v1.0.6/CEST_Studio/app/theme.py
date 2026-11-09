# -*- coding: utf-8 -*-
"""app/theme.py —— 统一外观（Fusion 底座 + QSS）。

想换配色/圆角/字号，只改本文件；控件通过 objectName 取样式
（如 #primary 主按钮、#nav 导航、#log 日志、#dropZone 拖拽区、#pageTitle 页标题）。
"""
from __future__ import annotations

# 调色板
C = {
    'bg':        '#f4f6fa',
    'card':      '#ffffff',
    'border':    '#e4e9f2',
    'border2':   '#d5dce8',
    'text':      '#1f2937',
    'muted':     '#6b7280',
    'faint':     '#9aa4b2',
    'primary':   '#2563eb',
    'primary_d': '#1d4ed8',
    'soft':      '#eaf1ff',
    'log_bg':    '#0f172a',
    'log_fg':    '#d7e0ea',
}

QSS = f"""
* {{ font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif; font-size: 12.5px;
     color: {C['text']}; }}

QMainWindow, QWidget#central {{ background: {C['bg']}; }}
QWidget#pageRoot, QWidget#pageLeft, QWidget#pageRight {{ background: transparent; }}

/* ---------- 卡片 ---------- */
QGroupBox {{
    background: {C['card']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    margin-top: 15px;
    padding: 16px 12px 12px 12px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 12px; top: 4px; padding: 0 6px;
    color: #475569; font-weight: 600; background: {C['bg']};
}}

/* ---------- 数据链条 ---------- */
QWidget#chainBox {{
    background: #f8fafc;
    border: 1px solid {C['border']};
    border-radius: 9px;
}}
QPushButton#stageBtn {{
    border: none; background: transparent; padding: 1px 6px;
    min-height: 16px; text-decoration: underline;
}}
QPushButton#stageBtn:hover {{ background: #eef3fb; border-radius: 6px; }}

/* ---------- 输入类 ---------- */
QLineEdit, QComboBox {{
    background: {C['card']};
    border: 1px solid {C['border2']};
    border-radius: 7px;
    padding: 5px 9px;
    min-height: 19px;
    selection-background-color: #bfdbfe;
}}
QLineEdit:hover, QComboBox:hover {{ border-color: #b9c4d6; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {C['primary']}; }}
QLineEdit:disabled, QComboBox:disabled {{
    background: #f4f6f9; color: {C['faint']}; border-color: {C['border']}; }}
QLineEdit[echoMode="2"] {{ letter-spacing: 2px; }}
QComboBox QAbstractItemView {{
    background: {C['card']}; border: 1px solid {C['border']};
    selection-background-color: {C['soft']}; selection-color: {C['text']};
    outline: none; padding: 3px;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {C['card']};
    border: 1px solid {C['border2']};
    border-radius: 7px;
    padding: 5px 15px;
    color: #374151;
    min-height: 19px;
}}
QPushButton:hover {{ background: #f3f6fb; border-color: #c3ccdb; }}
QPushButton:pressed {{ background: #e8eef8; }}
QPushButton:disabled {{ color: {C['faint']}; background: #f4f6f9;
                        border-color: {C['border']}; }}
QPushButton#primary {{
    background: {C['primary']}; border: 1px solid {C['primary']};
    color: #ffffff; font-weight: 600; padding: 5px 22px;
}}
QPushButton#primary:hover {{ background: {C['primary_d']}; }}
QPushButton#primary:pressed {{ background: #1741b6; }}
QPushButton#primary:disabled {{ background: #adc4f4; border-color: #adc4f4;
                                color: #eef3ff; }}
QPushButton#pick {{ padding: 5px 9px; }}          /* 「…」小按钮 */

/* ---------- 导航 ---------- */
QListWidget#nav {{
    background: transparent; border: none; outline: none; padding: 2px;
}}
QListWidget#nav::item {{
    height: 34px; padding-left: 10px; margin: 2px 3px;
    border-radius: 7px; color: #374151;
}}
QListWidget#nav::item:hover {{ background: #eaeff8; }}
QListWidget#nav::item:selected {{
    background: {C['soft']}; color: {C['primary_d']}; font-weight: 600;
}}

/* ---------- 标题 / 文本 ---------- */
QLabel#pageTitle {{ font-size: 17px; font-weight: 600; color: #111827;
                    padding: 0 0 2px 3px; }}
QLabel#sectionTitle {{ color: {C['muted']}; font-weight: 600; padding: 2px 0 0 2px; }}
QLabel#summary {{
    background: {C['card']}; border: 1px solid {C['border']};
    border-radius: 8px; padding: 9px 11px; color: #334155;
}}
QLabel#inSummary {{ color: #334155; }}
QLabel#muted {{ color: {C['muted']}; }}
QLabel#srcLabel {{
    background: #f8fafc; border: 1px solid {C['border']};
    border-radius: 7px; padding: 7px 9px; color: #334155;
}}
QLabel#checkLabel {{ padding: 2px 0; }}
QPlainTextEdit {{
    background: {C['card']}; border: 1px solid {C['border2']};
    border-radius: 8px; padding: 5px 7px;
    font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12.5px;
}}
QLabel#outLabel {{ color: {C['primary_d']}; }}
QLabel#outPreview {{ color: {C['muted']}; font-size: 12px; }}
QLabel#stepLabel  {{ color: {C['muted']}; }}
QLabel#preview {{
    background: #fbfcfe; border: 1px dashed #dbe2ec; border-radius: 8px;
    color: {C['faint']};
}}

/* ---------- 拖拽区 ---------- */
QFrame#dropZone {{
    background: {C['card']}; border: 2px dashed {C['border2']}; border-radius: 10px;
}}
QFrame#dropZone:hover {{ border-color: {C['primary']}; background: #f8fbff; }}
QLabel#dropText {{ color: {C['muted']}; font-size: 13px; }}

/* ---------- 进度条 ---------- */
QProgressBar {{
    background: #e9edf3; border: none; border-radius: 6px;
    height: 12px; text-align: center; color: {C['muted']}; font-size: 11px;
}}
QProgressBar::chunk {{ background: {C['primary']}; border-radius: 6px; }}

/* ---------- 日志 / 结果 ---------- */
QPlainTextEdit#log {{
    background: {C['log_bg']}; color: {C['log_fg']};
    border: 1px solid #1e293b; border-radius: 9px; padding: 7px 9px;
    font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12px;
}}
QTextEdit#result {{
    background: {C['card']}; border: 1px solid {C['border']};
    border-radius: 9px; padding: 7px 9px;
    font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12px;
}}

/* ---------- 勾选框 ---------- */
QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px;
                        border: 1px solid #c3ccdb; background: {C['card']}; }}
QCheckBox::indicator:hover {{ border-color: {C['primary']}; }}
QCheckBox::indicator:checked {{ background: {C['primary']};
                                border: 1px solid {C['primary']}; }}

/* ---------- 滚动条 / 分割条 ---------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #ccd6e3; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #a8b6c8; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #ccd6e3; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 8px; }}
QSplitter::handle:vertical {{ height: 8px; }}
QSplitter::handle:hover {{ background: #b9c6d8; }}

QToolTip {{
    background: #1f2937; color: #f8fafc; border: none; padding: 5px 7px;
    border-radius: 5px;
}}
"""


def apply(app):
    """给 QApplication 装主题（Fusion 是跨平台一致的底座）。"""
    try:
        app.setStyle('Fusion')
    except Exception:                                            # noqa: BLE001
        pass
    app.setStyleSheet(QSS)
