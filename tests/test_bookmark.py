import unittest
import os
import pymupdf
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from beamerplus.PdfReader import PdfReader
from beamerplus.BookmarkDrawer import BookmarkDrawer


class TestBookmarkFunctionality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # GUIテスト用のQApplication（ヘッドレスで実行）
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        cls.app = QApplication.instance() or QApplication([])

    def test_pdf_reader_toc_extraction(self):
        pdf_path = "beamerplus/slides.pdf"
        self.assertTrue(os.path.exists(pdf_path), "Sample slides.pdf must exist")

        reader = PdfReader(pdf_path)
        self.assertTrue(reader.has_toc)
        self.assertGreater(len(reader.toc), 0)

        # 最初のしおり項目の型と内容を確認
        lvl, title, page_idx = reader.toc[0]
        self.assertIsInstance(lvl, int)
        self.assertIsInstance(title, str)
        self.assertIsInstance(page_idx, int)
        self.assertEqual(lvl, 1)
        self.assertEqual(title, "1 離散時間マルコフ連鎖")
        self.assertEqual(page_idx, 2)  # 1-indexedの3ページ目は 0-indexed で 2

    def test_pdf_reader_without_toc(self):
        # しおりのない一時PDFを作成して検証
        tmp_pdf_path = "tests_no_toc.pdf"
        try:
            doc = pymupdf.open()
            doc.new_page()
            doc.save(tmp_pdf_path)
            doc.close()

            reader = PdfReader(tmp_pdf_path)
            self.assertFalse(reader.has_toc)
            self.assertEqual(reader.toc, [])
        finally:
            if os.path.exists(tmp_pdf_path):
                os.remove(tmp_pdf_path)

    def test_bookmark_drawer_tree_population_and_signals(self):
        reader = PdfReader("beamerplus/slides.pdf")
        drawer = BookmarkDrawer()

        # PDFロード前
        self.assertFalse(drawer.has_bookmarks)
        self.assertFalse(drawer.isVisible())

        # PDFロード後
        drawer.load_pdf(reader)
        self.assertTrue(drawer.has_bookmarks)
        self.assertFalse(drawer.is_open)

        # ツリーのトップレベルアイテム数を確認
        self.assertGreater(drawer.tree.topLevelItemCount(), 0)
        first_top = drawer.tree.topLevelItem(0)
        self.assertEqual(first_top.text(0), "1 離散時間マルコフ連鎖")
        self.assertEqual(first_top.data(0, Qt.ItemDataRole.UserRole), 2)
        # 子要素の確認（例: 1.1 基本的な表記）
        self.assertGreater(first_top.childCount(), 0)
        first_child = first_top.child(0)
        self.assertEqual(first_child.text(0), "1.1 基本的な表記")

        # ページ選択シグナルの発火テスト
        received_pages = []
        drawer.page_selected.connect(lambda p: received_pages.append(p))
        drawer._on_item_clicked(first_child, 0)
        self.assertEqual(received_pages, [2])

    def test_bookmark_drawer_toggle_and_tab_symbol(self):
        reader = PdfReader("beamerplus/slides.pdf")
        drawer = BookmarkDrawer()
        state_history = []
        drawer.drawer_state_changed.connect(lambda s: state_history.append(s))
        drawer.load_pdf(reader)

        # 初期状態: 閉
        self.assertFalse(drawer.is_open)

        # 開く
        drawer.open_drawer()
        self.assertTrue(drawer.is_open)
        self.assertTrue(state_history[-1])

        # 閉じる
        drawer.close_drawer()
        self.assertFalse(drawer.is_open)
        self.assertFalse(state_history[-1])

        # トグル
        drawer.toggle_drawer()
        self.assertTrue(drawer.is_open)
        drawer.toggle_drawer()
        self.assertFalse(drawer.is_open)

    def test_bookmark_drawer_search_filter(self):
        reader = PdfReader("beamerplus/slides.pdf")
        drawer = BookmarkDrawer()
        drawer.load_pdf(reader)

        # 検索フィルタ: "斉時"
        drawer._filter_tree("斉時")
        first_top = drawer.tree.topLevelItem(0)
        # マッチする子（1.2 斉時なマルコフ連鎖）を持つ親は表示される
        self.assertFalse(first_top.isHidden())
        # マッチしない子（1.1 基本的な表記）は隠れる
        self.assertTrue(first_top.child(0).isHidden())
        # マッチする子（1.2 斉時なマルコフ連鎖）は表示される
        self.assertFalse(first_top.child(1).isHidden())

        # 検索クリア
        drawer._filter_tree("")
        self.assertFalse(first_top.child(0).isHidden())
        self.assertFalse(first_top.child(1).isHidden())

    def test_bookmark_drawer_highlight_current_page(self):
        reader = PdfReader("beamerplus/slides.pdf")
        drawer = BookmarkDrawer()
        drawer.load_pdf(reader)

        # ページ4 (index 3: 1.2 斉時なマルコフ連鎖) に移動したときのハイライト
        drawer.highlight_current_page(3)
        current_item = drawer.tree.currentItem()
        self.assertIsNotNone(current_item)
        self.assertEqual(current_item.text(0), "1.2 斉時なマルコフ連鎖")


    def test_mainwindow_sidebar_button_layout_and_interaction(self):
        from beamerplus.MainWindow import MainWindow
        win = MainWindow()
        win.resize(1000, 700)
        win.show()

        # PDFロード前: 非表示
        self.assertFalse(win.btn_sidebar_drawer.isVisible())

        # PDFロード後: 表示され、高さがcentral_widgetと一致
        win.load_pdf("beamerplus/slides.pdf")
        self.app.processEvents()
        self.assertTrue(win.btn_sidebar_drawer.isVisible())
        self.assertEqual(win.btn_sidebar_drawer.text(), ">")
        self.assertEqual(win.btn_sidebar_drawer.width(), 20)
        self.assertEqual(win.btn_sidebar_drawer.height(), win.centralWidget().height())

        # クリックして開く
        win.btn_sidebar_drawer.click()
        win.bookmark_drawer.animation.setCurrentTime(win.bookmark_drawer.animation.duration())
        self.app.processEvents()
        self.assertTrue(win.bookmark_drawer.is_open)
        self.assertEqual(win.bookmark_drawer.pos().x(), 0)

        # 外側（スライド領域など）をクリックして閉じるテスト（スライドが進まないこと）
        from PySide6.QtGui import QMouseEvent
        canvas = win.slide_view.canvas
        target_pos = canvas.mapToGlobal(canvas.rect().center())
        press_ev = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            canvas.mapFromGlobal(target_pos),
            target_pos,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier
        )
        self.app.sendEvent(canvas, press_ev)
        self.assertFalse(win.bookmark_drawer.is_open)
        self.assertEqual(win.controller.current_page, 0)


if __name__ == "__main__":
    unittest.main()
