import sys
import os
from PySide6.QtCore import Qt, QLoggingCategory, qInstallMessageHandler, QtMsgType
from PySide6.QtGui import QScreen
from PySide6.QtWidgets import QApplication

from beamerplus.MainWindow import MainWindow
from beamerplus.Viewer import (
    Viewer,
    place_viewer_on_screen,
    place_viewer_windowed,
    select_projector_screen,
)

# 1. QtWayland textinput などの不要な警告・デバッグログを抑制
os.environ.setdefault(
    "QT_LOGGING_RULES",
    "qt.qpa.wayland*=false;qt.qpa.wayland.textinput*=false;qt.qpa.wayland.textinput.warning=false"
)

# 2. Wayland 環境で GTK ネイティブダイアログとのウィンドウハンドル不整合 (Gdk-CRITICAL) を防止
QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)


def _qt_message_filter(msg_type, context, message):
    """Wayland / Gdk の偽陽性・ノイズ警告メッセージをターミナル出力から抑制"""
    if any(keyword in message for keyword in ("disableSurface", "QWaylandTextInputv3", "GDK_IS_WAYLAND_WINDOW")):
        return
    if msg_type == QtMsgType.QtFatalMsg:
        sys.stderr.write(f"Fatal: {message}\n")
        sys.exit(1)
    elif msg_type == QtMsgType.QtCriticalMsg:
        sys.stderr.write(f"Critical: {message}\n")
    elif msg_type == QtMsgType.QtWarningMsg:
        sys.stderr.write(f"Warning: {message}\n")
    elif msg_type == QtMsgType.QtInfoMsg:
        sys.stdout.write(f"Info: {message}\n")


qInstallMessageHandler(_qt_message_filter)


def select_startup_screens(app: QApplication) -> tuple[QScreen | None, QScreen | None]:
    """発表者画面用 (primaryScreen) とプロジェクタ画面用のスクリーンを選択する。"""
    primary_screen = app.primaryScreen()
    if primary_screen is None:
        return None, None

    projector_screen = select_projector_screen(app.screens(), primary_screen)
    return primary_screen, projector_screen


def place_startup_windows(app: QApplication, viewer: Viewer, main_window: MainWindow) -> None:
    """モニター構成に応じて発表者画面とプロジェクタ画面を配置する。"""
    primary_screen, projector_screen = select_startup_screens(app)

    if primary_screen is not None and projector_screen is not None:
        # プライマリ以外の画面のうち最もインデックスが小さいものにViewerを配置して全画面表示
        place_viewer_on_screen(viewer, projector_screen, fullscreen=True)

        # プライマリ画面に発表者ウィンドウを配置
        main_window.setGeometry(primary_screen.geometry().adjusted(50, 50, -50, -50))
        main_window.show()
    else:
        # 単一画面の場合は両方をウィンドウ表示
        place_viewer_windowed(viewer, width=800, height=600)
        main_window.show()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("BeamerPlus")

    # Viewer（プロジェクタ用画面）と MainWindow（発表者画面）のインスタンス生成
    viewer = Viewer()
    main_window = MainWindow(viewer=viewer)

    # マルチモニターのチェックと配置
    place_startup_windows(app, viewer, main_window)

    # コマンドライン引数にPDFが指定されていたら自動でロード
    pdf_path = None
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".pdf"):
        pdf_path = sys.argv[1]

    if pdf_path and os.path.exists(pdf_path):
        main_window.load_pdf(pdf_path)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
