from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch
import hashlib
import errno
import io
import json
import sys

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pdf_engine as engine


def request(**kwargs):
    return dict(operation="split", inputs=["unused.pdf"], suffix="_split", existing="rename",
                order="L", mode="keep", spanning="both", ratio=50, dpi=144,
                context=0, filter="all", **kwargs)


def landscape(rotation=0, cropped=True):
    doc = fitz.open()
    page = doc.new_page(width=700 if rotation % 180 else 1000, height=1000 if rotation % 180 else 700)
    if cropped:
        page.set_cropbox(fitz.Rect(80, 70, 620, 930) if rotation % 180 else fitz.Rect(70, 80, 930, 620))
    page.set_rotation(rotation)
    width, height = page.rect.width, page.rect.height
    inv = page.derotation_matrix
    page.draw_rect(fitz.Rect(0, 0, width / 2, height) * inv, color=None, fill=(1, .2, .2))
    page.draw_rect(fitz.Rect(width / 2, 0, width, height) * inv, color=None, fill=(.2, .2, 1))
    page.add_text_annot(fitz.Point(width * .2, height * .2) * inv, "left comment")
    page.add_text_annot(fitz.Point(width * .8, height * .2) * inv, "right comment")
    return doc


def comment_texts(page):
    return sorted(a.info["content"] for a in page.annots() or [])


def pixel(page):
    pix = page.get_pixmap(alpha=False)
    return pix.pixel(pix.width // 2, pix.height // 2)


class SplitTests(TestCase):
    def test_rotation_and_crop_preserve_correct_annotations_and_visual_sides(self):
        for rotation in (0, 90, 180, 270):
            for cropped in (False, True):
                with self.subTest(rotation=rotation, cropped=cropped), landscape(rotation, cropped) as doc:
                    original = (tuple(doc[0].cropbox), doc[0].rotation, comment_texts(doc[0]))
                    with engine.split_document(doc, request(), [0]) as out:
                        self.assertEqual(len(out), 2)
                        self.assertEqual(comment_texts(out[0]), ["left comment"])
                        self.assertEqual(comment_texts(out[1]), ["right comment"])
                        self.assertAlmostEqual(out[0].rect.width, doc[0].rect.width / 2)
                        self.assertAlmostEqual(out[1].rect.height, doc[0].rect.height)
                        self.assertEqual([a.type[0] for a in out[1].annots()], [fitz.PDF_ANNOT_TEXT])
                        self.assertGreater(pixel(out[0])[0], 240)
                        self.assertGreater(pixel(out[1])[2], 240)
                        self.assertEqual((tuple(doc[0].cropbox), doc[0].rotation, comment_texts(doc[0])), original)

    def test_reverse_order_and_ratio(self):
        with landscape() as doc:
            req = request(); req.update(order="R", ratio=35)
            with engine.split_document(doc, req, [0]) as out:
                self.assertEqual(comment_texts(out[0]), ["right comment"])
                self.assertAlmostEqual(out[0].rect.width, doc[0].rect.width * .65)
                self.assertAlmostEqual(out[1].rect.width, doc[0].rect.width * .35)

    def test_spanning_annotation_policy(self):
        expected = {"both": (True, True), "left": (True, False), "right": (False, True), "drop": (False, False)}
        for policy, retained in expected.items():
            with self.subTest(policy=policy), landscape() as doc:
                page = doc[0]
                middle = page.rect.width / 2
                annot = page.add_rect_annot(fitz.Rect(middle - 40, 100, middle + 40, 140))
                annot.set_info(content="spanning"); annot.update()
                req = request(); req["spanning"] = policy
                with engine.split_document(doc, req, [0]) as out:
                    self.assertEqual(tuple("spanning" in comment_texts(p) for p in out), retained)

    def test_portrait_copy_and_force_split(self):
        with fitz.open() as doc:
            page = doc.new_page(width=400, height=700); page.add_text_annot((30, 30), "portrait")
            with engine.split_document(doc, request(), [0]) as out:
                self.assertEqual(len(out), 1); self.assertEqual(comment_texts(out[0]), ["portrait"])
            req = request(); req["split_all"] = True
            with engine.split_document(doc, req, [0]) as out:
                self.assertEqual(len(out), 2); self.assertEqual(out[0].rect.width, 200)

    def test_ink_highlight_subtypes_and_forms_preserved(self):
        with landscape() as doc:
            page = doc[0]
            page.add_ink_annot([[(500, 100), (550, 130), (600, 100)]])
            page.add_highlight_annot(fitz.Rect(40, 150, 150, 170))
            widget = fitz.Widget(); widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT; widget.field_name = "right_field"; widget.field_value = "hello"; widget.rect = fitz.Rect(600, 200, 700, 240); page.add_widget(widget)
            with engine.split_document(doc, request(), [0]) as out:
                self.assertIn(fitz.PDF_ANNOT_HIGHLIGHT, [a.type[0] for a in out[0].annots()])
                self.assertIn(fitz.PDF_ANNOT_INK, [a.type[0] for a in out[1].annots()])
                self.assertEqual(list(out[0].widgets() or []), [])
                self.assertEqual([w.field_value for w in out[1].widgets()], ["hello"])

    def test_flatten_includes_visible_annotation_and_removes_editability(self):
        with landscape() as doc, engine.split_document(doc, request(), [0]) as split:
            with engine.flatten_document(split, 144) as flat:
                self.assertEqual(len(flat), 2)
                self.assertEqual(list(flat[0].annots() or []), [])
                self.assertTrue(flat[0].get_images())
                self.assertEqual(flat[0].get_text(), "")
                self.assertEqual(pixel(flat[0]), pixel(split[0]))
                # Entire annotation appearance must survive, not only the background color.
                self.assertEqual(flat[0].get_pixmap(dpi=144).samples, split[0].get_pixmap(dpi=144).samples)


class ExtractTests(TestCase):
    def setUp(self):
        self.doc = fitz.open()
        for i in range(6):
            page = self.doc.new_page(); page.insert_text((50, 50), f"Page {i + 1}")
        self.doc[1].add_text_annot((50, 100), "comment")
        self.doc[3].add_ink_annot([[(50, 100), (90, 130)]])
        self.doc[4].insert_link(dict(kind=fitz.LINK_URI, uri="https://example.com", **{"from": fitz.Rect(50, 50, 100, 80)}))
        page = self.doc[5]
        annot = page.add_highlight_annot(fitz.Rect(50, 50, 100, 80)); annot.set_info(title="Author only"); annot.update()
    def tearDown(self): self.doc.close()

    def test_all_annotations_and_links_excluded(self):
        out, matched, selected = engine.extract_document(self.doc, request(), list(range(6)))
        with out:
            self.assertEqual(matched, [2, 4, 6]); self.assertEqual(selected, [2, 4, 6])
            self.assertEqual(len(out), 3); self.assertEqual(out[0].get_label(), "2")
            page = out[1]
            self.assertEqual(page.first_annot.type[0], fitz.PDF_ANNOT_INK)

    def test_text_does_not_treat_author_as_comment(self):
        req = request(); req["filter"] = "text"
        out, matched, _ = engine.extract_document(self.doc, req, list(range(6)))
        with out: self.assertEqual(matched, [2])

    def test_ink_filter(self):
        req = request(); req["filter"] = "ink"
        out, matched, _ = engine.extract_document(self.doc, req, list(range(6)))
        with out: self.assertEqual(matched, [4])

    def test_context_deduplication_and_page_range(self):
        req = request(); req["context"] = 1
        out, matched, selected = engine.extract_document(self.doc, req, [1, 2, 3, 4])
        with out: self.assertEqual(selected, [2, 3, 4, 5]); self.assertEqual(matched, [2, 4])

    def test_no_match_produces_empty_document(self):
        out, matched, _ = engine.extract_document(self.doc, request(), [0, 4])
        with out: self.assertEqual(len(out), 0); self.assertEqual(matched, [])


class FileTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        self.source = self.root / "日本語 資料.PDF"
        with landscape() as doc: doc.save(self.source)
    def tearDown(self): self.temp.cleanup()

    def test_default_output_and_source_integrity(self):
        before = hashlib.sha256(self.source.read_bytes()).digest()
        req = request(); req["inputs"] = [str(self.source)]
        entries = engine.collect(req); self.assertEqual(entries[0][1], self.root / "分割PDF/日本語 資料_split.pdf")
        result = engine.process_file(*entries[0], req, {self.source}, 1, 1)
        self.assertEqual(result["page_count"], 2)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), before)
        with fitz.open(result["output"]) as doc: self.assertEqual(comment_texts(doc[1]), ["right comment"])

    def test_recursive_uppercase_dedupe_and_output_exclusion(self):
        nested = self.root / "下位"; nested.mkdir(); second = nested / "2.pdf"; second.write_bytes(self.source.read_bytes())
        (self.root / "分割PDF").mkdir(); (self.root / "分割PDF/old.pdf").write_bytes(self.source.read_bytes())
        req = request(); req.update(inputs=[str(self.root), str(self.source)], recursive=True)
        entries = engine.collect(req); self.assertEqual(len(entries), 2)
        self.assertIn(self.root / "分割PDF/下位/2_split.pdf", [target for _, target in entries])

    def test_collisions_rename_skip_overwrite_and_input_protection(self):
        target = self.root / "out.pdf"
        with landscape() as doc:
            first = engine.write_atomic(doc, target, "rename", {self.source})
            second = engine.write_atomic(doc, target, "rename", {self.source})
            self.assertEqual(first, target); self.assertEqual(second.name, "out (2).pdf")
            self.assertIsNone(engine.write_atomic(doc, target, "skip", {self.source}))
            target.write_bytes(b"old")
            engine.write_atomic(doc, target, "overwrite", {self.source})
            with fitz.open(target) as result: self.assertEqual(len(result), 1)
            with self.assertRaisesRegex(ValueError, "入力PDFと同じ"):
                engine.write_atomic(doc, self.source, "overwrite", {self.source})
        self.assertEqual(list(self.root.glob(".pdf-tools-*")), [])

    def test_failed_save_preserves_existing_and_cleans_temp(self):
        target = self.root / "out.pdf"; target.write_bytes(b"existing")
        with landscape() as doc, patch.object(fitz.Document, "save", side_effect=engine.Cancelled):
            with self.assertRaises(engine.Cancelled): engine.write_atomic(doc, target, "overwrite", {self.source})
        self.assertEqual(target.read_bytes(), b"existing")
        self.assertEqual(list(self.root.glob(".pdf-tools-*")), [])

    def test_external_volume_fallback_does_not_overwrite(self):
        if sys.platform != "darwin": self.skipTest("Darwin-only rename")
        target = self.root / "external.pdf"
        target.write_bytes(b"existing")
        with landscape() as doc, patch.object(engine.os, "link", side_effect=OSError(errno.ENOTSUP, "unsupported")):
            result = engine.write_atomic(doc, target, "rename", {self.source})
        self.assertEqual(target.read_bytes(), b"existing")
        self.assertEqual(result.name, "external (2).pdf")
        with fitz.open(result) as doc: self.assertEqual(len(doc), 1)
        self.assertEqual(list(self.root.glob(".pdf-tools-*")), [])

    def test_batch_error_does_not_stop_valid_file(self):
        bad = self.root / "broken.pdf"; bad.write_bytes(b"not pdf")
        req = request(); req["inputs"] = [str(bad), str(self.source)]
        stream = io.StringIO()
        with redirect_stdout(stream): engine.run(req)
        events = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([e["status"] for e in events if e["event"] == "result"], ["error", "ok"])
        self.assertEqual(events[-1]["event"], "done")

    def test_password_and_permissions(self):
        locked = self.root / "locked.pdf"
        with landscape() as doc: doc.save(locked, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret", permissions=fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_COPY | fitz.PDF_PERM_PRINT)
        req = request()
        with self.assertRaisesRegex(ValueError, "パスワード"): engine.process_file(locked, self.root / "out.pdf", req, {locked}, 1, 1)
        req["password"] = "secret"
        result = engine.process_file(locked, self.root / "out.pdf", req, {locked}, 1, 1)
        with fitz.open(result["output"]) as doc: self.assertFalse(doc.needs_pass)
        restricted = self.root / "restricted.pdf"
        with landscape() as doc: doc.save(restricted, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret", permissions=fitz.PDF_PERM_PRINT)
        with self.assertRaisesRegex(ValueError, "編集制限"): engine.process_file(restricted, self.root / "denied.pdf", req, {restricted}, 1, 1)


class ValidationTests(TestCase):
    def test_ranges(self):
        self.assertEqual(engine.parse_pages("1-3, 5, 7-", 8), [0, 1, 2, 4, 6, 7])
        self.assertEqual(engine.parse_pages("-2,2", 8), [0, 1])
        for value in ("0", "9", "3-1", "-", "1,,3", "foo"):
            with self.subTest(value=value), self.assertRaises(ValueError): engine.parse_pages(value, 8)
    def test_request_validation(self):
        for key, value in [("suffix", ""), ("suffix", "../out"), ("ratio", 0), ("dpi", 9999), ("context", -1)]:
            req = request(); req[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): engine.validate(req)


if __name__ == "__main__":
    main()
