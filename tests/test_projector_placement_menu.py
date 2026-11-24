import os
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from beamerplus.MainWindow import MainWindow
from beamerplus.Viewer import Viewer, place_viewer_on_screen, place_viewer_windowed


class _FakeScreen:
    def __init__(self, name: str, rect: QRect):
        self._name = name
        self._rect = rect

    def name(self) -> str:
        return self._name

    def geometry(self) -> QRect:
        return QRect(self._rect)

    def availableGeometry(self) -> QRect:
        return QRect(self._rect)


class TestProjectorPlacementMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def test_presentation_menu_contains_projector_placement_submenu(self):
        viewer = Viewer()
        win = MainWindow(viewer=viewer)
        try:
            pres_actions = win.menuBar().actions()
            pres_menu = next(
                (a.menu() for a in pres_actions if "プレゼンテーション" in a.text()),
                None,
            )
            self.assertIsNotNone(pres_menu)
            submenu_texts = [a.text() for a in pres_menu.actions()]
            self.assertIn("プロジェクタウィンドウの配置(&L)", submenu_texts)
            self.assertTrue(win.projector_placement_menu.isEnabled())
        finally:
            win.close()
            viewer.close()

    def test_placement_submenu_disabled_without_viewer(self):
        win = MainWindow(viewer=None)
        try:
            self.assertFalse(win.projector_placement_menu.isEnabled())
        finally:
            win.close()

    def test_populate_menu_lists_all_screens_and_marks_current(self):
        s0 = _FakeScreen("eDP-1", QRect(0, 0, 1920, 1080))
        s1 = _FakeScreen("HDMI-1", QRect(1920, 0, 2560, 1440))

        mock_viewer = MagicMock()
        mock_viewer.isVisible.return_value = True
        mock_viewer.screen.return_value = s1

        win = MainWindow(viewer=mock_viewer)
        try:
            with patch.object(self.app, "screens", return_value=[s0, s1]), patch.object(
                self.app, "primaryScreen", return_value=s1
            ):
                win._populate_projector_placement_menu()

            actions = win.projector_placement_menu.actions()
            texts = [a.text() for a in actions if not a.isSeparator()]
            self.assertEqual(
                texts,
                [
                    "自動配置（サブモニターへ全画面）",
                    "ディスプレイ 1: eDP-1 (1920×1080)",
                    "ディスプレイ 2: HDMI-1 (2560×1440) [メイン]",
                ],
            )

            screen_actions = [a for a in actions if a.isCheckable()]
            self.assertEqual(len(screen_actions), 2)
            self.assertFalse(screen_actions[0].isChecked())
            self.assertTrue(screen_actions[1].isChecked())
        finally:
            win.close()

    def test_menu_actions_place_viewer_on_selected_screen_and_auto(self):
        s0 = _FakeScreen("eDP-1", QRect(0, 0, 1920, 1080))
        s1 = _FakeScreen("HDMI-1", QRect(1920, 0, 2560, 1440))

        mock_viewer = MagicMock()
        mock_viewer.isVisible.return_value = True
        mock_viewer.isFullScreen.return_value = True
        mock_viewer.screen.return_value = s0
        mock_viewer.geometry.return_value = s0.geometry()

        win = MainWindow(viewer=mock_viewer)
        try:
            with patch.object(self.app, "screens", return_value=[s0, s1]), patch.object(
                self.app, "primaryScreen", return_value=s1
            ):
                win._populate_projector_placement_menu()
                screen_actions = [a for a in win.projector_placement_menu.actions() if a.isCheckable()]

                # ディスプレイ 2 (HDMI-1) を選択 -> フルスクリーン解除後に s1 へ配置
                screen_actions[1].trigger()
                mock_viewer.showNormal.assert_called_once_with()
                mock_viewer.setGeometry.assert_called_with(QRect(1920, 0, 2560, 1440))
                mock_viewer.show.assert_called()
                mock_viewer.showFullScreen.assert_called()
                mock_viewer.fit_view.assert_called()

                # 自動配置 -> primary(s1) 以外の最小インデックス s0 へ配置
                mock_viewer.reset_mock()
                mock_viewer.isFullScreen.return_value = True
                mock_viewer.screen.return_value = s1
                mock_viewer.geometry.return_value = s1.geometry()

                auto_action = win.projector_placement_menu.actions()[0]
                auto_action.trigger()
                mock_viewer.showNormal.assert_called_once_with()
                mock_viewer.setGeometry.assert_called_with(QRect(0, 0, 1920, 1080))
                mock_viewer.showFullScreen.assert_called_once_with()
        finally:
            win.close()

    def test_auto_place_single_monitor_falls_back_to_windowed(self):
        s0 = _FakeScreen("eDP-1", QRect(100, 50, 1920, 1080))
        mock_viewer = MagicMock()
        mock_viewer.isFullScreen.return_value = True

        win = MainWindow(viewer=mock_viewer)
        try:
            with patch.object(self.app, "screens", return_value=[s0]), patch.object(
                self.app, "primaryScreen", return_value=s0
            ):
                win.auto_place_viewer()

            mock_viewer.showNormal.assert_called_once_with()
            mock_viewer.setGeometry.assert_called_once_with(660, 290, 800, 600)
            mock_viewer.show.assert_called_once_with()
            mock_viewer.fit_view.assert_called_once_with()
        finally:
            win.close()

    def test_place_viewer_helpers_with_real_viewer(self):
        viewer = Viewer()
        win = MainWindow(viewer=viewer)
        try:
            screen = self.app.primaryScreen()
            self.assertIsNotNone(screen)

            place_viewer_on_screen(viewer, screen, fullscreen=True)
            self.assertTrue(viewer.isVisible())
            self.assertTrue(viewer.isFullScreen())

            place_viewer_windowed(viewer, screen=screen)
            self.assertTrue(viewer.isVisible())
            self.assertFalse(viewer.isFullScreen())
            self.assertEqual(viewer.width(), 800)
            self.assertEqual(viewer.height(), 600)
        finally:
            win.close()
            viewer.close()


if __name__ == "__main__":
    unittest.main()
