from dataclasses import dataclass, field
import math
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QColor, QPainterPath


@dataclass
class Stroke:
    color: str = "#e53935"       # HEX または色文字列
    width_ratio: float = 0.005   # 画像の短辺に対する比率
    opacity: float = 0.85
    # (norm_x, norm_y, pressure): pressure は 0.0 ~ 1.0 (マウス時は1.0)
    points: list[tuple[float, float, float]] = field(default_factory=list)


def create_smooth_path(points: list[tuple[float, ...]], origin_x: float, origin_y: float, scale_w: float, scale_h: float) -> QPainterPath:
    """点列から中点二次ベジェスプライン曲線を用いた滑らかな QPainterPath を生成する"""
    path = QPainterPath()
    if not points:
        return path

    p0x = origin_x + points[0][0] * scale_w
    p0y = origin_y + points[0][1] * scale_h
    path.moveTo(p0x, p0y)

    if len(points) == 2:
        path.lineTo(origin_x + points[1][0] * scale_w, origin_y + points[1][1] * scale_h)
    elif len(points) >= 3:
        p1x = origin_x + points[1][0] * scale_w
        p1y = origin_y + points[1][1] * scale_h
        path.lineTo((p0x + p1x) * 0.5, (p0y + p1y) * 0.5)

        for i in range(1, len(points) - 1):
            cpx = origin_x + points[i][0] * scale_w
            cpy = origin_y + points[i][1] * scale_h
            next_x = origin_x + points[i + 1][0] * scale_w
            next_y = origin_y + points[i + 1][1] * scale_h
            mid_x = (cpx + next_x) * 0.5
            mid_y = (cpy + next_y) * 0.5
            path.quadTo(cpx, cpy, mid_x, mid_y)

        last_x = origin_x + points[-1][0] * scale_w
        last_y = origin_y + points[-1][1] * scale_h
        path.lineTo(last_x, last_y)

    return path


@dataclass
class TextItem:
    text: str = ""
    norm_x: float = 0.0          # スライド上の相対X座標 (0.0 ~ 1.0)
    norm_y: float = 0.0          # スライド上の相対Y座標 (0.0 ~ 1.0)
    color: str = "#e53935"       # 文字色
    size: int = 28               # フォントサイズ指標 (短辺比率 = size * 0.0015)


def text_intersects_circle(item: TextItem, center: tuple[float, float], r: float, aspect_ratio: float = 1.0) -> bool:
    """テキストのバウンディングボックスが消しゴム円 (center, r) と交差するか判定"""
    if not item.text:
        return False
    h = item.size * 0.0015
    w = max(0.01, len(item.text) * (h * 0.55) / aspect_ratio)
    x0, y0 = item.norm_x, item.norm_y
    x1, y1 = item.norm_x + w, item.norm_y + h

    cx, cy = center
    closest_x = max(x0, min(cx, x1))
    closest_y = max(y0, min(cy, y1))
    dx = (cx - closest_x) * aspect_ratio
    dy = cy - closest_y
    return (dx * dx + dy * dy) <= (r * r)


def stroke_intersects_circle(stroke: Stroke, center: tuple[float, float], r: float, aspect_ratio: float = 1.0) -> bool:
    """ストローク（点列および線分）が円 (center, r) と交差するか判定"""
    if not stroke.points:
        return False
    if len(stroke.points) == 1:
        dx = (stroke.points[0][0] - center[0]) * aspect_ratio
        dy = stroke.points[0][1] - center[1]
        return (dx * dx + dy * dy) < (r * r)

    cx, cy = center[0] * aspect_ratio, center[1]
    r_sq = r * r

    for i in range(len(stroke.points) - 1):
        p1 = stroke.points[i]
        p2 = stroke.points[i + 1]
        x1, y1 = p1[0] * aspect_ratio, p1[1]
        x2, y2 = p2[0] * aspect_ratio, p2[1]
        dx = x2 - x1
        dy = y2 - y1
        l2 = dx * dx + dy * dy
        if l2 == 0.0:
            if ((cx - x1) ** 2 + (cy - y1) ** 2) < r_sq:
                return True
            continue
        t = max(0.0, min(1.0, ((cx - x1) * dx + (cy - y1) * dy) / l2))
        proj_x = x1 + t * dx
        proj_y = y1 + t * dy
        if ((cx - proj_x) ** 2 + (cy - proj_y) ** 2) < r_sq:
            return True
    return False


def clip_stroke_by_circle(stroke: Stroke, center: tuple[float, float], r: float, aspect_ratio: float = 1.0) -> list[Stroke]:
    """
    ストロークの折れ線を消しゴム円 (center, r) で切断し、
    円の外側に残った部分を複数のサブストロークに分割して返す（通常消しゴム用）
    """
    if not stroke.points or len(stroke.points) < 2:
        return []

    cx, cy = center
    r_sq = r * r

    def is_inside(p: tuple[float, float, float]) -> bool:
        dx = (p[0] - cx) * aspect_ratio
        dy = p[1] - cy
        return (dx * dx + dy * dy) < r_sq

    def lerp_pt(p1: tuple[float, float, float], p2: tuple[float, float, float], t: float) -> tuple[float, float, float]:
        x = p1[0] + (p2[0] - p1[0]) * t
        y = p1[1] + (p2[1] - p1[1]) * t
        pr = p1[2] + (p2[2] - p1[2]) * t
        return (x, y, pr)

    result_strokes: list[Stroke] = []
    current_points: list[tuple[float, float, float]] = []

    for i in range(len(stroke.points) - 1):
        p1 = stroke.points[i]
        p2 = stroke.points[i + 1]

        x1 = (p1[0] - cx) * aspect_ratio
        y1 = p1[1] - cy
        x2 = (p2[0] - cx) * aspect_ratio
        y2 = p2[1] - cy

        dx = x2 - x1
        dy = y2 - y1

        a = dx * dx + dy * dy
        ts: list[float] = []
        if a > 1e-12:
            b = 2.0 * (x1 * dx + y1 * dy)
            c = x1 * x1 + y1 * y1 - r_sq
            disc = b * b - 4.0 * a * c
            if disc >= 0.0:
                s_disc = math.sqrt(disc)
                t1 = (-b - s_disc) / (2.0 * a)
                t2 = (-b + s_disc) / (2.0 * a)
                if 1e-5 < t1 < 1.0 - 1e-5:
                    ts.append(t1)
                if 1e-5 < t2 < 1.0 - 1e-5:
                    ts.append(t2)
                ts.sort()

        sub_pts = [p1] + [lerp_pt(p1, p2, t) for t in ts] + [p2]

        for j in range(len(sub_pts) - 1):
            sa = sub_pts[j]
            sb = sub_pts[j + 1]
            mid = ((sa[0] + sb[0]) / 2.0, (sa[1] + sb[1]) / 2.0, (sa[2] + sb[2]) / 2.0)
            if not is_inside(mid):
                if not current_points:
                    current_points.append(sa)
                elif current_points[-1][:2] != sa[:2]:
                    current_points.append(sa)
                current_points.append(sb)
            else:
                if len(current_points) >= 2:
                    result_strokes.append(Stroke(stroke.color, stroke.width_ratio, stroke.opacity, current_points))
                current_points = []

    if len(current_points) >= 2:
        result_strokes.append(Stroke(stroke.color, stroke.width_ratio, stroke.opacity, current_points))

    return result_strokes


class PresentationController(QObject):
    """
    スライド状態、描画ストローク、ポインタ位置、ツール設定を一括管理し、
    MainWindow と Viewer の間で同期を行うコントローラ
    """
    # ページ変更
    page_changed = Signal(int)
    jump_history_changed = Signal(bool)                   # ジャンプ履歴の有無（戻れるかどうか）

    # ポインタ同期 (0.0 ~ 1.0)
    pointer_updated = Signal(float, float, bool, bool)  # norm_x, norm_y, visible, is_down

    # 描画ストローク同期
    stroke_started = Signal(int, Stroke)                  # page_index, new_stroke
    stroke_added_point = Signal(int, float, float, float)  # page_index, norm_x, norm_y, pressure
    stroke_line_updated = Signal(int, float, float)       # page_index, norm_x, norm_y (直線プレビュー更新)
    stroke_finished = Signal(int)                         # page_index
    strokes_cleared = Signal(int)                         # page_index
    stroke_undone = Signal(int)                           # page_index
    strokes_updated = Signal(int)                         # page_index (消しゴム等での再描画)

    # テキスト書き込み同期
    text_added = Signal(int, TextItem)                    # page_index, new_text_item
    texts_updated = Signal(int)                           # page_index (テキスト変更・消去)

    # ツールモード変更
    tool_changed = Signal(str)                            # "hand", "pointer", "pen", "highlighter", "eraser", "text"
    tool_size_changed = Signal(str, int)                  # tool_name, size (1 ~ 50)
    color_changed = Signal(str, str)                      # tool_name, hex_color
    line_mode_changed = Signal(bool)                      # 直線モードのON/OFF
    eraser_mode_changed = Signal(str)                     # "partial" (通常消しゴム) / "stroke" (オブジェクト消しゴム)
    blank_state_changed = Signal(bool)                    # ブランクスクリーン（暗転）のON/OFF
    sketchbook_state_changed = Signal(bool)               # スケッチブック（白紙）のON/OFF
    sketch_visibility_changed = Signal(bool)              # スケッチ（描画）の表示/非表示

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.current_page: int = 0
        self.total_pages: int = 0
        self.current_tool: str = "hand"       # 起動時は通常モード
        self.is_line_mode: bool = False       # 直線モードON/OFF
        self.eraser_mode: str = "partial"     # "partial" (通常消しゴム) / "stroke" (オブジェクト消しゴム)
        self.is_blank: bool = False           # ブランクスクリーン（暗転）状態
        self.is_sketchbook: bool = False      # スケッチブック（白紙）モード状態
        self.is_sketch_visible: bool = True   # スケッチ（手書き描画）の表示/非表示状態
        
        # ページごとのストローク保存: dict[page_index, list[Stroke]]
        self.page_strokes: dict[int, list[Stroke]] = {}
        self.current_stroke: Stroke | None = None

        # スケッチブック用ストローク・テキスト・履歴保存
        self.sketchbook_strokes: list[Stroke] = []
        self.sketchbook_texts: list[TextItem] = []
        self.sketchbook_history: list[tuple[str, object]] = []

        # ページごとのテキスト保存: dict[page_index, list[TextItem]]
        self.page_texts: dict[int, list[TextItem]] = {}

        # ページごとの操作履歴（Undo用）: dict[page_index, list[tuple[str, object]]]
        self.page_history: dict[int, list[tuple[str, object]]] = {}

        # 各ツールのサイズ有効範囲 (min, max)
        self.tool_ranges: dict[str, tuple[int, int]] = {
            "pointer": (5, 60),
            "pen": (1, 30),
            "highlighter": (5, 160),  # 蛍光ペンは行全体・大きな見出しをハイライトできるように極太まで対応
            "eraser": (5, 100),
            "text": (10, 80),         # テキストフォントサイズ指標
        }

        # 各ツールのサイズ（太さ / 半径 / 文字サイズ）設定 (width_ratio = size * 0.001)
        self.tool_sizes: dict[str, int] = {
            "pointer": 18,      # 半径比率: 0.018 (短辺の1.8%)
            "pen": 5,          # 線幅比率: 0.005 (短辺の0.5%)
            "highlighter": 45,  # 線幅比率: 0.045 (短辺の4.5%: 一般的な文字行をしっかり覆う太さ)
            "eraser": 25,       # 消去判定半径比率: 0.025 (短辺の2.5%)
            "text": 28,         # デフォルトフォントサイズ指標
        }

        # 色設定
        self.pen_color: str = "#e53935"            # ペンの色（デフォルト赤）
        self.highlighter_color: str = "#ffeb3b"    # 蛍光ペンの色（デフォルト蛍光イエロー）
        self.text_color: str = "#e53935"           # テキストの色（デフォルト赤）

        # 不透明度設定 (0.0 = 完全透明, 1.0 = 完全不透明)
        self.pen_opacity: float = 0.85
        self.highlighter_opacity: float = 0.28     # 蛍光ペンの不透明度（透明度72%: 文字がクリアに透けて見える設定）

        # ページ正規化（Beamer \pause の同一フレーム集約用写像: 物理ページ -> canonicalページ）
        self.page_mapping: dict[int, int] = {}

        # ハイパーリンク・目次ジャンプ履歴スタック (jump back用)
        self.jump_history: list[int] = []

    @property
    def can_jump_back(self) -> bool:
        return len(self.jump_history) > 0

    def set_page_mapping(self, mapping: dict[int, int] | None):
        self.page_mapping = mapping or {}

    def get_canonical_page(self, page_index: int) -> int:
        return self.page_mapping.get(page_index, page_index)

    def set_total_pages(self, total: int):
        self.total_pages = total
        self.clear_jump_history()

    def jump_to_page(self, index: int):
        """ハイパーリンクや目次等から指定ページへジャンプ（直前のページを履歴スタックに記録）"""
        if self.total_pages > 0 and 0 <= index < self.total_pages:
            if self.current_page != index:
                self.jump_history.append(self.current_page)
                self.set_page(index)
                self.jump_history_changed.emit(self.can_jump_back)

    def jump_back(self) -> bool:
        """ジャンプ元のスライドに戻る"""
        if self.jump_history:
            prev = self.jump_history.pop()
            self.set_page(prev)
            self.jump_history_changed.emit(self.can_jump_back)
            return True
        return False

    def clear_jump_history(self):
        if self.jump_history:
            self.jump_history.clear()
            self.jump_history_changed.emit(False)

    def set_page(self, index: int):
        if self.total_pages > 0 and 0 <= index < self.total_pages:
            if self.is_blank:
                self.set_blank(False)
            if self.current_page != index:
                self.current_page = index
                self.pointer_updated.emit(0.0, 0.0, False, False)
                self.page_changed.emit(index)

    def next_page(self):
        if self.current_page < self.total_pages - 1:
            self.set_page(self.current_page + 1)
        elif self.total_pages > 0 and self.current_page == self.total_pages - 1:
            if not self.is_blank:
                self.set_blank(True)

    def prev_page(self):
        if self.is_blank:
            self.set_blank(False)
        elif self.current_page > 0:
            self.set_page(self.current_page - 1)

    def set_tool(self, tool_name: str):
        if tool_name in ("hand", "pointer", "eraser", "text"):
            self.set_line_mode(False)

        if tool_name != self.current_tool:
            self.current_tool = tool_name
            self.tool_changed.emit(tool_name)
            if tool_name != "pointer":
                self.pointer_updated.emit(0.0, 0.0, False, False)

    def set_line_mode(self, enabled: bool):
        if self.is_line_mode != enabled:
            self.is_line_mode = enabled
            self.line_mode_changed.emit(enabled)

    def toggle_line_mode(self):
        if self.current_tool not in ("pen", "highlighter"):
            self.set_tool("pen")
            self.set_line_mode(True)
        else:
            self.set_line_mode(not self.is_line_mode)

    def set_eraser_mode(self, mode: str):
        if mode in ("partial", "stroke") and self.eraser_mode != mode:
            self.eraser_mode = mode
            self.eraser_mode_changed.emit(mode)

    def toggle_eraser_mode(self):
        new_mode = "stroke" if self.eraser_mode == "partial" else "partial"
        self.set_eraser_mode(new_mode)

    def set_blank(self, enabled: bool):
        if self.is_blank != enabled:
            self.is_blank = enabled
            self.blank_state_changed.emit(enabled)

    def toggle_blank(self):
        self.set_blank(not self.is_blank)

    def set_sketchbook(self, enabled: bool):
        if self.is_sketchbook != enabled:
            self.is_sketchbook = enabled
            self.sketchbook_state_changed.emit(enabled)

    def toggle_sketchbook(self):
        self.set_sketchbook(not self.is_sketchbook)

    def set_sketch_visibility(self, visible: bool):
        if self.is_sketch_visible != visible:
            self.is_sketch_visible = visible
            self.sketch_visibility_changed.emit(visible)

    def toggle_sketch_visibility(self):
        self.set_sketch_visibility(not self.is_sketch_visible)

    def get_tool_range(self, tool_name: str) -> tuple[int, int]:
        return self.tool_ranges.get(tool_name, (1, 50))

    def get_tool_size(self, tool_name: str) -> int:
        return self.tool_sizes.get(tool_name, 5)

    def set_tool_size(self, tool_name: str, size: int):
        min_v, max_v = self.get_tool_range(tool_name)
        clamped_size = max(min_v, min(max_v, size))
        self.tool_sizes[tool_name] = clamped_size
        self.tool_size_changed.emit(tool_name, clamped_size)

    def get_current_tool_size(self) -> int:
        return self.get_tool_size(self.current_tool)

    def set_current_tool_size(self, size: int):
        self.set_tool_size(self.current_tool, size)

    def get_tool_color(self, tool_name: str) -> str:
        if tool_name == "highlighter":
            return self.highlighter_color
        elif tool_name == "text":
            return self.text_color
        return self.pen_color

    def set_tool_color(self, tool_name: str, hex_color: str):
        if tool_name == "highlighter":
            self.highlighter_color = hex_color
        elif tool_name == "text":
            self.text_color = hex_color
        else:
            self.pen_color = hex_color
        self.color_changed.emit(tool_name, hex_color)

    def update_pointer(self, norm_x: float, norm_y: float, visible: bool, is_down: bool = False):
        self.pointer_updated.emit(norm_x, norm_y, visible, is_down)

    def begin_stroke(self, norm_x: float, norm_y: float, pressure: float = 1.0):
        if self.current_tool not in ("pen", "highlighter"):
            return
        
        size = self.get_tool_size(self.current_tool)
        width_ratio = size * 0.001

        if self.current_tool == "highlighter":
            color = self.highlighter_color
            opacity = self.highlighter_opacity
        else:
            color = self.pen_color
            opacity = self.pen_opacity

        self.begin_custom_stroke(color, width_ratio, opacity, norm_x, norm_y, pressure)

    def begin_custom_stroke(
        self,
        color: str,
        width_ratio: float,
        opacity: float,
        norm_x: float,
        norm_y: float,
        pressure: float = 1.0
    ):
        """PC側の選択ツール（current_tool）や色・太さ設定を変更せずに直接ストロークを開始"""
        stroke = Stroke(color=color, width_ratio=width_ratio, opacity=opacity, points=[(norm_x, norm_y, pressure)])
        self.current_stroke = stroke

        if self.is_sketchbook:
            self.sketchbook_strokes.append(stroke)
            self.sketchbook_history.append(("stroke", stroke))
            self.stroke_started.emit(-1, stroke)
            return

        target_page = self.get_canonical_page(self.current_page)
        if target_page not in self.page_strokes:
            self.page_strokes[target_page] = []
        self.page_strokes[target_page].append(stroke)

        if target_page not in self.page_history:
            self.page_history[target_page] = []
        self.page_history[target_page].append(("stroke", stroke))

        self.stroke_started.emit(self.current_page, stroke)

    def add_stroke_point(self, norm_x: float, norm_y: float, pressure: float = 1.0):
        if self.current_stroke is not None:
            self.current_stroke.points.append((norm_x, norm_y, pressure))
            page = -1 if self.is_sketchbook else self.current_page
            self.stroke_added_point.emit(page, norm_x, norm_y, pressure)

    def update_line_endpoint(self, norm_x: float, norm_y: float, pressure: float = 1.0):
        if self.current_stroke is not None and len(self.current_stroke.points) >= 1:
            p0 = self.current_stroke.points[0]
            self.current_stroke.points = [p0, (norm_x, norm_y, pressure)]
            page = -1 if self.is_sketchbook else self.current_page
            self.stroke_line_updated.emit(page, norm_x, norm_y)

    def end_stroke(self):
        if self.current_stroke is not None:
            self.current_stroke = None
            page = -1 if self.is_sketchbook else self.current_page
            self.stroke_finished.emit(page)

    def add_text(self, norm_x: float, norm_y: float, text: str) -> TextItem | None:
        """指定した相対座標にテキストアイテムを追加"""
        text = text.strip()
        if not text:
            return None
        size = self.get_tool_size("text")
        color = self.text_color
        item = TextItem(text=text, norm_x=norm_x, norm_y=norm_y, color=color, size=size)

        if self.is_sketchbook:
            self.sketchbook_texts.append(item)
            self.sketchbook_history.append(("text", item))
            self.text_added.emit(-1, item)
            return item

        target_page = self.get_canonical_page(self.current_page)
        if target_page not in self.page_texts:
            self.page_texts[target_page] = []
        self.page_texts[target_page].append(item)

        if target_page not in self.page_history:
            self.page_history[target_page] = []
        self.page_history[target_page].append(("text", item))

        self.text_added.emit(self.current_page, item)
        return item

    def get_texts_for_page(self, page_index: int) -> list[TextItem]:
        """指定ページの全テキストアイテムを取得"""
        if self.is_sketchbook:
            return self.sketchbook_texts
        target_page = self.get_canonical_page(page_index)
        return self.page_texts.get(target_page, [])

    def erase_at_point(
        self,
        norm_x: float,
        norm_y: float,
        threshold: float = 0.02,
        aspect_ratio: float = 1.0,
        override_mode: str | None = None
    ) -> bool:
        """
        消しゴム処理:
        - mode == "stroke" の場合: オブジェクト消しゴム（触れたストローク全体を削除）
        - mode == "partial" の場合: 通常消しゴム（円の範囲内のみを部分消去・分割）
        - テキスト: 消しゴムの円とバウンディングボックスが交差したテキストを削除
        """
        center = (norm_x, norm_y)
        erased_anything = False
        mode = override_mode if override_mode else self.eraser_mode

        if self.is_sketchbook:
            has_strokes = bool(self.sketchbook_strokes)
            has_texts = bool(self.sketchbook_texts)
            if not has_strokes and not has_texts:
                return False

            if has_texts:
                remaining_texts = []
                text_erased = False
                for t_item in self.sketchbook_texts:
                    if text_intersects_circle(t_item, center, threshold, aspect_ratio):
                        text_erased = True
                    else:
                        remaining_texts.append(t_item)
                if text_erased:
                    self.sketchbook_texts = remaining_texts
                    self.texts_updated.emit(-1)
                    erased_anything = True

            if has_strokes:
                if mode == "stroke":
                    remaining_strokes = []
                    erased = False
                    for stroke in self.sketchbook_strokes:
                        if stroke_intersects_circle(stroke, center, threshold, aspect_ratio):
                            erased = True
                        else:
                            remaining_strokes.append(stroke)
                    if erased:
                        self.sketchbook_strokes = remaining_strokes
                        self.strokes_updated.emit(-1)
                        erased_anything = True
                else:
                    new_strokes: list[Stroke] = []
                    erased_any = False
                    for stroke in self.sketchbook_strokes:
                        if not stroke_intersects_circle(stroke, center, threshold, aspect_ratio):
                            new_strokes.append(stroke)
                        else:
                            erased_any = True
                            clipped = clip_stroke_by_circle(stroke, center, threshold, aspect_ratio)
                            new_strokes.extend(clipped)
                    if erased_any:
                        self.sketchbook_strokes = new_strokes
                        self.strokes_updated.emit(-1)
                        erased_anything = True
            return erased_anything

        target_page = self.get_canonical_page(self.current_page)
        has_strokes = target_page in self.page_strokes and bool(self.page_strokes[target_page])
        has_texts = target_page in self.page_texts and bool(self.page_texts[target_page])
        if not has_strokes and not has_texts:
            return False

        # 1. テキスト消去判定
        if has_texts:
            remaining_texts = []
            text_erased = False
            for t_item in self.page_texts[target_page]:
                if text_intersects_circle(t_item, center, threshold, aspect_ratio):
                    text_erased = True
                else:
                    remaining_texts.append(t_item)
            if text_erased:
                self.page_texts[target_page] = remaining_texts
                self.texts_updated.emit(self.current_page)
                erased_anything = True

        # 2. ストローク消去判定
        if has_strokes:
            if mode == "stroke":
                # オブジェクト消しゴム（線分交差判定）
                remaining_strokes = []
                erased = False
                for stroke in self.page_strokes[target_page]:
                    if stroke_intersects_circle(stroke, center, threshold, aspect_ratio):
                        erased = True
                    else:
                        remaining_strokes.append(stroke)
                if erased:
                    self.page_strokes[target_page] = remaining_strokes
                    self.strokes_updated.emit(self.current_page)
                    erased_anything = True
            else:
                # 通常消しゴム（部分消去）
                new_page_strokes: list[Stroke] = []
                erased_any = False

                for stroke in self.page_strokes[target_page]:
                    if not stroke_intersects_circle(stroke, center, threshold, aspect_ratio):
                        new_page_strokes.append(stroke)
                    else:
                        erased_any = True
                        clipped = clip_stroke_by_circle(stroke, center, threshold, aspect_ratio)
                        new_page_strokes.extend(clipped)

                if erased_any:
                    self.page_strokes[target_page] = new_page_strokes
                    self.strokes_updated.emit(self.current_page)
                    erased_anything = True

        return erased_anything

    def erase_strokes_near(self, norm_x: float, norm_y: float, threshold: float = 0.02, aspect_ratio: float = 1.0) -> bool:
        return self.erase_at_point(norm_x, norm_y, threshold, aspect_ratio)

    def clear_current_page_strokes(self):
        if self.is_sketchbook:
            had_strokes = bool(self.sketchbook_strokes)
            had_texts = bool(self.sketchbook_texts)
            if had_strokes:
                self.sketchbook_strokes = []
                self.strokes_cleared.emit(-1)
            if had_texts:
                self.sketchbook_texts = []
                self.texts_updated.emit(-1)
            self.sketchbook_history = []
            return

        target_page = self.get_canonical_page(self.current_page)
        had_strokes = target_page in self.page_strokes and bool(self.page_strokes[target_page])
        had_texts = target_page in self.page_texts and bool(self.page_texts[target_page])

        if had_strokes:
            self.page_strokes[target_page] = []
            self.strokes_cleared.emit(self.current_page)
        if had_texts:
            self.page_texts[target_page] = []
            self.texts_updated.emit(self.current_page)
        if target_page in self.page_history:
            self.page_history[target_page] = []

    def undo_current_page_stroke(self):
        if self.is_sketchbook:
            if self.sketchbook_history:
                kind, item = self.sketchbook_history.pop()
                if kind == "stroke":
                    if item in self.sketchbook_strokes:
                        self.sketchbook_strokes.remove(item)
                        self.stroke_undone.emit(-1)
                elif kind == "text":
                    if item in self.sketchbook_texts:
                        self.sketchbook_texts.remove(item)
                        self.texts_updated.emit(-1)
            elif self.sketchbook_strokes:
                self.sketchbook_strokes.pop()
                self.stroke_undone.emit(-1)
            return

        target_page = self.get_canonical_page(self.current_page)
        if target_page in self.page_history and self.page_history[target_page]:
            kind, item = self.page_history[target_page].pop()
            if kind == "stroke":
                if target_page in self.page_strokes and item in self.page_strokes[target_page]:
                    self.page_strokes[target_page].remove(item)
                    self.stroke_undone.emit(self.current_page)
            elif kind == "text":
                if target_page in self.page_texts and item in self.page_texts[target_page]:
                    self.page_texts[target_page].remove(item)
                    self.texts_updated.emit(self.current_page)
        elif target_page in self.page_strokes and self.page_strokes[target_page]:
            self.page_strokes[target_page].pop()
            self.stroke_undone.emit(self.current_page)

    def undo(self):
        """undo_current_page_stroke のエイリアス"""
        self.undo_current_page_stroke()

    def clear_strokes(self):
        """clear_current_page_strokes のエイリアス"""
        self.clear_current_page_strokes()

    def get_strokes_for_page(self, page_index: int) -> list[Stroke]:
        if self.is_sketchbook:
            return self.sketchbook_strokes
        target_page = self.get_canonical_page(page_index)
        return self.page_strokes.get(target_page, [])
