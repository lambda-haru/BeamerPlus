import os
import time
from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QTimer, QUrl
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsPathItem, QGraphicsEllipseItem, QGraphicsSimpleTextItem
)
from PySide6.QtGui import (
    QPixmap, QPainter, QResizeEvent, QKeyEvent, QMouseEvent, QColor, QPen,
    QPainterPath, QRadialGradient, QBrush, QFont, QDesktopServices
)

from beamerplus.DrawingModel import PresentationController, Stroke, TextItem, create_smooth_path
from beamerplus.PdfReader import PdfLink

# スライド短辺に対するポインタ半径の比率 (視認性の高い大きめのレーザーポインタ)
POINTER_RADIUS_RATIO = 0.018


class ViewerGraphicsView(QGraphicsView):
    def __init__(self, viewer: "Viewer", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.viewer = viewer
        self.setMouseTracking(True)

    def drawForeground(self, painter: QPainter, rect: QRectF):
        super().drawForeground(painter, rect)
        self.viewer.draw_trail(painter, rect)

    def _get_normalized_pos(self, view_pos) -> tuple[float, float] | None:
        if not self.viewer.page_pixmap or self.viewer.page_pixmap.isNull():
            return None
        pw = float(self.viewer.page_pixmap.width())
        ph = float(self.viewer.page_pixmap.height())
        if pw <= 0 or ph <= 0:
            return None
        scene_pos = self.mapToScene(view_pos)
        nx = scene_pos.x() / pw
        ny = scene_pos.y() / ph
        if 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0:
            return (nx, ny)
        return None

    def mouseMoveEvent(self, event: QMouseEvent):
        norm = self._get_normalized_pos(event.position().toPoint())
        if norm:
            link = self.viewer._find_link_at(norm)
            if link:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.BackButton:
            if self.viewer.controller and self.viewer.controller.can_jump_back:
                self.viewer.controller.jump_back()
                return

        norm = self._get_normalized_pos(event.position().toPoint())
        if norm and event.button() == Qt.MouseButton.LeftButton:
            link = self.viewer._find_link_at(norm)
            if link:
                self.viewer._execute_link(link)
                return

        super().mousePressEvent(event)


class Viewer(QMainWindow):
    blank_state_changed = Signal(bool)

    def __init__(self, controller: PresentationController | None = None):
        super().__init__()

        self.setWindowTitle("BeamerPlus プロジェクタ")
        self.controller = controller
        self.links: list[PdfLink] = []

        self.graphics_view = ViewerGraphicsView(self)
        self.setCentralWidget(self.graphics_view)
        self.graphics_view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.graphics_view.setStyleSheet("background-color: black; border: none;")
        self.graphics_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.graphics_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.graphics_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.graphics_view.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.graphics_view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        self.scene = QGraphicsScene(self)
        self.graphics_view.setScene(self.scene)

        self.page_pixmap: QPixmap | None = None

        # スライド画像アイテム（高品質バイリニア補間 SmoothTransformation を有効化）
        self.pixmap_item: QGraphicsPixmapItem = self.scene.addPixmap(QPixmap())
        self.pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.pixmap_item.setZValue(0)

        # ストローク管理
        self.stroke_items: list[QGraphicsPathItem] = []
        self.active_path_item: QGraphicsPathItem | None = None
        self.active_path: QPainterPath | None = None
        self.active_points: list[tuple[float, float]] = []

        # レーザーポインタ一時軌跡（トレイル）管理
        self.current_trail: list[tuple[float, float, float]] = []  # 現在ドラッグ中の軌跡 (norm_x, norm_y, timestamp)
        self.fading_trails: list[tuple[list[tuple[float, float, float]], float]] = []  # 離されたフェードアウト中ストローク [(points, release_time), ...]
        self.is_pointer_down: bool = False
        self.trail_timer = QTimer(self)
        self.trail_timer.setInterval(20)
        self.trail_timer.timeout.connect(self._on_trail_timer_tick)

        # テキスト管理
        self.text_items: list[QGraphicsSimpleTextItem] = []

        # レーザーポインタアイテム（一度作成して再利用）
        self.pointer_radius: float = 24.0
        self.pointer_item: QGraphicsEllipseItem = QGraphicsEllipseItem(
            -self.pointer_radius, -self.pointer_radius, self.pointer_radius * 2, self.pointer_radius * 2
        )
        self.pointer_item.setPen(QPen(Qt.PenStyle.NoPen))
        self.pointer_item.setZValue(1000)  # 最前面
        self.pointer_item.setVisible(False)
        self.scene.addItem(self.pointer_item)

        # ブランクスクリーン用オーバーレイ（ウィンドウ全体を完全に黒く覆う）
        self.is_blank: bool = False
        self.blank_overlay = QWidget(self)
        self.blank_overlay.setStyleSheet("background-color: black;")
        self.blank_overlay.hide()

        if self.controller:
            self.connect_controller(self.controller)

    def connect_controller(self, controller: PresentationController):
        self.controller = controller
        controller.pointer_updated.connect(self.on_pointer_updated)
        controller.stroke_started.connect(self.on_stroke_started)
        controller.stroke_added_point.connect(self.on_stroke_added_point)
        controller.stroke_line_updated.connect(self.on_stroke_line_updated)
        controller.stroke_finished.connect(self.on_stroke_finished)
        controller.strokes_cleared.connect(self.on_strokes_cleared)
        controller.stroke_undone.connect(self.on_stroke_undone)
        controller.strokes_updated.connect(self.on_strokes_updated)
        controller.text_added.connect(self.on_text_added)
        controller.texts_updated.connect(self.on_texts_updated)
        controller.tool_size_changed.connect(self.on_tool_size_changed)
        controller.blank_state_changed.connect(self.on_blank_state_changed)
        controller.sketchbook_state_changed.connect(self.on_sketchbook_state_changed)
        controller.sketch_visibility_changed.connect(self.on_sketch_visibility_changed)

    def _is_target_page(self, page_index: int) -> bool:
        if not self.controller:
            return True
        if self.controller.is_sketchbook:
            return page_index == -1
        return self.controller.get_canonical_page(page_index) == self.controller.get_canonical_page(self.controller.current_page)

    def _setup_pointer_item(self):
        """スライド解像度とコントローラ設定に応じたポインタアイテムのサイズとグラデーションを更新"""
        if not self.page_pixmap or self.page_pixmap.isNull():
            return

        w = self.page_pixmap.width()
        h = self.page_pixmap.height()
        base_dim = min(w, h)
        pointer_size = self.controller.get_tool_size("pointer") if self.controller else 18
        self.pointer_radius = max(12.0, base_dim * (pointer_size * 0.001))

        r = self.pointer_radius
        self.pointer_item.setRect(-r, -r, r * 2, r * 2)

        # 視認性の高い発光レーザーグラデーション
        gradient = QRadialGradient(0, 0, r)
        gradient.setColorAt(0.0, QColor(255, 255, 255, 255))   # 中心核（眩しい白）
        gradient.setColorAt(0.25, QColor(255, 60, 60, 240))    # 高輝度赤
        gradient.setColorAt(0.65, QColor(220, 0, 0, 180))     # 鮮烈な赤
        gradient.setColorAt(1.0, QColor(200, 0, 0, 0))         # 外側のぼかしフェード

        self.pointer_item.setBrush(QBrush(gradient))
        self.pointer_item.setVisible(False)

    def _clear_scene_strokes(self):
        """シーンから描画ストロークアイテムのみを安全に削除"""
        for item in self.stroke_items:
            try:
                self.scene.removeItem(item)
            except RuntimeError:
                pass
        self.stroke_items.clear()
        self.active_path_item = None
        self.active_path = None

    def _clear_scene_texts(self):
        """シーンからテキストアイテムのみを安全に削除"""
        for item in self.text_items:
            try:
                self.scene.removeItem(item)
            except RuntimeError:
                pass
        self.text_items.clear()

    def show_image(self, image: QPixmap) -> None:
        self.page_pixmap = image

        # 既存ストロークおよびテキストのみ削除（シーン全体を clear して C++ オブジェクトを破棄しない）
        self._clear_scene_strokes()
        self._clear_scene_texts()

        # ピックスマップ更新
        self.pixmap_item.setPixmap(self.page_pixmap)
        self.scene.setSceneRect(QRectF(self.page_pixmap.rect()))

        # スライド解像度に合わせたポインタサイズ・グラデーション更新
        self._setup_pointer_item()

        # 既存ストローク・テキストの復元
        if self.controller:
            page_strokes = self.controller.get_strokes_for_page(self.controller.current_page)
            for stroke in page_strokes:
                self._draw_complete_stroke(stroke)
            page_texts = self.controller.get_texts_for_page(self.controller.current_page)
            for t_item in page_texts:
                self._draw_complete_text(t_item)

        self.fit_view()

    def _draw_complete_stroke(self, stroke: Stroke):
        if not self.page_pixmap or not stroke.points:
            return
        
        w = self.page_pixmap.width()
        h = self.page_pixmap.height()
        base_dim = min(w, h)
        pen_width = max(2.5, base_dim * stroke.width_ratio)

        color = QColor(stroke.color)
        color.setAlphaF(stroke.opacity)
        pen = QPen(color, pen_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

        if len(stroke.points) == 1:
            dot_item = self.scene.addEllipse(
                stroke.points[0][0] * w - pen_width / 2.0,
                stroke.points[0][1] * h - pen_width / 2.0,
                pen_width, pen_width,
                QPen(Qt.PenStyle.NoPen), QBrush(color)
            )
            dot_item.setZValue(10)
            self.stroke_items.append(dot_item)
            return

        path = create_smooth_path(stroke.points, 0.0, 0.0, w, h)
        path_item = self.scene.addPath(path, pen)
        path_item.setZValue(10)
        self.stroke_items.append(path_item)

    def _draw_complete_text(self, item: TextItem):
        if not self.page_pixmap or not item.text:
            return
        
        w = self.page_pixmap.width()
        h = self.page_pixmap.height()
        base_dim = min(w, h)
        font_size = max(10, int(base_dim * (item.size * 0.0015)))

        font = QFont()
        font.setPixelSize(font_size)
        font.setBold(False)
        font.setWeight(QFont.Weight.Normal)

        text_item = QGraphicsSimpleTextItem(item.text)
        text_item.setFont(font)
        text_item.setBrush(QBrush(QColor(item.color)))
        text_item.setPen(QPen(Qt.PenStyle.NoPen))
        
        pad_x = 6.0 * (w / 800.0) if w > 0 else 6.0
        pad_y = 3.0 * (h / 600.0) if h > 0 else 3.0
        text_item.setPos(item.norm_x * w + pad_x, item.norm_y * h + pad_y)
        text_item.setZValue(20)  # ストロークの上に配置
        self.scene.addItem(text_item)
        self.text_items.append(text_item)

    def on_pointer_updated(self, norm_x: float, norm_y: float, visible: bool, is_down: bool = False):
        if not self.pointer_item:
            return
        now = time.time()
        if not visible or self.is_blank or self.scene.sceneRect().isEmpty():
            self.pointer_item.setVisible(False)
            if self.is_pointer_down:
                self.is_pointer_down = False
                if len(self.current_trail) >= 2:
                    self.fading_trails.append((list(self.current_trail), now))
                self.current_trail = []
            return

        w = self.scene.sceneRect().width()
        h = self.scene.sceneRect().height()
        self.pointer_item.setPos(norm_x * w, norm_y * h)
        self.pointer_item.setVisible(True)

        prev_down = self.is_pointer_down
        self.is_pointer_down = is_down

        if is_down:
            if not prev_down:
                # 新しいストロークの開始（前のストロークとは完全に分離）
                if len(self.current_trail) >= 2:
                    self.fading_trails.append((list(self.current_trail), now))
                self.current_trail = [(norm_x, norm_y, now)]
            else:
                self.current_trail.append((norm_x, norm_y, now))
            if not self.trail_timer.isActive():
                self.trail_timer.start()
        elif prev_down and not is_down:
            # 離した瞬間: 現在のストロークをフェードアウト一覧へ移行
            if len(self.current_trail) >= 2:
                self.fading_trails.append((list(self.current_trail), now))
            self.current_trail = []

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
        self.graphics_view.viewport().update()

    def draw_trail(self, painter: QPainter, rect: QRectF):
        if self.is_blank or self.scene.sceneRect().isEmpty():
            return

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

        if not trails_to_draw:
            return

        sw = self.scene.sceneRect().width()
        sh = self.scene.sceneRect().height()
        base_dim = min(sw, sh)
        base_w = max(4.0, base_dim * 0.006)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        for stroke_pts, fade_alpha in trails_to_draw:
            smooth_path = create_smooth_path(stroke_pts, 0.0, 0.0, sw, sh)

            # 1. 外側の光彩（グロー）: 継ぎ目のない単一パス
            glow_alpha = int(120 * fade_alpha)
            glow_pen = QPen(
                QColor(255, 30, 30, glow_alpha),
                base_w * 2.4,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin
            )
            painter.strokePath(smooth_path, glow_pen)

            # 2. 中心の明るい線（コア）: 継ぎ目のない単一パス
            core_alpha = int(245 * fade_alpha)
            core_pen = QPen(
                QColor(255, 90, 90, core_alpha),
                base_w,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin
            )
            painter.strokePath(smooth_path, core_pen)

        painter.restore()

    def on_stroke_started(self, page_index: int, stroke: Stroke):
        if not self._is_target_page(page_index) or self.scene.sceneRect().isEmpty():
            return
        
        w = self.scene.sceneRect().width()
        h = self.scene.sceneRect().height()
        base_dim = min(w, h)
        pen_width = max(2.5, base_dim * stroke.width_ratio)

        color = QColor(stroke.color)
        color.setAlphaF(stroke.opacity)
        pen = QPen(color, pen_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

        p0 = stroke.points[0]
        self.active_points = [(p0[0], p0[1])]
        self.active_path = QPainterPath()
        self.active_path.moveTo(p0[0] * w, p0[1] * h)

        self.active_path_item = self.scene.addPath(self.active_path, pen)
        self.active_path_item.setZValue(10)
        self.stroke_items.append(self.active_path_item)

    def on_stroke_added_point(self, page_index: int, norm_x: float, norm_y: float, pressure: float = 1.0):
        if not self._is_target_page(page_index) or self.active_path_item is None or self.scene.sceneRect().isEmpty():
            return

        w = self.scene.sceneRect().width()
        h = self.scene.sceneRect().height()
        self.active_points.append((norm_x, norm_y))
        self.active_path = create_smooth_path(self.active_points, 0.0, 0.0, w, h)
        self.active_path_item.setPath(self.active_path)

    def on_stroke_line_updated(self, page_index: int, norm_x: float, norm_y: float):
        """直線の終点ドラッグ中プレビュー更新"""
        if not self._is_target_page(page_index) or self.active_path_item is None or not self.controller or self.scene.sceneRect().isEmpty():
            return

        w = self.scene.sceneRect().width()
        h = self.scene.sceneRect().height()
        if self.controller.current_stroke and self.controller.current_stroke.points:
            p0 = self.controller.current_stroke.points[0]
            new_path = QPainterPath()
            new_path.moveTo(p0[0] * w, p0[1] * h)
            new_path.lineTo(norm_x * w, norm_y * h)
            self.active_path = new_path
            self.active_path_item.setPath(self.active_path)

    def on_tool_size_changed(self, tool_name: str, size: int):
        if tool_name == "pointer":
            self._setup_pointer_item()

    def on_stroke_finished(self, page_index: int):
        self.active_path = None
        self.active_path_item = None
        self.active_points = []

    def on_strokes_cleared(self, page_index: int):
        if not self._is_target_page(page_index):
            return
        self._clear_scene_strokes()
        self._clear_scene_texts()

    def on_stroke_undone(self, page_index: int):
        if not self._is_target_page(page_index):
            return
        if self.stroke_items:
            last_item = self.stroke_items.pop()
            try:
                self.scene.removeItem(last_item)
            except RuntimeError:
                pass

    def on_text_added(self, page_index: int, item: TextItem):
        if not self._is_target_page(page_index) or self.scene.sceneRect().isEmpty():
            return
        self._draw_complete_text(item)

    def on_texts_updated(self, page_index: int):
        if not self._is_target_page(page_index) or self.scene.sceneRect().isEmpty():
            return
        self._clear_scene_texts()
        if self.controller:
            page_texts = self.controller.get_texts_for_page(page_index)
            for t_item in page_texts:
                self._draw_complete_text(t_item)

    def on_strokes_updated(self, page_index: int):
        """消しゴム等でストロークが変更されたときの全ストローク再描画"""
        if not self._is_target_page(page_index) or self.scene.sceneRect().isEmpty():
            return
        self._clear_scene_strokes()

        if self.controller:
            page_strokes = self.controller.get_strokes_for_page(page_index)
            for stroke in page_strokes:
                self._draw_complete_stroke(stroke)

    def on_blank_state_changed(self, enabled: bool):
        self.is_blank = enabled
        if self.is_blank:
            self.blank_overlay.setGeometry(self.rect())
            self.blank_overlay.show()
            self.blank_overlay.raise_()
            if self.pointer_item:
                self.pointer_item.setVisible(False)
            self.current_trail.clear()
            self.fading_trails.clear()
            self.trail_timer.stop()
        else:
            self.blank_overlay.hide()
        self.blank_state_changed.emit(self.is_blank)

    def on_sketch_visibility_changed(self, visible: bool):
        for item in self.stroke_items:
            item.setVisible(visible)
        for item in self.text_items:
            item.setVisible(visible)

    def on_sketchbook_state_changed(self, enabled: bool):
        self._clear_scene_strokes()
        self._clear_scene_texts()

        if enabled:
            # スケッチブック（白紙）モード
            w = self.page_pixmap.width() if self.page_pixmap and not self.page_pixmap.isNull() else 1920
            h = self.page_pixmap.height() if self.page_pixmap and not self.page_pixmap.isNull() else 1080
            white_pixmap = QPixmap(w, h)
            white_pixmap.fill(Qt.GlobalColor.white)
            self.pixmap_item.setPixmap(white_pixmap)
            self.scene.setSceneRect(QRectF(white_pixmap.rect()))
            self._setup_pointer_item()

            if self.controller:
                for stroke in self.controller.sketchbook_strokes:
                    self._draw_complete_stroke(stroke)
                for t_item in self.controller.sketchbook_texts:
                    self._draw_complete_text(t_item)
        else:
            # 通常スライド復帰
            if self.page_pixmap and not self.page_pixmap.isNull():
                self.pixmap_item.setPixmap(self.page_pixmap)
                self.scene.setSceneRect(QRectF(self.page_pixmap.rect()))
                self._setup_pointer_item()

                if self.controller:
                    for stroke in self.controller.get_strokes_for_page(self.controller.current_page):
                        self._draw_complete_stroke(stroke)
                    for t_item in self.controller.get_texts_for_page(self.controller.current_page):
                        self._draw_complete_text(t_item)

        self.fit_view()

    def fit_view(self):
        if not self.scene.sceneRect().isEmpty():
            self.graphics_view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        self.fit_view()
        if self.is_blank:
            self.blank_overlay.setGeometry(self.rect())

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def toggle_blank(self):
        """ブランクスクリーン（ウィンドウを維持したまま全画面を真っ黒にする）"""
        if self.controller:
            self.controller.toggle_blank()
        else:
            self.on_blank_state_changed(not self.is_blank)

    def blank_screen(self):
        self.toggle_blank()

    def set_links(self, links: list[PdfLink]):
        self.links = list(links) if links else []

    def _find_link_at(self, norm: tuple[float, float]) -> PdfLink | None:
        if not self.links or (self.controller and self.controller.is_sketchbook):
            return None
        nx, ny = norm
        for l in self.links:
            x0, y0, x1, y1 = l.rect
            if x0 <= nx <= x1 and y0 <= ny <= y1:
                return l
        return None

    def _execute_link(self, link: PdfLink):
        if link.page is not None:
            if self.controller:
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

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key in (Qt.Key.Key_F5, Qt.Key.Key_F11):
            self.toggle_fullscreen()
        elif key == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
        elif key == Qt.Key.Key_B:
            self.toggle_blank()
        elif key in (Qt.Key.Key_S, Qt.Key.Key_W):
            if self.controller:
                self.controller.toggle_sketchbook()
        elif key == Qt.Key.Key_H:
            if self.controller:
                self.controller.toggle_sketch_visibility()
        elif (key == Qt.Key.Key_Left and event.modifiers() & Qt.KeyboardModifier.AltModifier) or key in (Qt.Key.Key_Back, Qt.Key.Key_Backspace):
            if self.controller and self.controller.can_jump_back:
                self.controller.jump_back()
                return
        elif self.controller:
            if key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.controller.next_page()
            elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp):
                self.controller.prev_page()
        super().keyPressEvent(event)
