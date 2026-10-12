"""Local PDF engine. The native app exchanges newline-delimited JSON over pipes."""
from __future__ import annotations

import json
import errno
import os
import re
import signal
import sys
import tempfile
from pathlib import Path

import pymupdf as fitz


class Cancelled(Exception):
    pass


def cancel(*_):
    raise Cancelled()


def emit(event: str, **values):
    print(json.dumps(dict(event=event, **values), ensure_ascii=False), flush=True)


def parse_pages(value: str, count: int) -> list[int]:
    if not value.strip():
        return list(range(count))
    pages: set[int] = set()
    for token in value.replace("，", ",").replace("、", ",").split(","):
        token = token.strip()
        if re.fullmatch(r"\d+", token):
            lo = hi = int(token)
        elif re.fullmatch(r"\d*\s*-\s*\d*", token) and token != "-":
            left, right = token.split("-")
            lo, hi = int(left.strip() or 1), int(right.strip() or count)
        else:
            raise ValueError("対象ページは「1-5,8,12-」の形式で指定してください。")
        if lo < 1 or hi < lo or hi > count:
            raise ValueError(f"対象ページが範囲外です。このPDFは{count}ページです。")
        pages.update(range(lo - 1, hi))
    return sorted(pages)


def validate(req: dict) -> dict:
    req = dict(req)
    if req.get("operation") not in ("split", "extract"):
        raise ValueError("処理の種類が正しくありません。")
    if not isinstance(req.get("inputs"), list) or not req["inputs"]:
        raise ValueError("PDFまたはフォルダを追加してください。")
    req.setdefault("suffix", "_split" if req["operation"] == "split" else "_extract")
    suffix = req["suffix"].strip()
    if not suffix or len(suffix) > 80 or any(c in suffix for c in '/:\\') or any(ord(c) < 32 for c in suffix):
        raise ValueError("ファイル名の末尾は1〜80文字で、 / : \\ を含めず指定してください。")
    req["suffix"] = suffix
    if req.get("existing", "rename") not in ("rename", "skip", "overwrite"):
        raise ValueError("既存ファイルの扱いが正しくありません。")
    if req.get("order", "L") not in ("L", "R"):
        raise ValueError("ページ順が正しくありません。")
    if req.get("mode", "keep") not in ("keep", "flatten"):
        raise ValueError("注釈の保存方法が正しくありません。")
    if req.get("spanning", "both") not in ("both", "left", "right", "drop"):
        raise ValueError("境界をまたぐ注釈の扱いが正しくありません。")
    if req.get("filter", "all") not in ("all", "text", "ink"):
        raise ValueError("注釈の判定条件が正しくありません。")
    req["ratio"] = float(req.get("ratio", 50))
    req["dpi"] = int(req.get("dpi", 144))
    req["context"] = int(req.get("context", 0))
    if not 10 <= req["ratio"] <= 90:
        raise ValueError("分割位置は10〜90%で指定してください。")
    if not 72 <= req["dpi"] <= 600:
        raise ValueError("解像度は72〜600 dpiで指定してください。")
    if not 0 <= req["context"] <= 5:
        raise ValueError("前後の追加ページ数は0〜5で指定してください。")
    return req


def is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def collect(req: dict) -> list[tuple[Path, Path]]:
    """Snapshot inputs before writing. Never recursively pick up our output folders."""
    output = Path(req["output_dir"]).expanduser().resolve() if req.get("output_dir") else None
    roots = list(dict.fromkeys(Path(s).expanduser().resolve() for s in req["inputs"]))
    label = "分割PDF" if req["operation"] == "split" else "注釈抽出PDF"
    entries: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            emit("result", status="error", input=str(root), message="ファイルまたはフォルダが見つかりません。")
            continue
        if root.is_file():
            if root.suffix.lower() != ".pdf":
                emit("result", status="error", input=str(root), message="PDFファイルを指定してください。")
                continue
            candidates = [root]
            base = root.parent
        elif root.is_dir():
            candidates = []
            base = root
            for parent, dirs, files in os.walk(root, followlinks=False):
                parent_path = Path(parent)
                dirs[:] = sorted(d for d in dirs if not d.startswith(".")
                                  and d not in ("分割PDF", "注釈抽出PDF")
                                  and not (output and output != root and is_within((parent_path / d).resolve(), output)))
                for name in sorted(files):
                    if name.startswith(".") or not name.lower().endswith(".pdf"):
                        continue
                    # When custom output is the input folder itself, avoid reprocessing our files.
                    if output == root and re.search(re.escape(req["suffix"]) + r"(?: \(\d+\))?$", Path(name).stem):
                        continue
                    candidates.append(parent_path / name)
                if not req.get("recursive", False):
                    break
            if not candidates:
                emit("result", status="skip", input=str(root), message="対象のPDFがありません。下位フォルダは設定で含められます。")
        else:
            emit("result", status="error", input=str(root), message="通常のファイルまたはフォルダを指定してください。")
            continue
        for path in sorted(candidates):
            source = path.resolve()
            if source in seen:
                continue
            seen.add(source)
            relative = path.relative_to(base)
            if output:
                # Multiple folder drops retain the root name to reduce name collisions.
                dest_dir = output / root.name / relative.parent if root.is_dir() and len(roots) > 1 else output / relative.parent
            else:
                dest_dir = base / label / relative.parent
            entries.append((source, dest_dir / (path.stem + req["suffix"] + ".pdf")))
    return entries


def side_matches(rect: fitz.Rect, middle: float, side: str, policy: str) -> bool:
    if rect.x1 <= middle:
        return side == "L"
    if rect.x0 >= middle:
        return side == "R"
    return policy == "both" or policy == {"L": "left", "R": "right"}[side]


def split_document(src: fitz.Document, req: dict, pages: list[int], progress=lambda *_: None) -> fitz.Document:
    out = fitz.open()
    try:
        for n, index in enumerate(pages):
            page = src[index]
            visible = page.rect
            if visible.width <= visible.height and not req.get("split_all", False):
                out.insert_pdf(src, from_page=index, to_page=index, annots=True, widgets=True)
                progress(n + 1, len(pages))
                continue
            middle = visible.width * req.get("ratio", 50) / 100
            rotation = page.rotation_matrix
            inverse = page.derotation_matrix
            crop_origin = page.cropbox.tl
            crops = {"L": fitz.Rect(0, 0, middle, visible.height),
                     "R": fitz.Rect(middle, 0, visible.width, visible.height)}
            for side in (("L", "R") if req.get("order", "L") == "L" else ("R", "L")):
                out.insert_pdf(src, from_page=index, to_page=index, annots=True, widgets=True)
                dest = out[-1]
                # Filter BEFORE cropping. Cropping changes the coordinate origin.
                for annot in list(dest.annots() or []):
                    rect = annot.rect * rotation
                    if not side_matches(rect, middle, side, req.get("spanning", "both")):
                        dest.delete_annot(annot)
                for widget in list(dest.widgets() or []):
                    if not side_matches(widget.rect * rotation, middle, side, req.get("spanning", "both")):
                        dest.delete_widget(widget)
                for link in dest.get_links():
                    if not side_matches(link["from"] * rotation, middle, side, req.get("spanning", "both")):
                        dest.delete_link(link)
                # CropBox uses unrotated media coordinates; rect and D&D viewing order use rotated coordinates.
                unrotated = crops[side] * inverse
                crop = fitz.Rect(unrotated.x0 + crop_origin.x, unrotated.y0 + crop_origin.y,
                                 unrotated.x1 + crop_origin.x, unrotated.y1 + crop_origin.y)
                dest.set_cropbox(crop)
            progress(n + 1, len(pages))
        return out
    except BaseException:
        out.close()
        raise


def has_comment(page: fitz.Page, kind: str) -> bool:
    for annot in page.annots() or []:
        if kind == "all":
            return True
        if kind == "ink" and annot.type[0] == fitz.PDF_ANNOT_INK:
            return True
        if kind == "text":
            content = (annot.info.get("content") or "").strip()
            if not content and annot.type[0] == fitz.PDF_ANNOT_FREE_TEXT:
                content = (annot.get_text("text") or "").strip()
            if content:
                return True
    return False


def extract_document(src: fitz.Document, req: dict, pages: list[int], progress=lambda *_: None):
    matched = []
    for n, index in enumerate(pages):
        if has_comment(src[index], req.get("filter", "all")):
            matched.append(index)
        progress(n + 1, len(pages))
    allowed = set(pages)
    context = req.get("context", 0)
    selected = sorted({j for i in matched for j in range(max(0, i - context), min(len(src), i + context + 1)) if j in allowed})
    out = fitz.open()
    try:
        for index in selected:
            out.insert_pdf(src, from_page=index, to_page=index, annots=True, widgets=True)
        if selected:
            # Preserve the source's displayed page labels, including non-numeric labels.
            out.set_page_labels([{"startpage": n, "prefix": src[index].get_label() or str(index + 1)} for n, index in enumerate(selected)])
        return out, [i + 1 for i in matched], [i + 1 for i in selected]
    except BaseException:
        out.close()
        raise


def flatten_document(src: fitz.Document, dpi: int, progress=lambda *_: None) -> fitz.Document:
    out = fitz.open()
    try:
        for i, page in enumerate(src):
            # Keep memory bounded by rendering and inserting one page at a time.
            pixels = page.rect.width * page.rect.height * (dpi / 72) ** 2
            if pixels > 120_000_000:
                raise ValueError("この用紙サイズでは画像が大きすぎます。解像度を下げてください。")
            pix = page.get_pixmap(dpi=dpi, alpha=False, annots=True)
            new = out.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, stream=pix.tobytes("png"))
            progress(i + 1, len(src))
        return out
    except BaseException:
        out.close()
        raise


def write_atomic(doc: fitz.Document, target: Path, policy: str, inputs: set[Path]) -> Path | None:
    inputs = {path.resolve() for path in inputs}
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.resolve() in inputs:
        raise ValueError("出力先が入力PDFと同じです。保存先またはファイル名の末尾を変更してください。")
    if policy == "skip" and target.exists():
        return None
    fd, tmp_name = tempfile.mkstemp(prefix=".pdf-tools-", suffix=".tmp", dir=target.parent)
    os.close(fd)
    temp = Path(tmp_name)
    try:
        doc.save(str(temp), garbage=3, deflate=True)
        # Reopen before publishing. An interrupted save must never replace a completed PDF.
        with fitz.open(temp) as checked:
            if len(checked) != len(doc):
                raise ValueError("保存後のPDFの確認に失敗しました。")
        if policy == "overwrite":
            os.replace(temp, target)
            return target
        candidate = target
        number = 2
        while True:
            try:
                # link is exclusive and atomic, so another process cannot be overwritten.
                try:
                    os.link(temp, candidate)
                except OSError as error:
                    if sys.platform != "darwin" or error.errno not in (errno.EPERM, errno.ENOTSUP):
                        raise
                    # Some external volumes do not support hard links. Darwin also
                    # provides an exclusive atomic rename; keep the no-overwrite guarantee.
                    import ctypes
                    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
                    rename = libc.renamex_np
                    rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
                    rename.restype = ctypes.c_int
                    if rename(os.fsencode(temp), os.fsencode(candidate), 4) != 0:
                        code = ctypes.get_errno()
                        raise OSError(code, os.strerror(code), candidate)
                return candidate
            except FileExistsError:
                if policy == "skip":
                    return None
                candidate = target.with_name(f"{target.stem} ({number}){target.suffix}")
                number += 1
    finally:
        temp.unlink(missing_ok=True)


def process_file(source: Path, target: Path, req: dict, all_inputs: set[Path], file_index: int, total: int):
    all_inputs = {path.resolve() for path in all_inputs}
    policy = req.get("existing", "rename")
    if target.resolve() in all_inputs:
        raise ValueError("出力先が入力PDFと同じです。保存先またはファイル名の末尾を変更してください。")
    if policy == "skip" and target.exists():
        return dict(status="skip", input=str(source), message="同名の出力があるためスキップしました。")
    with fitz.open(source) as src:
        if not src.is_pdf:
            raise ValueError("PDFとして読み込めませんでした。")
        if src.needs_pass and not src.authenticate(req.get("password", "")):
            raise ValueError("パスワードが必要、またはパスワードが違います。設定欄に入力して再実行してください。")
        if src.is_encrypted or (src.permissions & fitz.PDF_PERM_MODIFY == 0):
            raise ValueError("このPDFには編集制限があります。編集可能なPDFを使用してください。")
        if not len(src):
            raise ValueError("PDFにページがありません。")
        pages = parse_pages(req.get("pages", ""), len(src))
        def progress(current, count, phase="processing"):
            emit("progress", file_index=file_index, total=total, page=current, pages=count,
                 phase=phase, name=source.name)
        result = dict(status="ok", input=str(source))
        if req["operation"] == "split":
            out = split_document(src, req, pages, progress)
        else:
            out, matched, selected = extract_document(src, req, pages, progress)
            result.update(matched=matched, selected=selected)
            if not len(out):
                out.close()
                return dict(status="skip", input=str(source), message="条件に合う注釈がありません。PDFは作成しませんでした。")
        try:
            if req.get("mode", "keep") == "flatten":
                flat = flatten_document(out, req["dpi"], lambda n, count: progress(n, count, "flatten"))
                out.close()
                out = flat
            metadata = dict(src.metadata)
            metadata["producer"] = "PDF Drop Tools / PyMuPDF"
            out.set_metadata(metadata)
            destination = write_atomic(out, target, policy, all_inputs)
            if destination is None:
                return dict(status="skip", input=str(source), message="同名の出力があるためスキップしました。")
            result.update(output=str(destination), page_count=len(out), message=f"{len(out)}ページを保存しました。")
            return result
        finally:
            out.close()


def run(req: dict) -> int:
    req = validate(req)
    entries = collect(req)
    emit("plan", total=len(entries))
    inputs = {p for p, _ in entries}
    for n, (source, target) in enumerate(entries, 1):
        emit("file", index=n, total=len(entries), name=source.name)
        try:
            result = process_file(source, target, req, inputs, n, len(entries))
        except Cancelled:
            raise
        except Exception as error:
            result = dict(status="error", input=str(source), message=str(error))
        emit("result", **result)
    emit("done")
    return 0


def main():
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    try:
        request = json.load(sys.stdin)
        return run(request)
    except Cancelled:
        emit("cancelled", message="中止しました。保存済みのPDFは残っています。")
        return 130
    except Exception as error:
        emit("fatal", message=str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
