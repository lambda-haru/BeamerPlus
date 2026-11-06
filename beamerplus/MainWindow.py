import os
from PySide6.QtCore import Qt, QTimer, QTime, QEvent, QByteArray, QBuffer, QIODevice, QDateTime, QPointF
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QFileDialog, QMessageBox, QToolBar, QPushButton, QLabel, QFrame,
    QApplication, QSizePolicy
)
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QPainter, QColor, QPen, QBrush, QFont, QPixmap

from beamerplus.PdfReader import PdfReader
from beamerplus.DrawingModel import PresentationController, create_smooth_path
from beamerplus.ThumbnailBar import ThumbnailBar
from beamerplus.BookmarkDrawer import BookmarkDrawer
from beamerplus.SimpleView import SimpleView
from beamerplus.NotePanel import NotePanel
from beamerplus.Viewer import Viewer
from beamerplus.WebServer import WebServer, WebBridge
from beamerplus.IpadDialog import IpadDialog


class MainWindow(QMainWindow):
    def __init__(self, viewer: Viewer | None = None):
        super().__init__()

        self.setWindowTitle("BeamerPlus 発表者ビュー")
        self.resize(1200, 800)

        self.controller = PresentationController(self)
        self.controller.blank_state_changed.connect(self._on_blank_state_changed)
        self.controller.sketchbook_state_changed.connect(self._on_sketchbook_state_changed)
        self.viewer = viewer
        if self.viewer:
            self.viewer.connect_controller(self.controller)

        self.pdf_reader: PdfReader | None = None
        self.render_dpi: int = 400  # デフォルト: 4K 超高精細 (400 DPI)

        # プレゼンタイマー
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self._update_timer)
        self.presentation_time = QTime(0, 0, 0)

        # メインレイアウト（水平: 左端に引き出しボタン、右側にスライドやスライド一覧）
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.root_hlayout = QHBoxLayout(central_widget)
        self.root_hlayout.setContentsMargins(0, 0, 0, 0)
        self.root_hlayout.setSpacing(0)

        # 0. ウィンドウ左端: しおり引き出しボタン（オーバーレイと同じ高さの縦長ボタン）
        self.btn_sidebar_drawer = QPushButton(">")
        self.btn_sidebar_drawer.setObjectName("SidebarDrawerBtn")
        self.btn_sidebar_drawer.setFixedWidth(20)
        self.btn_sidebar_drawer.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.btn_sidebar_drawer.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sidebar_drawer.setToolTip("しおり / 目次を開く (Ctrl+B)")
        self.btn_sidebar_drawer.setStyleSheet(
            "QPushButton#SidebarDrawerBtn {"
            "  background-color: #252526;"
            "  color: #888888;"
            "  border: none;"
            "  border-right: 1px solid #333336;"
            "  font-size: 13px;"
            "  font-weight: bold;"
            "  padding: 0px;"
            "}"
            "QPushButton#SidebarDrawerBtn:hover {"
            "  background-color: #2d2d30;"
            "  color: #007acc;"
            "}"
            "QPushButton#SidebarDrawerBtn:pressed {"
            "  background-color: #1e1e1e;"
            "}"
        )
        self.btn_sidebar_drawer.clicked.connect(self.toggle_bookmark_drawer)
        self.btn_sidebar_drawer.hide()  # 初期状態（PDF未ロードまたはしおり無し）は非表示
        self.root_hlayout.addWidget(self.btn_sidebar_drawer)

        # メインコンテンツコンテナ（スライド操作領域 ＋ スライド一覧バー）
        main_content_container = QWidget()
        self.main_layout = QVBoxLayout(main_content_container)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        self.root_hlayout.addWidget(main_content_container, 1)

        # 中央スプリッター (左: スライド画面＋ツールバー, 右: ノートパネル)
        self.center_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.center_splitter.setStyleSheet(
            "QSplitter::handle { background-color: #333336; width: 4px; }"
        )

        # 1. メインスライド操作領域（ポインタ・ペンなどのツールバー ＋ キャンバス：常時表示）
        self.slide_view = SimpleView(self.controller)
        self.slide_view.save_sketch_requested.connect(self.save_current_page_sketch)
        self.center_splitter.addWidget(self.slide_view)

        # 2. ノートパネル（右サイド、表示/非表示切り替え可能）
        self.note_panel = NotePanel()
        self.center_splitter.addWidget(self.note_panel)

        # 誤操作による完全な潰れ（非表示）を防ぎ、スムーズなリサイズを実現
        self.center_splitter.setCollapsible(0, False)
        self.center_splitter.setCollapsible(1, False)

        # スプリッターの比率設定 (スライド: 70%, ノート: 30%)
        self.center_splitter.setStretchFactor(0, 7)
        self.center_splitter.setStretchFactor(1, 3)

        self.main_layout.addWidget(self.center_splitter, 1)

        # 3. 下部: スライド一覧（ThumbnailBar、表示/非表示切り替え可能）
        self.thumbnail_bar = ThumbnailBar()
        self.thumbnail_bar.page_selected.connect(self.controller.jump_to_page)
        self.main_layout.addWidget(self.thumbnail_bar)

        # 4. しおり（目次）引き出しオーバーレイ
        self.bookmark_drawer = BookmarkDrawer(central_widget)
        self.bookmark_drawer.set_trigger_widget(self.btn_sidebar_drawer)
        self.bookmark_drawer.page_selected.connect(self.controller.jump_to_page)
        self.bookmark_drawer.drawer_state_changed.connect(self._on_bookmark_drawer_state_changed)

        # コントローラからのシグナル受信
        self.controller.page_changed.connect(self._on_page_changed)

        # iPad / Web リモート連携サーバーの初期化
        self._is_handling_web_stroke: bool = False
        self.web_bridge = WebBridge()
        self.web_server = WebServer(
            self.web_bridge,
            self._get_slide_image_bytes,
            self._get_web_status
        )
        self._connect_web_signals()
        self.web_server.start()

        # UIメニューとツールバー作成
        self._create_menus_and_toolbars()

        # スタイル設定
        self.setStyleSheet(
            "QMainWindow { background-color: #1e1e1e; color: #ffffff; }"
            "QToolBar { background-color: #2d2d30; border-bottom: 1px solid #3e3e42; spacing: 6px; padding: 3px; }"
            "QMenuBar { background-color: #2d2d30; color: #cccccc; }"
            "QMenuBar::item:selected { background-color: #3e3e42; }"
            "QMenu { background-color: #252526; color: #cccccc; border: 1px solid #3e3e42; }"
            "QMenu::item:selected { background-color: #094771; }"
        )

    def _create_menus_and_toolbars(self):
        menu_bar = self.menuBar()

        # ファイルメニュー
        file_menu = menu_bar.addMenu("ファイル(&F)")
        
        open_action = QAction("開く(&O)...", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_file_dialog)
        file_menu.addAction(open_action)

        save_notes_action = QAction("ノートを保存(&S)", self)
        save_notes_action.setShortcut(QKeySequence.StandardKey.Save)
        save_notes_action.triggered.connect(self.save_notes)
        file_menu.addAction(save_notes_action)

        save_sketch_action = QAction("現在のページのスケッチを画像保存(&E)...", self)
        save_sketch_action.setShortcut("Ctrl+Shift+S")
        save_sketch_action.triggered.connect(lambda: self.save_current_page_sketch())
        file_menu.addAction(save_sketch_action)

        file_menu.addSeparator()

        quit_action = QAction("終了(&Q)", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # 表示メニュー
        view_menu = menu_bar.addMenu("表示(&V)")

        self.act_toggle_notes = QAction("ノートパネルの表示/非表示", self)
        self.act_toggle_notes.setShortcut("Ctrl+N")
        self.act_toggle_notes.setCheckable(True)
        self.act_toggle_notes.setChecked(True)
        self.act_toggle_notes.triggered.connect(self.toggle_note_panel)
        view_menu.addAction(self.act_toggle_notes)

        self.act_toggle_thumbnails = QAction("スライド一覧バーの表示/非表示", self)
        self.act_toggle_thumbnails.setShortcut("Ctrl+T")
        self.act_toggle_thumbnails.setCheckable(True)
        self.act_toggle_thumbnails.setChecked(True)
        self.act_toggle_thumbnails.triggered.connect(self.toggle_thumbnail_bar)
        view_menu.addAction(self.act_toggle_thumbnails)

        self.act_toggle_bookmarks = QAction("しおり（目次）の表示/非表示", self)
        self.act_toggle_bookmarks.setShortcut("Ctrl+B")
        self.act_toggle_bookmarks.setEnabled(False)
        self.act_toggle_bookmarks.triggered.connect(self.toggle_bookmark_drawer)
        view_menu.addAction(self.act_toggle_bookmarks)

        self.act_toggle_toolbar = QAction("メインツールバーの表示/非表示", self)
        self.act_toggle_toolbar.setCheckable(True)
        self.act_toggle_toolbar.setChecked(True)
        self.act_toggle_toolbar.triggered.connect(self.toggle_main_toolbar)
        view_menu.addAction(self.act_toggle_toolbar)

        view_menu.addSeparator()

        self.act_toggle_sketch = QAction("スケッチの表示/非表示", self)
        self.act_toggle_sketch.setShortcut("H")
        self.act_toggle_sketch.triggered.connect(self.controller.toggle_sketch_visibility)
        view_menu.addAction(self.act_toggle_sketch)

        view_menu.addSeparator()

        # 画質設定サブメニュー
        quality_menu = view_menu.addMenu("レンダリング画質(&Q)")
        self.dpi_group = QActionGroup(self)
        self.dpi_group.setExclusive(True)

        for label, dpi in [
            ("高画質 (250 DPI - フルHD向け)", 250),
            ("4K 超高精細 (400 DPI - デフォルト/4K向け)", 400),
            ("5K / Ultra (500 DPI)", 500),
        ]:
            action = QAction(label, self, checkable=True)
            if dpi == self.render_dpi:
                action.setChecked(True)
            action.triggered.connect(lambda checked, d=dpi: self.set_render_dpi(d))
            self.dpi_group.addAction(action)
            quality_menu.addAction(action)

        # プレゼンテーションメニュー
        pres_menu = menu_bar.addMenu("プレゼンテーション(&P)")

        act_proj_toggle = QAction("プロジェクタ画面の表示/非表示", self)
        act_proj_toggle.setShortcut("F5")
        act_proj_toggle.triggered.connect(self.toggle_viewer_window)
        pres_menu.addAction(act_proj_toggle)

        act_proj_fullscreen = QAction("プロジェクタ画面を全画面表示", self)
        act_proj_fullscreen.setShortcut("F11")
        act_proj_fullscreen.triggered.connect(self.fullscreen_viewer)
        pres_menu.addAction(act_proj_fullscreen)

        act_blank = QAction("画面を暗転 (ブラックアウト)", self)
        act_blank.setShortcut("B")
        act_blank.triggered.connect(self.toggle_blank_screen)
        pres_menu.addAction(act_blank)

        # ツールメニュー
        tool_menu = menu_bar.addMenu("ツール(&T)")
        act_ipad = QAction("📱 iPad / タブレット連携(&I)...", self)
        act_ipad.setToolTip("iPad のブラウザから Apple Pencil で手書き描画するための接続情報を表示")
        act_ipad.triggered.connect(self.show_ipad_dialog)
        tool_menu.addAction(act_ipad)

        # 上部ツールバー
        self.main_toolbar = QToolBar("メインツールバー")
        self.main_toolbar.setMovable(False)
        self.main_toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.addToolBar(self.main_toolbar)

        btn_open = QPushButton("📁 開く")
        btn_open.setStyleSheet(self._tb_btn_style())
        btn_open.clicked.connect(self.open_file_dialog)
        self.main_toolbar.addWidget(btn_open)

        self.main_toolbar.addSeparator()

        # 表示トグルボタン: ノート
        self.btn_toggle_notes = QPushButton("📝 ノート")
        self.btn_toggle_notes.setCheckable(True)
        self.btn_toggle_notes.setChecked(True)
        self.btn_toggle_notes.setStyleSheet(self._tb_btn_style())
        self.btn_toggle_notes.setToolTip("ノートパネルの表示/非表示 (Ctrl+N)")
        self.btn_toggle_notes.clicked.connect(self.toggle_note_panel)
        self.main_toolbar.addWidget(self.btn_toggle_notes)

        # 表示トグルボタン: スライド一覧
        self.btn_toggle_thumbs = QPushButton("🎞️ スライド一覧")
        self.btn_toggle_thumbs.setCheckable(True)
        self.btn_toggle_thumbs.setChecked(True)
        self.btn_toggle_thumbs.setStyleSheet(self._tb_btn_style())
        self.btn_toggle_thumbs.setToolTip("スライド一覧バーの表示/非表示 (Ctrl+T)")
        self.btn_toggle_thumbs.clicked.connect(self.toggle_thumbnail_bar)
        self.main_toolbar.addWidget(self.btn_toggle_thumbs)

        # 表示トグルボタン: しおり
        self.btn_toggle_bookmarks = QPushButton("📑 しおり")
        self.btn_toggle_bookmarks.setStyleSheet(self._tb_btn_style())
        self.btn_toggle_bookmarks.setToolTip("しおり（目次）引き出しの表示/非表示 (Ctrl+B)")
        self.btn_toggle_bookmarks.clicked.connect(self.toggle_bookmark_drawer)
        self.btn_toggle_bookmarks.setEnabled(False)
        self.main_toolbar.addWidget(self.btn_toggle_bookmarks)

        self.main_toolbar.addSeparator()

        # タイマー表示
        self.lbl_timer = QLabel("⏱️ 00:00:00")
        self.lbl_timer.setStyleSheet("color: #4fc3f7; font-size: 13px; margin-left: 8px;")
        self.lbl_timer.setFixedWidth(105)
        self.lbl_timer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.main_toolbar.addWidget(self.lbl_timer)

        self.btn_timer_toggle = QPushButton("▶ 開始")
        self.btn_timer_toggle.setStyleSheet(self._tb_btn_style())
        self.btn_timer_toggle.setFixedWidth(100)
        self.btn_timer_toggle.clicked.connect(lambda: self._toggle_timer(self.btn_timer_toggle))
        self.main_toolbar.addWidget(self.btn_timer_toggle)

        btn_timer_reset = QPushButton("↺ リセット")
        btn_timer_reset.setStyleSheet(self._tb_btn_style())
        btn_timer_reset.clicked.connect(self._reset_timer)
        self.main_toolbar.addWidget(btn_timer_reset)

        self.main_toolbar.addSeparator()

        # プロジェクタ画面制御ボタン
        btn_viewer = QPushButton("📽️ プロジェクタ画面")
        btn_viewer.setStyleSheet(self._tb_btn_style())
        btn_viewer.clicked.connect(self.toggle_viewer_window)
        self.main_toolbar.addWidget(btn_viewer)

        self.btn_sketchbook = QPushButton("📋 板書 (S)")
        self.btn_sketchbook.setStyleSheet(self._tb_btn_style())
        self.btn_sketchbook.setFixedWidth(120)
        self.btn_sketchbook.setToolTip("白紙の板書画面（スケッチブック）に切り替えます [S/W]")
        self.btn_sketchbook.clicked.connect(self.toggle_sketchbook)
        self.main_toolbar.addWidget(self.btn_sketchbook)

        self.btn_blank = QPushButton("⬛ 暗転 (B)")
        self.btn_blank.setStyleSheet(self._tb_btn_style())
        self.btn_blank.setFixedWidth(120)
        self.btn_blank.setToolTip("プロジェクタ画面を一時的に真っ黒にします (B)")
        self.btn_blank.clicked.connect(self.toggle_blank_screen)
        self.main_toolbar.addWidget(self.btn_blank)

        self.main_toolbar.addSeparator()

        # iPad連携ボタン
        self.btn_ipad_connect = QPushButton("📱 iPad 連携")
        self.btn_ipad_connect.setStyleSheet(self._tb_btn_style())
        self.btn_ipad_connect.setToolTip("iPad のブラウザから Apple Pencil で手書き描画するための接続情報を表示")
        self.btn_ipad_connect.clicked.connect(self.show_ipad_dialog)
        self.main_toolbar.addWidget(self.btn_ipad_connect)

        # ツールバーの全ボタンからフォーカスを外す（クリック後に矢印キーが吸われないようにする）
        for btn in (
            btn_open, self.btn_toggle_notes, self.btn_toggle_thumbs,
            self.btn_timer_toggle, btn_timer_reset, btn_viewer, self.btn_sketchbook, self.btn_blank,
            self.btn_ipad_connect
        ):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # アプリケーション全体のキーイベントフィルターを登録
        # （ボタンやスライダー等の操作後でも、ノート編集中でなければ矢印キー等で確実にスライド操作可能にする）
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def _tb_btn_style(self) -> str:
        return (
            "QPushButton { background-color: #3e3e42; color: #ffffff; border: 1px solid #555555; border-radius: 3px; padding: 4px 10px; font-size: 12px; }"
            "QPushButton:hover { background-color: #505054; }"
            "QPushButton:checked { background-color: #007acc; border-color: #0098ff; }"
        )

    def set_render_dpi(self, dpi: int):
        if self.render_dpi != dpi:
            self.render_dpi = dpi
            if self.pdf_reader:
                self._update_current_page_display(self.controller.current_page)

    def toggle_note_panel(self):
        """ノートパネルの表示/非表示切り替え (Ctrl+N)"""
        is_visible = not self.note_panel.isVisible()
        self.note_panel.setVisible(is_visible)
        self.btn_toggle_notes.setChecked(is_visible)
        self.act_toggle_notes.setChecked(is_visible)

    def toggle_thumbnail_bar(self):
        """スライド一覧バーの表示/非表示切り替え (Ctrl+T)"""
        is_visible = not self.thumbnail_bar.isVisible()
        self.thumbnail_bar.setVisible(is_visible)
        self.btn_toggle_thumbs.setChecked(is_visible)
        self.act_toggle_thumbnails.setChecked(is_visible)

    def toggle_bookmark_drawer(self):
        """しおり（目次）引き出しオーバーレイの表示/非表示切り替え (Ctrl+B)"""
        if hasattr(self, "bookmark_drawer"):
            self.bookmark_drawer.toggle_drawer()

    def _on_bookmark_drawer_state_changed(self, is_open: bool):
        if not is_open:
            if hasattr(self, "slide_view") and hasattr(self.slide_view, "canvas"):
                self.slide_view.canvas.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "bookmark_drawer"):
            self.bookmark_drawer.set_sidebar_offset(0)
            self.bookmark_drawer.update_layout()

    def toggle_main_toolbar(self):
        """メインツールバーの表示/非表示切り替え"""
        is_visible = not self.main_toolbar.isVisible()
        self.main_toolbar.setVisible(is_visible)
        self.act_toggle_toolbar.setChecked(is_visible)

    def createPopupMenu(self):
        """ツールバー右クリック時のデフォルトコンテキストメニューを無効化"""
        return None

    def toggle_viewer_window(self):
        if not self.viewer:
            return
        if self.viewer.isVisible():
            self.viewer.hide()
        else:
            self.viewer.show()
            self.viewer.fit_view()

    def fullscreen_viewer(self):
        if self.viewer:
            self.viewer.show()
            self.viewer.toggle_fullscreen()

    def toggle_blank_screen(self):
        self.controller.toggle_blank()

    def toggle_sketchbook(self):
        self.controller.toggle_sketchbook()

    def _on_blank_state_changed(self, is_blank: bool):
        if hasattr(self, "btn_blank"):
            if is_blank:
                self.btn_blank.setText("⬛ 暗転中 (B)")
                self.btn_blank.setStyleSheet(
                    "QPushButton { background-color: #b71c1c; color: #ffffff; border: 1px solid #f44336; border-radius: 3px; padding: 4px 10px; font-size: 12px; }"
                )
            else:
                self.btn_blank.setText("⬛ 暗転 (B)")
                self.btn_blank.setStyleSheet(self._tb_btn_style())

    def _on_sketchbook_state_changed(self, is_sketchbook: bool):
        if hasattr(self, "btn_sketchbook"):
            if is_sketchbook:
                self.btn_sketchbook.setText("📋 板書中 (S)")
                self.btn_sketchbook.setStyleSheet(
                    "QPushButton { background-color: #007acc; color: #ffffff; border: 1px solid #38bdf8; border-radius: 3px; padding: 4px 10px; font-size: 12px; font-weight: bold; }"
                )
            else:
                self.btn_sketchbook.setText("📋 板書 (S)")
                self.btn_sketchbook.setStyleSheet(self._tb_btn_style())

    def _toggle_timer(self, button: QPushButton):
        if self.elapsed_timer.isActive():
            self.elapsed_timer.stop()
            button.setText("▶ 再開")
        else:
            self.elapsed_timer.start()
            button.setText("⏸ 一時停止")

    def _reset_timer(self):
        self.presentation_time = QTime(0, 0, 0)
        self.lbl_timer.setText("⏱️ 00:00:00")

    def _update_timer(self):
        self.presentation_time = self.presentation_time.addSecs(1)
        self.lbl_timer.setText(f"⏱️ {self.presentation_time.toString('hh:mm:ss')}")

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "PDFスライドを開く",
            "",
            "PDFファイル (*.pdf)",
            options=QFileDialog.Option.DontUseNativeDialog
        )
        if path:
            self.load_pdf(path)

    def load_pdf(self, path: str):
        try:
            self.pdf_reader = PdfReader(path)
            self.controller.set_total_pages(len(self.pdf_reader))
            self.controller.set_page_mapping(self.pdf_reader.page_to_canonical)
            self.note_panel.set_pdf_reader(self.pdf_reader)
            self.thumbnail_bar.load_pdf(self.pdf_reader)
            has_toc = self.pdf_reader.has_toc
            self.btn_sidebar_drawer.setVisible(has_toc)
            self.btn_sidebar_drawer.setText(">")
            self.btn_toggle_bookmarks.setEnabled(has_toc)
            self.act_toggle_bookmarks.setEnabled(has_toc)
            self.bookmark_drawer.set_sidebar_offset(0)
            self.bookmark_drawer.load_pdf(self.pdf_reader)
            self.setWindowTitle(f"BeamerPlus 発表者ビュー - {os.path.basename(path)}")

            # 最初のページを表示
            self.controller.set_page(0)
            self._update_current_page_display(0)

            # iPad 側へも新しいPDFの情報を同期
            if hasattr(self, "web_server") and self.web_server:
                init_data = self._get_web_status()
                init_data["type"] = "state_sync"
                self.web_server.broadcast(init_data)
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"PDFファイルの読み込みに失敗しました:\n{str(e)}")

    def _on_page_changed(self, index: int):
        self._update_current_page_display(index)

    def _update_current_page_display(self, index: int):
        if not self.pdf_reader:
            return

        # スライド画像取得 (高解像度 DPI)
        current_pixmap = self.pdf_reader.get_page_image(index, auto_crop=True, dpi=self.render_dpi)
        total = len(self.pdf_reader)

        next_pixmap = None
        if index + 1 < total:
            next_pixmap = self.pdf_reader.get_page_image(index + 1, auto_crop=True, dpi=self.render_dpi)

        # プロジェクタ（Viewer）更新
        if self.viewer and current_pixmap:
            self.viewer.show_image(current_pixmap)

        # メインスライド表示領域（ツールバー＋キャンバス）更新
        self.slide_view.set_pixmap(current_pixmap)
        links = self.pdf_reader.get_page_links(index, auto_crop=True)
        self.slide_view.set_links(links)
        if self.viewer:
            self.viewer.set_links(links)

        group = self.pdf_reader.get_group_for_page(index)
        sub_step = None
        if group and len(group.page_indices) > 1:
            sub_step = (group.page_indices.index(index) + 1, len(group.page_indices))
        self.slide_view.update_page_info(
            index,
            total,
            frame_label=group.label if group else None,
            sub_step=sub_step
        )

        # ノートパネル更新
        self.note_panel.set_page(index, next_pixmap)

        # サムネイルバー更新
        self.thumbnail_bar.set_current_page(index)

        # しおりハイライト更新
        self.bookmark_drawer.highlight_current_page(index)

    def save_notes(self):
        if self.pdf_reader:
            self.note_panel.save_current_note_to_memory()
            try:
                self.pdf_reader.save_note_to_file()
                QMessageBox.information(self, "保存完了", "ノートをファイルに保存しました。")
            except Exception as e:
                QMessageBox.warning(self, "エラー", f"ノートの保存に失敗しました:\n{str(e)}")

    def render_current_page_image_with_sketch(self) -> QPixmap | None:
        """現在のページ（またはスケッチブック）に手書きストローク・テキストを合成した高品質 QPixmap を生成"""
        if self.controller.is_sketchbook:
            w, h = 1920, 1080
            if self.pdf_reader and len(self.pdf_reader) > 0:
                pm = self.pdf_reader.get_page_image(self.controller.current_page, auto_crop=True, dpi=self.render_dpi)
                if pm and not pm.isNull():
                    w, h = pm.width(), pm.height()
            pixmap = QPixmap(w, h)
            pixmap.fill(Qt.GlobalColor.white)
        else:
            pixmap = None
            if self.pdf_reader:
                pm = self.pdf_reader.get_page_image(self.controller.current_page, auto_crop=True, dpi=self.render_dpi)
                if pm and not pm.isNull():
                    pixmap = pm.copy()

            if not pixmap or pixmap.isNull():
                pixmap = QPixmap(1920, 1080)
                pixmap.fill(QColor("#1e1e1e"))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w = pixmap.width()
        h = pixmap.height()
        base_dim = min(w, h)

        # ストローク描画
        strokes = self.controller.get_strokes_for_page(self.controller.current_page)
        for stroke in strokes:
            if not stroke.points:
                continue
            color = QColor(stroke.color)
            color.setAlphaF(stroke.opacity)
            pen_w = max(2.0, base_dim * stroke.width_ratio)
            pen = QPen(color, pen_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)

            if len(stroke.points) == 1:
                painter.setBrush(QBrush(color))
                p0x = stroke.points[0][0] * w
                p0y = stroke.points[0][1] * h
                painter.drawEllipse(QPointF(p0x, p0y), pen_w / 2.0, pen_w / 2.0)
                continue

            path = create_smooth_path(stroke.points, 0.0, 0.0, w, h)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        # テキスト描画
        texts = self.controller.get_texts_for_page(self.controller.current_page)
        for item in texts:
            if not item.text:
                continue
            font_size = max(12, int(base_dim * (item.size * 0.0015)))
            font = QFont()
            font.setPixelSize(font_size)
            painter.setFont(font)
            painter.setPen(QPen(QColor(item.color)))
            metrics = painter.fontMetrics()
            tx = item.norm_x * w
            ty = item.norm_y * h
            painter.drawText(int(tx + 6), int(ty + metrics.ascent() + 3), item.text)

        painter.end()
        return pixmap

    def save_current_page_sketch(self, filepath: str | None = None):
        """現在のページのスケッチを画像ファイルとして保存"""
        pixmap = self.render_current_page_image_with_sketch()
        if not pixmap or pixmap.isNull():
            QMessageBox.warning(self, "保存不可", "保存するスライドまたはスケッチがありません。")
            return

        if not filepath:
            if self.controller.is_sketchbook:
                default_name = f"sketchbook_{QDateTime.currentDateTime().toString('yyyyMMdd_hhmmss')}.png"
            else:
                default_name = f"slide_page_{self.controller.current_page + 1}.png"

            filepath, _ = QFileDialog.getSaveFileName(
                self,
                "現在のページのスケッチを画像保存",
                default_name,
                "PNG画像 (*.png);;すべてのファイル (*.*)",
                options=QFileDialog.Option.DontUseNativeDialog
            )

        if filepath:
            if not filepath.lower().endswith(".png"):
                filepath += ".png"
            if pixmap.save(filepath, "PNG"):
                QMessageBox.information(self, "保存完了", f"スケッチを画像として保存しました:\n{os.path.basename(filepath)}")
            else:
                QMessageBox.warning(self, "保存失敗", "画像の保存に失敗しました。")

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress:
            # モーダルダイアログ表示中、ノートエディタ編集中、テキスト入力中、しおり検索中は通常のキー操作を最優先
            if QApplication.activeModalWidget() is not None:
                return super().eventFilter(watched, event)
            if hasattr(self, "note_panel") and self.note_panel.editor.hasFocus():
                return super().eventFilter(watched, event)
            if hasattr(self, "slide_view") and hasattr(self.slide_view, "canvas") and hasattr(self.slide_view.canvas, "text_input") and self.slide_view.canvas.text_input.hasFocus():
                return super().eventFilter(watched, event)
            if hasattr(self, "bookmark_drawer") and self.bookmark_drawer.search_edit.hasFocus():
                return super().eventFilter(watched, event)

            key = event.key()
            if key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.controller.next_page()
                return True
            elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp):
                self.controller.prev_page()
                return True
            elif key == Qt.Key.Key_B:
                self.controller.toggle_blank()
                return True
            elif key in (Qt.Key.Key_S, Qt.Key.Key_W):
                self.controller.toggle_sketchbook()
                return True
            elif key == Qt.Key.Key_H:
                self.controller.toggle_sketch_visibility()
                return True

        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        key = event.key()
        # エディタ編集中やテキスト入力中でない場合のみページ送りショートカットを有効化
        is_typing = (
            (hasattr(self, "note_panel") and self.note_panel.editor.hasFocus()) or
            (hasattr(self, "slide_view") and hasattr(self.slide_view, "canvas") and hasattr(self.slide_view.canvas, "text_input") and self.slide_view.canvas.text_input.hasFocus()) or
            (hasattr(self, "bookmark_drawer") and self.bookmark_drawer.search_edit.hasFocus())
        )
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not is_typing:
                self.controller.next_page()
                return
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp):
            if not is_typing:
                self.controller.prev_page()
                return
        elif (key == Qt.Key.Key_Left and event.modifiers() & Qt.KeyboardModifier.AltModifier) or key in (Qt.Key.Key_Back, Qt.Key.Key_Backspace):
            if not is_typing and self.controller.can_jump_back:
                self.controller.jump_back()
                return
        elif key == Qt.Key.Key_B:
            if not is_typing:
                self.controller.toggle_blank()
                return
        elif key in (Qt.Key.Key_S, Qt.Key.Key_W):
            if not is_typing:
                self.controller.toggle_sketchbook()
                return
        elif key == Qt.Key.Key_H:
            if not is_typing:
                self.controller.toggle_sketch_visibility()
                return

        super().keyPressEvent(event)

    # ==========================================
    # iPad / Web リモート連携ハンドラ
    # ==========================================
    def _connect_web_signals(self):
        self.web_bridge.stroke_started.connect(self._on_web_stroke_started)
        self.web_bridge.stroke_moved.connect(self._on_web_stroke_moved)
        self.web_bridge.stroke_ended.connect(self._on_web_stroke_ended)
        self.web_bridge.pointer_updated.connect(self._on_web_pointer_updated)
        self.web_bridge.erase_requested.connect(self._on_web_erase_requested)
        self.web_bridge.line_update_command.connect(self._on_web_line_update_command)
        self.web_bridge.page_command.connect(self._on_web_page_command)
        self.web_bridge.undo_requested.connect(self._on_web_undo_requested)
        self.web_bridge.clear_requested.connect(self._on_web_clear_requested)
        self.web_bridge.blank_command.connect(self._on_web_blank_command)
        self.web_bridge.sketchbook_command.connect(self._on_web_sketchbook_command)
        self.web_bridge.toggle_blank_command.connect(self._on_web_toggle_blank_command)
        self.web_bridge.toggle_sketchbook_command.connect(self._on_web_toggle_sketchbook_command)
        self.web_bridge.sketch_visibility_command.connect(self._on_web_sketch_visibility_command)
        self.web_bridge.toggle_sketch_visibility_command.connect(self._on_web_toggle_sketch_visibility_command)
        self.web_bridge.save_sketch_requested.connect(self._on_web_save_sketch_requested)
        self.web_bridge.client_count_changed.connect(self._on_web_client_count_changed)
        self.web_bridge.color_command.connect(self._on_web_color_command)

        # コントローラからの変化を Web クライアントへブロードキャスト（ツール選択は独立管理のため除外）
        self.controller.page_changed.connect(self._sync_web_page_changed)
        self.controller.stroke_started.connect(self._sync_web_stroke_started)
        self.controller.stroke_added_point.connect(self._sync_web_stroke_added_point)
        self.controller.stroke_finished.connect(self._sync_web_stroke_finished)
        self.controller.strokes_cleared.connect(self._sync_web_strokes_cleared)
        self.controller.stroke_undone.connect(self._sync_web_stroke_undone)
        self.controller.strokes_updated.connect(self._sync_web_strokes_updated)
        self.controller.pointer_updated.connect(self._sync_web_pointer_updated)
        self.controller.blank_state_changed.connect(self._sync_web_blank_changed)
        self.controller.sketchbook_state_changed.connect(self._sync_web_sketchbook_changed)
        self.controller.sketch_visibility_changed.connect(self._sync_web_sketch_visibility_changed)
        self.controller.color_changed.connect(self._sync_web_color_changed)

    def _get_slide_image_bytes(self, page_idx: int) -> bytes | None:
        if not self.pdf_reader:
            return None
        # iPad用に高速かつ Retina でも十分鮮明な 180 DPI でレンダリング
        pixmap = self.pdf_reader.get_page_image(page_idx, auto_crop=True, dpi=180)
        if not pixmap or pixmap.isNull():
            return None
        buf = QByteArray()
        qbuf = QBuffer(buf)
        qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(qbuf, "PNG")
        return bytes(buf.data())

    def _get_strokes_data_for_page(self, page_idx: int) -> list[dict]:
        strokes_data = []
        strokes = self.controller.get_strokes_for_page(page_idx)
        for s in strokes:
            strokes_data.append({
                "color": s.color,
                "width_ratio": s.width_ratio,
                "opacity": s.opacity,
                "points": s.points
            })
        return strokes_data

    def _get_web_status(self) -> dict:
        strokes_data = self._get_strokes_data_for_page(self.controller.current_page)
        sketchbook_data = []
        for s in self.controller.sketchbook_strokes:
            sketchbook_data.append({
                "color": s.color,
                "width_ratio": s.width_ratio,
                "opacity": s.opacity,
                "points": s.points
            })
        links_data = []
        if self.pdf_reader:
            links = self.pdf_reader.get_page_links(self.controller.current_page, auto_crop=True)
            links_data = [
                {
                    "rect": list(l.rect),
                    "kind": l.kind,
                    "page": l.page,
                    "uri": l.uri,
                    "file": l.file,
                    "tooltip": l.tooltip
                }
                for l in links
            ]

        return {
            "currentPage": self.controller.current_page,
            "totalPages": self.controller.total_pages,
            "currentTool": self.controller.current_tool,
            "color": self.controller.pen_color,
            "highlighterColor": self.controller.highlighter_color,
            "penOpacity": self.controller.pen_opacity,
            "highlighterOpacity": self.controller.highlighter_opacity,
            "toolSizes": self.controller.tool_sizes,
            "toolRanges": self.controller.tool_ranges,
            "eraserMode": self.controller.eraser_mode,
            "isLineMode": self.controller.is_line_mode,
            "isBlank": self.controller.is_blank,
            "isSketchbook": self.controller.is_sketchbook,
            "isSketchVisible": self.controller.is_sketch_visible,
            "strokes": strokes_data,
            "links": links_data,
            "sketchbookStrokes": sketchbook_data,
        }

    def _on_web_stroke_started(self, data: dict):
        self._is_handling_web_stroke = True
        tool = data.get("tool", "pen")
        color = data.get("color")
        if not color:
            color = self.controller.highlighter_color if tool == "highlighter" else self.controller.pen_color

        width_ratio = data.get("widthRatio")
        if width_ratio is None:
            size = data.get("size", 5)
            width_ratio = float(size) * 0.001

        opacity = self.controller.highlighter_opacity if tool == "highlighter" else self.controller.pen_opacity

        # PC側の current_tool や is_line_mode などの選択状態は一切変えずに直接ストロークを開始
        self.controller.begin_custom_stroke(
            color=color,
            width_ratio=float(width_ratio),
            opacity=opacity,
            norm_x=float(data["normX"]),
            norm_y=float(data["normY"]),
            pressure=1.0
        )

    def _on_web_stroke_moved(self, data: dict):
        self._is_handling_web_stroke = True
        self.controller.add_stroke_point(
            data["normX"],
            data["normY"],
            pressure=1.0  # 筆圧検知は無効化
        )

    def _on_web_stroke_ended(self):
        try:
            self.controller.end_stroke()
        finally:
            self._is_handling_web_stroke = False

    def _on_web_pointer_updated(self, norm_x: float, norm_y: float, visible: bool, is_down: bool = False):
        self.controller.update_pointer(norm_x, norm_y, visible, is_down=is_down)

    def _on_web_erase_requested(self, data: dict):
        norm_x = float(data.get("normX", 0.0))
        norm_y = float(data.get("normY", 0.0))
        mode = data.get("mode")
        size = data.get("size")
        rect = self.slide_view.canvas._get_slide_rect()
        aspect_ratio = rect.width() / rect.height() if rect.height() > 0 else 1.0
        eraser_size = int(size) if size is not None else self.controller.get_tool_size("eraser")
        threshold = max(0.008, eraser_size * 0.001)
        self.controller.erase_at_point(
            norm_x, norm_y,
            threshold=threshold,
            aspect_ratio=aspect_ratio,
            override_mode=mode
        )

    def _on_web_eraser_mode_command(self, mode: str):
        self.controller.set_eraser_mode(mode)

    def _on_web_line_mode_command(self, is_line: bool):
        self.controller.set_line_mode(is_line)

    def _on_web_line_update_command(self, norm_x: float, norm_y: float):
        self.controller.update_line_endpoint(norm_x, norm_y, pressure=1.0)

    def _on_web_color_command(self, tool: str, color: str):
        self.controller.set_tool_color(tool, color)

    def _on_web_page_command(self, cmd: str, page_idx: int):
        if cmd == "next":
            self.controller.next_page()
        elif cmd == "prev":
            self.controller.prev_page()
        elif cmd == "set":
            self.controller.set_page(page_idx)

    def _on_web_undo_requested(self):
        self.controller.undo_current_page_stroke()

    def _on_web_clear_requested(self):
        self.controller.clear_current_page_strokes()

    def _on_web_blank_command(self, enabled: bool):
        self.controller.set_blank(enabled)

    def _on_web_toggle_blank_command(self):
        self.controller.toggle_blank()

    def _on_web_sketchbook_command(self, enabled: bool):
        self.controller.set_sketchbook(enabled)

    def _on_web_toggle_sketchbook_command(self):
        self.controller.toggle_sketchbook()

    def _on_web_sketch_visibility_command(self, visible: bool):
        self.controller.set_sketch_visibility(visible)

    def _on_web_toggle_sketch_visibility_command(self):
        self.controller.toggle_sketch_visibility()

    def _on_web_save_sketch_requested(self):
        timestamp = QDateTime.currentDateTime().toString("yyyyMMdd_hhmmss")
        if self.controller.is_sketchbook:
            fname = f"sketchbook_{timestamp}.png"
        else:
            fname = f"slide_page_{self.controller.current_page + 1}_{timestamp}.png"

        save_dir = os.path.dirname(self.pdf_reader.path) if self.pdf_reader else os.getcwd()
        filepath = os.path.join(save_dir, fname)
        pixmap = self.render_current_page_image_with_sketch()
        if pixmap and not pixmap.isNull():
            pixmap.save(filepath, "PNG")
            if hasattr(self, "web_server") and self.web_server:
                self.web_server.broadcast({
                    "type": "sketch_saved",
                    "filename": fname
                })

    def _on_web_client_count_changed(self, count: int):
        if hasattr(self, "btn_ipad_connect"):
            if count > 0:
                self.btn_ipad_connect.setText(f"📱 iPad ({count})")
                self.btn_ipad_connect.setStyleSheet(
                    "QPushButton { background-color: #065f46; color: #34d399; border: 1px solid #059669; border-radius: 3px; padding: 4px 10px; font-size: 12px; font-weight: bold; }"
                )
            else:
                self.btn_ipad_connect.setText("📱 iPad 連携")
                self.btn_ipad_connect.setStyleSheet(self._tb_btn_style())

    def _sync_web_page_changed(self, index: int):
        if hasattr(self, "web_server") and self.web_server:
            links_data = []
            if self.pdf_reader:
                links = self.pdf_reader.get_page_links(index, auto_crop=True)
                links_data = [
                    {
                        "rect": list(l.rect),
                        "kind": l.kind,
                        "page": l.page,
                        "uri": l.uri,
                        "file": l.file,
                        "tooltip": l.tooltip
                    }
                    for l in links
                ]
            self.web_server.broadcast({
                "type": "page_changed",
                "page": index,
                "totalPages": self.controller.total_pages,
                "strokes": self._get_strokes_data_for_page(index),
                "links": links_data,
            })

    def _sync_web_stroke_started(self, page_index: int, stroke):
        if self._is_handling_web_stroke:
            return
        if hasattr(self, "web_server") and self.web_server:
            p0 = stroke.points[0] if stroke.points else (0.0, 0.0, 1.0)
            self.web_server.broadcast({
                "type": "remote_stroke_start",
                "page": page_index,
                "color": stroke.color,
                "widthRatio": stroke.width_ratio,
                "opacity": stroke.opacity,
                "p0": [p0[0], p0[1]]
            })

    def _sync_web_stroke_added_point(self, page_index: int, norm_x: float, norm_y: float, pressure: float):
        if self._is_handling_web_stroke:
            return
        if hasattr(self, "web_server") and self.web_server:
            self.web_server.broadcast({
                "type": "remote_stroke_move",
                "page": page_index,
                "normX": norm_x,
                "normY": norm_y
            })

    def _sync_web_stroke_finished(self, page_index: int):
        if self._is_handling_web_stroke:
            return
        if hasattr(self, "web_server") and self.web_server:
            self.web_server.broadcast({
                "type": "remote_stroke_end",
                "page": page_index
            })

    def _sync_web_strokes_cleared(self, index: int):
        if hasattr(self, "web_server") and self.web_server:
            self.web_server.broadcast({
                "type": "strokes_cleared",
                "page": index
            })

    def _sync_web_stroke_undone(self, index: int):
        if hasattr(self, "web_server") and self.web_server:
            status = self._get_web_status()
            target_strokes = status["sketchbookStrokes"] if self.controller.is_sketchbook else status["strokes"]
            self.web_server.broadcast({
                "type": "stroke_undone",
                "page": index,
                "strokes": target_strokes
            })

    def _sync_web_strokes_updated(self, index: int):
        if hasattr(self, "web_server") and self.web_server:
            status = self._get_web_status()
            target_strokes = status["sketchbookStrokes"] if self.controller.is_sketchbook else status["strokes"]
            self.web_server.broadcast({
                "type": "strokes_updated",
                "page": index,
                "strokes": target_strokes
            })

    def _sync_web_blank_changed(self, enabled: bool):
        if hasattr(self, "web_server") and self.web_server:
            self.web_server.broadcast({
                "type": "blank_changed",
                "enabled": enabled
            })

    def _sync_web_sketchbook_changed(self, enabled: bool):
        if hasattr(self, "web_server") and self.web_server:
            sketchbook_data = [
                {
                    "color": s.color,
                    "width_ratio": s.width_ratio,
                    "opacity": s.opacity,
                    "points": s.points
                }
                for s in self.controller.sketchbook_strokes
            ]
            self.web_server.broadcast({
                "type": "sketchbook_changed",
                "enabled": enabled,
                "strokes": sketchbook_data
            })

    def _sync_web_sketch_visibility_changed(self, visible: bool):
        if hasattr(self, "web_server") and self.web_server:
            self.web_server.broadcast({
                "type": "sketch_visibility_changed",
                "visible": visible
            })

    def _sync_web_pointer_updated(self, norm_x: float, norm_y: float, visible: bool, is_down: bool = False):
        if hasattr(self, "web_server") and self.web_server:
            self.web_server.broadcast({
                "type": "pointer_updated",
                "normX": norm_x,
                "normY": norm_y,
                "visible": visible,
                "isDown": is_down
            })

    def _sync_web_color_changed(self, tool: str, color: str):
        if hasattr(self, "web_server") and self.web_server:
            self.web_server.broadcast({
                "type": "color_changed",
                "tool": tool,
                "color": color
            })

    def show_ipad_dialog(self):
        dialog = IpadDialog(
            self.web_bridge,
            http_port=self.web_server.http_port,
            passcode=self.web_server.passcode,
            server=self.web_server,
            parent=self
        )
        dialog.exec()

    def closeEvent(self, event):
        if hasattr(self, "web_server") and self.web_server:
            self.web_server.stop()
        if self.pdf_reader:
            self.note_panel.save_current_note_to_memory()
            try:
                self.pdf_reader.save_note_to_file()
            except Exception:
                pass
        if self.viewer:
            self.viewer.close()
        super().closeEvent(event)
