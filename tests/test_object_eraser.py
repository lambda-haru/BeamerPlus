import os
import unittest

from PySide6.QtCore import Qt, QPointF, QEvent
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication

from beamerplus.DrawingModel import (
    OBJECT_ERASER_HIT_RADIUS,
    PresentationController,
)
from beamerplus.SimpleView import SimpleView
from beamerplus.SlideCanvas import SlideCanvas
from beamerplus.WebServer import HTML_CONTENT


class TestObjectEraserNoSize(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        cls.app = QApplication.instance() or QApplication([])

    def _make_controller(self) -> PresentationController:
        c = PresentationController()
        c.set_total_pages(2)
        return c

    def test_simple_view_disables_size_slider_in_object_eraser_mode(self):
        controller = self._make_controller()
        view = SimpleView(controller)

        # 通常消しゴム選択時はスライダーが有効で「半径:」表示
        controller.set_tool("eraser")
        self.assertTrue(view.size_slider.isEnabled())
        self.assertEqual(view.lbl_size_title.text(), "半径:")
        self.assertEqual(view.lbl_size_val.text(), str(controller.get_tool_size("eraser")))

        # オブジェクト消しゴムへ切替えるとサイズスライダーは無効化され「-」表示
        controller.toggle_eraser_mode()
        self.assertEqual(controller.eraser_mode, "stroke")
        self.assertFalse(view.size_slider.isEnabled())
        self.assertEqual(view.lbl_size_title.text(), "サイズ:")
        self.assertEqual(view.lbl_size_val.text(), "-")

        # 他のツールへ移って再び消しゴムに戻しても、オブジェクトモードのままなら無効維持
        controller.set_tool("pen")
        self.assertTrue(view.size_slider.isEnabled())
        controller.set_tool("eraser")
        self.assertFalse(view.size_slider.isEnabled())
        self.assertEqual(view.lbl_size_val.text(), "-")

        # モデル側で eraser サイズ変更通知が来てもオブジェクトモード中は「-」のまま
        controller.set_tool_size("eraser", 80)
        self.assertFalse(view.size_slider.isEnabled())
        self.assertEqual(view.lbl_size_val.text(), "-")

        # 通常消しゴムへ戻すとスライダーが再有効化され数値が戻る
        controller.toggle_eraser_mode()
        self.assertEqual(controller.eraser_mode, "partial")
        self.assertTrue(view.size_slider.isEnabled())
        self.assertEqual(view.lbl_size_title.text(), "半径:")
        self.assertEqual(view.lbl_size_val.text(), "80")

    def test_object_eraser_hit_testing_is_independent_of_eraser_size(self):
        for is_sketchbook in (False, True):
            controller = self._make_controller()
            controller.set_sketchbook(is_sketchbook)
            controller.set_eraser_mode("stroke")

            # 細いペンストローク (y = 0.5) と太い蛍光ペン (y = 0.8) とテキスト (x=0.2, y=0.2)
            controller.set_tool("pen")
            controller.begin_custom_stroke("#e53935", 0.005, 0.85, 0.2, 0.5)
            controller.add_stroke_point(0.8, 0.5)
            controller.end_stroke()

            controller.begin_custom_stroke("#ffeb3b", 0.045, 0.28, 0.2, 0.8)
            controller.add_stroke_point(0.8, 0.8)
            controller.end_stroke()

            controller.add_text(0.2, 0.2, "Hello")

            # 巨大な threshold=0.20 を渡しても、オブジェクトモードでは固定半径外 (0.05 離れた位置) は消えない
            erased = controller.erase_at_point(0.5, 0.55, threshold=0.20, aspect_ratio=1.0)
            self.assertFalse(erased)
            self.assertEqual(len(controller.get_strokes_for_page(0)), 2)
            self.assertEqual(len(controller.get_texts_for_page(0)), 1)

            # 極小の threshold=0.0001 を渡しても、固定ヒット半径内 (0.008 離れた位置) なら消える
            erased = controller.erase_at_point(0.5, 0.5 + min(0.008, OBJECT_ERASER_HIT_RADIUS * 0.8), threshold=0.0001, aspect_ratio=1.0)
            self.assertTrue(erased)
            self.assertEqual(len(controller.get_strokes_for_page(0)), 1)

            # 太い蛍光ペンの端付近 (中心線から 0.025 離れ、width_ratio*0.5 + HIT_RADIUS 内) でも消える
            erased = controller.erase_at_point(0.5, 0.775, threshold=0.0001, aspect_ratio=1.0)
            self.assertTrue(erased)
            self.assertEqual(len(controller.get_strokes_for_page(0)), 0)

            # テキストも巨大な threshold=0.20 に引きずられず、離れた位置 (y=0.35) では消えない
            self.assertFalse(controller.erase_at_point(0.22, 0.35, threshold=0.20, aspect_ratio=1.0))
            self.assertEqual(len(controller.get_texts_for_page(0)), 1)

            # テキスト位置付近なら極小 threshold でも消える
            self.assertTrue(controller.erase_at_point(0.21, 0.21, threshold=0.0001, aspect_ratio=1.0))
            self.assertEqual(len(controller.get_texts_for_page(0)), 0)

    def test_slide_canvas_draws_circle_only_in_partial_eraser_mode(self):
        controller = self._make_controller()
        controller.set_tool("eraser")
        controller.set_tool_size("eraser", 100)
        canvas = SlideCanvas(controller)
        canvas.resize(400, 400)
        pixmap = QPixmap(400, 400)
        pixmap.fill(QColor("white"))
        canvas.set_pixmap(pixmap)

        move_ev = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(200, 200),
            QPointF(200, 200),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        canvas.mouseMoveEvent(move_ev)

        # 通常モード (size=100 -> r=40px): 円周上 (240, 200) に赤系の消しゴム枠が描画される
        img_partial = QImage(400, 400, QImage.Format.Format_ARGB32)
        canvas.render(img_partial)
        border_px = img_partial.pixelColor(240, 200)
        self.assertLess(border_px.green(), 240)

        # オブジェクトモード: サイズ円は描画されないため (240, 200) も (200, 200) も白のまま
        controller.set_eraser_mode("stroke")
        img_stroke = QImage(400, 400, QImage.Format.Format_ARGB32)
        canvas.render(img_stroke)
        self.assertEqual(img_stroke.pixelColor(240, 200), QColor("white"))
        self.assertEqual(img_stroke.pixelColor(200, 200), QColor("white"))

    def test_web_ui_disables_size_slider_in_object_eraser_mode(self):
        self.assertIn("#size-wrapper.disabled", HTML_CONTENT)
        self.assertIn("id=\"size-label\"", HTML_CONTENT)
        self.assertIn("currentTool === 'eraser' && eraserMode === 'stroke'", HTML_CONTENT)
        self.assertIn("updateEraserModeUI();\n      updateSizeUI();", HTML_CONTENT)
        # モード切替用ドロップダウンを開く必要があるため、トリガー自体は有効のまま維持する
        self.assertIn("colorWrapper.disabled = false;", HTML_CONTENT)

    def test_web_ui_style_popover_combines_color_size_and_modes(self):
        ui_bar_html = HTML_CONTENT.split('<div id="ui-bar">', 1)[1].split('<button id="ui-toggle">', 1)[0]
        popover_html = HTML_CONTENT.split('<div id="style-popover"', 1)[1].split('<script>', 1)[0]

        # ツールバー内には色トリガーのみ置き、サイズ・消しゴムモード・ポインタ軌跡切替はインライン配置しない
        self.assertIn('id="color-picker-wrapper"', ui_bar_html)
        self.assertNotIn('id="size-slider"', ui_bar_html)
        self.assertNotIn('id="btn-eraser-mode"', ui_bar_html)
        self.assertNotIn('id="btn-pointer-trail"', ui_bar_html)

        # ドロップダウンポップオーバー内に色選択・太さ・消しゴムモード・ポインタ軌跡切替をまとめる
        self.assertIn('id="popover-color-section"', popover_html)
        self.assertIn('class="color-swatch"', popover_html)
        self.assertIn('id="native-color-picker"', popover_html)
        self.assertIn('id="popover-eraser-mode-section"', popover_html)
        self.assertIn('id="btn-eraser-partial"', popover_html)
        self.assertIn('id="btn-eraser-stroke"', popover_html)
        self.assertIn('id="popover-pointer-trail-section"', popover_html)
        self.assertIn('id="btn-pointer-trail-on"', popover_html)
        self.assertIn('id="btn-pointer-trail-off"', popover_html)
        self.assertIn('id="size-wrapper"', popover_html)
        self.assertIn('id="size-slider"', popover_html)
        self.assertIn('id="size-preview-dot"', popover_html)
        self.assertIn("function openStylePopover()", HTML_CONTENT)
        self.assertIn("function closeStylePopover()", HTML_CONTENT)
        self.assertIn("function updateStyleTriggerUI()", HTML_CONTENT)


if __name__ == "__main__":
    unittest.main()
