import unittest
import os
import tempfile
import json
import pymupdf

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QMouseEvent
from PySide6.QtCore import Qt, QPointF

from beamerplus.PdfReader import PdfReader, compute_page_groups, PageGroup
from beamerplus.DrawingModel import PresentationController, Stroke, TextItem
from beamerplus.ThumbnailBar import ThumbnailBar
from beamerplus.Viewer import Viewer
from beamerplus.NotePanel import NotePanel


class TestPauseGrouping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        cls.app = QApplication.instance() or QApplication([])

    def test_compute_page_groups_logic(self):
        # 1. 隣接する同一ラベルが正しくグループ化されること
        labels = ["1", "1", "2", "3", "3", "3", "4"]
        groups = compute_page_groups(labels)
        self.assertEqual(len(groups), 4)
        self.assertEqual(groups[0], PageGroup(group_index=0, label="1", page_indices=[0, 1]))
        self.assertEqual(groups[1], PageGroup(group_index=1, label="2", page_indices=[2]))
        self.assertEqual(groups[2], PageGroup(group_index=2, label="3", page_indices=[3, 4, 5]))
        self.assertEqual(groups[3], PageGroup(group_index=3, label="4", page_indices=[6]))

        # 2. 空文字ラベルはグループ化されず各々独立すること
        empty_labels = ["", "", ""]
        groups_empty = compute_page_groups(empty_labels)
        self.assertEqual(len(groups_empty), 3)
        self.assertEqual(groups_empty[0].page_indices, [0])
        self.assertEqual(groups_empty[1].page_indices, [1])
        self.assertEqual(groups_empty[2].page_indices, [2])

        # 3. 隣接していない同一ラベルはグループ化されないこと
        non_adjacent = ["1", "2", "1"]
        groups_non_adj = compute_page_groups(non_adjacent)
        self.assertEqual(len(groups_non_adj), 3)
        self.assertEqual(groups_non_adj[0].page_indices, [0])
        self.assertEqual(groups_non_adj[1].page_indices, [1])
        self.assertEqual(groups_non_adj[2].page_indices, [2])

        # 4. 空配列・単一要素
        self.assertEqual(compute_page_groups([]), [])
        single = compute_page_groups(["title"])
        self.assertEqual(len(single), 1)
        self.assertEqual(single[0], PageGroup(group_index=0, label="title", page_indices=[0]))

    def test_pdf_reader_grouping_and_notes(self):
        pdf_path = "beamerplus/slides.pdf"
        reader = PdfReader(pdf_path)

        # 全56ページ、同一ラベル "1" が2ページ存在するため全55グループ
        self.assertEqual(len(reader), 56)
        self.assertEqual(len(reader.page_groups), 55)

        group_0 = reader.get_group_for_page(0)
        group_1 = reader.get_group_for_page(1)
        self.assertIsNotNone(group_0)
        self.assertIs(group_0, group_1)
        self.assertEqual(group_0.page_indices, [0, 1])
        self.assertEqual(group_0.label, "1")

        self.assertEqual(reader.get_canonical_page_index(0), 0)
        self.assertEqual(reader.get_canonical_page_index(1), 0)
        self.assertEqual(reader.get_canonical_page_index(2), 2)

        # ノートの共有テスト（キーはページラベル "1"）
        test_note = "これはグループ化されたスライド1の共有ノートです"
        reader.save_note(0, test_note)
        self.assertEqual(reader.get_note(0), test_note)
        self.assertEqual(reader.get_note(1), test_note)
        self.assertEqual(reader.notes.get("1"), test_note)

        # ページ1側から変更してもページ0に反映されること
        updated_note = "更新されたスライド1のノート"
        reader.save_note(1, updated_note)
        self.assertEqual(reader.get_note(0), updated_note)
        self.assertEqual(reader.get_note(1), updated_note)
        self.assertEqual(reader.notes.get("1"), updated_note)

        # ファイル保存時にヘッダ {"version": 2, "notes": {...}} が書き出されることの検証
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = f.name
        try:
            reader.note_path = tmp_path
            reader.save_note_to_file()

            with open(tmp_path, "r", encoding="utf-8") as f:
                saved_json = json.load(f)
            self.assertEqual(saved_json.get("version"), 2)
            self.assertIn("notes", saved_json)
            self.assertIn("1", saved_json["notes"])
            self.assertEqual(saved_json["notes"]["1"], updated_note)

            # 旧形式（ヘッダなし 0-based インデックス）からの完全自動移行テスト
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f_pdf:
                tmp_pdf = f_pdf.name
            legacy_json = tmp_pdf + ".json"
            import shutil
            shutil.copyfile(pdf_path, tmp_pdf)
            with open(legacy_json, "w", encoding="utf-8") as f:
                json.dump({"0": "旧形式メモ0", "2": "旧形式メモ2"}, f)

            reader_migrated = PdfReader(tmp_pdf)
            self.assertEqual(reader_migrated.get_note(0), "旧形式メモ0")
            self.assertEqual(reader_migrated.get_note(1), "旧形式メモ0")
            self.assertEqual(reader_migrated.get_note(2), "旧形式メモ2")

            # 保存すると新形式(v2)に自動更新されることの検証
            reader_migrated.save_note_to_file()
            with open(legacy_json, "r", encoding="utf-8") as f:
                migrated_saved = json.load(f)
            self.assertEqual(migrated_saved.get("version"), 2)
            self.assertEqual(migrated_saved["notes"]["1"], "旧形式メモ0")
            self.assertEqual(migrated_saved["notes"]["2"], "旧形式メモ2")

            if os.path.exists(tmp_pdf):
                os.remove(tmp_pdf)
            if os.path.exists(legacy_json):
                os.remove(legacy_json)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_drawing_model_canonical_mapping(self):
        controller = PresentationController()
        controller.set_total_pages(10)
        # 0と1をグループ化 (canonical: 0)
        controller.set_page_mapping({0: 0, 1: 0, 2: 2})

        # ページ0でストローク開始・追加
        controller.set_page(0)
        controller.begin_custom_stroke("#e53935", 0.005, 0.85, 0.1, 0.1)
        controller.add_stroke_point(0.2, 0.2)
        controller.end_stroke()

        # ページ0のストロークがページ1でも共有されていること
        strokes_0 = controller.get_strokes_for_page(0)
        strokes_1 = controller.get_strokes_for_page(1)
        self.assertEqual(len(strokes_0), 1)
        self.assertEqual(len(strokes_1), 1)
        self.assertIs(strokes_0, strokes_1)

        # ページ0でテキスト追加
        controller.add_text(0.3, 0.3, "テストテキスト")
        texts_0 = controller.get_texts_for_page(0)
        texts_1 = controller.get_texts_for_page(1)
        self.assertEqual(len(texts_0), 1)
        self.assertEqual(len(texts_1), 1)
        self.assertEqual(texts_1[0].text, "テストテキスト")

        # ページ1へ移動してさらにストローク追加
        controller.set_page(1)
        controller.begin_custom_stroke("#00ff00", 0.005, 0.85, 0.4, 0.4)
        controller.end_stroke()

        self.assertEqual(len(controller.get_strokes_for_page(0)), 2)
        self.assertEqual(len(controller.get_strokes_for_page(1)), 2)

        # ページ1でUndoを実行 -> ページ0側も戻る
        controller.undo()
        self.assertEqual(len(controller.get_strokes_for_page(0)), 1)
        self.assertEqual(len(controller.get_strokes_for_page(1)), 1)

        # ページ1で消去
        controller.clear_strokes()
        self.assertEqual(len(controller.get_strokes_for_page(0)), 0)
        self.assertEqual(len(controller.get_strokes_for_page(1)), 0)
        self.assertEqual(len(controller.get_texts_for_page(0)), 0)

    def test_thumbnail_bar_consolidation(self):
        reader = PdfReader("beamerplus/slides.pdf")
        bar = ThumbnailBar()
        bar.load_pdf(reader)

        # 56ページ中、ページ0と1が集約されて55アイテムになること
        self.assertEqual(len(bar.items), 55)

        first_item = bar.items[0]
        self.assertEqual(first_item.page_indices, [0, 1])
        self.assertTrue(first_item.has_multiple_pages)

        # ページ0を選択中: "1 (1/2)" と表示されハイライトされること
        bar.set_current_page(0)
        self.assertTrue(first_item.is_selected)
        self.assertEqual(first_item.caption_label.text(), "1 (1/2)")

        # ページ1を選択中: アイテム0が選択されたまま "1 (2/2)" と表示されること
        bar.set_current_page(1)
        self.assertTrue(first_item.is_selected)
        self.assertEqual(first_item.caption_label.text(), "1 (2/2)")

        # ページ2を選択: アイテム0は非選択になり "1 (2枚)"、アイテム1が選択され "2"
        bar.set_current_page(2)
        self.assertFalse(first_item.is_selected)
        self.assertEqual(first_item.caption_label.text(), "1 (2枚)")
        self.assertTrue(bar.items[1].is_selected)
        self.assertEqual(bar.items[1].caption_label.text(), "2")

        # アイテム0をクリックしたら先頭サブページ(0)が発行されること
        clicked_pages = []
        bar.page_selected.connect(lambda p: clicked_pages.append(p))
        ev = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(10, 10),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier
        )
        first_item.mousePressEvent(ev)
        self.assertEqual(clicked_pages, [0])

    def test_viewer_target_page_matching(self):
        controller = PresentationController()
        controller.set_total_pages(10)
        controller.set_page_mapping({0: 0, 1: 0, 2: 2})
        viewer = Viewer(controller)

        # 現在ページが 0 のとき
        controller.set_page(0)
        self.assertTrue(viewer._is_target_page(0))
        self.assertTrue(viewer._is_target_page(1))  # 1もcanonical 0 なのでTrue
        self.assertFalse(viewer._is_target_page(2))

        # 現在ページが 1 のとき
        controller.set_page(1)
        self.assertTrue(viewer._is_target_page(0))  # 0もcanonical 0 なのでTrue
        self.assertTrue(viewer._is_target_page(1))
        self.assertFalse(viewer._is_target_page(2))

    def test_note_panel_display(self):
        reader = PdfReader("beamerplus/slides.pdf")
        note_panel = NotePanel()
        note_panel.set_pdf_reader(reader)

        # ページ0を設定
        note_panel.set_page(0, None)
        self.assertEqual(note_panel.lbl_page_note.text(), "スライド 1 (1/2)")

        # ページ1を設定
        note_panel.set_page(1, None)
        self.assertEqual(note_panel.lbl_page_note.text(), "スライド 1 (2/2)")

        # ページ2（単一ページ）を設定
        note_panel.set_page(2, None)
        self.assertEqual(note_panel.lbl_page_note.text(), "スライド 2")

    def test_enter_key_advances_slide(self):
        from PySide6.QtGui import QKeyEvent
        from beamerplus.MainWindow import MainWindow

        viewer = Viewer()
        win = MainWindow(viewer=viewer)
        try:
            win.controller.set_total_pages(5)
            win.controller.set_page(0)

            # 1. MainWindow で Return キー押下 -> ページが進む
            ret_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
            win.keyPressEvent(ret_event)
            self.assertEqual(win.controller.current_page, 1)

            # 2. MainWindow で Enter (テンキー) 押下 -> ページが進む
            enter_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Enter, Qt.KeyboardModifier.NoModifier)
            win.keyPressEvent(enter_event)
            self.assertEqual(win.controller.current_page, 2)

            # 3. MainWindow.eventFilter 経由での Return / Enter キー押下 -> ページが進む
            handled = win.eventFilter(win, ret_event)
            self.assertTrue(handled)
            self.assertEqual(win.controller.current_page, 3)

            # 4. Viewer で Return / Enter キー押下 -> ページが進む
            viewer.keyPressEvent(enter_event)
            self.assertEqual(win.controller.current_page, 4)

            # 5. 最後のスライドでさらに送る -> 画面が暗転 (is_blank = True)
            self.assertFalse(win.controller.is_blank)
            self.assertFalse(viewer.is_blank)
            win.keyPressEvent(ret_event)
            self.assertEqual(win.controller.current_page, 4)
            self.assertTrue(win.controller.is_blank)
            self.assertTrue(viewer.is_blank)
            self.assertFalse(win.slide_view.btn_next.isEnabled())
            self.assertTrue(win.slide_view.btn_prev.isEnabled())

            # 6. 暗転中に戻す (prev_page) -> 暗転が解除され、最後のスライドに戻る
            left_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier)
            win.keyPressEvent(left_event)
            self.assertFalse(win.controller.is_blank)
            self.assertFalse(viewer.is_blank)
            self.assertEqual(win.controller.current_page, 4)
            self.assertTrue(win.slide_view.btn_next.isEnabled())
            self.assertTrue(win.slide_view.btn_prev.isEnabled())

            # 7. もう一度戻す -> 前のスライド (page 3) へ移動
            win.keyPressEvent(left_event)
            self.assertEqual(win.controller.current_page, 3)
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main()
