# coding: utf-8
"""
启飞输入法 · 一键安装程序

自身是一个独立的 exe：内置 payload.zip（整个应用目录，含中文润色模型），
运行后引导用户选择安装位置，解压、建快捷方式、写卸载信息。

覆盖安装时 models/ 下已存在的文件一律跳过：用户以后人工换的 / 升级的
模型不能被安装包冲掉，程序本体则照常覆盖。

装之前会先结束正在运行的输入法：它常驻托盘、还可能开机自启，连
multiprocessing 的 worker 都叫 QifeiServer.exe，会锁住 exe 导致覆盖安装
半途失败（实测抓到过）。

选择安装到 %LOCALAPPDATA%\\Programs 下，全程不需要管理员权限。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QThread, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

BRAND = '启飞输入法'
VERSION = '2.0.4'
APP_DIR_NAME = 'QifeiIME'
EXE_NAME = 'QifeiIME.exe'

CREATE_NO_WINDOW = 0x08000000

# 覆盖安装时这些进程会锁住 exe。multiprocessing fork 出来的 worker 也叫
# QifeiServer.exe，只结束主进程没用；必须按进程名整组清掉。
RUNNING_EXES = ('QifeiIME.exe', 'QifeiClient.exe', 'QifeiServer.exe')


def stop_running_apps() -> list[str]:
    """装之前结束正在运行的启飞输入法，返回确实被结束的进程名。

    输入法常驻托盘、还可能开机自启，指望用户手动退出不现实。taskkill 是系统
    自带命令，用标准库 subprocess 就能调，不给安装器增加 psutil 之类的依赖；
    GUI 与 --silent 两条安装路径共用这段逻辑。
    """
    stopped: list[str] = []
    for name in RUNNING_EXES:
        try:
            result = subprocess.run(
                ['taskkill', '/F', '/IM', name],
                creationflags=CREATE_NO_WINDOW, timeout=15,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                stopped.append(name)
        except Exception:
            pass
    return stopped


def payload_path() -> Path:
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    for candidate in (base / 'payload.zip', Path(__file__).resolve().parent / 'payload.zip'):
        if candidate.exists():
            return candidate
    raise FileNotFoundError('安装包缺少 payload.zip')


def default_install_dir() -> Path:
    local = os.environ.get('LOCALAPPDATA') or str(Path.home())
    return Path(local) / 'Programs' / APP_DIR_NAME


def make_icon() -> QIcon:
    """安装器窗口图标，与 assets/icon.ico 同款（见 tools/make_icon.py）。

    安装器是独立 onefile exe，运行到这里时 payload 还没解压，拿不到随包
    ico，所以现场画一份；参数和 qifei/app.py 的 fallback 保持一致。
    """
    n = 128.0
    pixmap = QPixmap(int(n), int(n))
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    gradient = QLinearGradient(QPointF(0, 0), QPointF(n, n))
    gradient.setColorAt(0.0, QColor('#7BA2FF'))
    gradient.setColorAt(0.5, QColor('#3D6BFF'))
    gradient.setColorAt(1.0, QColor('#2A45B8'))
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(QRectF(0, 0, n, n), n * 0.225, n * 0.225)

    painter.setBrush(QColor(255, 255, 255, 255))

    def bar(cx: float, cy: float, width: float, half: float) -> None:
        painter.drawRoundedRect(
            QRectF(cx - width / 2, cy - half, width, half * 2),
            width / 2, width / 2)

    for x, ratio in zip((0.24, 0.37, 0.50, 0.63, 0.76),
                        (0.34, 0.62, 1.00, 0.62, 0.34)):
        bar(x * n, 0.42 * n, 0.075 * n, 0.25 * n * ratio)

    thick = 0.062 * n
    painter.drawRoundedRect(
        QRectF(0.22 * n, 0.79 * n - thick / 2, 0.46 * n, thick),
        thick / 2, thick / 2)
    cw = 0.045 * n
    painter.drawRoundedRect(
        QRectF(0.73 * n - cw / 2, 0.71 * n, cw, 0.16 * n),
        cw / 2, cw / 2)
    painter.end()
    return QIcon(pixmap)


def ensure_dirs(target: Path) -> None:
    """模型和日志目录先建好，首次启动时界面才不会因为目录不存在而报错

    models/llm 放安装包内置的推荐润色模型，也是用户以后人工换模型、
    下载其他 .gguf 的目标目录，目录不存在就得让用户自己猜目录名。
    """
    for name in ('models', 'models/llm', 'logs'):
        try:
            (target / name).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


# 用户模型保护：这些目录下的文件已存在时不覆盖。程序本体不在此列，
# 否则覆盖安装会留下旧 exe / 旧 DLL，升级等于没升。
USER_DATA_PREFIX = 'models/'


def extract_payload(zf: zipfile.ZipFile, target: Path, on_progress=None):
    """解压 payload 到安装目录，返回 (释放文件数, 跳过的用户模型数)。

    models/ 下已经存在的文件一律跳过：内置模型只在第一次安装时落地，
    用户后来人工换过的同名模型、下载的其他模型都原样保留。
    """
    members = zf.infolist()
    total = len(members) or 1
    written = skipped = 0
    for index, member in enumerate(members, 1):
        name = member.filename
        if (name.startswith(USER_DATA_PREFIX) and not name.endswith('/')
                and (target / name).exists()):
            skipped += 1
        else:
            try:
                zf.extract(member, target)
            except PermissionError:
                # 刚 taskkill 完文件句柄可能还没释放，稍等再试一次；
                # 仍失败就给一句能照做的提示，别丢裸的 Errno 13。
                time.sleep(0.6)
                try:
                    zf.extract(member, target)
                except PermissionError as exc:
                    raise PermissionError(
                        f'文件被占用，无法覆盖 {name}。请退出正在运行的启飞输入法'
                        f'（托盘图标右键退出，或在任务管理器结束 Qifei*.exe），'
                        f'再重新运行安装程序。'
                    ) from exc
            written += 1
        if on_progress:
            on_progress(index, total)
    return written, skipped


class InstallWorker(QThread):
    progress = Signal(int, str)
    finished_ok = Signal(bool, str)

    def __init__(self, target: Path, launch: bool, parent=None):
        super().__init__(parent)
        self.target = target
        self.launch = launch

    def run(self) -> None:
        try:
            archive = payload_path()
            self.progress.emit(2, '正在准备安装目录…')
            self.target.mkdir(parents=True, exist_ok=True)

            stopped = stop_running_apps()
            if stopped:
                self.progress.emit(3, '已结束正在运行的输入法…')
                time.sleep(0.4)   # 等文件句柄彻底释放

            with zipfile.ZipFile(archive) as zf:
                def progress(index: int, total: int) -> None:
                    if index % 15 == 0 or index == total:
                        percent = 5 + int(index * 82 / total)
                        self.progress.emit(percent, f'正在释放文件 {index}/{total}')

                written, skipped = extract_payload(zf, self.target, progress)

            note = f'已释放 {written} 个文件'
            if skipped:
                note += f'，保留 {skipped} 个用户模型（未覆盖）'
            self.progress.emit(88, note)
            self.progress.emit(90, '正在创建快捷方式…')
            ensure_dirs(self.target)
            # 必须先写卸载脚本，create_shortcuts 才有东西可以指向
            write_uninstall_info(self.target)
            write_uninstaller(self.target)
            create_shortcuts(self.target)

            self.progress.emit(98, '正在完成…')
            if self.launch:
                launch_app(self.target)
            self.progress.emit(100, '安装完成')
            self.finished_ok.emit(True, str(self.target))
        except Exception as exc:
            self.finished_ok.emit(False, str(exc))


def _powershell(script: str) -> None:
    try:
        subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
            creationflags=CREATE_NO_WINDOW, timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


CSIDL_DESKTOPDIRECTORY = 0x0010
CSIDL_PROGRAMS = 0x0002


def special_dir(csidl: int, fallback: Path) -> Path:
    """用系统 API 取桌面 / 开始菜单目录

    比读 %USERPROFILE%、%APPDATA% 可靠：环境变量缺失、被改写、
    或者用户把桌面重定向到 OneDrive 时都能拿到正确位置。
    """
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer) == 0:
            path = Path(buffer.value)
            if path.exists():
                return path
    except Exception:
        pass
    return fallback


def create_shortcuts(target: Path) -> None:
    exe = target / EXE_NAME
    desktop = special_dir(
        CSIDL_DESKTOPDIRECTORY,
        Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'Desktop',
    )
    start_menu = special_dir(
        CSIDL_PROGRAMS,
        Path(os.environ.get('APPDATA', str(Path.home())))
        / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs',
    )
    for link in (desktop / f'{BRAND}.lnk', start_menu / f'{BRAND}.lnk'):
        _powershell(
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
            f"'{link}');$s.TargetPath='{exe}';$s.WorkingDirectory='{target}';"
            f"$s.IconLocation='{exe},0';$s.Save()"
        )

    uninstall = target / 'uninstall.bat'
    if uninstall.exists():
        uninstall_link = start_menu / f'卸载{BRAND}.lnk'
        _powershell(
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
            f"'{uninstall_link}');$s.TargetPath='{uninstall}';"
            f"$s.WorkingDirectory='{target}';$s.Save()"
        )


def write_uninstall_info(target: Path) -> None:
    try:
        import winreg
        key_path = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\QifeiIME'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, BRAND)
            winreg.SetValueEx(key, 'DisplayVersion', 0, winreg.REG_SZ, VERSION)
            winreg.SetValueEx(key, 'Publisher', 0, winreg.REG_SZ, 'Qifei')
            winreg.SetValueEx(key, 'InstallLocation', 0, winreg.REG_SZ, str(target))
            winreg.SetValueEx(key, 'DisplayIcon', 0, winreg.REG_SZ, str(target / EXE_NAME))
            winreg.SetValueEx(key, 'UninstallString', 0, winreg.REG_SZ,
                              f'"{target / "uninstall.bat"}"')
            winreg.SetValueEx(key, 'NoModify', 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, 'NoRepair', 0, winreg.REG_DWORD, 1)
    except Exception:
        pass


def write_uninstaller(target: Path) -> None:
    script = f'''@echo off
chcp 65001 >nul
echo 正在卸载 {BRAND} ...
taskkill /f /im QifeiIME.exe >nul 2>nul
taskkill /f /im QifeiClient.exe >nul 2>nul
taskkill /f /im QifeiServer.exe >nul 2>nul
reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\QifeiIME" /f >nul 2>nul
reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" /v QifeiIME /f >nul 2>nul
del "%USERPROFILE%\\Desktop\\{BRAND}.lnk" >nul 2>nul
del "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\{BRAND}.lnk" >nul 2>nul
del "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\卸载{BRAND}.lnk" >nul 2>nul
cd /d "%TEMP%"
rmdir /s /q "{target}"
echo 卸载完成，用户数据（配置与录音）保留在 %APPDATA%\\QifeiIME
pause
'''
    try:
        (target / 'uninstall.bat').write_text(script, encoding='utf-8')
    except Exception:
        pass


def launch_app(target: Path) -> None:
    try:
        subprocess.Popen([str(target / EXE_NAME)], cwd=str(target),
                         creationflags=0x00000008)  # DETACHED_PROCESS
    except Exception:
        pass


class Installer(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'{BRAND} 安装程序')
        self.setWindowIcon(make_icon())
        self.setFixedSize(660, 430)
        self.setStyleSheet(self._qss())
        self._worker: InstallWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_body(), 1)
        layout.addWidget(self._build_footer())

    # ---------- UI ----------

    @staticmethod
    def _qss() -> str:
        return '''
        QWidget { font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 13px; color: #141A24; }
        QWidget#Root { background: #FFFFFF; }
        QFrame#Header { background: #F5F7FB; border-bottom: 1px solid #E6EAF2; }
        QLabel#Title { font-size: 19px; font-weight: 600; }
        QLabel#Sub { color: #66707F; font-size: 12px; }
        QLabel#Hint { color: #98A1B0; font-size: 12px; }
        QLineEdit { border: 1px solid #D6DCE8; border-radius: 9px; padding: 9px 10px; }
        QLineEdit:focus { border-color: #3D6BFF; }
        QPushButton { border: 1px solid #D6DCE8; border-radius: 9px; padding: 9px 20px;
                      background: #FFFFFF; }
        QPushButton:hover { background: #F0F3F9; }
        QPushButton#Primary { background: #3D6BFF; color: #FFFFFF; border: none; font-weight: 600; }
        QPushButton#Primary:hover { background: #2F5AE0; }
        QPushButton#Primary:disabled { background: #B9C6E8; }
        QProgressBar { background: #F0F3F9; border: none; border-radius: 6px;
                       height: 12px; text-align: center; font-size: 11px; color: #66707F; }
        QProgressBar::chunk { background: #3D6BFF; border-radius: 6px; }
        QCheckBox { color: #66707F; font-size: 12px; }
        '''

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName('Header')
        header.setFixedHeight(96)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(14)

        icon = QLabel()
        icon.setPixmap(make_icon().pixmap(48, 48))
        layout.addWidget(icon)

        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(f'安装 {BRAND}')
        title.setObjectName('Title')
        sub = QLabel(f'版本 {VERSION}　完全离线的中文语音输入')
        sub.setObjectName('Sub')
        text.addWidget(title)
        text.addWidget(sub)
        layout.addLayout(text, 1)
        return header

    def _build_body(self) -> QWidget:
        body = QWidget()
        body.setObjectName('Root')
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 22, 28, 12)
        layout.setSpacing(12)

        label = QLabel('安装位置')
        label.setStyleSheet('font-weight: 600;')
        layout.addWidget(label)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.edit_dir = QLineEdit(str(default_install_dir()))
        row.addWidget(self.edit_dir, 1)
        browse = QPushButton('浏览…')
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        hint = QLabel('安装到当前用户目录，不需要管理员权限。已内置中文润色模型，'
                     '装完即可用；语音模型首次启动时按引导下载。'
                     '覆盖安装不会动你已换过的模型。')
        hint.setObjectName('Hint')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.check_launch = QCheckBox('安装完成后立即运行')
        self.check_launch.setChecked(True)
        layout.addWidget(self.check_launch)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.label_status = QLabel('')
        self.label_status.setObjectName('Hint')
        self.label_status.setWordWrap(True)
        layout.addWidget(self.label_status)

        layout.addStretch(1)
        return body

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setObjectName('Root')
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(28, 10, 28, 20)
        layout.addStretch(1)

        self.btn_cancel = QPushButton('取消')
        self.btn_cancel.clicked.connect(self.close)
        layout.addWidget(self.btn_cancel)

        self.btn_install = QPushButton('立即安装')
        self.btn_install.setObjectName('Primary')
        self.btn_install.clicked.connect(self._install)
        layout.addWidget(self.btn_install)
        return footer

    # ---------- 行为 ----------

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, '选择安装目录', self.edit_dir.text())
        if path:
            self.edit_dir.setText(str(Path(path) / APP_DIR_NAME))

    def _install(self) -> None:
        target = Path(self.edit_dir.text().strip() or str(default_install_dir()))
        self.btn_install.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.edit_dir.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)

        worker = InstallWorker(target, self.check_launch.isChecked(), self)
        worker.progress.connect(self._on_progress)
        worker.finished_ok.connect(self._on_done)
        self._worker = worker
        worker.start()

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(percent)
        self.progress.setFormat(f'{percent}%')
        self.label_status.setText(message)

    def _on_done(self, ok: bool, message: str) -> None:
        self.btn_cancel.setEnabled(True)
        self.btn_install.setEnabled(True)
        if ok:
            self.label_status.setText(f'安装完成：{message}')
            self.btn_install.setText('完成')
            try:
                self.btn_install.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.btn_install.clicked.connect(self.close)
        else:
            self.label_status.setText(f'安装失败：{message}')
            self.edit_dir.setEnabled(True)


def _silent_install(argv: list[str]) -> int:
    """静默安装：--silent [--dir 目标目录] [--no-launch]

    给批量部署用，也方便自动化验证。结果写进安装目录旁的 install.log。
    """
    target = default_install_dir()
    launch = True
    if '--dir' in argv:
        index = argv.index('--dir')
        if index + 1 < len(argv):
            target = Path(argv[index + 1]).expanduser()
    if '--no-launch' in argv:
        launch = False

    lines: list[str] = []

    try:
        archive = payload_path()
        target.mkdir(parents=True, exist_ok=True)
        stopped = stop_running_apps()
        if stopped:
            time.sleep(0.4)
            lines.append(f'[ok] 已结束正在运行的输入法：{", ".join(stopped)}')
        with zipfile.ZipFile(archive) as zf:
            written, skipped = extract_payload(zf, target)
        line = f'[ok] 解压 {written} 个文件到 {target}'
        if skipped:
            line += f'（保留 {skipped} 个用户模型，未覆盖）'
        lines.append(line)

        ensure_dirs(target)
        # 先写卸载脚本，快捷方式才有目标可指
        write_uninstall_info(target)
        write_uninstaller(target)
        create_shortcuts(target)
        lines.append('[ok] 已创建快捷方式')

        lines.append('[ok] 已写入卸载信息')

        if launch:
            launch_app(target)
            lines.append('[ok] 已启动主程序')

        code = 0
    except Exception as exc:
        lines.append(f'[fail] {type(exc).__name__}: {exc}')
        code = 1

    text = '\n'.join(lines)
    try:
        (target / 'install.log').write_text(text, encoding='utf-8')
    except OSError:
        pass
    if sys.stdout is not None:   # 窗口程序没有控制台
        print(text, flush=True)
    return code


def main() -> int:
    if '--silent' in sys.argv:
        return _silent_install(sys.argv)

    app = QApplication(sys.argv)
    app.setApplicationName(f'{BRAND} 安装程序')
    window = Installer()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
