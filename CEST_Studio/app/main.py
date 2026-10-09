# -*- coding: utf-8 -*-
"""app/main.py —— CEST Studio 入口。

    python app\\main.py                正常打开窗口
    python app\\main.py --open <目录>   打开并识别指定目录
    python app\\main.py --shot out.png  离屏渲染一张窗口截图后退出（自测用）
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent          # CEST_Studio/app
ROOT = HERE.parent                              # CEST_Studio
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault('MPLBACKEND', 'Agg')      # 出图走 Agg，不弹 matplotlib 窗口
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:                               # noqa: BLE001
    pass


def _ensure_streams():
    """用 pythonw.exe（无控制台）启动时 sys.stdout/stderr 是 None：
    把输出落到 `logs\\gui_<日期>.log`，否则出错时什么都看不到。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir = ROOT / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    f = open(log_dir / f'gui_{time.strftime("%Y%m%d")}.log', 'a',
             encoding='utf-8', buffering=1)

    class _Tee:
        def write(self, s):
            try:
                f.write(str(s))
            except Exception:                                    # noqa: BLE001
                pass

        def flush(self):
            try:
                f.flush()
            except Exception:                                    # noqa: BLE001
                pass

        def isatty(self):
            return False

    t = _Tee()
    sys.stdout = t
    sys.stderr = t
    print(f'--- {time.strftime("%Y-%m-%d %H:%M:%S")} 启动（无控制台，输出落此文件） ---')


def _install_cjk_font(app):
    """保证中文能显示：字体库里没有 CJK 就直接从 Windows 字体文件加载。

    （离屏渲染 / 精简环境里 Qt 字体库可能是空的，界面会全是方块。）
    """
    import os
    from PySide6.QtGui import QFont, QFontDatabase
    fams = QFontDatabase.families()
    if not any('YaHei' in f or 'SimHei' in f or 'SimSun' in f for f in fams):
        for c in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf',
                  r'C:\Windows\Fonts\simsun.ttc'):
            if os.path.exists(c):
                QFontDatabase.addApplicationFont(c)
    for fam in ('Microsoft YaHei', 'SimHei', 'SimSun', 'Arial'):
        if QFont(fam).exactMatch():
            app.setFont(QFont(fam, 9))
            return fam
    return None


def _install_quiet_dialogs():
    """--autorun（无人值守）时，模态弹窗会一直等 → 改成打印并自动应答。"""
    from PySide6.QtWidgets import QMessageBox

    def _w(*args, **kw):
        msg = args[2] if len(args) > 2 else (args[1] if len(args) > 1 else '')
        print(f'[autorun][弹窗] {msg}')
        return QMessageBox.StandardButton.Ok

    def _q(*args, **kw):
        msg = args[2] if len(args) > 2 else (args[1] if len(args) > 1 else '')
        print(f'[autorun][询问→是] {msg}')
        return QMessageBox.StandardButton.Yes

    QMessageBox.warning = staticmethod(_w)
    QMessageBox.information = staticmethod(_w)
    QMessageBox.critical = staticmethod(_w)
    QMessageBox.question = staticmethod(_q)


def main(argv=None):
    _ensure_streams()

    # 解释器缺依赖时给一句能看懂的提示（否则只剩一个导入报错）
    try:
        import PySide6  # noqa: F401
    except ImportError:
        msg = ('[ERROR] 这个 Python 没装运行依赖（PySide6 缺失）：' + sys.executable
               + '\n        请双击交付包里的「安装依赖.bat」；'
               + '或让 启动.vbs 指向装了依赖的那个 Python。')
        print(msg)
        print('        日志：' + str(ROOT / 'logs'))
        return 1

    import argparse
    ap = argparse.ArgumentParser(description='CEST Studio')
    ap.add_argument('--open', default='', help='启动时识别的输入总目录')
    ap.add_argument('--proj', default='', help='启动时指定输出总目录（PROJ）')
    ap.add_argument('--nav', type=int, default=0, help='启动时选中第几个模块（0 基）')
    ap.add_argument('--shot', default='', help='离屏截图存到该 png 后退出')
    ap.add_argument('--autorun', action='store_true',
                    help='开发用：自动切到 ① 页、真的点「开始」，跑完退出')
    ap.add_argument('--mid-shot', default='',
                    help='开发用：autorun 途中截一张图（配 --mid-sec）')
    ap.add_argument('--mid-sec', type=float, default=6.0,
                    help='中途截图在启动后多少秒拍（默认 6）')
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    import matplotlib
    matplotlib.use('Agg')
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from app.main_window import MainWindow
    from app import theme

    app = QApplication([])
    app.setApplicationName('CEST Studio')
    _install_cjk_font(app)
    theme.apply(app)
    win = MainWindow()
    win.resize(1480, 940)
    win.show()

    # 有些启动方式（如 WScript.Shell.Run 的 SW_HIDE / vbHide）会把 SW_HIDE 塞进
    # STARTUPINFO，Windows 用它吞掉主窗的第一次 ShowWindow → 进程在跑、窗口却不出现
    # （“双击没反应”）。这里再强制显示一次，并置前，作为兜底。
    if sys.platform == 'win32':
        try:
            import ctypes
            _hwnd = int(win.winId())
            ctypes.windll.user32.ShowWindow(_hwnd, 5)        # SW_SHOW
            ctypes.windll.user32.SetForegroundWindow(_hwnd)
        except Exception:                                    # noqa: BLE001
            pass

    if a.open:
        win.set_input(a.open, confirm=False)   # 命令行/脚本入口不再弹确认
    if a.proj:
        win.out_edit.setText(a.proj)
    if a.nav:
        win.nav.setCurrentRow(a.nav)
    if a.shot and not a.autorun:
        def _shot():
            win.grab().save(a.shot)
            print('已保存截图：', a.shot)
            app.quit()
        QTimer.singleShot(1500, _shot)

    if a.autorun:
        _install_quiet_dialogs()

        def _done():
            print('[autorun] 任务结束')
            if a.shot:
                def _s():
                    win.grab().save(a.shot)
                    print('已保存截图：', a.shot)
                    app.quit()
                QTimer.singleShot(1500, _s)
            else:
                QTimer.singleShot(600, app.quit)

        def _kick():
            win.nav.setCurrentRow(0)
            page = win.pages.currentWidget()
            print(f'[autorun] 切到页面 {page.module.id}，点击「开始」…')
            page._on_run()
            if page.thread is None:
                print('[autorun] 任务没起来（校验没过？）')
                _done()
                return
            page.thread.finished.connect(_done)
            if a.mid_shot:
                def _mid():
                    win.grab().save(a.mid_shot)
                    print('已保存中途截图：', a.mid_shot)
                QTimer.singleShot(int(a.mid_sec * 1000), _mid)

        QTimer.singleShot(1500, _kick)
    try:
        return app.exec()
    except Exception:                                            # noqa: BLE001
        tb = traceback.format_exc()
        print(tb)
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, 'CEST Studio 出错', tb[-1500:])
        return 1


if __name__ == '__main__':
    sys.exit(main())
