from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QColorDialog, QToolButton, QLabel, QFrame, QSlider, QScrollArea
)
from PySide6.QtGui import QColor, QPixmap

from beamerplus.DrawingModel import PresentationController
from beamerplus.SlideCanvas import SlideCanvas


class ToolbarScrollArea(QScrollArea):
    """メインスライド上部のツールバーを柔軟に収めるための横スクロールコンテナ。
    ウィンドウ幅やスプリッターの調整で領域が狭くなった場合でもツールバーがはみ出さず、
    ホイールやスクロールバーで快適に全機能へアクセス可能。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFixedHeight(46)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            "QScrollArea { background-color: #252526; border-bottom: 1px solid #333333; }"
            "QScrollBar:horizontal { height: 4px; background: #252526; margin: 0; }"
            "QScrollBar::handle:horizontal { background: #444448; border-radius: 2px; min-width: 20px; }"
            "QScrollBar::handle:horizontal:hover { background: #007acc; }"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; height: 0px; }"
            "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }"
        )

    def wheelEvent(self, event):
        # 縦ホイール回転またはトラックパッドの水平スクロールを処理
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta != 0:
            sb = self.horizontalScrollBar()
            sb.setValue(sb.value() - int(delta * 0.8))
            event.accept()
        else:
            super().wheelEvent(event)


class SimpleView(QWidget):
    save_sketch_requested = Signal()

    def __init__(self, controller: PresentationController, parent: QWidget | None = None):
        super().__init__(parent)
        self.controller = controller

        # 最小サイズを柔軟な値に設定（スプリッターやウィンドウ縮小でスライド画面を小さくできるようにする）
        self.setMinimumSize(QSize(180, 150))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ツールバー作成（横スクロールコンテナでラップして最小サイズ制約を解消）
        self.toolbar_content = self._create_toolbar()
        self.toolbar_scroll = ToolbarScrollArea(self)
        self.toolbar_scroll.setWidget(self.toolbar_content)
        layout.addWidget(self.toolbar_scroll)

        # 互換性のため self.toolbar も保持
        self.toolbar = self.toolbar_content

        # スライドキャンバス
        self.canvas = SlideCanvas(self.controller)
        layout.addWidget(self.canvas, 1)

        # コントローラシグナル接続
        self.controller.tool_changed.connect(self._on_tool_changed)
        self.controller.tool_size_changed.connect(self._on_tool_size_changed)
        self.controller.line_mode_changed.connect(self._on_line_mode_changed)
        self.controller.color_changed.connect(self._on_color_changed)
        self.controller.eraser_mode_changed.connect(self._on_eraser_mode_changed)
        self.controller.sketch_visibility_changed.connect(self._on_sketch_visibility_changed)
        self.controller.blank_state_changed.connect(self._on_blank_state_changed)
        self.controller.jump_history_changed.connect(self.btn_jump_back.setEnabled)

        # 初期表示の更新
        self._on_tool_changed(self.controller.current_tool)
        self._on_eraser_mode_changed(self.controller.eraser_mode)
        self._on_sketch_visibility_changed(self.controller.is_sketch_visible)
        self.btn_jump_back.setEnabled(self.controller.can_jump_back)

    def _create_toolbar(self) -> QWidget:
        bar = QFrame()
        bar.setFixedHeight(46)
        bar.setStyleSheet(
            "QFrame { background-color: #252526; border: none; }"
            "QPushButton, QToolButton { background-color: #333333; color: #ffffff; border: 1px solid #444444; border-radius: 4px; padding: 4px 7px; font-size: 12px; }"
            "QPushButton:hover, QToolButton:hover { background-color: #444444; }"
            "QPushButton:checked, QToolButton:checked { background-color: #007acc; border-color: #0098ff; }"
            "QLabel { color: #cccccc; font-size: 12px; }"
            "QSlider::groove:horizontal { height: 4px; background: #444448; border-radius: 2px; }"
            "QSlider::sub-page:horizontal { background: #007acc; border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #ffffff; width: 12px; margin-top: -4px; margin-bottom: -4px; border-radius: 6px; }"
            "QSlider::handle:horizontal:hover { background: #4fc3f7; }"
        )
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(8, 2, 8, 2)
        bar_layout.setSpacing(5)

        # 基本ツールボタン
        self.btn_pointer = QPushButton("🔴")
        self.btn_pointer.setCheckable(True)
        self.btn_pointer.setToolTip("レーザーポインタ (もう一度クリックで通常モード)")
        self.btn_pointer.clicked.connect(lambda: self._on_tool_btn_clicked("pointer"))
        bar_layout.addWidget(self.btn_pointer)

        self.btn_pen = QPushButton("✏️")
        self.btn_pen.setCheckable(True)
        self.btn_pen.setToolTip("手書きペン (もう一度クリックで通常モード)")
        self.btn_pen.clicked.connect(lambda: self._on_tool_btn_clicked("pen"))
        bar_layout.addWidget(self.btn_pen)

        self.btn_highlighter = QPushButton("🖍️")
        self.btn_highlighter.setCheckable(True)
        self.btn_highlighter.setToolTip("蛍光ペン (もう一度クリックで通常モード)")
        self.btn_highlighter.clicked.connect(lambda: self._on_tool_btn_clicked("highlighter"))
        bar_layout.addWidget(self.btn_highlighter)

        self.btn_text = QPushButton("🔤")
        self.btn_text.setCheckable(True)
        self.btn_text.setToolTip("テキスト書き込み (もう一度クリックで通常モード)")
        self.btn_text.clicked.connect(lambda: self._on_tool_btn_clicked("text"))
        bar_layout.addWidget(self.btn_text)

        self.btn_eraser = QPushButton("🧹")
        self.btn_eraser.setCheckable(True)
        self.btn_eraser.setToolTip("消しゴム (もう一度クリックで通常モード)")
        self.btn_eraser.clicked.connect(lambda: self._on_tool_btn_clicked("eraser"))
        bar_layout.addWidget(self.btn_eraser)

        # 消しゴムモード切替ボタン（通常消しゴム / オブジェクト消しゴム）
        self.btn_eraser_mode = QPushButton("通常")
        self.btn_eraser_mode.setToolTip("消しゴムモード: 通常（なぞった部分のみ消去。クリックでオブジェクト消しゴムに切替）")
        self.btn_eraser_mode.setStyleSheet(
            "QPushButton { background-color: #38383c; color: #4fc3f7; border: 1px solid #555558; border-radius: 3px; padding: 2px 4px; font-size: 11px; }"
            "QPushButton:hover { background-color: #48484e; color: #ffffff; }"
            "QPushButton:disabled { background-color: #2a2a2d; color: #555558; border-color: #38383c; }"
        )
        self.btn_eraser_mode.setFixedWidth(82)
        self.btn_eraser_mode.clicked.connect(self.controller.toggle_eraser_mode)
        self.btn_eraser_mode.setEnabled(False)  # 常に表示し、消しゴム選択時のみ有効化
        bar_layout.addWidget(self.btn_eraser_mode)

        self.tool_buttons = {
            "pointer": self.btn_pointer,
            "pen": self.btn_pen,
            "highlighter": self.btn_highlighter,
            "text": self.btn_text,
            "eraser": self.btn_eraser,
        }

        # 直線修飾ボタン（ペンまたは蛍光ペンで直線を引く）
        self.btn_line = QPushButton("📏")
        self.btn_line.setCheckable(True)
        self.btn_line.setToolTip("選択中のペン/蛍光ペンで直線を引く（もう一度クリックでフリーハンドに戻る）")
        self.btn_line.clicked.connect(self._on_line_btn_clicked)
        bar_layout.addWidget(self.btn_line)

        # ペン / 蛍光ペンの色選択ボタン
        self.btn_color = QPushButton("■")
        self.btn_color.setToolTip("選択中ツール（ペン/蛍光ペン）の色を変更")
        self.btn_color.setStyleSheet(f"color: {self.controller.pen_color}; font-size: 16px; padding: 2px 6px;")
        self.btn_color.clicked.connect(self._choose_color)
        bar_layout.addWidget(self.btn_color)

        # 区切り線
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setStyleSheet("color: #444444;")
        bar_layout.addWidget(sep1)

        # --- 共通サイズスライダー ---
        self.lbl_size_title = QLabel("太さ:")
        self.lbl_size_title.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        self.lbl_size_title.setFixedWidth(38)
        self.lbl_size_title.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar_layout.addWidget(self.lbl_size_title)

        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(1, 160)
        self.size_slider.setValue(18)
        self.size_slider.setFixedWidth(70)
        self.size_slider.setToolTip("選択中ツールの太さ・半径を変更")
        self.size_slider.valueChanged.connect(self._on_slider_value_changed)
        bar_layout.addWidget(self.size_slider)

        self.lbl_size_val = QLabel("18")
        self.lbl_size_val.setFixedWidth(24)
        self.lbl_size_val.setStyleSheet("color: #4fc3f7; font-size: 11px;")
        bar_layout.addWidget(self.lbl_size_val)

        # 区切り線
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet("color: #444444;")
        bar_layout.addWidget(sep2)

        # 描画消去
        self.btn_clear = QPushButton("🗑️")
        self.btn_clear.setToolTip("描画消去")
        self.btn_clear.clicked.connect(self.controller.clear_current_page_strokes)
        bar_layout.addWidget(self.btn_clear)

        # スケッチ表示/非表示
        self.btn_visibility = QPushButton("👁️")
        self.btn_visibility.setToolTip("スケッチの表示/非表示切替 [H]")
        self.btn_visibility.clicked.connect(self.controller.toggle_sketch_visibility)
        bar_layout.addWidget(self.btn_visibility)

        # スケッチ保存
        self.btn_save = QPushButton("💾")
        self.btn_save.setToolTip("現在のページのスケッチを画像保存")
        self.btn_save.clicked.connect(self.save_sketch_requested.emit)
        bar_layout.addWidget(self.btn_save)

        bar_layout.addStretch()

        # ジャンプ復帰ボタン
        self.btn_jump_back = QPushButton("↩")
        self.btn_jump_back.setToolTip("直前のスライドに戻る (Alt+← / Backspace)")
        self.btn_jump_back.setFixedWidth(32)
        self.btn_jump_back.setEnabled(False)
        self.btn_jump_back.clicked.connect(self.controller.jump_back)
        bar_layout.addWidget(self.btn_jump_back)

        # ページ送りボタン
        self.btn_prev = QPushButton("◀ 前へ")
        self.btn_prev.clicked.connect(self.controller.prev_page)
        bar_layout.addWidget(self.btn_prev)

        self.page_label = QLabel("0 / 0")
        self.page_label.setFixedWidth(56)
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar_layout.addWidget(self.page_label)

        self.btn_next = QPushButton("次へ ▶")
        self.btn_next.clicked.connect(self.controller.next_page)
        bar_layout.addWidget(self.btn_next)

        # 全ボタン・スライダーのフォーカスを無効化（矢印キー操作がボタンに吸われないようにする）
        for widget in (
            self.btn_pointer, self.btn_pen, self.btn_highlighter, self.btn_text, self.btn_eraser,
            self.btn_eraser_mode, self.btn_line, self.btn_color, self.size_slider,
            self.btn_clear, self.btn_visibility, self.btn_save, self.btn_jump_back, self.btn_prev, self.btn_next
        ):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        return bar

    def _on_tool_btn_clicked(self, tool_name: str):
        if self.controller.current_tool == tool_name:
            # 有効になっている機能のボタンをクリックしたら通常モード（hand）に戻る
            self.controller.set_line_mode(False)
            self.controller.set_tool("hand")
        else:
            self.controller.set_tool(tool_name)

    def _on_line_btn_clicked(self):
        self.controller.toggle_line_mode()

    def _on_line_mode_changed(self, is_line: bool):
        self.btn_line.setChecked(is_line)

    def _on_sketch_visibility_changed(self, visible: bool):
        if visible:
            self.btn_visibility.setText("👁️")
            self.btn_visibility.setToolTip("スケッチの表示/非表示切替 [H]")
        else:
            self.btn_visibility.setText("👁️‍🗨️")
            self.btn_visibility.setToolTip("スケッチ非表示中（クリックで再表示） [H]")

    def _on_eraser_mode_changed(self, mode: str):
        if mode == "partial":
            self.btn_eraser_mode.setText("通常")
            self.btn_eraser_mode.setToolTip("消しゴムモード: 通常（なぞった部分のみ消去。クリックでオブジェクト消しゴムに切替）")
        else:
            self.btn_eraser_mode.setText("オブジェクト")
            self.btn_eraser_mode.setToolTip("消しゴムモード: オブジェクト（線全体を消去。クリックで通常消しゴムに切替）")

    def _choose_color(self):
        # 現在アクティブなツールの色を変更
        tool = self.controller.current_tool
        if tool == "highlighter":
            target_tool = "highlighter"
            title = "蛍光ペンの色選択"
        elif tool == "text":
            target_tool = "text"
            title = "テキストの色選択"
        else:
            target_tool = "pen"
            title = "ペンの色選択"

        current_color = self.controller.get_tool_color(target_tool)
        color = QColorDialog.getColor(
            QColor(current_color),
            self,
            title,
            options=QColorDialog.ColorDialogOption.DontUseNativeDialog
        )
        if color.isValid():
            hex_color = color.name()
            self.controller.set_tool_color(target_tool, hex_color)
            self._update_color_button()

    def _update_color_button(self):
        tool = self.controller.current_tool
        if tool == "highlighter":
            col = self.controller.highlighter_color
            self.btn_color.setEnabled(True)
            self.btn_color.setToolTip("蛍光ペンの色を変更")
        elif tool == "text":
            col = self.controller.text_color
            self.btn_color.setEnabled(True)
            self.btn_color.setToolTip("テキストの色を変更")
        elif tool == "eraser":
            col = "#555555"
            self.btn_color.setEnabled(False)
            self.btn_color.setToolTip("消しゴム選択中は色変更不可")
        else:
            # pen, pointer, hand
            col = self.controller.pen_color
            self.btn_color.setEnabled(True)
            self.btn_color.setToolTip("ペンの色を変更")

        self.btn_color.setStyleSheet(f"color: {col}; font-size: 16px; padding: 2px 8px;")

    def _on_color_changed(self, tool_name: str, hex_color: str):
        self._update_color_button()

    def _on_slider_value_changed(self, val: int):
        self.controller.set_current_tool_size(val)
        self.lbl_size_val.setText(str(val))

    def _on_tool_changed(self, tool_name: str):
        # 1. 各ボタンのチェック状態を同期
        for name, btn in self.tool_buttons.items():
            btn.setChecked(name == tool_name)
        self.btn_line.setChecked(self.controller.is_line_mode)

        # 2. 消しゴムモード切替ボタンの有効/無効（常に表示、消しゴム選択時のみ有効）
        self.btn_eraser_mode.setEnabled(tool_name == "eraser")

        # 3. 色選択ボタンの更新
        self._update_color_button()

        # 4. 直線ボタンの有効化/無効化（消しゴムおよびテキスト時は無効）
        self.btn_line.setEnabled(tool_name not in ("eraser", "text"))

        # 5. スライダーを該当ツールの保持サイズ・範囲に更新
        if tool_name == "hand":
            self.size_slider.setEnabled(False)
            self.lbl_size_title.setText("サイズ:")
            self.lbl_size_val.setText("-")
        else:
            self.size_slider.setEnabled(True)
            if tool_name in ("pointer", "eraser"):
                self.lbl_size_title.setText("半径:")
            elif tool_name == "text":
                self.lbl_size_title.setText("サイズ:")
            else:
                self.lbl_size_title.setText("太さ:")

            min_v, max_v = self.controller.get_tool_range(tool_name)
            size = self.controller.get_tool_size(tool_name)
            self.size_slider.blockSignals(True)
            self.size_slider.setRange(min_v, max_v)
            self.size_slider.setValue(size)
            self.size_slider.blockSignals(False)
            self.lbl_size_val.setText(str(size))

    def _on_tool_size_changed(self, tool_name: str, size: int):
        if tool_name == self.controller.current_tool:
            min_v, max_v = self.controller.get_tool_range(tool_name)
            self.size_slider.blockSignals(True)
            self.size_slider.setRange(min_v, max_v)
            self.size_slider.setValue(size)
            self.size_slider.blockSignals(False)
            self.lbl_size_val.setText(str(size))

    def set_pixmap(self, pixmap: QPixmap | None):
        self.canvas.set_pixmap(pixmap)

    def set_links(self, links: list):
        self.canvas.set_links(links)

    def update_page_info(
        self,
        current: int,
        total: int,
        frame_label: str | None = None,
        sub_step: tuple[int, int] | None = None
    ):
        self._last_current = current
        self._last_total = total
        if sub_step is not None:
            sub_idx, sub_total = sub_step
            lbl = frame_label if frame_label else str(current + 1)
            self.page_label.setText(f"{lbl} ({sub_idx}/{sub_total}) [{current + 1}/{total}]")
        else:
            self.page_label.setText(f"{current + 1} / {total}")
        self._update_nav_buttons(current, total)

    def _update_nav_buttons(self, current: int | None = None, total: int | None = None):
        if current is None:
            current = getattr(self, "_last_current", self.controller.current_page)
        if total is None:
            total = getattr(self, "_last_total", self.controller.total_pages)

        if total <= 0:
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return

        can_prev = (current > 0) or self.controller.is_blank
        can_next = (current < total - 1) or (not self.controller.is_blank)
        self.btn_prev.setEnabled(can_prev)
        self.btn_next.setEnabled(can_next)

    def _on_blank_state_changed(self, is_blank: bool):
        self._update_nav_buttons()
