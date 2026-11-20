import os
import unittest

from PySide6.QtCore import Qt, QPointF, QEvent
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QPixmap, QColor, QTabletEvent, QPointingDevice
from PySide6.QtWidgets import QApplication

from beamerplus.DrawingModel import (
    LASER_TRAIL_FADE_DURATION,
    LASER_TRAIL_GRACE_DURATION,
    LaserTrailManager,
    PresentationController,
    draw_laser_trails,
)
from beamerplus.MainWindow import MainWindow
from beamerplus.SlideCanvas import SlideCanvas
from beamerplus.Viewer import Viewer
from beamerplus.WebServer import HTML_CONTENT


class TestLaserTrailManager(unittest.TestCase):
    def test_single_stroke_never_disappears_while_pressed(self):
        mgr = LaserTrailManager(grace_duration=1.0, fade_duration=0.25)

        self.assertTrue(mgr.update(0.1, 0.1, True, True, now=0.0))
        self.assertTrue(mgr.update(0.2, 0.2, True, True, now=0.5))
        # 従来の 0.45 秒を大きく超えて 10 秒間ドラッグし続けても、最初の点も消えない
        self.assertTrue(mgr.update(0.3, 0.3, True, True, now=5.0))
        self.assertTrue(mgr.update(0.4, 0.4, True, True, now=10.0))
        self.assertTrue(mgr.tick(now=10.5))

        trails = mgr.get_render_trails(now=10.5)
        self.assertEqual(len(trails), 1)
        pts, alpha = trails[0]
        self.assertEqual(pts, [(0.1, 0.1), (0.2, 0.2), (0.3, 0.3), (0.4, 0.4)])
        self.assertEqual(alpha, 1.0)

    def test_duplicate_stationary_points_filtered(self):
        mgr = LaserTrailManager()
        mgr.update(0.5, 0.5, True, True, now=0.0)
        mgr.update(0.5, 0.5, True, True, now=1.0)
        mgr.update(0.50005, 0.50005, True, True, now=2.0)
        mgr.update(0.6, 0.6, True, True, now=3.0)

        trails = mgr.get_render_trails(now=3.0)
        self.assertEqual(len(trails), 1)
        self.assertEqual(trails[0][0], [(0.5, 0.5), (0.6, 0.6)])

    def test_continuous_multi_stroke_remains_visible_within_grace_period(self):
        mgr = LaserTrailManager()

        # 1画目
        mgr.update(0.1, 0.1, True, True, now=0.0)
        mgr.update(0.2, 0.1, True, True, now=0.2)
        mgr.update(0.2, 0.1, True, False, now=0.3)

        # 猶予時間内 (0.35秒後) は1画目が不透明度 1.0 のまま保持される
        self.assertTrue(mgr.tick(now=0.65))
        trails = mgr.get_render_trails(now=0.65)
        self.assertEqual(len(trails), 1)
        self.assertEqual(trails[0][1], 1.0)

        # iPad などで指を離し visible=False になっても猶予は維持される
        self.assertTrue(mgr.update(0.0, 0.0, False, False, now=0.70))
        trails = mgr.get_render_trails(now=0.75)
        self.assertEqual(len(trails), 1)
        self.assertEqual(trails[0][1], 1.0)

        # 0.5秒以内 (0.45秒後 = t=0.75) に2画目を開始し、さらに2秒間押し続ける
        mgr.update(0.3, 0.3, True, True, now=0.75)
        mgr.update(0.4, 0.4, True, True, now=2.75)

        # 2画目を描いている間も1画目・2画目ともに 100% 表示のまま
        trails = mgr.get_render_trails(now=2.75)
        self.assertEqual(len(trails), 2)
        self.assertEqual(trails[0], ([(0.1, 0.1), (0.2, 0.1)], 1.0))
        self.assertEqual(trails[1], ([(0.3, 0.3), (0.4, 0.4)], 1.0))

        # 2画目を離す (t=3.0)
        mgr.update(0.4, 0.4, True, False, now=3.0)
        # 離して 0.49秒後 (t=3.49) はまだ両方とも 1.0
        trails = mgr.get_render_trails(now=3.49)
        self.assertEqual(len(trails), 2)
        self.assertTrue(all(alpha == 1.0 for _, alpha in trails))

        # 0.5秒経過後、フェード途中 (t=3.625 = fade開始から 0.125s -> alpha=0.5)
        trails = mgr.get_render_trails(now=3.625)
        self.assertEqual(len(trails), 2)
        self.assertAlmostEqual(trails[0][1], 0.5, places=6)
        self.assertAlmostEqual(trails[1][1], 0.5, places=6)

        # フェード完了 (t=3.75 = 3.0 + 0.5 + 0.25) で消滅
        self.assertFalse(mgr.tick(now=3.75))
        self.assertEqual(mgr.get_render_trails(now=3.75), [])

    def test_single_point_tap_preserved_for_punctuation_and_dots(self):
        mgr = LaserTrailManager()

        # 「i」の縦棒を描いたあと、離れた位置に点だけタップする
        mgr.update(0.5, 0.4, True, True, now=0.0)
        mgr.update(0.5, 0.6, True, True, now=0.1)
        mgr.update(0.5, 0.6, True, False, now=0.15)

        mgr.update(0.5, 0.3, True, True, now=0.4)
        mgr.update(0.5, 0.3, True, False, now=0.45)

        trails = mgr.get_render_trails(now=0.8)
        self.assertEqual(len(trails), 2)
        self.assertEqual(trails[0], ([(0.5, 0.4), (0.5, 0.6)], 1.0))
        self.assertEqual(trails[1], ([(0.5, 0.3)], 1.0))

    def test_new_stroke_during_fade_revives_existing_trails(self):
        mgr = LaserTrailManager()

        # 1画目を描いて離す (t=0.2)
        mgr.update(0.1, 0.1, True, True, now=0.0)
        mgr.update(0.2, 0.2, True, True, now=0.1)
        mgr.update(0.2, 0.2, True, False, now=0.2)

        # 猶予 0.5s を過ぎてフェード中 (t=0.80 -> elapsed=0.60 -> alpha=0.6) を確認
        trails = mgr.get_render_trails(now=0.80)
        self.assertEqual(len(trails), 1)
        self.assertAlmostEqual(trails[0][1], 0.6, places=6)

        # フェード中に2画目を開始 (t=0.85) すると、1画目も不透明度 1.0 に復活する
        mgr.update(0.7, 0.7, True, True, now=0.85)
        mgr.update(0.8, 0.8, True, True, now=1.20)

        trails = mgr.get_render_trails(now=1.20)
        self.assertEqual(len(trails), 2)
        self.assertEqual(trails[0], ([(0.1, 0.1), (0.2, 0.2)], 1.0))
        self.assertEqual(trails[1], ([(0.7, 0.7), (0.8, 0.8)], 1.0))

        # 2画目を離して 0.5s + 0.25s 経過後には両方とも消滅
        mgr.update(0.8, 0.8, True, False, now=1.30)
        self.assertEqual(mgr.get_render_trails(now=2.05), [])

        # 完全に消滅した後 (t=2.10) に新規書き込みしても、過去の軌跡は復活しない
        mgr.update(0.3, 0.3, True, True, now=2.10)
        trails = mgr.get_render_trails(now=2.10)
        self.assertEqual(trails, [([(0.3, 0.3)], 1.0)])


class TestLaserTrailWidgets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        cls.app = QApplication.instance() or QApplication([])

    def _create_controller_and_pixmap(self):
        controller = PresentationController()
        controller.set_total_pages(3)
        controller.set_tool("pointer")
        pixmap = QPixmap(800, 600)
        pixmap.fill(QColor("white"))
        return controller, pixmap

    def test_slide_canvas_mouse_multi_stroke_and_clearing(self):
        controller, pixmap = self._create_controller_and_pixmap()
        canvas = SlideCanvas(controller)
        canvas.resize(800, 600)
        canvas.set_pixmap(pixmap)

        def mouse_ev(ev_type, x, y, btn, btns):
            return QMouseEvent(
                ev_type,
                QPointF(x, y),
                QPointF(x, y),
                btn,
                btns,
                Qt.KeyboardModifier.NoModifier,
            )

        # 1画目: Press -> Move -> Release (以前の is_pointer_down 先行更新バグが直っていること)
        canvas.mousePressEvent(mouse_ev(QEvent.Type.MouseButtonPress, 100, 100, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
        canvas.mouseMoveEvent(mouse_ev(QEvent.Type.MouseMove, 200, 150, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
        self.assertTrue(canvas.trail_manager.is_down)
        self.assertEqual(len(canvas.trail_manager.current_trail), 2)

        canvas.mouseReleaseEvent(mouse_ev(QEvent.Type.MouseButtonRelease, 200, 150, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
        self.assertFalse(canvas.trail_manager.is_down)
        self.assertEqual(len(canvas.trail_manager.current_trail), 0)
        self.assertEqual(len(canvas.trail_manager.held_trails), 1)

        # ホバー移動や領域外離脱 (leaveEvent) でも保持中の軌跡が消えないこと
        canvas.mouseMoveEvent(mouse_ev(QEvent.Type.MouseMove, 300, 300, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton))
        canvas.leaveEvent(QEvent(QEvent.Type.Leave))
        self.assertEqual(len(canvas.trail_manager.held_trails), 1)

        # 2画目: 再び Press -> Move -> Release で2本とも保持される
        canvas.mousePressEvent(mouse_ev(QEvent.Type.MouseButtonPress, 400, 200, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
        canvas.mouseMoveEvent(mouse_ev(QEvent.Type.MouseMove, 500, 250, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton))
        canvas.mouseReleaseEvent(mouse_ev(QEvent.Type.MouseButtonRelease, 500, 250, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
        self.assertEqual(len(canvas.trail_manager.held_trails), 2)

        # ページ切替時は即座にクリアされる
        controller.set_page(1)
        self.assertFalse(canvas.trail_manager.has_active_trails())
        self.assertFalse(canvas.trail_timer.isActive())

        # ツール切替・暗転時もクリアされる
        canvas.mousePressEvent(mouse_ev(QEvent.Type.MouseButtonPress, 120, 120, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton))
        canvas.mouseReleaseEvent(mouse_ev(QEvent.Type.MouseButtonRelease, 120, 120, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton))
        self.assertTrue(canvas.trail_manager.has_active_trails())
        controller.set_tool("pen")
        self.assertFalse(canvas.trail_manager.has_active_trails())

    def test_viewer_receives_multi_stroke_and_clears_on_page_or_tool_change(self):
        controller, pixmap = self._create_controller_and_pixmap()
        viewer = Viewer(controller)
        viewer.show_image(pixmap)

        # 1画目
        controller.update_pointer(0.2, 0.2, True, True)
        controller.update_pointer(0.4, 0.2, True, True)
        controller.update_pointer(0.4, 0.2, True, False)
        # iPad 離し相当で visible=False
        controller.update_pointer(0.0, 0.0, False, False)
        self.assertFalse(viewer.pointer_item.isVisible())
        self.assertEqual(len(viewer.trail_manager.held_trails), 1)

        # 2画目（単一点）
        controller.update_pointer(0.6, 0.6, True, True)
        controller.update_pointer(0.6, 0.6, True, False)
        self.assertEqual(len(viewer.trail_manager.held_trails), 2)

        # ページ送り時はクリアされる
        controller.set_page(1)
        self.assertFalse(viewer.trail_manager.has_active_trails())

        # スケッチブック切替時・暗転時もクリアされる
        controller.update_pointer(0.3, 0.3, True, True)
        controller.update_pointer(0.5, 0.5, True, True)
        controller.update_pointer(0.5, 0.5, True, False)
        self.assertTrue(viewer.trail_manager.has_active_trails())
        controller.set_sketchbook(True)
        self.assertFalse(viewer.trail_manager.has_active_trails())

    def test_draw_laser_trails_renders_line_and_single_dot(self):
        img = QImage(200, 200, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.black)
        painter = QPainter(img)
        draw_laser_trails(
            painter,
            [
                ([(0.2, 0.2), (0.8, 0.2)], 1.0),
                ([(0.5, 0.7)], 1.0),
            ],
            0.0,
            0.0,
            200.0,
            200.0,
        )
        painter.end()

        # 線上のピクセル (x=100, y=40) と単一点のピクセル (x=100, y=140) が赤く描画されていること
        line_pixel = img.pixelColor(100, 40)
        dot_pixel = img.pixelColor(100, 140)
        self.assertGreater(line_pixel.red(), 150)
        self.assertGreater(dot_pixel.red(), 150)

    def test_default_constants_match_spec(self):
        self.assertEqual(LASER_TRAIL_GRACE_DURATION, 0.5)
        self.assertEqual(LASER_TRAIL_FADE_DURATION, 0.25)

    def test_slide_canvas_tablet_release_ends_stroke_without_dropping_trail(self):
        controller, pixmap = self._create_controller_and_pixmap()
        canvas = SlideCanvas(controller)
        canvas.resize(800, 600)
        canvas.set_pixmap(pixmap)

        device = QPointingDevice.primaryPointingDevice()

        def tab_ev(ev_type, x, y, pressure, buttons):
            return QTabletEvent(
                ev_type,
                device,
                QPointF(x, y),
                QPointF(x, y),
                pressure,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                Qt.KeyboardModifier.NoModifier,
                Qt.MouseButton.LeftButton if buttons else Qt.MouseButton.NoButton,
                buttons,
            )

        canvas.tabletEvent(tab_ev(QEvent.Type.TabletPress, 100, 100, 0.5, Qt.MouseButton.LeftButton))
        canvas.tabletEvent(tab_ev(QEvent.Type.TabletMove, 220, 150, 0.6, Qt.MouseButton.LeftButton))
        self.assertTrue(canvas.trail_manager.is_down)
        self.assertEqual(len(canvas.trail_manager.current_trail), 2)

        # TabletRelease はわずかな残圧があっても必ずリリース扱いになること
        canvas.tabletEvent(tab_ev(QEvent.Type.TabletRelease, 220, 150, 0.1, Qt.MouseButton.NoButton))
        self.assertFalse(canvas.trail_manager.is_down)
        self.assertEqual(len(canvas.trail_manager.held_trails), 1)

    def test_mainwindow_does_not_echo_web_pointer_back(self):
        win = MainWindow()
        try:
            broadcasted = []
            win.web_server.broadcast = lambda msg: broadcasted.append(msg)

            # Web(iPad)側からの受信時は Web クライアントへエコーバックしない
            win._on_web_pointer_updated(0.3, 0.4, True, True)
            self.assertEqual(broadcasted, [])
            self.assertTrue(win.slide_view.canvas.trail_manager.is_down)

            # PC側からの更新は Web クライアントへ配信される
            win.controller.update_pointer(0.5, 0.6, True, True)
            self.assertEqual(len(broadcasted), 1)
            self.assertEqual(broadcasted[0]["type"], "pointer_updated")
            self.assertEqual(broadcasted[0]["isDown"], True)
        finally:
            win.close()

    def test_web_html_trail_logic_constants(self):
        self.assertIn("const TRAIL_GRACE_MS = 500;", HTML_CONTENT)
        self.assertIn("const TRAIL_FADE_MS = 250;", HTML_CONTENT)
        self.assertIn("function clearPointerTrails()", HTML_CONTENT)
        self.assertIn("function pruneExpiredHeldTrails(now)", HTML_CONTENT)
        self.assertNotIn("fadingTrails", HTML_CONTENT)
        self.assertNotIn("fadingBatches", HTML_CONTENT)


if __name__ == "__main__":
    unittest.main()
