import os
import math
import time
from PySide6.QtCore import Qt, QPointF, QRectF, QEvent, QSize, QTimer, QUrl
from PySide6.QtWidgets import QWidget, QLineEdit
from PySide6.QtGui import (
    QPixmap, QPainter, QPaintEvent, QMouseEvent, QTabletEvent,
    QPointingDevice, QColor, QPen, QPainterPath, QRadialGradient, QBrush, QFont,
    QDesktopServices
)

from beamerplus.DrawingModel import PresentationController, Stroke, TextItem, create_smooth_path
from beamerplus.PdfReader import PdfLink


class SlideCanvas(QWidget):
    def __init__(self, controller: PresentationController, parent: QWidget | None = None):
        super().__init__(parent)
        self.controller = controller
        self.pixmap: QPixmap | None = None

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("background-color: #121212;")

        # コントローラシグナル接続
        self.controller.pointer_updated.connect(self._on_pointer_updated)
        self.controller.stroke_started.connect(self._on_stroke_started)
        self.controller.stroke_added_point.connect(self._on_stroke_added_point)
        self.controller.stroke_line_updated.connect(self._on_stroke_line_updated)
        self.controller.stroke_finished.connect(self._on_stroke_finished)
        self.controller.strokes_cleared.connect(self._on_strokes_cleared)
        self.controller.stroke_undone.connect(self._on_stroke_undone)
        self.controller.strokes_updated.connect(self._on_strokes_updated)
        self.controller.text_added.connect(self._on_text_added)
        self.controller.texts_updated.connect(self._on_texts_updated)
        self.controller.tool_changed.connect(self._on_tool_changed)
        self.controller.tool_size_changed.connect(self._on_tool_size_changed)
        self.controller.line_mode_changed.connect(self._on_line_mode_changed)
        self.controller.eraser_mode_changed.connect(self._on_eraser_mode_changed)
        self.controller.color_changed.connect(self._on_color_changed)
        self.controller.blank_state_changed.connect(self._on_blank_state_changed)
        self.controller.sketchbook_state_changed.connect(self._on_sketchbook_state_changed)
        self.controller.sketch_visibility_changed.connect(self._on_sketch_visibility_changed)

        self.pointer_pos: tuple[float, float] | None = None
        self.is_pointer_visible: bool = False
        self.is_pointer_down: bool = False
        self.current_trail: list[tuple[float, float, float]] = []  # 現在ドラッグ中の軌跡 (norm_x, norm_y, timestamp)
        self.fading_trails: list[tuple[list[tuple[float, float, float]], float]] = []  # 離されたフェードアウト中ストローク [(points, release_time), ...]
        self.trail_timer = QTimer(self)
        self.trail_timer.setInterval(20)
        self.trail_timer.timeout.connect(self._on_trail_timer_tick)

        # Wacom / タブレット用状態
        self.is_tablet_drawing: bool = False
        self.hover_pos: tuple[float, float] | None = None

        # インラインテキスト入力用 QLineEdit
        self.text_input = QLineEdit(self)
        self.text_input.hide()
        self.text_input.returnPressed.connect(self._commit_text)
        self.text_input.installEventFilter(self)
        self.text_input_norm_pos: tuple[float, float] | None = None

        # 高品質縮小キャッシュ（面積平均 SmoothTransformation & Retina/HiDPI 対応）
        self._cached_scaled_pixmap: QPixmap | None = None

        # ハイパーリンク情報
        self.links: list[PdfLink] = []
        self.hovered_link: PdfLink | None = None

    def set_links(self, links: list[PdfLink]):
        self.links = list(links) if links else []
        self.hovered_link = None
        self.update()

    def _find_link_at(self, norm: tuple[float, float]) -> PdfLink | None:
        if not self.links or self.controller.is_sketchbook:
            return None
        nx, ny = norm
        for l in self.links:
            x0, y0, x1, y1 = l.rect
            if x0 <= nx <= x1 and y0 <= ny <= y1:
                return l
        return None

    def _execute_link(self, link: PdfLink):
        if link.page is not None:
            self.controller.jump_to_page(link.page)
        elif link.uri:
            try:
                QDesktopServices.openUrl(QUrl(link.uri))
            except Exception:
                pass
        elif link.file:
            try:
                if os.path.exists(link.file):
                    QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(link.file)))
                else:
                    QDesktopServices.openUrl(QUrl(link.file))
            except Exception:
                pass

    def set_pixmap(self, pixmap: QPixmap | None):
        if self.text_input.isVisible():
            self._commit_text()
        self.pixmap = pixmap
        self._update_scaled_pixmap()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_scaled_pixmap()

    def _update_scaled_pixmap(self):
        """現在の表示サイズと画面の物理ピクセル比（DPR）に合わせて高品質に縮小したキャッシュを生成"""
        if not self.pixmap or self.pixmap.isNull():
            self._cached_scaled_pixmap = None
            return

        rect = self._get_slide_rect()
        if rect.isEmpty():
            self._cached_scaled_pixmap = None
            return

        # Retina / HiDPI (devicePixelRatio) を考慮した物理ピクセル解像度
        dpr = self.devicePixelRatioF()
        target_w = max(1, int(round(rect.width() * dpr)))
        target_h = max(1, int(round(rect.height() * dpr)))

        # 面積平均アルゴリズム（SmoothTransformation）で高品質に縮小
        scaled = self.pixmap.scaled(
            QSize(target_w, target_h),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        scaled.setDevicePixelRatio(dpr)
        self._cached_scaled_pixmap = scaled

    def _get_slide_rect(self) -> QRectF:
        if self.pixmap and not self.pixmap.isNull():
            pw = self.pixmap.width()
            ph = self.pixmap.height()
        elif self.controller.is_sketchbook:
            # スライド未ロード時でもスケッチブックなら 16:9 領域を使用
            pw = 1920
            ph = 1080
        else:
            return QRectF()

        if pw == 0 or ph == 0:
            return QRectF()

        w = self.width()
        h = self.height()
        scale = min(w / pw, h / ph)
        target_w = pw * scale
        target_h = ph * scale
        x = (w - target_w) / 2.0
        y = (h - target_h) / 2.0
        return QRectF(x, y, target_w, target_h)

    def _pos_to_normalized(self, pos: QPointF) -> tuple[float, float] | None:
        rect = self._get_slide_rect()
        if rect.isEmpty():
            return None
        
        norm_x = (pos.x() - rect.left()) / rect.width()
        norm_y = (pos.y() - rect.top()) / rect.height()

        if 0.0 <= norm_x <= 1.0 and 0.0 <= norm_y <= 1.0:
            return (norm_x, norm_y)
        return None

    def _snap_line_endpoint(self, end_norm: tuple[float, float]) -> tuple[float, float]:
        """水平・垂直方向（±5度以内）の自動スナップ補正"""
        if not self.controller.current_stroke or not self.controller.current_stroke.points:
            return end_norm

        p0 = self.controller.current_stroke.points[0]
        x0, y0 = p0[0], p0[1]
        x1, y1 = end_norm

        rect = self._get_slide_rect()
        if rect.isEmpty():
            return end_norm

        # 画面ピクセル基準での差分
        dx_px = (x1 - x0) * rect.width()
        dy_px = (y1 - y0) * rect.height()

        dist_px = math.hypot(dx_px, dy_px)
        if dist_px < 8.0:
            return end_norm

        # 角度判定 (0 ~ 90度)
        angle_deg = math.degrees(math.atan2(abs(dy_px), abs(dx_px)))
        SNAP_DEG = 5.0

        if angle_deg <= SNAP_DEG:
            # 水平線にスナップ（Y座標を始点と同一にする）
            return (x1, y0)
        elif angle_deg >= (90.0 - SNAP_DEG):
            # 垂直線にスナップ（X座標を始点と同一にする）
            return (x0, y1)

        return end_norm

    def _on_pointer_updated(self, nx: float, ny: float, visible: bool, is_down: bool = False):
        self.is_pointer_visible = visible
        prev_down = self.is_pointer_down
        self.is_pointer_down = is_down
        now = time.time()

        if visible:
            self.pointer_pos = (nx, ny)
            if is_down:
                if not prev_down:
                    # 新しいストロークの開始（前のストロークとは完全に分離）
                    if len(self.current_trail) >= 2:
                        self.fading_trails.append((list(self.current_trail), now))
                    self.current_trail = [(nx, ny, now)]
                else:
                    self.current_trail.append((nx, ny, now))
                if not self.trail_timer.isActive():
                    self.trail_timer.start()
            elif prev_down and not is_down:
                # 指・マウスを離した瞬間: 現在のストロークをフェードアウト一覧へ移行
                if len(self.current_trail) >= 2:
                    self.fading_trails.append((list(self.current_trail), now))
                self.current_trail = []
        else:
            self.pointer_pos = None
            if prev_down:
                if len(self.current_trail) >= 2:
                    self.fading_trails.append((list(self.current_trail), now))
                self.current_trail = []

        self.update()

    def _on_trail_timer_tick(self):
        now = time.time()
        lifetime = 0.45      # ドラッグ中の軌跡の寿命（0.45秒）
        fade_duration = 0.25 # 離した後のフェードアウト時間（0.25秒）

        # ドラッグ中ストロークの古い点を削除
        if self.current_trail:
            self.current_trail = [p for p in self.current_trail if now - p[2] < lifetime]

        # 離されてから 0.25秒 経過したフェードアウト中ストロークを削除
        self.fading_trails = [
            (pts, rel_t) for pts, rel_t in self.fading_trails
            if now - rel_t < fade_duration
        ]

        if not self.current_trail and not self.fading_trails:
            self.trail_timer.stop()
        self.update()

    def _on_stroke_started(self, page_index: int, stroke: Stroke):
        self.update()

    def _on_stroke_added_point(self, page_index: int, nx: float, ny: float, pressure: float):
        self.update()

    def _on_stroke_line_updated(self, page_index: int, nx: float, ny: float):
        self.update()

    def _on_stroke_finished(self, page_index: int):
        self.update()

    def _on_strokes_cleared(self, page_index: int):
        self.update()

    def _on_stroke_undone(self, page_index: int):
        self.update()

    def _on_strokes_updated(self, page_index: int):
        self.update()

    def _on_text_added(self, page_index: int, item: TextItem):
        self.update()

    def _on_texts_updated(self, page_index: int):
        self.update()

    def _on_tool_changed(self, tool_name: str):
        if tool_name != "text" and self.text_input.isVisible():
            self._commit_text()
        if tool_name != "pointer":
            self.current_trail.clear()
            self.fading_trails.clear()
            self.trail_timer.stop()
        if tool_name == "text":
            self.setCursor(Qt.CursorShape.IBeamCursor)
        elif tool_name == "hand":
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def eventFilter(self, watched, event):
        if watched == self.text_input:
            if event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape:
                    self._cancel_text()
                    return True
                elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self._commit_text()
                    return True
            elif event.type() == QEvent.Type.FocusOut:
                if self.text_input.isVisible():
                    self._commit_text()
        return super().eventFilter(watched, event)

    def _commit_text(self):
        if not self.text_input.isVisible() or self.text_input_norm_pos is None:
            return
        text = self.text_input.text().strip()
        norm_x, norm_y = self.text_input_norm_pos
        self.text_input.hide()
        self.text_input.clear()
        self.text_input_norm_pos = None
        if text:
            self.controller.add_text(norm_x, norm_y, text)
        self.setFocus()
        self.update()

    def _cancel_text(self):
        self.text_input.hide()
        self.text_input.clear()
        self.text_input_norm_pos = None
        self.setFocus()
        self.update()

    def _on_tool_size_changed(self, tool_name: str, size: int):
        self.update()

    def _on_line_mode_changed(self, is_line: bool):
        self.update()

    def _on_eraser_mode_changed(self, mode: str):
        self.update()

    def _on_color_changed(self, tool_name: str, color: str):
        self.update()

    def _on_blank_state_changed(self, enabled: bool):
        if enabled:
            self.current_trail.clear()
            self.fading_trails.clear()
            self.trail_timer.stop()
        self.update()

    def _on_sketchbook_state_changed(self, enabled: bool):
        self._update_scaled_pixmap()
        self.update()

    def _on_sketch_visibility_changed(self, visible: bool):
        self.update()

    # ==========================================
    # Wacom / スタイラスペン入力イベント処理
    # ==========================================
    def tabletEvent(self, event: QTabletEvent):
        norm = self._pos_to_normalized(event.position())
        device = event.pointingDevice()
        is_eraser = device is not None and device.pointerType() == QPointingDevice.PointerType.Eraser
        pressure = event.pressure()
        tool = self.controller.current_tool
        ev_type = event.type()

        # 1. 消しゴム機能（ペン尻 または 消しゴムツール選択時）
        if is_eraser or tool == "eraser":
            rect = self._get_slide_rect()
            aspect_ratio = rect.width() / rect.height() if rect.height() > 0 else 1.0
            eraser_size = self.controller.get_tool_size("eraser")
            threshold = max(0.008, eraser_size * 0.001)

            if pressure > 0.0 and norm is not None:
                self.controller.erase_at_point(norm[0], norm[1], threshold=threshold, aspect_ratio=aspect_ratio)
            self.hover_pos = norm
            self.update()
            event.accept()
            return

        # 2. レーザーポインタモード（ホバー時はポインタのみ、接地ドラッグ時は一時的な線を描画）
        if tool == "pointer":
            if norm is not None:
                is_down = pressure > 0.05 or bool(event.buttons() & Qt.MouseButton.LeftButton)
                self.controller.update_pointer(norm[0], norm[1], True, is_down=is_down)
            else:
                self.controller.update_pointer(0, 0, False, is_down=False)
            event.accept()
            return

        # 3. 描画ツール（ペン / 蛍光ペン）
        if tool in ("pen", "highlighter"):
            if ev_type == QEvent.Type.TabletPress:
                if norm is not None and pressure > 0.0:
                    self.is_tablet_drawing = True
                    self.hover_pos = None
                    self.controller.begin_stroke(norm[0], norm[1], pressure=pressure)

            elif ev_type == QEvent.Type.TabletMove:
                if pressure > 0.0 and norm is not None:
                    # なぞっている最中
                    if not self.is_tablet_drawing:
                        self.is_tablet_drawing = True
                        self.controller.begin_stroke(norm[0], norm[1], pressure=pressure)
                    else:
                        if self.controller.is_line_mode:
                            snapped = self._snap_line_endpoint(norm)
                            self.controller.update_line_endpoint(snapped[0], snapped[1], pressure=pressure)
                        else:
                            self.controller.add_stroke_point(norm[0], norm[1], pressure=pressure)
                    self.hover_pos = None
                else:
                    # ホバー中
                    if self.is_tablet_drawing:
                        self.is_tablet_drawing = False
                        self.controller.end_stroke()
                    self.hover_pos = norm
                    self.update()

            elif ev_type == QEvent.Type.TabletRelease:
                if self.is_tablet_drawing:
                    self.is_tablet_drawing = False
                    self.controller.end_stroke()
                self.hover_pos = norm
                self.update()

            event.accept()
            return

        # 4. 通常モード（ハンドツール）
        if tool == "hand":
            if ev_type == QEvent.Type.TabletPress and norm is not None:
                if event.button() == Qt.MouseButton.RightButton:
                    self.controller.prev_page()
                else:
                    link = self._find_link_at(norm)
                    if link:
                        self._execute_link(link)
                    else:
                        self.controller.next_page()
            event.accept()
            return

        # 5. テキスト入力ツール
        if tool == "text":
            if ev_type == QEvent.Type.TabletPress and norm is not None and pressure > 0.0:
                self._start_text_input(norm)
            event.accept()
            return

        super().tabletEvent(event)

    # ==========================================
    # 通常マウス操作
    # ==========================================
    def mousePressEvent(self, event: QMouseEvent):
        # マウスの戻るボタン (BackButton) でジャンプ元に戻る
        if event.button() == Qt.MouseButton.BackButton:
            if self.controller.can_jump_back:
                self.controller.jump_back()
                return

        norm = self._pos_to_normalized(event.position())

        # 既存テキスト入力があり、かつテキストツール以外をクリックした場合は確定
        if self.text_input.isVisible():
            if not norm or self.controller.current_tool != "text":
                self._commit_text()

        if not norm:
            super().mousePressEvent(event)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            tool = self.controller.current_tool
            # リンククリック判定 (通常ハンドモード、または Ctrl/Cmd 押下時)
            is_ctrl_or_cmd = bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
            if tool == "hand" or is_ctrl_or_cmd:
                link = self._find_link_at(norm)
                if link:
                    self._execute_link(link)
                    return

            if tool == "text":
                self._start_text_input(norm)
                return
            elif tool in ("pen", "highlighter"):
                self.controller.begin_stroke(norm[0], norm[1], pressure=1.0)
            elif tool == "eraser":
                rect = self._get_slide_rect()
                aspect_ratio = rect.width() / rect.height() if rect.height() > 0 else 1.0
                eraser_size = self.controller.get_tool_size("eraser")
                threshold = max(0.008, eraser_size * 0.001)
                self.controller.erase_at_point(norm[0], norm[1], threshold=threshold, aspect_ratio=aspect_ratio)
            elif tool == "hand":
                self.controller.next_page()
            elif tool == "pointer":
                self.is_pointer_down = True
                if norm:
                    self.controller.update_pointer(norm[0], norm[1], True, is_down=True)
        elif event.button() == Qt.MouseButton.RightButton:
            self.controller.prev_page()

        super().mousePressEvent(event)

    def _start_text_input(self, norm: tuple[float, float]):
        """クリック位置にインラインテキスト入力ボックスを表示"""
        if self.text_input.isVisible():
            self._commit_text()

        rect = self._get_slide_rect()
        if rect.isEmpty():
            return

        base_dim = min(rect.width(), rect.height())
        text_size = self.controller.get_tool_size("text")
        font_size = max(11, int(base_dim * (text_size * 0.0015)))

        font = self.text_input.font()
        font.setPixelSize(font_size)
        font.setBold(False)
        font.setWeight(QFont.Weight.Normal)
        self.text_input.setFont(font)

        text_color = self.controller.get_tool_color("text")
        self.text_input.setStyleSheet(
            f"QLineEdit {{ background-color: rgba(20, 20, 24, 0.92); color: {text_color}; border: 1.5px solid {text_color}; border-radius: 3px; padding: 2px 6px; }}"
        )

        px = rect.left() + norm[0] * rect.width()
        py = rect.top() + norm[1] * rect.height()
        box_w = max(160, int(font_size * 10))
        box_h = int(font_size * 1.8) + 6

        if px + box_w > rect.right():
            px = max(rect.left(), rect.right() - box_w)
        if py + box_h > rect.bottom():
            py = max(rect.top(), rect.bottom() - box_h)

        self.text_input_norm_pos = (
            (px - rect.left()) / rect.width(),
            (py - rect.top()) / rect.height()
        )
        self.text_input.setGeometry(int(px), int(py), box_w, box_h)
        self.text_input.clear()
        self.text_input.show()
        self.text_input.setFocus()

    def mouseMoveEvent(self, event: QMouseEvent):
        norm = self._pos_to_normalized(event.position())
        tool = self.controller.current_tool
        is_ctrl_or_cmd = bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))

        if norm:
            # リンクホバー検出 (ハンドモード、または Ctrl/Cmd 押下時)
            link = self._find_link_at(norm) if (tool == "hand" or is_ctrl_or_cmd) else None
            if link is not None:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                if self.hovered_link != link:
                    self.hovered_link = link
                    self.update()
            else:
                if self.hovered_link is not None:
                    self.hovered_link = None
                    self.update()

            if tool == "pointer":
                is_down = bool(event.buttons() & Qt.MouseButton.LeftButton) or self.is_pointer_down
                self.controller.update_pointer(norm[0], norm[1], True, is_down=is_down)
                if link is None:
                    self.setCursor(Qt.CursorShape.ArrowCursor)
            elif tool == "text":
                if link is None:
                    self.setCursor(Qt.CursorShape.IBeamCursor)
            elif tool == "eraser":
                if link is None:
                    self.setCursor(Qt.CursorShape.CrossCursor)
                if event.buttons() & Qt.MouseButton.LeftButton:
                    rect = self._get_slide_rect()
                    aspect_ratio = rect.width() / rect.height() if rect.height() > 0 else 1.0
                    eraser_size = self.controller.get_tool_size("eraser")
                    threshold = max(0.008, eraser_size * 0.001)
                    self.controller.erase_at_point(norm[0], norm[1], threshold=threshold, aspect_ratio=aspect_ratio)
                self.hover_pos = norm
                self.update()
            elif tool in ("pen", "highlighter"):
                if link is None:
                    self.setCursor(Qt.CursorShape.CrossCursor)
                if event.buttons() & Qt.MouseButton.LeftButton:
                    if self.controller.is_line_mode:
                        snapped = self._snap_line_endpoint(norm)
                        self.controller.update_line_endpoint(snapped[0], snapped[1], pressure=1.0)
                    else:
                        self.controller.add_stroke_point(norm[0], norm[1], pressure=1.0)
                    self.hover_pos = None
                else:
                    self.hover_pos = norm
                    self.update()
            else:
                if link is None:
                    self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            if self.hovered_link is not None:
                self.hovered_link = None
                self.update()
            if tool == "pointer":
                self.is_pointer_down = False
                self.controller.update_pointer(0, 0, False, is_down=False)
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.hover_pos = None
            self.update()

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            tool = self.controller.current_tool
            if tool in ("pen", "highlighter"):
                self.controller.end_stroke()
            elif tool == "pointer":
                self.is_pointer_down = False
                norm = self._pos_to_normalized(event.position())
                if norm:
                    self.controller.update_pointer(norm[0], norm[1], True, is_down=False)
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self.hovered_link is not None:
            self.hovered_link = None
        if self.controller.current_tool == "pointer":
            self.is_pointer_down = False
            self.controller.update_pointer(0, 0, False, is_down=False)
        self.hover_pos = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # 背景塗りつぶし
        painter.fillRect(self.rect(), QColor("#121212"))

        rect = self._get_slide_rect()
        if rect.isEmpty():
            painter.setPen(QColor("#666666"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "スライドが読み込まれていません")
            return

        if self.controller.is_sketchbook:
            # スケッチブック（白紙キャンバス）描画
            painter.fillRect(rect, QColor("#ffffff"))
            painter.setPen(QPen(QColor("#555555"), 1.0))
            painter.drawRect(rect)
        elif self.pixmap and not self.pixmap.isNull():
            # スライド画像描画（面積平均縮小・Retina/HiDPI対応キャッシュを使用）
            if self._cached_scaled_pixmap is not None:
                painter.drawPixmap(rect.topLeft(), self._cached_scaled_pixmap)
            else:
                painter.drawPixmap(rect, self.pixmap, QRectF(self.pixmap.rect()))
        else:
            painter.setPen(QColor("#666666"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "スライドが読み込まれていません")
            return

        # ハイパーリンクのホバーハイライト描画
        if self.hovered_link and not self.controller.is_sketchbook:
            lx0, ly0, lx1, ly1 = self.hovered_link.rect
            hl_rect = QRectF(
                rect.left() + lx0 * rect.width(),
                rect.top() + ly0 * rect.height(),
                (lx1 - lx0) * rect.width(),
                (ly1 - ly0) * rect.height()
            )
            painter.setPen(QPen(QColor(0, 122, 204, 160), 1.2))
            painter.setBrush(QBrush(QColor(0, 122, 204, 38)))
            painter.drawRoundedRect(hl_rect, 2.0, 2.0)

        # ストローク・テキスト描画（スケッチが表示状態の時のみ）
        base_dim = min(rect.width(), rect.height())
        if self.controller.is_sketch_visible:
            strokes = self.controller.get_strokes_for_page(self.controller.current_page)
            for stroke in strokes:
                if not stroke.points:
                    continue
                color = QColor(stroke.color)
                color.setAlphaF(stroke.opacity)
                pen_w = max(1.5, base_dim * stroke.width_ratio)
                pen = QPen(color, pen_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)

                if len(stroke.points) == 1:
                    painter.setBrush(QBrush(color))
                    p0x = rect.left() + stroke.points[0][0] * rect.width()
                    p0y = rect.top() + stroke.points[0][1] * rect.height()
                    painter.drawEllipse(QPointF(p0x, p0y), pen_w / 2.0, pen_w / 2.0)
                    continue

                path = create_smooth_path(stroke.points, rect.left(), rect.top(), rect.width(), rect.height())
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)

            # テキスト描画
            texts = self.controller.get_texts_for_page(self.controller.current_page)
            for item in texts:
                if not item.text:
                    continue
                font_size = max(10, int(base_dim * (item.size * 0.0015)))
                font = QFont(painter.font())
                font.setPixelSize(font_size)
                font.setBold(False)
                font.setWeight(QFont.Weight.Normal)
                painter.setFont(font)
                painter.setPen(QPen(QColor(item.color)))

                metrics = painter.fontMetrics()
                tx = rect.left() + item.norm_x * rect.width()
                ty = rect.top() + item.norm_y * rect.height()
                painter.drawText(int(tx + 6), int(ty + metrics.ascent() + 3), item.text)

        # 消しゴムカーソル（手元画面のみ、消去範囲を示す円）
        if self.controller.current_tool == "eraser" and self.hover_pos:
            hx = rect.left() + self.hover_pos[0] * rect.width()
            hy = rect.top() + self.hover_pos[1] * rect.height()
            size = self.controller.get_tool_size("eraser")
            eraser_r = max(6.0, base_dim * (size * 0.001))

            if self.controller.eraser_mode == "partial":
                # 通常消しゴム: くり抜く範囲を示す実線円＋半透明塗り
                eraser_pen = QPen(QColor(255, 120, 120, 240), 1.5, Qt.PenStyle.SolidLine)
                painter.setPen(eraser_pen)
                painter.setBrush(QBrush(QColor(255, 100, 100, 45)))
            else:
                # オブジェクト消しゴム: 接触判定を示す点線円
                eraser_pen = QPen(QColor(255, 160, 160, 200), 1.2, Qt.PenStyle.DashLine)
                painter.setPen(eraser_pen)
                painter.setBrush(QBrush(QColor(255, 255, 255, 20)))

            painter.drawEllipse(QPointF(hx, hy), eraser_r, eraser_r)

        # ホバー時カーソル（手元画面のみ、ペン先の位置・太さを示すペンの色で塗りつぶした丸）
        if self.controller.current_tool in ("pen", "highlighter") and self.hover_pos:
            hx = rect.left() + self.hover_pos[0] * rect.width()
            hy = rect.top() + self.hover_pos[1] * rect.height()
            current_size = self.controller.get_current_tool_size()
            curr_r = max(3.0, (base_dim * (current_size * 0.001)) / 2.0)

            tool_color = self.controller.get_tool_color(self.controller.current_tool)
            fill_color = QColor(tool_color)
            if self.controller.current_tool == "highlighter":
                # 蛍光ペン: 文字を隠さない適度な透明度＋輪郭線
                fill_color.setAlphaF(0.40)
                border_color = QColor(tool_color).darker(120)
                border_color.setAlphaF(0.85)
            else:
                # 通常ペン: ペンの色でしっかり塗りつぶし
                fill_color.setAlphaF(0.9)
                border_color = QColor(tool_color).darker(115)
                border_color.setAlphaF(1.0)

            painter.setPen(QPen(border_color, 1.2, Qt.PenStyle.SolidLine))
            painter.setBrush(QBrush(fill_color))
            painter.drawEllipse(QPointF(hx, hy), curr_r, curr_r)

        # レーザーポインタの一時的な線（軌跡）描画（各ストロークを独立したベジェ曲線として一括描画）
        now = time.time()
        lifetime = 0.45
        fade_duration = 0.25

        trails_to_draw: list[tuple[list[tuple[float, float, float]], float]] = []

        # 1. 離されたフェードアウト中ストローク
        for stroke_pts, rel_t in self.fading_trails:
            elapsed = now - rel_t
            f_alpha = max(0.0, min(1.0, 1.0 - elapsed / fade_duration))
            if f_alpha > 0.0 and len(stroke_pts) >= 2:
                trails_to_draw.append((stroke_pts, f_alpha))

        # 2. 現在ドラッグ中のストローク
        if self.current_trail:
            active_pts = [p for p in self.current_trail if now - p[2] < lifetime]
            if len(active_pts) >= 2:
                trails_to_draw.append((active_pts, 1.0))

        if trails_to_draw:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            base_w = max(4.0, base_dim * 0.006)

            for stroke_pts, fade_alpha in trails_to_draw:
                smooth_path = create_smooth_path(
                    stroke_pts,
                    rect.left(),
                    rect.top(),
                    rect.width(),
                    rect.height()
                )

                # 外側の光彩（グロー）: 継ぎ目のない単一パス
                glow_alpha = int(120 * fade_alpha)
                glow_pen = QPen(
                    QColor(255, 30, 30, glow_alpha),
                    base_w * 2.4,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin
                )
                painter.setPen(glow_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.strokePath(smooth_path, glow_pen)

                # 中心の明るい線（コア）: 継ぎ目のない単一パス
                core_alpha = int(245 * fade_alpha)
                core_pen = QPen(
                    QColor(255, 90, 90, core_alpha),
                    base_w,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin
                )
                painter.setPen(core_pen)
                painter.strokePath(smooth_path, core_pen)

            painter.restore()

        # レーザーポインタ描画
        if self.is_pointer_visible and self.pointer_pos:
            px = rect.left() + self.pointer_pos[0] * rect.width()
            py = rect.top() + self.pointer_pos[1] * rect.height()
            pointer_size = self.controller.get_tool_size("pointer")
            radius = max(8.0, base_dim * (pointer_size * 0.001))

            gradient = QRadialGradient(px, py, radius)
            gradient.setColorAt(0.0, QColor(255, 255, 255, 255))
            gradient.setColorAt(0.25, QColor(255, 60, 60, 240))
            gradient.setColorAt(0.65, QColor(220, 0, 0, 180))
            gradient.setColorAt(1.0, QColor(200, 0, 0, 0))

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(gradient))
            painter.drawEllipse(QPointF(px, py), radius, radius)

        # スケッチ非表示中バッジ（右上）
        if not self.controller.is_sketch_visible and not self.controller.is_blank:
            badge_rect = QRectF(rect.right() - 155, rect.top() + 10, 145, 26)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(40, 40, 40, 200)))
            painter.drawRoundedRect(badge_rect, 6, 6)
            painter.setPen(QColor("#ffb74d"))
            b_font = QFont(painter.font())
            b_font.setPixelSize(12)
            b_font.setBold(True)
            painter.setFont(b_font)
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, "👁️‍🗨️ スケッチ非表示中")

        # スケッチブック表示中バッジ（右上）
        if self.controller.is_sketchbook and not self.controller.is_blank:
            badge_y = rect.top() + (42 if not self.controller.is_sketch_visible else 10)
            badge_rect = QRectF(rect.right() - 145, badge_y, 135, 26)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(30, 30, 30, 190)))
            painter.drawRoundedRect(badge_rect, 6, 6)
            painter.setPen(QColor("#4fc3f7"))
            b_font = QFont(painter.font())
            b_font.setPixelSize(12)
            b_font.setBold(True)
            painter.setFont(b_font)
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, "📋 スケッチブック")

        # ブランクスクリーン表示（画面暗転）
        if self.controller.is_blank:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 230))
            painter.setPen(QColor(255, 255, 255))
            b_font = QFont(painter.font())
            b_font.setPixelSize(22)
            b_font.setBold(True)
            painter.setFont(b_font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "⬛ 暗転中 (Bキーで解除)")
