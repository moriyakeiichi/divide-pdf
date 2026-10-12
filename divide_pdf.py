#!/usr/bin/env python3
"""横長のPDFページを左右に分割する。

縦長のページはそのままコピーする。注釈は作り直さず、元ページを複製して
CropBox で表示範囲を左右に切る。そのため注釈の種類（Subtype）は元のまま残る。
中央をまたぐ注釈は --spanning で振り分ける（既定: both）。

--mode keep     注釈を編集できるPDFとして保存する（既定）
--mode flatten  分割後に各ページを画像化し、見た目を焼き込む。解像度は --dpi

入力はPDFファイル、またはPDFが入ったディレクトリ（--recursive で下位フォルダも対象）。
出力は --output-dir 以下の「元の名前 + suffix」.pdf（既定の suffix は _split）。
同名の出力がすでにある場合は、--overwrite を付けない限りスキップする。

依存: PyMuPDF（pip install pymupdf）

例:
  python3 divide_pdf.py -i input.pdf -o out
  python3 divide_pdf.py -i input.pdf -o out --order R
  python3 divide_pdf.py -i ./pdfs -o out --recursive
  python3 divide_pdf.py -i input.pdf -o out --mode flatten --dpi 200
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import fitz  # PyMuPDF


def _decide_sides(a_rect: fitz.Rect, middle: float, spanning: str) -> tuple[bool, bool]:
    """注釈の矩形と中央線から、(左ページに残す, 右ページに残す) を返す。"""
    is_left = a_rect.x1 <= middle
    is_right = a_rect.x0 >= middle
    spans = (a_rect.x0 < middle) and (a_rect.x1 > middle)

    if is_left:
        return True, False
    if is_right:
        return False, True

    if not spans:
        return False, False

    if spanning == "drop":
        return False, False
    if spanning == "both":
        return True, True
    if spanning == "left":
        return True, False
    if spanning == "right":
        return False, True

    return False, False


def _annot_type_name(annot: fitz.Annot) -> str:
    """注釈タイプ名を小文字で返す。取得できないときは空文字。"""
    try:
        return (annot.type[1] or "").lower()
    except Exception:
        return ""


def _extract_comment_text(annot: fitz.Annot) -> str:
    """注釈から、人が読むコメント本文を取り出す。"""
    info = annot.info or {}

    text = (info.get("content") or "").strip()
    if text:
        return text

    for k in ("text", "subject", "title"):
        t = (info.get(k) or "").strip()
        if t:
            return t

    try:
        t = (annot.get_text("text") or "").strip()
        if t:
            return t
    except Exception:
        pass

    return ""


def _min_rect(r: fitz.Rect, min_w: float = 220, min_h: float = 80) -> fitz.Rect:
    """小さすぎる注釈枠を、読める大きさまで広げる。"""
    new_w = r.width if r.width >= min_w else min_w
    new_h = r.height if r.height >= min_h else min_h
    return fitz.Rect(r.x0, r.y0, r.x0 + new_w, r.y0 + new_h)


def _apply_colors_best_effort(new_annot: fitz.Annot, src_annot: fitz.Annot) -> None:
    """色、不透明度、枠線の太さを、取れる範囲でコピーする。"""
    try:
        colors = getattr(src_annot, "colors", None) or {}
        stroke = colors.get("stroke")
        fill = colors.get("fill")
        if stroke or fill:
            new_annot.set_colors(stroke=stroke, fill=fill)
    except Exception:
        pass

    try:
        op = getattr(src_annot, "opacity", None)
        if op is not None:
            new_annot.set_opacity(op)
    except Exception:
        pass

    try:
        b = getattr(src_annot, "border", None)
        if b and isinstance(b, dict) and "width" in b:
            new_annot.set_border(width=b["width"])
    except Exception:
        pass


def _copy_one_annot_as_visible_comment(
    target_page: fitz.Page,
    annot: fitz.Annot,
    text: str,
    target_rect: fitz.Rect,
    prefer_sticky: bool = True,
) -> None:
    """コメント本文が見える形で注釈をコピーする。

    テキスト注釈とポップアップは付箋にする。フリーテキストは枠を確保して残す。
    それ以外は、本文を失わないよう付箋にまとめる。
    """
    a_type = _annot_type_name(annot)

    if prefer_sticky and a_type in ("text", "popup"):
        p = (target_rect.x0, target_rect.y0)
        new_annot = target_page.add_text_annot(p, text)
        _apply_colors_best_effort(new_annot, annot)
        try:
            new_annot.update()
        except Exception:
            pass
        return

    if a_type == "freetext":
        r = _min_rect(target_rect)
        new_annot = target_page.add_freetext_annot(r, text)
        _apply_colors_best_effort(new_annot, annot)
        try:
            new_annot.update()
        except Exception:
            pass
        return

    p = (target_rect.x0, target_rect.y0)
    new_annot = target_page.add_text_annot(p, text)
    _apply_colors_best_effort(new_annot, annot)
    try:
        new_annot.update()
    except Exception:
        pass


def _get_ink_strokes(annot: fitz.Annot):
    """手書き注釈の筆跡を list[list[(x, y)]] で返す。

    PyMuPDF の版によって inklist か vertices のどちらかに入る。
    """
    inklist = getattr(annot, "inklist", None)
    if inklist:
        strokes = []
        for stroke in inklist:
            pts = []
            for p in stroke:
                if hasattr(p, "x") and hasattr(p, "y"):
                    pts.append((float(p.x), float(p.y)))
                else:
                    pts.append((float(p[0]), float(p[1])))
            strokes.append(pts)
        return strokes

    verts = getattr(annot, "vertices", None)
    if verts:
        if len(verts) > 0 and isinstance(verts[0], (list, tuple)):
            first = verts[0][0] if len(verts[0]) > 0 else None
            if first is not None and (hasattr(first, "x") or (isinstance(first, (list, tuple)) and len(first) == 2)):
                strokes = []
                for stroke in verts:
                    pts = []
                    for p in stroke:
                        if hasattr(p, "x") and hasattr(p, "y"):
                            pts.append((float(p.x), float(p.y)))
                        else:
                            pts.append((float(p[0]), float(p[1])))
                    strokes.append(pts)
                return strokes

        pts = []
        for p in verts:
            if hasattr(p, "x") and hasattr(p, "y"):
                pts.append((float(p.x), float(p.y)))
            else:
                pts.append((float(p[0]), float(p[1])))
        if pts:
            return [pts]

    return []


def _shift_strokes(strokes, dx: float, dy: float = 0.0):
    """筆跡を (dx, dy) だけ平行移動する。"""
    return [[(x + dx, y + dy) for (x, y) in stroke] for stroke in strokes]


def _copy_ink_annot(target_page: fitz.Page, src_annot: fitz.Annot, strokes) -> None:
    """手書き注釈を筆跡としてコピーし、色と線幅は取れる範囲で残す。"""
    new_annot = target_page.add_ink_annot(strokes)
    _apply_colors_best_effort(new_annot, src_annot)
    try:
        new_annot.update()
    except Exception:
        pass


def _filter_annots_for_side(
    page: fitz.Page,
    side: str,
    middle: float,
    spanning: str,
) -> None:
    """切り出したページに残す注釈だけを残し、種類は変えない。

    side が L なら左ページ用、R なら右ページ用の注釈を残す。
    """
    annots = list(page.annots() or [])
    for annot in annots:
        try:
            to_left, to_right = _decide_sides(annot.rect, middle, spanning)
            keep = to_left if side == "L" else to_right
            if not keep:
                page.delete_annot(annot)
        except Exception:
            continue


def split_doc(
    src: fitz.Document,
    order: str = "L",
    copy_annots: bool = True,
    spanning: str = "both",
) -> fitz.Document:
    """横長ページを左右2ページに分ける。縦長ページはそのままコピーする。

    copy_annots が真のときは、注釈オブジェクトを作り直さず、ページを複製して
    CropBox を当てる。残す注釈の種類は元のままになる。
    """
    order = order.upper()
    if order not in ("L", "R"):
        raise ValueError("order must be 'L' or 'R'")
    if spanning not in ("both", "drop", "left", "right"):
        raise ValueError("spanning must be one of: both, drop, left, right")

    out = fitz.open()

    for page_index in range(len(src)):
        page = src.load_page(page_index)
        rect = page.rect

        if rect.width <= rect.height:
            out.insert_pdf(src, from_page=page_index, to_page=page_index)
            continue

        middle = rect.width / 2.0

        left_crop = fitz.Rect(0, 0, middle, rect.height)
        right_crop = fitz.Rect(middle, 0, rect.width, rect.height)

        # ページの複製と CropBox で、注釈の種類を保ったまま左右に分ける。
        if order == "L":
            sides = (("L", left_crop), ("R", right_crop))
        else:
            sides = (("R", right_crop), ("L", left_crop))

        for side, crop in sides:
            out.insert_pdf(src, from_page=page_index, to_page=page_index, annots=copy_annots)
            new_page = out[-1]
            new_page.set_cropbox(crop)

            if copy_annots:
                _filter_annots_for_side(new_page, side=side, middle=middle, spanning=spanning)

    return out


def flatten_doc(src: fitz.Document, dpi: int = 144) -> fitz.Document:
    """各ページを画像にして新しいPDFを作る。注釈の見た目は焼き込まれ、編集はできない。"""
    if dpi <= 0:
        raise ValueError("dpi must be positive")

    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)

    out = fitz.open()
    for i in range(len(src)):
        page = src.load_page(i)
        rect = page.rect
        pix = page.get_pixmap(matrix=mat, alpha=False, annots=True)
        new_page = out.new_page(width=rect.width, height=rect.height)
        new_page.insert_image(rect, stream=pix.tobytes("png"))
    return out


def iter_pdf_files(input_path: Path, recursive: bool) -> list[Path]:
    """入力パスから処理対象のPDF一覧を返す。"""
    if input_path.is_file():
        return [input_path]
    if not input_path.is_dir():
        raise FileNotFoundError(f"Input path not found: {input_path}")
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted(input_path.glob(pattern))


def build_output_path(in_file: Path, out_dir: Path, suffix: str) -> Path:
    """出力ファイルのパスを組み立てる。"""
    return out_dir / f"{in_file.stem}{suffix}{in_file.suffix}"


def process_one(
    in_file: Path,
    out_file: Path,
    order: str,
    mode: str,
    spanning: str,
    dpi: int,
    overwrite: bool,
) -> str:
    """1つのPDFを分割して保存する。同名があり上書きしないときはスキップする。"""
    if out_file.exists() and not overwrite:
        return f"SKIP (exists): {out_file.name}"

    src = fitz.open(str(in_file))

    split = split_doc(src, order=order, copy_annots=True, spanning=spanning)

    if mode == "flatten":
        flat = flatten_doc(split, dpi=dpi)
        flat.save(str(out_file))
        flat.close()
    else:
        split.save(str(out_file))

    split.close()
    src.close()
    return f"OK: {in_file.name} -> {out_file.name} ({mode})"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description="横長のPDFページを左右に分割する。既定では注釈を残す。flatten で見た目を画像に焼き込める。"
    )
    p.add_argument("-i", "--input", required=True, help="入力PDF、またはPDFが入ったディレクトリ")
    p.add_argument("-o", "--output-dir", required=True, help="出力先ディレクトリ")
    p.add_argument("--order", default="L", choices=["L", "R"], help="L は左から右、R は右から左（右綴じ）")
    p.add_argument("--mode", default="keep", choices=["keep", "flatten"], help="keep は編集可能（既定）、flatten は画像化")
    p.add_argument("--spanning", default="both", choices=["both", "drop", "left", "right"],
                   help="中央をまたぐ注釈の扱い（既定: both）")
    p.add_argument("--dpi", type=int, default=144, help="flatten の解像度（既定: 144）")
    p.add_argument("--recursive", action="store_true", help="入力がディレクトリのとき、下位フォルダのPDFも対象にする")
    p.add_argument("--suffix", default="_split", help="出力ファイル名に付ける末尾（既定: _split）")
    p.add_argument("--overwrite", action="store_true", help="同名の出力ファイルを上書きする")
    args = p.parse_args(argv)

    input_path = Path(args.input).expanduser().resolve()
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = iter_pdf_files(input_path, args.recursive)
    if not pdf_files:
        print("No PDF files found.", file=sys.stderr)
        return 2

    ok = 0
    ng = 0
    for in_file in pdf_files:
        out_file = build_output_path(in_file, out_dir, args.suffix)
        try:
            msg = process_one(
                in_file=in_file,
                out_file=out_file,
                order=args.order,
                mode=args.mode,
                spanning=args.spanning,
                dpi=args.dpi,
                overwrite=args.overwrite,
            )
            print(msg)
            if msg.startswith("OK"):
                ok += 1
        except Exception as e:
            print(f"NG: {in_file.name} ({e})", file=sys.stderr)
            ng += 1

    print(f"Done. OK={ok}, NG={ng}")
    return 0 if ng == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
