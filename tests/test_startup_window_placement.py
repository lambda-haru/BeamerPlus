import os
import unittest
from unittest.mock import MagicMock

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from beamerplus.MainWindow import MainWindow
from beamerplus.Viewer import Viewer
from beamerplus.main import place_startup_windows, select_startup_screens


class _FakeScreen:
    def __init__(self, name: str, rect: QRect):
        self.name = name
        self._rect = rect

    def geometry(self) -> QRect:
        return QRect(self._rect)


class _FakeApp:
    def __init__(self, screens: list[_FakeScreen], primary_screen: _FakeScreen | None):
        self._screens = screens
        self._primary_screen = primary_screen

    def screens(self) -> list[_FakeScreen]:
        return list(self._screens)

    def primaryScreen(self) -> _FakeScreen | None:
        return self._primary_screen


class TestStartupWindowPlacement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def test_select_screens_single_monitor(self):
        s0 = _FakeScreen("eDP-1", QRect(0, 0, 1920, 1080))
        app = _FakeApp([s0], s0)

        primary, projector = select_startup_screens(app)
        self.assertIs(primary, s0)
        self.assertIsNone(projector)

    def test_select_screens_dual_monitor_primary_first(self):
        s0 = _FakeScreen("eDP-1", QRect(0, 0, 1920, 1080))
        s1 = _FakeScreen("HDMI-1", QRect(1920, 0, 1920, 1080))
        app = _FakeApp([s0, s1], s0)

        primary, projector = select_startup_screens(app)
        self.assertIs(primary, s0)
        self.assertIs(projector, s1)

    def test_select_screens_dual_monitor_primary_second(self):
        # screens[0] ではなく primaryScreen (screens[1]) が発表者画面になり、
        # primary 以外の最小インデックス screens[0] がプロジェクタ画面になること
        s0 = _FakeScreen("eDP-1", QRect(0, 0, 1920, 1080))
        s1 = _FakeScreen("HDMI-1", QRect(1920, 0, 2560, 1440))
        app = _FakeApp([s0, s1], s1)

        primary, projector = select_startup_screens(app)
        self.assertIs(primary, s1)
        self.assertIs(projector, s0)

    def test_select_screens_triple_monitor_picks_smallest_non_primary_index(self):
        s0 = _FakeScreen("DP-1", QRect(0, 0, 1920, 1080))
        s1 = _FakeScreen("HDMI-1", QRect(1920, 0, 2560, 1440))
        s2 = _FakeScreen("eDP-1", QRect(4480, 0, 1920, 1080))
        app = _FakeApp([s0, s1, s2], s1)

        primary, projector = select_startup_screens(app)
        self.assertIs(primary, s1)
        self.assertIs(projector, s0)

    def test_select_screens_no_primary_screen(self):
        s0 = _FakeScreen("eDP-1", QRect(0, 0, 1920, 1080))
        app = _FakeApp([s0], None)

        primary, projector = select_startup_screens(app)
        self.assertIsNone(primary)
        self.assertIsNone(projector)

    def test_place_windows_multi_monitor_uses_primary_and_smallest_non_primary(self):
        s0 = _FakeScreen("eDP-1", QRect(0, 0, 1920, 1080))
        s1 = _FakeScreen("HDMI-1", QRect(1920, 0, 2560, 1440))
        s2 = _FakeScreen("DP-2", QRect(4480, 0, 1280, 720))
        app = _FakeApp([s0, s1, s2], s1)

        viewer = MagicMock()
        main_window = MagicMock()

        place_startup_windows(app, viewer, main_window)

        viewer.setGeometry.assert_called_once_with(QRect(0, 0, 1920, 1080))
        viewer.show.assert_called_once_with()
        viewer.showFullScreen.assert_called_once_with()
        viewer.resize.assert_not_called()

        main_window.setGeometry.assert_called_once_with(
            QRect(1970, 50, 2460, 1340)
        )
        main_window.show.assert_called_once_with()

    def test_place_windows_single_monitor_fallback(self):
        s0 = _FakeScreen("eDP-1", QRect(0, 0, 1920, 1080))
        app = _FakeApp([s0], s0)

        viewer = MagicMock()
        main_window = MagicMock()

        place_startup_windows(app, viewer, main_window)

        viewer.resize.assert_called_once_with(800, 600)
        viewer.show.assert_called_once_with()
        viewer.showFullScreen.assert_not_called()
        viewer.setGeometry.assert_not_called()

        main_window.setGeometry.assert_not_called()
        main_window.show.assert_called_once_with()

    def test_place_windows_with_real_qt_widgets(self):
        viewer = Viewer()
        main_window = MainWindow(viewer=viewer)
        try:
            place_startup_windows(self.app, viewer, main_window)
            self.assertTrue(viewer.isVisible())
            self.assertTrue(main_window.isVisible())
            if len(self.app.screens()) <= 1:
                self.assertEqual(viewer.width(), 800)
                self.assertEqual(viewer.height(), 600)
                self.assertFalse(viewer.isFullScreen())
        finally:
            main_window.close()
            viewer.close()


if __name__ == "__main__":
    unittest.main()
