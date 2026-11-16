# -*- coding: utf-8 -*-
"""apply_update.py —— 把「代码更新包」应用到当前 CEST Studio 安装目录（离线、一键）。

平时由安装目录里的「应用更新.bat」双击调用：
    应用更新.bat                                   → 自动找附近的更新包，零输入装完
    应用更新.bat 更新包.zip                        → 直接用这个包（也可以拖到 bat 上）
    应用更新.bat 更新包文件夹                       → 同上（更新包本身可以是文件夹）
    应用更新.bat 更新包.zip --yes                   → 不询问，直接应用

没给包参数时按这个顺序自动找（找到就用）：
    ① 脚本自己就在一个包里（同级有 update_manifest.json） → 用它；
    ② 脚本同级／上级 的 更新包\、更新工具\更新包\ 里最新的 CEST-Studio-更新-v*；
    ③ 安装目录下的 更新包\、更新工具\更新包\；
    ④ 当前工作目录下的 更新包\。

★ 本脚本**永远不弹任何窗口、不往桌面写任何东西**（不出压缩包、不弹文件选择框）：
  找不到包 → 红字提示 + 退出码 1，让你把包拖到「应用更新.bat」上或把「更新工具」
  文件夹整个放进 cest-gui 再双击。

更新包两种形态都认（2026-10-16 起默认是**文件夹**，省得开发机到处生成压缩包）：
  · 文件夹包：根下有 update_manifest.json + 更新说明.txt + 代码目录树；
  · zip 包   ：老格式，内容一样，继续兼容。

做四件事：
  ① 检查：包里必须有 update_manifest.json；包里**绝不允许**出现
     runtime/ · _dev/ · logs/ · __pycache__ · settings.json · *.bak_* / *.log —— 出现即整包拒绝。
  ② 备份：即将被覆盖的现有文件，改名移动到 _bak_更新_<时间戳>\\（同盘改名，瞬间、不占空间）。
  ③ 覆盖：按清单逐字节写入，并逐文件核对 sha256；任何一步失败 → 自动全量回滚。
  ④ 报版本：旧版本 → 新版本，提示关窗口重新双击「启动 CEST Studio.vbs」。

绝不触碰：runtime\\ · _dev\\ · logs\\ · config\\settings.json · 你的输入/输出数据目录。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

MANIFEST_NAME = 'update_manifest.json'
PACK_PREFIX = 'CEST-Studio-更新-v'
PACK_DIRNAME = '更新包'
TOOL_DIRNAME = '更新工具'
FORBIDDEN_TOP = {'runtime', '_dev', 'logs', '.git'}
FORBIDDEN_FILE = 'settings.json'


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def is_package(p: Path) -> bool:
    """这一坨是不是更新包：文件夹包看根下有没有 update_manifest.json，
    zip 包看包内有没有（允许外面多套一层目录）。"""
    p = Path(p)
    if p.is_dir():
        return (p / MANIFEST_NAME).is_file()
    if p.is_file() and p.suffix.lower() == '.zip':
        try:
            with zipfile.ZipFile(p) as z:
                names = z.namelist()
        except Exception:
            return False
        return any(n == MANIFEST_NAME or n.endswith('/' + MANIFEST_NAME)
                   for n in names)
    return False


def _scan_dir(d: Path):
    """从目录 d 里挑候选包：d 自己 / d\更新包\ / d\更新工具\更新包\ 下的 CEST-Studio-更新-v*。"""
    out = [Path(d)]
    for sub in (Path(d) / PACK_DIRNAME, Path(d) / TOOL_DIRNAME / PACK_DIRNAME):
        out.append(sub)
        if sub.is_dir():
            out += sorted(sub.glob(PACK_PREFIX + '*'))
    return out


def find_package(install: Path, script_dir: Path) -> "Path | None":
    """没给包参数时自动找包（脚本同级 → 往上到安装目录 → 安装目录 → 当前目录）。
    多个候选按修改时间取最新。找不到返回 None。"""
    seen, cands = set(), []
    dirs = [script_dir] + list(script_dir.parents)[:5] + [install, Path.cwd()]
    for d in dirs:
        d = Path(d)
        if d in seen:
            continue
        seen.add(d)
        cands += _scan_dir(d)
    good = [c for c in cands if is_package(c)]
    if not good:
        return None
    return max(good, key=lambda p: p.stat().st_mtime)


class Package:
    """更新包读取器 —— 文件夹包与 zip 包都能读（zip 是老格式，继续兼容）。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.is_dir = self.path.is_dir()
        self._zf = None
        if self.is_dir:
            self.names = [p.relative_to(self.path).as_posix()
                          for p in sorted(self.path.rglob('*')) if p.is_file()]
        else:
            self._zf = zipfile.ZipFile(self.path)
            self.names = [n for n in self._zf.namelist() if not n.endswith('/')]

    def read(self, name: str) -> bytes:
        if self.is_dir:
            return (self.path / PurePosixPath(name)).read_bytes()
        return self._zf.read(name)

    def size(self) -> int:
        if self.is_dir:
            return sum((self.path / PurePosixPath(n)).stat().st_size
                       for n in self.names)
        return self.path.stat().st_size

    def close(self) -> None:
        if self._zf is not None:
            self._zf.close()


def check_forbidden(rel: PurePosixPath) -> "str | None":
    """返回拒绝理由；None = 可以放。"""
    parts = [p for p in rel.parts]
    low = [p.lower() for p in parts]
    if low and low[0] in FORBIDDEN_TOP:
        return f'更新包里不该有顶层目录 {parts[0]}\\（运行时/开发工具/日志不随更新包发）'
    if any(p == '__pycache__' for p in low):
        return f'{rel} — 不该打包 __pycache__'
    if low[-1] == FORBIDDEN_FILE:
        return f'{rel} — 不该覆盖 settings.json（里面是你本机的目录设置）'
    if any('bak' in p for p in low):
        return f'{rel} — 不该打包备份文件'
    if rel.suffix.lower() in ('.pyc', '.pyo', '.log'):
        return f'{rel} — 不该打包缓存/日志'
    return None


def collect_entries(pkg: Package):
    """→ (manifest_dict, [(rel, name)])；顺带剥掉可能多出的一层包裹目录。"""
    names = list(pkg.names)
    wrapped = None
    if names and not any('/' not in n for n in names):
        tops = {PurePosixPath(n).parts[0] for n in names}
        if len(tops) == 1:
            wrapped = next(iter(tops))
    rels = []
    for n in names:
        parts = PurePosixPath(n).parts
        if wrapped and parts and parts[0] == wrapped:
            parts = parts[1:]
        if not parts:
            continue
        rels.append((PurePosixPath(*parts), n))
    man_pairs = [(r, n) for r, n in rels if r == PurePosixPath(MANIFEST_NAME)]
    if len(man_pairs) != 1:
        raise SystemExit(f'[拒绝] 更新包里必须（且只能）有一份 {MANIFEST_NAME}，'
                         f'实际找到 {len(man_pairs)} 份。')
    man = json.loads(pkg.read(man_pairs[0][1]).decode('utf-8'))
    files = [(r, n) for r, n in rels if r != PurePosixPath(MANIFEST_NAME)]
    return man, files


def load_package(path: Path):
    """→ (manifest, [(rel, name)], {name: bytes}, 包总字节)。整包读进内存（1~2 MB）。"""
    pkg = Package(path)
    try:
        man, files = collect_entries(pkg)
        blobs = {name: pkg.read(name) for _, name in files}
        total = pkg.size()
    finally:
        pkg.close()
    return man, files, blobs, total


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('zip', nargs='?', help='更新包（zip 或文件夹；不填=弹窗选）')
    ap.add_argument('--root', default=None, help='安装目录（默认=本脚本往上两级）')
    ap.add_argument('--yes', action='store_true', help='不询问，直接应用')
    ap.add_argument('--force', action='store_true', help='版本号相同/更旧也照样应用')
    ap.add_argument('--dry-run', action='store_true', help='只看会改什么，不动文件')
    args = ap.parse_args(argv)

    install = Path(args.root).resolve() if args.root else \
        Path(__file__).resolve().parents[2]
    print('=' * 66)
    print('CEST Studio 代码更新包 —— 一键应用')
    print('=' * 66)
    print(f'安装目录：{install}')

    # ---- 安装目录自检（防止把包解到别处）----
    need = [install / 'CEST_Studio' / 'app' / 'main.py',
            install / 'runtime' / 'pythonw.exe']
    miss = [str(p) for p in need if not p.exists()]
    if miss:
        print('[拒绝] 看起来不是完整的 CEST Studio 安装目录，缺：')
        for m in miss:
            print('        ' + m)
        print('       请把更新包放到「启动 CEST Studio.vbs」所在的那个文件夹里再应用。')
        return 2

    auto = not args.zip
    zpath = Path(args.zip).expanduser() if args.zip else \
        find_package(install, Path(__file__).resolve().parent)
    if zpath is None:
        print('[错误] 没找到更新包（本工具不弹窗口、不往桌面写东西）。')
        print('       ① 把「更新工具」文件夹整个放进 cest-gui（和「启动 CEST Studio.vbs」')
        print('          同一层最省事），再双击里面的「应用更新.bat」；或者')
        print('       ② 把更新包（文件夹或 zip）直接拖到「应用更新.bat」上放开。')
        return 1
    if not zpath.exists():
        print(f'[拒绝] 找不到更新包：{zpath}')
        return 2
    print(f'更新包  ：{zpath}' + ('   （自动找到，无需手选）' if auto else ''))

    man, files, blobs, total = load_package(zpath)
    print(f'        （{"文件夹包" if zpath.is_dir() else "zip 包"}，'
          f'{len(files) + 1} 个文件 / {total / 1048576:.2f} MB）')

    for rel, _ in files:                              # ① 安全闸门
        why = check_forbidden(rel)
        if why:
            print(f'[拒绝] 这个包不干净：{why}')
            print('       更新包只能装代码（CEST_Studio\\ · 03_Python_全链路\\ · 启动脚本）。')
            return 3
    if check_forbidden(PurePosixPath(MANIFEST_NAME)):
        return 3

    version = str(man.get('version', '')).strip()
    base_version = str(man.get('base_version', '')).strip()
    note = str(man.get('note', '')).strip()
    listed = {str(e.get('path', '')).replace('\\', '/') for e in man.get('files', [])}
    actual = {r.as_posix() for r, _ in files}
    if listed != actual:
        only_z = sorted(actual - listed)[:5]
        only_m = sorted(listed - actual)[:5]
        print('[拒绝] 包内文件与清单对不上（包可能损坏或被改过）：')
        if only_z:
            print('       包里多出：' + '、'.join(only_z))
        if only_m:
            print('       清单多出：' + '、'.join(only_m))
        return 3

    old_version = ''
    vf = install / 'CEST_Studio' / 'VERSION'
    if vf.is_file():
        old_version = vf.read_text(encoding='utf-8-sig', errors='replace').strip()
    print(f'版本    ：{old_version or "（未知）"}  →  {version or "（未知）"}')
    if note:
        print(f'说明    ：{note}')
    if not version:
        print('[拒绝] 包里没写版本号。')
        return 3
    if old_version == version and not args.force:
        print(f'[提示] 本地已经是 v{version} 了。若确认要重装这个包，加 --force。')
        return 1
    if version and old_version and _cmp(version, old_version) < 0 and not args.force:
        print(f'[提示] 这个包(v{version})比本地(v{old_version})还旧，疑似选错，先拦下。'
              f'\n       确实要降级就加 --force。')
        return 1

    # ---- ②③ 备份 + 覆盖 ----
    todo = []           # [(rel, bytes, sha)]
    for rel, name in files:
        b = blobs[name]
        e = next((x for x in man.get('files', [])
                  if str(x.get('path', '')).replace('\\', '/') == rel.as_posix()), {})
        want = str(e.get('sha256', '')).lower()
        got = sha256_bytes(b)
        if want and want != got:
            print(f'[拒绝] {rel} 内容与清单 sha256 不符 —— 包损坏，整包不装。')
            return 3
        todo.append((rel, b, got))

    want_map = {str(r): y for r, _, y in todo}
    changed_new = [r for r, _, _ in todo if not (install / r).exists()]
    changed_mod = [r for r, _, _ in todo
                   if (install / r).is_file()
                   and sha256_file(install / r) != want_map[str(r)]]
    print(f'文件    ：清单 {len(todo)} 个 —— 新增 {len(changed_new)}、'
          f'内容变化 {len(changed_mod)}、其余一致')
    for r in (changed_new + changed_mod)[:40]:
        print(f'          {"+" if r in changed_new else "~"} {r.as_posix()}')
    if len(changed_new) + len(changed_mod) > 40:
        print(f'          …还有 {len(changed_new) + len(changed_mod) - 40} 个')
    if not changed_new and not changed_mod:
        print('[提示] 包里所有文件都和本地一样，没什么可更新的。')
        return 0
    if args.dry_run:
        print('[dry-run] 到此为止，没有动任何文件。')
        return 0
    if not args.yes:
        print('确认应用请按回车（输入 n 取消）：')
        if input().strip().lower() in ('n', 'no', 'q'):
            print('[取消] 什么都没动。')
            return 1

    stamp = time.strftime('%Y%m%d_%H%M%S')
    backup = install / f'_bak_更新_{stamp}'
    written = []
    moved = []
    try:
        for rel, b, got in todo:
            tgt = install / rel
            tgt.parent.mkdir(parents=True, exist_ok=True)
            if tgt.is_file():
                bk = backup / rel
                bk.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tgt, bk)          # 同盘改名：瞬间、不占额外空间
                moved.append(rel)
            with open(tgt, 'wb') as f:
                f.write(b)
            written.append(rel)
            if sha256_file(tgt) != got:      # 落盘核对
                raise OSError(f'写入后校验失败：{rel}')
    except Exception as e:
        print(f'[出错] {e}')
        print('       正在回滚…')
        for rel in written:
            try:
                (install / rel).unlink()
            except Exception:
                pass
        for rel in moved:
            try:
                os.replace(backup / rel, install / rel)
            except Exception:
                pass
        shutil.rmtree(backup, ignore_errors=True)
        print('[回滚完成] 本地文件已恢复原样，什么都没变。')
        return 4

    print('-' * 66)
    print(f'已更新：{len(written)} 个文件，版本 {old_version or "?"} → {version}')
    if backup.exists():
        n_bk = sum(1 for _ in backup.rglob('*') if _.is_file())
        print(f'旧文件备份：{backup}  （{n_bk} 个；不要了可以直接删这个文件夹）')
    else:
        print('（没有需要覆盖的旧文件，无需备份）')
    print()
    print('★ 请先关掉 CEST Studio 窗口，再重新双击「启动 CEST Studio.vbs」才生效。')
    print('★ 如果这次更新动了运行环境/依赖（很少见），才会需要重新下载完整免安装包。')
    return 0


def _cmp(a: str, b: str) -> int:
    """宽松版本比较：v1.2.10 这种按段比数字。"""
    def key(s):
        s = str(s).strip().lstrip('vV')
        out = []
        for seg in s.replace('-', '.').split('.'):
            out.append(int(seg) if seg.isdigit() else 0)
        return out
    ka, kb = key(a), key(b)
    n = max(len(ka), len(kb))
    ka += [0] * (n - len(ka))
    kb += [0] * (n - len(kb))
    return (ka > kb) - (ka < kb)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n[取消]')
        sys.exit(1)
