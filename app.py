import os

# Must be set before any Tesseract subprocess is spawned. On a throttled
# / fractional-CPU host (e.g. Render free tier), Tesseract's default
# internal multi-threading just adds scheduling overhead with no real
# extra core to use -- forcing 1 thread is measurably faster there.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

import gc
import io
import json
import math
import re
import threading
import time
import uuid
import cv2
from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_cors import CORS
import numpy as np
from PIL import Image
import pytesseract
from pytesseract import Output

app = Flask(__name__)
CORS(app)
IMAGES_DIR = os.path.join(app.root_path, "static", "images")

# Free-tier hosts give you a fraction of one CPU core, not real
# parallelism. Rather than let concurrent uploads fight over that sliver
# of CPU (which is what was causing the "stuck on loading" behavior),
# serialize OCR work: one job runs at a time, others wait their turn.
_ocr_lock = threading.Lock()

app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

# Where flagged uploads (ones that failed the arithmetic self-check) get
# saved for later review.
FLAGGED_DIR = os.environ.get("FLAGGED_UPLOADS_DIR", "flagged_uploads")


def _save_flagged_upload(image_bytes, filename, extracted_grid, warnings):
    """
    Persist an upload that failed the arithmetic self-check: the original
    image, plus what OCR actually extracted, plus why it was flagged. Best
    effort only -- a logging failure must never break the actual request.
    """
    try:
        os.makedirs(FLAGGED_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        uid = uuid.uuid4().hex[:8]
        base = f"{stamp}-{uid}"

        ext = os.path.splitext(filename or "")[1] or ".jpg"
        with open(os.path.join(FLAGGED_DIR, base + ext), "wb") as f:
            f.write(image_bytes)

        sidecar = {
            "original_filename": filename,
            "warnings": warnings,
            "extracted_grid": [
                [cell["text"] for cell in row] for row in extracted_grid
            ],
        }
        with open(os.path.join(FLAGGED_DIR, base + ".json"), "w") as f:
            json.dump(sidecar, f, indent=2)
    except Exception as e:
        print(f"[flagged-upload] failed to save flagged upload: {e}")


# --- OCR-noise-tolerant numeric parsing -------------------------------------
_OCR_DIGIT_FIX = str.maketrans({
    "o": "0", "O": "0", "i": "1", "I": "1", "l": "1",
    "s": "5", "S": "5", "z": "2", "Z": "2", "b": "8", "B": "8",
})


def parse_score(val):
    if not val or str(val).strip() in ["-", "--", "null", "None", "", "."]:
        return 0.0
    part = str(val).split("/")[0] if ("/" in str(val)) else val
    try:
        return float(part)
    except (ValueError, TypeError):
        try:
            return float(str(part).translate(_OCR_DIGIT_FIX))
        except (ValueError, TypeError):
            return 0.0


def round_half_up(n):
    return math.floor(n + 0.5)


def is_blank_or_none(val):
    """Utility to verify if a raw cell value is empty, absent, or a placeholder."""
    if val is None:
        return True
    s = str(val).strip()
    return s in ["", "-", "--", "None", "null", "."]


def calculate_analytics(
    u1, u2, u3, obj, assign, mid2_u1=None, mid2_u2=None, mid2_u3=None, mid2_obj=None, mid2_assign=None
):
    m1_units_sum = parse_score(u1) + parse_score(u2) + parse_score(u3)
    m1_units = round_half_up(m1_units_sum / 2.0)
    mid1_total = int(m1_units + parse_score(obj) + parse_score(assign))

    # Check if all Mid 2 fields are missing/pending
    mid2_fields = [mid2_u1, mid2_u2, mid2_u3, mid2_obj, mid2_assign]
    is_mid2_pending = all(is_blank_or_none(f) for f in mid2_fields)

    if is_mid2_pending:
        return {
            "Mid1_Score": mid1_total,
            "Mid2_Score": None,
            "Best_Mid_80": None,
            "Other_Mid_20": None,
            "Final_Mid_Average": None,
            "Required_Sem_Marks": None,
        }

    m2_units_sum = parse_score(mid2_u1) + parse_score(mid2_u2) + parse_score(mid2_u3)
    m2_units = round_half_up(m2_units_sum / 2.0)
    mid2_total = int(m2_units + parse_score(mid2_obj) + parse_score(mid2_assign))

    best_mid = max(mid1_total, mid2_total)
    other_mid = min(mid1_total, mid2_total)

    best_80 = int(round_half_up(best_mid * 0.8))
    other_20 = int(round_half_up(other_mid * 0.2)) if (mid1_total > 0 or mid2_total > 0) else 0

    final_mid_avg = best_80 + other_20
    req_sem = max(24, 40 - final_mid_avg)

    return {
        "Mid1_Score": mid1_total,
        "Mid2_Score": mid2_total,
        "Best_Mid_80": best_80,
        "Other_Mid_20": other_20,
        "Final_Mid_Average": final_mid_avg,
        "Required_Sem_Marks": req_sem,
    }


def erase_grid_lines(gray, table_grid):
    line_mask = cv2.dilate(table_grid, np.ones((3, 3), np.uint8), iterations=1)
    cleaned = gray.copy()
    cleaned[line_mask > 0] = 255
    return cleaned


INK_DENSITY_THRESHOLD = 0.015
OCR_UPSCALE = 2


def ocr_grid_batch(cleaned_gray, thresh, grid_rows):
    extracted_grid = []
    pad = 4
    for row in grid_rows:
        rx0 = max(0, min(b[0] for b in row) - pad)
        ry0 = max(0, min(b[1] for b in row) - pad)
        rx1 = min(cleaned_gray.shape[1], max(b[0] + b[2] for b in row) + pad)
        ry1 = min(cleaned_gray.shape[0], max(b[1] + b[3] for b in row) + pad)
        row_crop = cleaned_gray[ry0:ry1, rx0:rx1]
        row_crop_up = cv2.resize(
            row_crop, None, fx=OCR_UPSCALE, fy=OCR_UPSCALE, interpolation=cv2.INTER_CUBIC
        )

        sparse = []
        for (x, y, w, h) in row:
            inner = thresh[y + 4:y + h - 4, x + 4:x + w - 4]
            if inner.size == 0:
                sparse.append(True)
                continue
            density = np.count_nonzero(inner) / inner.size
            sparse.append(density < INK_DENSITY_THRESHOLD)

        data = pytesseract.image_to_data(
            row_crop_up, config="--oem 1 --psm 7", output_type=Output.DICT
        )

        col_words = {c_idx: [] for c_idx in range(len(row))}
        n = len(data["text"])
        for i in range(n):
            word = data["text"][i].strip()
            if not word:
                continue
            wx = (data["left"][i] + data["width"][i] / 2) / OCR_UPSCALE + rx0
            for c_idx, (x, y, w, h) in enumerate(row):
                if x <= wx <= x + w:
                    col_words[c_idx].append((data["left"][i], word))
                    break

        row_cells = []
        for c_idx, (x, y, w, h) in enumerate(row):
            if sparse[c_idx]:
                text = ""
            else:
                words = sorted(col_words[c_idx], key=lambda t: t[0])
                text = " ".join(wd for _, wd in words).strip()
            row_cells.append({"x0": x, "x1": x + w, "text": text})
        extracted_grid.append(row_cells)

    return extracted_grid


def cell_text(row, idx):
    return row[idx]["text"] if 0 <= idx < len(row) else ""


def align_row_to_header(row, header_cells):
    aligned = []
    for h in header_cells:
        best_text, best_overlap = "", 0
        for c in row:
            overlap = min(h["x1"], c["x1"]) - max(h["x0"], c["x0"])
            if overlap > best_overlap:
                best_overlap = overlap
                best_text = c["text"]
        min_overlap = 0.5 * (h["x1"] - h["x0"])
        aligned.append(best_text if best_overlap >= min_overlap else "")
    return aligned


TARGET_COLUMNS = [
    "Unit-1", "Unit-2", "Unit-3(1)", "Obj-1(A)", "Assignment-1(A)", "Internal-I total",
    "Unit-3(2)", "Unit-4", "Unit-5", "Obj-2(A)", "Assignment-2(A)", "Internal-II total",
]


def check_section_consistency(components, printed_total, section_label):
    if is_blank_or_none(printed_total):
        return None
    expected = sum(parse_score(c) for c in components)
    printed = parse_score(printed_total)
    if abs(expected - printed) > 0.01:
        return (
            f"{section_label}: extracted marks sum to {expected:.0f}, "
            f"but the sheet shows {printed_total} -- one of these was "
            f"likely misread, please double-check this subject"
        )
    return None


def _digit(ch):
    return "1" if ch in ("i", "l", "1") else ch


def normalize_metric_label(raw_label):
    s = raw_label.lower().strip()
    s = re.sub(r"\s+", "", s)
    if not s:
        return None

    m = re.match(r"^unit-?([1-5s])\)?\(?([12il])?\)?", s)
    if m:
        digit = "5" if m.group(1) == "s" else m.group(1)
        if digit == "3":
            return "Unit-3(2)" if m.group(2) == "2" else "Unit-3(1)"
        return f"Unit-{digit}"

    m = re.match(r"^obj-?([i1l2])\)?\(?a\)?", s)
    if m:
        return f"Obj-{_digit(m.group(1))}(A)"

    m = re.match(r"^assign(ment)?-?([i1l2])\)?\(?a\)?", s)
    if m:
        return f"Assignment-{_digit(m.group(2))}(A)"

    m = re.match(r"^internal-?([il1|!]{1,2})\s*total", s)
    if m:
        return "Internal-II total" if len(m.group(1)) == 2 else "Internal-I total"

    return None


@app.route("/")
def home():
    return render_template("index.html")

@app.route("/girl")
def girl_page():
    return render_template("girl.html")

@app.route("/boy")
def boy_page():
    return render_template("boy.html")

@app.route("/calculator")
def calculator_page():
    return render_template("calculator.html")

@app.route("/images/<path:filename>")
def serve_image(filename):
    return send_from_directory(IMAGES_DIR, filename)

@app.route("/process-image", methods=["POST"])
def process_image():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    acquired = _ocr_lock.acquire(timeout=90)
    if not acquired:
        return jsonify({
            "error": "Server is busy processing another upload right now. Please try again in a few seconds."
        }), 503

    try:
        image_bytes = file.read()
        pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        max_width = 1200
        if pil_img.width > max_width:
            ratio = max_width / float(pil_img.width)
            new_height = int(float(pil_img.height) * ratio)
            pil_img = pil_img.resize((max_width, new_height), Image.Resampling.LANCZOS)

        img = np.array(pil_img)
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

        thresh = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )[1]

        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 30))

        h_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, h_kernel)
        v_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, v_kernel)

        table_grid = cv2.add(h_lines, v_lines)

        grid_h, grid_w = table_grid.shape
        cv2.rectangle(table_grid, (0, 0), (grid_w - 1, grid_h - 1), 255, thickness=3)

        contours, _ = cv2.findContours(
            table_grid, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
        )

        boxes = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if w > 20 and h > 12 and w < img.shape[1] * 0.9:
                boxes.append((x, y, w, h))

        if not boxes:
            return jsonify({"error": "Could not detect table grid lines"}), 400

        boxes = sorted(boxes, key=lambda b: b[1])

        row_threshold = 12
        grid_rows = []
        for box in boxes:
            placed = False
            for r in grid_rows:
                avg_y = sum(b[1] for b in r) / len(r)
                if abs(box[1] - avg_y) < row_threshold:
                    r.append(box)
                    placed = True
                    break
            if not placed:
                grid_rows.append([box])

        for r in grid_rows:
            r.sort(key=lambda b: b[0])

        cleaned_gray = erase_grid_lines(gray, table_grid)
        extracted_grid = ocr_grid_batch(cleaned_gray, thresh, grid_rows)

        header_idx = 0
        for i, row in enumerate(extracted_grid):
            if row and "subject" in cell_text(row, 0).lower():
                header_idx = i
                break
        header_row = extracted_grid[header_idx]

        subjects = []
        subject_columns = []
        for idx, cell in enumerate(header_row):
            cleaned = cell["text"].strip()
            if idx > 0 and cleaned.lower() not in ["subject", "total", ""] and len(cleaned) > 0:
                subjects.append(cleaned)
                subject_columns.append(cell)

        if not subjects and len(header_row) > 1:
            for idx in range(1, len(header_row)):
                subjects.append(header_row[idx]["text"] or f"Subject_{idx}")
                subject_columns.append(header_row[idx])

        subject_data = {s: {col: None for col in TARGET_COLUMNS} for s in subjects}

        for r_idx in range(header_idx + 1, len(extracted_grid)):
            row = extracted_grid[r_idx]
            if not row:
                continue

            raw_label = cell_text(row, 0)
            matched_key = normalize_metric_label(raw_label)

            if not matched_key:
                continue

            row_values = align_row_to_header(row, subject_columns)

            for subject, cell_val in zip(subjects, row_values):
                cell_val = cell_val.replace(" ", "")
                if cell_val and cell_val not in ["-", "--", "None", "null", "."]:
                    subject_data[subject][matched_key] = cell_val

        final_rows = []
        all_warnings = []
        for s in subjects:
            s_dict = {"Subject": s}
            s_dict.update(subject_data[s])

            analytics = calculate_analytics(
                u1=s_dict["Unit-1"],
                u2=s_dict["Unit-2"],
                u3=s_dict["Unit-3(1)"],
                obj=s_dict["Obj-1(A)"],
                assign=s_dict["Assignment-1(A)"],
                mid2_u1=s_dict["Unit-3(2)"],
                mid2_u2=s_dict["Unit-4"],
                mid2_u3=s_dict["Unit-5"],
                mid2_obj=s_dict["Obj-2(A)"],
                mid2_assign=s_dict["Assignment-2(A)"],
            )

            s_dict.update(analytics)

            row_warnings = []
            w1 = check_section_consistency(
                [s_dict["Unit-1"], s_dict["Unit-2"], s_dict["Unit-3(1)"],
                 s_dict["Obj-1(A)"], s_dict["Assignment-1(A)"]],
                s_dict["Internal-I total"], f"{s} / Internal-I",
            )
            if w1:
                row_warnings.append(w1)
            w2 = check_section_consistency(
                [s_dict["Unit-3(2)"], s_dict["Unit-4"], s_dict["Unit-5"],
                 s_dict["Obj-2(A)"], s_dict["Assignment-2(A)"]],
                s_dict["Internal-II total"], f"{s} / Internal-II",
            )
            if w2:
                row_warnings.append(w2)

            if row_warnings:
                s_dict["_needs_review"] = True
                all_warnings.extend(row_warnings)

            final_rows.append(s_dict)

        if all_warnings:
            _save_flagged_upload(image_bytes, file.filename, extracted_grid, all_warnings)

        gc.collect()
        response = {"rows": final_rows}
        if all_warnings:
            response["warnings"] = all_warnings
        return jsonify(response)

    except Exception as e:
        gc.collect()
        return jsonify({"error": str(e)}), 500
    finally:
        _ocr_lock.release()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
