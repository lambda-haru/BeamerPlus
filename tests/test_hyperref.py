import unittest
import os
import tempfile
import pymupdf

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QMouseEvent, QPixmap, QColor
from PySide6.QtCore import Qt, QPointF, QEvent

from beamerplus.PdfReader import PdfReader, PdfLink
from beamerplus.DrawingModel import PresentationController
from beamerplus.SlideCanvas import SlideCanvas
from beamerplus.Viewer import Viewer


class TestHyperrefFunctionality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        cls.app = QApplication.instance() or QApplication([])

    def test_extract_links_from_real_beamer_pdf(self):
        pdf_path = "beamerplus/slides.pdf"
        self.assertTrue(os.path.exists(pdf_path), "Sample slides.pdf must exist")

        reader = PdfReader(pdf_path)
        # ページ1のリンク確認（ページ右下のナビゲーションリンク）
        links_p1 = reader.get_page_links(1, auto_crop=True)
        self.assertGreater(len(links_p1), 0)
        l0 = links_p1[0]
        self.assertEqual(l0.page, 55)
        self.assertIn("55", l0.tooltip)
        # 正規化座標が [0.0, 1.0] の範囲に収まっていること
        x0, y0, x1, y1 = l0.rect
        self.assertTrue(0.0 <= x0 <= x1 <= 1.0)
        self.assertTrue(0.0 <= y0 <= y1 <= 1.0)

        # ページ3のリンク確認（定理参照等のリンク）
        links_p3 = reader.get_page_links(3, auto_crop=True)
        self.assertEqual(len(links_p3), 3)
        for l in links_p3:
            rx0, ry0, rx1, ry1 = l.rect
            self.assertTrue(0.0 <= rx0 <= rx1 <= 1.0)
            self.assertTrue(0.0 <= ry0 <= ry1 <= 1.0)

    def test_synthetic_pdf_links(self):
        # 一時PDFで LINK_URI, LINK_GOTO を検証
        base_pdf = tempfile.mktemp(suffix="_base.pdf")
        out_pdf = tempfile.mktemp(suffix="_out.pdf")

        try:
            doc = pymupdf.Document()
            doc.new_page(width=800, height=600)
            doc.new_page(width=800, height=600)
            doc.save(base_pdf)
            doc.close()

            doc = pymupdf.open(base_pdf)
            p0 = doc[0]
            # Web URL リンク
            p0.insert_link({
                "kind": pymupdf.LINK_URI,
                "from": pymupdf.Rect(100, 100, 300, 200),
                "uri": "https://github.com/lambda-haru/BeamerPlus"
            })
            # ページジャンプリンク (p.2 へ)
            p0.insert_link({
                "kind": pymupdf.LINK_GOTO,
                "from": pymupdf.Rect(400, 100, 600, 200),
                "page": 1
            })
            doc.save(out_pdf)
            doc.close()

            reader = PdfReader(out_pdf)
            links = reader.get_page_links(0)
            self.assertEqual(len(links), 2)

            uri_link = next(l for l in links if l.kind == pymupdf.LINK_URI)
            self.assertEqual(uri_link.uri, "https://github.com/lambda-haru/BeamerPlus")
            self.assertIn("https://github.com/lambda-haru/BeamerPlus", uri_link.tooltip)
            self.assertAlmostEqual(uri_link.rect[0], 100 / 800)
            self.assertAlmostEqual(uri_link.rect[1], 100 / 600)
            self.assertAlmostEqual(uri_link.rect[2], 300 / 800)
            self.assertAlmostEqual(uri_link.rect[3], 200 / 600)

            goto_link = next(l for l in links if l.kind == pymupdf.LINK_GOTO)
            self.assertEqual(goto_link.page, 1)
            self.assertIn("2", goto_link.tooltip)

        finally:
            for p in (base_pdf, out_pdf):
                if os.path.exists(p):
                    os.remove(p)

    def test_dual_screen_auto_crop_links(self):
        # 左右2画面 (show notes on second screen=right)
        base_pdf = tempfile.mktemp(suffix="_base.pdf")
        out_pdf = tempfile.mktemp(suffix="_out.pdf")

        try:
            doc = pymupdf.Document()
            # 幅1600, 高さ600 (比率 > 2.0 -> 左800がスライド、右800がノート)
            doc.new_page(width=1600, height=600)
            doc.save(base_pdf)
            doc.close()

            doc = pymupdf.open(base_pdf)
            p = doc[0]
            # スライド側 (x: 200~400)
            p.insert_link({
                "kind": pymupdf.LINK_URI,
                "from": pymupdf.Rect(200, 100, 400, 200),
                "uri": "https://slide.example.com"
            })
            # ノート側 (x: 1000~1200) -> スライド側には表示されないため除外されるべき
            p.insert_link({
                "kind": pymupdf.LINK_URI,
                "from": pymupdf.Rect(1000, 100, 1200, 200),
                "uri": "https://notes.example.com"
            })
            doc.save(out_pdf)
            doc.close()

            reader = PdfReader(out_pdf)
            self.assertTrue(reader.is_dual_screen_page(0))

            # auto_crop=True の場合、左半分のリンクのみが [0, 1] にリスケールされて抽出される
            cropped_links = reader.get_page_links(0, auto_crop=True)
            self.assertEqual(len(cropped_links), 1)
            l = cropped_links[0]
            self.assertEqual(l.uri, "https://slide.example.com")
            # 200 / 800 = 0.25, 400 / 800 = 0.5
            self.assertAlmostEqual(l.rect[0], 0.25)
            self.assertAlmostEqual(l.rect[1], 100 / 600)
            self.assertAlmostEqual(l.rect[2], 0.5)
            self.assertAlmostEqual(l.rect[3], 200 / 600)

        finally:
            for p in (base_pdf, out_pdf):
                if os.path.exists(p):
                    os.remove(p)

    def test_presentation_controller_jump_history(self):
        controller = PresentationController()
        controller.set_total_pages(10)

        history_events: list[bool] = []
        controller.jump_history_changed.connect(lambda can_back: history_events.append(can_back))

        # 初期状態
        self.assertEqual(controller.current_page, 0)
        self.assertFalse(controller.can_jump_back)
        self.assertEqual(controller.jump_history, [])

        # スライド 5 へジャンプ
        controller.jump_to_page(5)
        self.assertEqual(controller.current_page, 5)
        self.assertTrue(controller.can_jump_back)
        self.assertEqual(controller.jump_history, [0])
        self.assertEqual(history_events[-1], True)

        # 同一ページへのジャンプは履歴に追加されない
        controller.jump_to_page(5)
        self.assertEqual(controller.jump_history, [0])

        # スライド 8 へジャンプ
        controller.jump_to_page(8)
        self.assertEqual(controller.current_page, 8)
        self.assertEqual(controller.jump_history, [0, 5])

        # 直前のスライド (5) へ戻る
        success = controller.jump_back()
        self.assertTrue(success)
        self.assertEqual(controller.current_page, 5)
        self.assertEqual(controller.jump_history, [0])
        self.assertTrue(controller.can_jump_back)

        # さらに直前のスライド (0) へ戻る
        success = controller.jump_back()
        self.assertTrue(success)
        self.assertEqual(controller.current_page, 0)
        self.assertEqual(controller.jump_history, [])
        self.assertFalse(controller.can_jump_back)
        self.assertEqual(history_events[-1], False)

        # 空の履歴での戻る操作
        success = controller.jump_back()
        self.assertFalse(success)
        self.assertEqual(controller.current_page, 0)

        # 新規PDFロードで履歴がクリアされること
        controller.jump_to_page(3)
        self.assertTrue(controller.can_jump_back)
        controller.set_total_pages(20)
        self.assertFalse(controller.can_jump_back)
        self.assertEqual(controller.jump_history, [])

    def test_slide_canvas_hit_testing_and_hover(self):
        controller = PresentationController()
        controller.set_total_pages(10)
        canvas = SlideCanvas(controller)
        canvas.resize(800, 600)

        # ダミースライド画像を設定
        dummy_pix = QPixmap(800, 600)
        dummy_pix.fill(QColor("white"))
        canvas.set_pixmap(dummy_pix)

        test_link = PdfLink(
            kind=1,
            rect=(0.2, 0.2, 0.4, 0.4),
            page=5,
            tooltip="スライド 5 へ移動"
        )
        canvas.set_links([test_link])

        # 1. _find_link_at の判定
        self.assertEqual(canvas._find_link_at((0.3, 0.3)), test_link)
        self.assertIsNone(canvas._find_link_at((0.1, 0.1)))
        self.assertIsNone(canvas._find_link_at((0.5, 0.5)))

        # 2. ホバー時のカーソル変更 (mouseMoveEvent)
        # スライド矩形内の (0.3, 0.3) に相当するピクセル座標
        slide_rect = canvas._get_slide_rect()
        px = slide_rect.left() + 0.3 * slide_rect.width()
        py = slide_rect.top() + 0.3 * slide_rect.height()

        pt_link = QPointF(px, py)
        move_ev_link = QMouseEvent(
            QEvent.Type.MouseMove,
            pt_link,
            pt_link,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier
        )
        canvas.mouseMoveEvent(move_ev_link)
        self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.PointingHandCursor)
        self.assertEqual(canvas.hovered_link, test_link)

        # リンク外へ移動
        px_out = slide_rect.left() + 0.8 * slide_rect.width()
        py_out = slide_rect.top() + 0.8 * slide_rect.height()
        pt_out = QPointF(px_out, py_out)
        move_ev_out = QMouseEvent(
            QEvent.Type.MouseMove,
            pt_out,
            pt_out,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier
        )
        canvas.mouseMoveEvent(move_ev_out)
        self.assertEqual(canvas.cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.assertIsNone(canvas.hovered_link)

        # 3. ハンドツールでのリンククリック (ジャンプしてページ送りされないこと)
        press_ev_link = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            pt_link,
            pt_link,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier
        )
        canvas.mousePressEvent(press_ev_link)
        self.assertEqual(controller.current_page, 5)
        self.assertTrue(controller.can_jump_back)

        # 4. マウス戻るボタンで直前ページに戻ること
        back_ev = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            pt_link,
            pt_link,
            Qt.MouseButton.BackButton,
            Qt.MouseButton.BackButton,
            Qt.KeyboardModifier.NoModifier
        )
        canvas.mousePressEvent(back_ev)
        self.assertEqual(controller.current_page, 0)
        self.assertFalse(controller.can_jump_back)

    def test_slide_canvas_drawing_mode_ctrl_click(self):
        controller = PresentationController()
        controller.set_total_pages(10)
        canvas = SlideCanvas(controller)
        canvas.resize(800, 600)

        dummy_pix = QPixmap(800, 600)
        dummy_pix.fill(QColor("white"))
        canvas.set_pixmap(dummy_pix)

        test_link = PdfLink(
            kind=1,
            rect=(0.2, 0.2, 0.4, 0.4),
            page=7,
            tooltip="スライド 7 へ移動"
        )
        canvas.set_links([test_link])

        slide_rect = canvas._get_slide_rect()
        px = slide_rect.left() + 0.3 * slide_rect.width()
        py = slide_rect.top() + 0.3 * slide_rect.height()

        # ペンツールを選択
        controller.set_tool("pen")

        # 通常クリック: 描画が優先され、ページジャンプしないこと
        pt = QPointF(px, py)
        press_pen_normal = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            pt,
            pt,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier
        )
        canvas.mousePressEvent(press_pen_normal)
        self.assertEqual(controller.current_page, 0)
        controller.end_stroke()

        # Ctrl+クリック: ペンモードでもリンクが動作してページジャンプすること
        press_pen_ctrl = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            pt,
            pt,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ControlModifier
        )
        canvas.mousePressEvent(press_pen_ctrl)
        self.assertEqual(controller.current_page, 7)
        self.assertTrue(controller.can_jump_back)

    def test_viewer_link_interaction(self):
        controller = PresentationController()
        controller.set_total_pages(10)
        viewer = Viewer(controller)
        viewer.resize(800, 600)

        dummy_pix = QPixmap(800, 600)
        dummy_pix.fill(QColor("white"))
        viewer.show_image(dummy_pix)

        test_link = PdfLink(
            kind=1,
            rect=(0.2, 0.2, 0.4, 0.4),
            page=4,
            tooltip="スライド 4 へ移動"
        )
        viewer.set_links([test_link])

        self.assertEqual(viewer._find_link_at((0.3, 0.3)), test_link)
        self.assertIsNone(viewer._find_link_at((0.6, 0.6)))

        viewer._execute_link(test_link)
        self.assertEqual(controller.current_page, 4)
        self.assertTrue(controller.can_jump_back)

        controller.jump_back()
        self.assertEqual(controller.current_page, 0)


if __name__ == "__main__":
    unittest.main()
