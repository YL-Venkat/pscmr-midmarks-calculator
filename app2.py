# import os

# # Must be set before any Tesseract subprocess is spawned. On a throttled
# # / fractional-CPU host (e.g. Render free tier), Tesseract's default
# # internal multi-threading just adds scheduling overhead with no real
# # extra core to use -- forcing 1 thread is measurably faster there.
# os.environ.setdefault("OMP_THREAD_LIMIT", "1")

# import gc
# import io
# import json
# import math
# import re
# import threading
# import time
# import uuid
# import cv2
# from flask import Flask, jsonify, render_template, request
# from flask_cors import CORS
# import numpy as np
# from PIL import Image
# import pytesseract
# from pytesseract import Output

# app = Flask(__name__)
# CORS(app)

# # Free-tier hosts give you a fraction of one CPU core, not real
# # parallelism. Rather than let concurrent uploads fight over that sliver
# # of CPU (which is what was causing the "stuck on loading" behavior),
# # serialize OCR work: one job runs at a time, others wait their turn.
# _ocr_lock = threading.Lock()

# app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

# # Where flagged uploads (ones that failed the arithmetic self-check) get
# # saved for later review, so real failures accumulate on disk instead of
# # only being visible to whichever user happened to hit them.
# #
# # CAVEAT: on an ephemeral host (Render free tier, most PaaS free tiers),
# # the local filesystem is wiped on every redeploy/restart -- this is a
# # starting point for "have something to look at locally / in a short-lived
# # session", not a durable audit trail. For that, point FLAGGED_DIR at a
# # mounted persistent volume, or swap `_save_flagged_upload` to upload to
# # S3/GCS/etc instead of writing to disk.
# FLAGGED_DIR = os.environ.get("FLAGGED_UPLOADS_DIR", "flagged_uploads")


# def _save_flagged_upload(image_bytes, filename, extracted_grid, warnings):
#     """
#     Persist an upload that failed the arithmetic self-check: the original
#     image, plus what OCR actually extracted, plus why it was flagged. Best
#     effort only -- a logging failure must never break the actual request,
#     so any exception here is swallowed (and printed, so it's still visible
#     in server logs).
#     """
#     try:
#         os.makedirs(FLAGGED_DIR, exist_ok=True)
#         stamp = time.strftime("%Y%m%d-%H%M%S")
#         uid = uuid.uuid4().hex[:8]
#         base = f"{stamp}-{uid}"

#         ext = os.path.splitext(filename or "")[1] or ".jpg"
#         with open(os.path.join(FLAGGED_DIR, base + ext), "wb") as f:
#             f.write(image_bytes)

#         sidecar = {
#             "original_filename": filename,
#             "warnings": warnings,
#             "extracted_grid": [
#                 [cell["text"] for cell in row] for row in extracted_grid
#             ],
#         }
#         with open(os.path.join(FLAGGED_DIR, base + ".json"), "w") as f:
#             json.dump(sidecar, f, indent=2)
#     except Exception as e:
#         print(f"[flagged-upload] failed to save flagged upload: {e}")


# # --- OCR-noise-tolerant numeric parsing -------------------------------------
# # Tesseract commonly confuses certain letters/digits (O<->0, I/l<->1, S<->5,
# # Z<->2, B<->8). Without this, a real mark like "S/10" silently becomes 0.0
# # with no signal that anything went wrong -- a mark quietly turning into a
# # zero is much worse than a mark that fails to parse loudly, so we try the
# # OCR-corrected reading before giving up.
# _OCR_DIGIT_FIX = str.maketrans({
#     "o": "0", "O": "0", "i": "1", "I": "1", "l": "1",
#     "s": "5", "S": "5", "z": "2", "Z": "2", "b": "8", "B": "8",
# })


# def parse_score(val):
#     if not val or val in ["-", "--", "null", "None", "", "."]:
#         return 0.0
#     part = val.split("/")[0] if (isinstance(val, str) and "/" in val) else val
#     try:
#         return float(part)
#     except (ValueError, TypeError):
#         try:
#             return float(str(part).translate(_OCR_DIGIT_FIX))
#         except (ValueError, TypeError):
#             return 0.0


# def round_half_up(n):
#     return math.floor(n + 0.5)


# def calculate_analytics(
#     u1, u2, u3, obj, assign, mid2_u1=0, mid2_u2=0, mid2_u3=0, mid2_obj=0, mid2_assign=0
# ):
#     m1_units_sum = parse_score(u1) + parse_score(u2) + parse_score(u3)
#     m1_units = round_half_up(m1_units_sum / 2.0)
#     mid1_total = int(m1_units + parse_score(obj) + parse_score(assign))

#     m2_units_sum = parse_score(mid2_u1) + parse_score(mid2_u2) + parse_score(mid2_u3)
#     m2_units = round_half_up(m2_units_sum / 2.0)
#     mid2_total = int(m2_units + parse_score(mid2_obj) + parse_score(mid2_assign))

#     best_mid = max(mid1_total, mid2_total)
#     other_mid = min(mid1_total, mid2_total)

#     best_80 = int(round_half_up(best_mid * 0.8))
#     other_20 = int(round_half_up(other_mid * 0.2)) if (mid1_total > 0 or mid2_total > 0) else 0

#     final_mid_avg = best_80 + other_20
#     req_sem = max(24, 40 - final_mid_avg)

#     return {
#         "Mid1_Score": mid1_total,
#         "Mid2_Score": mid2_total,
#         "Best_Mid_80": best_80,
#         "Other_Mid_20": other_20,
#         "Final_Mid_Average": final_mid_avg,
#         "Required_Sem_Marks": req_sem,
#     }


# def erase_grid_lines(gray, table_grid):
#     """
#     Paint over the detected table grid lines so Tesseract doesn't read
#     them as stray characters (vertical borders get misread as '|', '*',
#     etc. and corrupt neighboring words) -- this is what made a single
#     whole-table OCR pass unreliable.
#     """
#     line_mask = cv2.dilate(table_grid, np.ones((3, 3), np.uint8), iterations=1)
#     cleaned = gray.copy()
#     cleaned[line_mask > 0] = 255
#     return cleaned


# INK_DENSITY_THRESHOLD = 0.015  # 1.5% of cell area -- dashes measure <0.5%,
#                                  # real marks measure 4.5%+, so this holds
#                                  # up across different image resolutions
#                                  # (unlike a hardcoded pixel count would)

# # Small screenshots/photos often render marks at a genuine pixel height of
# # ~15-18px after the table is cropped -- legible to a human, but too small
# # for Tesseract to reliably tell "/1" apart from nothing (verified: a real
# # "7/10" cell was read as "70" and "9/10" as "ono" at native resolution on
# # one user's screenshot). Upscaling each row crop before OCR fixed those
# # misreads. IMPORTANT: 2x is the tested sweet spot, not "more is better" --
# # 3x was tried first and actually *introduced* a new misread on a different
# # sample (a "5" rendered large enough that Tesseract read it as "9"), so
# # this is a measured trade-off, not a knob to bump casually.
# OCR_UPSCALE = 2


# def ocr_grid_batch(cleaned_gray, thresh, grid_rows):
#     """
#     Run Tesseract once PER ROW (not once per cell, and not once for the
#     whole table).

#     - Per-cell is accurate but spawns a subprocess per cell -- the main
#       source of slowness (rows * cols calls).
#     - Whole-table-at-once is fast but unreliable: Tesseract's page
#       segmentation assumes flowing text, so it merges adjacent cells'
#       text into single "words" and misreads grid lines as characters.
#     - Per-row is the sweet spot: a row genuinely *is* one line of text,
#       so Tesseract segments it correctly, and it cuts subprocess calls
#       down to just the row count.

#     Before trusting any OCR result, each cell is first checked for ink
#     density on the (pre-cleanup) binary `thresh` image. A cell that's
#     essentially blank -- a lone "-" -- can get misread by Tesseract as a
#     stray digit or symbol; treating any near-empty cell as blank instead
#     of trusting that guess avoids phantom marks like a "-" becoming "2".

#     Each returned cell carries its own x-range alongside its text
#     (not just a bare string) so callers can line columns up across rows
#     by actual horizontal position instead of by list index. That matters
#     because grid detection doesn't guarantee every row has the same
#     number of cells -- a single faint/broken internal line in just one
#     row means one cell's "hole" never closes and that row comes back
#     one cell short. Matching by index in that case silently shifts every
#     later value in the row onto the wrong subject; matching by position
#     just leaves that one cell blank.
#     """
#     extracted_grid = []
#     pad = 4
#     for row in grid_rows:
#         rx0 = max(0, min(b[0] for b in row) - pad)
#         ry0 = max(0, min(b[1] for b in row) - pad)
#         rx1 = min(cleaned_gray.shape[1], max(b[0] + b[2] for b in row) + pad)
#         ry1 = min(cleaned_gray.shape[0], max(b[1] + b[3] for b in row) + pad)
#         row_crop = cleaned_gray[ry0:ry1, rx0:rx1]
#         row_crop_up = cv2.resize(
#             row_crop, None, fx=OCR_UPSCALE, fy=OCR_UPSCALE, interpolation=cv2.INTER_CUBIC
#         )

#         sparse = []
#         for (x, y, w, h) in row:
#             inner = thresh[y + 4:y + h - 4, x + 4:x + w - 4]
#             if inner.size == 0:
#                 sparse.append(True)
#                 continue
#             density = np.count_nonzero(inner) / inner.size
#             sparse.append(density < INK_DENSITY_THRESHOLD)

#         data = pytesseract.image_to_data(
#             row_crop_up, config="--oem 1 --psm 7", output_type=Output.DICT
#         )

#         col_words = {c_idx: [] for c_idx in range(len(row))}
#         n = len(data["text"])
#         for i in range(n):
#             word = data["text"][i].strip()
#             if not word:
#                 continue
#             # data["left"]/["width"] are in the upscaled row crop's pixel
#             # space -- divide back down before comparing to the original
#             # (non-scaled) per-cell box coordinates.
#             wx = (data["left"][i] + data["width"][i] / 2) / OCR_UPSCALE + rx0
#             for c_idx, (x, y, w, h) in enumerate(row):
#                 if x <= wx <= x + w:
#                     col_words[c_idx].append((data["left"][i], word))
#                     break

#         row_cells = []
#         for c_idx, (x, y, w, h) in enumerate(row):
#             if sparse[c_idx]:
#                 text = ""
#             else:
#                 words = sorted(col_words[c_idx], key=lambda t: t[0])
#                 text = " ".join(wd for _, wd in words).strip()
#             row_cells.append({"x0": x, "x1": x + w, "text": text})
#         extracted_grid.append(row_cells)

#     return extracted_grid


# def cell_text(row, idx):
#     """Bare text of a cell dict list at position idx, or '' if out of range."""
#     return row[idx]["text"] if 0 <= idx < len(row) else ""


# def align_row_to_header(row, header_cells):
#     """
#     Return this row's text, one entry per header column, matched by
#     x-position rather than trusting that row[i] corresponds to
#     header[i]. For each header cell, pick whichever cell in this row has
#     the most horizontal overlap with it; if nothing overlaps enough
#     (that column's box never formed for this row), that slot is "".
#     This is what keeps one row's dropped/merged cell from silently
#     shifting every later column onto the wrong subject.
#     """
#     aligned = []
#     for h in header_cells:
#         best_text, best_overlap = "", 0
#         for c in row:
#             overlap = min(h["x1"], c["x1"]) - max(h["x0"], c["x0"])
#             if overlap > best_overlap:
#                 best_overlap = overlap
#                 best_text = c["text"]
#         # Require at least half the header column's width to actually
#         # overlap -- a sliver of overlap from a neighboring cell isn't a
#         # real match, it's just two adjacent boxes touching.
#         min_overlap = 0.5 * (h["x1"] - h["x0"])
#         aligned.append(best_text if best_overlap >= min_overlap else "")
#     return aligned


# # --- Row-label matching ------------------------------------------------------
# # The row labels (Unit-1, Obj-1(A), Internal-I total, ...) come from a
# # fixed, small vocabulary baked into calculate_analytics' formula -- these
# # aren't "headings" the sheet designer can rename, they're the mid-exam
# # structure itself. What *does* vary a lot between screenshots is how badly
# # OCR mangles them (S<->5, I/l<->1, roman numerals). Free-text fuzzy
# # matching against that vocabulary is actually dangerous here: it will
# # confidently match "Unit-S" to "Unit-1" instead of "Unit-5", and
# # "Internal-Il total" to "Internal-I total" instead of "Internal-II total"
# # (wrong section entirely). Matching the *shape* of each label pattern
# # instead avoids that trap.
# TARGET_COLUMNS = [
#     "Unit-1", "Unit-2", "Unit-3(1)", "Obj-1(A)", "Assignment-1(A)", "Internal-I total",
#     "Unit-3(2)", "Unit-4", "Unit-5", "Obj-2(A)", "Assignment-2(A)", "Internal-II total",
# ]


# # --- Self-consistency check (catches OCR misreads for free) ----------------
# # The sheet's own "Internal-I total" / "Internal-II total" is literally the
# # sum of that section's 5 component marks -- verified against every row in
# # every sample seen so far, exact match every time. That means we can catch
# # a misread digit with ZERO extra OCR calls: if the components we already
# # extracted don't add up to the total the sheet itself prints, at least one
# # of those 6 numbers was probably misread. This doesn't tell us *which* one,
# # but it turns "silently wrong" into "flagged for a human to glance at" --
# # which is the whole point, since no matching-logic fix can make character-
# # level OCR perfect on an image we've never seen.
# def check_section_consistency(components, printed_total, section_label):
#     if not printed_total:
#         return None  # nothing printed to check against (e.g. section absent)
#     expected = sum(parse_score(c) for c in components)
#     printed = parse_score(printed_total)
#     if abs(expected - printed) > 0.01:
#         return (
#             f"{section_label}: extracted marks sum to {expected:.0f}, "
#             f"but the sheet shows {printed_total} -- one of these was "
#             f"likely misread, please double-check this subject"
#         )
#     return None


# def _digit(ch):
#     return "1" if ch in ("i", "l", "1") else ch


# def normalize_metric_label(raw_label):
#     """
#     Returns the canonical column name for a row label, tolerant of common
#     OCR confusions, or None if the row isn't a recognized metric (a
#     section banner like "Internal-I", an extra row like "Lab Internal",
#     or OCR garbage). Returning None means "skip this row" rather than
#     guessing -- much safer than a positional fallback when the row order
#     can shift (banners, extra rows, missing sections all shift it).
#     """
#     s = raw_label.lower().strip()
#     s = re.sub(r"\s+", "", s)
#     if not s:
#         return None

#     m = re.match(r"^unit-?([1-5s])\)?\(?([12il])?\)?", s)
#     if m:
#         digit = "5" if m.group(1) == "s" else m.group(1)
#         if digit == "3":
#             return "Unit-3(2)" if m.group(2) == "2" else "Unit-3(1)"
#         return f"Unit-{digit}"

#     m = re.match(r"^obj-?([i1l2])\)?\(?a\)?", s)
#     if m:
#         return f"Obj-{_digit(m.group(1))}(A)"

#     m = re.match(r"^assign(ment)?-?([i1l2])\)?\(?a\)?", s)
#     if m:
#         return f"Assignment-{_digit(m.group(2))}(A)"

#     m = re.match(r"^internal-?([il1|!]{1,2})\s*total", s)
#     if m:
#         return "Internal-II total" if len(m.group(1)) == 2 else "Internal-I total"

#     return None


# @app.route("/")
# def home():
#     return render_template("index.html")

# @app.route("/guide")
# def guide_page():
#     return render_template("bs.html")

# @app.route("/process-image", methods=["POST"])
# def process_image():
#     if "file" not in request.files:
#         return jsonify({"error": "No file uploaded"}), 400

#     file = request.files["file"]
#     if file.filename == "":
#         return jsonify({"error": "No selected file"}), 400

#     # Wait up to 90s for another upload's OCR to finish rather than
#     # racing it for the same fractional CPU. This is what turns
#     # concurrent users into a short queue instead of a hang.
#     acquired = _ocr_lock.acquire(timeout=90)
#     if not acquired:
#         return jsonify({
#             "error": "Server is busy processing another upload right now. Please try again in a few seconds."
#         }), 503

#     try:
#         image_bytes = file.read()
#         pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

#         max_width = 1200
#         if pil_img.width > max_width:
#             ratio = max_width / float(pil_img.width)
#             new_height = int(float(pil_img.height) * ratio)
#             pil_img = pil_img.resize((max_width, new_height), Image.Resampling.LANCZOS)

#         img = np.array(pil_img)
#         gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

#         thresh = cv2.threshold(
#             gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
#         )[1]

#         h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
#         v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 30))

#         h_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, h_kernel)
#         v_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, v_kernel)

#         table_grid = cv2.add(h_lines, v_lines)

#         # Force-close the outer boundary before hole detection. Screenshots
#         # cropped tight to the table often have NO real top/bottom/left/right
#         # border line at all (verified: pure white right to the image edge,
#         # except tiny stubs where vertical lines happen to touch it). Without
#         # this, cv2.findContours' RETR_TREE can't form an enclosed "hole" for
#         # header-row, footer-row, and edge-column cells, and they silently
#         # vanish from `boxes` -- this is what caused whole rows/columns
#         # (header, totals row, first/last column) to go missing.
#         grid_h, grid_w = table_grid.shape
#         cv2.rectangle(table_grid, (0, 0), (grid_w - 1, grid_h - 1), 255, thickness=3)

#         contours, _ = cv2.findContours(
#             table_grid, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
#         )

#         boxes = []
#         for c in contours:
#             x, y, w, h = cv2.boundingRect(c)
#             if w > 20 and h > 12 and w < img.shape[1] * 0.9:
#                 boxes.append((x, y, w, h))

#         if not boxes:
#             return jsonify({"error": "Could not detect table grid lines"}), 400

#         boxes = sorted(boxes, key=lambda b: b[1])

#         row_threshold = 12
#         grid_rows = []
#         for box in boxes:
#             placed = False
#             for r in grid_rows:
#                 avg_y = sum(b[1] for b in r) / len(r)
#                 if abs(box[1] - avg_y) < row_threshold:
#                     r.append(box)
#                     placed = True
#                     break
#             if not placed:
#                 grid_rows.append([box])

#         for r in grid_rows:
#             r.sort(key=lambda b: b[0])

#         # --- Erase grid lines, then OCR once per ROW instead of once
#         # per CELL (rows * cols subprocess calls -> just rows) ---
#         cleaned_gray = erase_grid_lines(gray, table_grid)
#         extracted_grid = ocr_grid_batch(cleaned_gray, thresh, grid_rows)

#         # Find the header row by content ("Subject" in its first cell)
#         # rather than assuming it's always row 0. This matters once a
#         # table can have a title/section-banner row above the real header
#         # that the box-detection didn't fully filter out.
#         header_idx = 0
#         for i, row in enumerate(extracted_grid):
#             if row and "subject" in cell_text(row, 0).lower():
#                 header_idx = i
#                 break
#         header_row = extracted_grid[header_idx]

#         subjects = []
#         subject_columns = []  # header cell dicts (x0/x1/text), one per subject
#         for idx, cell in enumerate(header_row):
#             cleaned = cell["text"].strip()
#             if idx > 0 and cleaned.lower() not in ["subject", "total", ""] and len(cleaned) > 0:
#                 subjects.append(cleaned)
#                 subject_columns.append(cell)

#         if not subjects and len(header_row) > 1:
#             for idx in range(1, len(header_row)):
#                 subjects.append(header_row[idx]["text"] or f"Subject_{idx}")
#                 subject_columns.append(header_row[idx])

#         subject_data = {s: {col: None for col in TARGET_COLUMNS} for s in subjects}

#         for r_idx in range(header_idx + 1, len(extracted_grid)):
#             row = extracted_grid[r_idx]
#             if not row:
#                 continue

#             raw_label = cell_text(row, 0)
#             matched_key = normalize_metric_label(raw_label)

#             if not matched_key:
#                 # Section banner ("Internal-I"/"Internal-II"), an extra row
#                 # like "Lab Internal", or unreadable OCR -- skip rather than
#                 # guess a position-based label, since row order shifts
#                 # between single-section and two-section images.
#                 continue

#             # Align by x-position, not list index -- a row with a missing/
#             # merged cell elsewhere must not shift every value after it
#             # onto the wrong subject (see align_row_to_header docstring).
#             row_values = align_row_to_header(row, subject_columns)

#             for subject, cell_val in zip(subjects, row_values):
#                 cell_val = cell_val.replace(" ", "")
#                 if cell_val and cell_val not in ["-", "--", "None", "null", "."]:
#                     subject_data[subject][matched_key] = cell_val

#         final_rows = []
#         all_warnings = []
#         for s in subjects:
#             s_dict = {"Subject": s}
#             s_dict.update(subject_data[s])

#             analytics = calculate_analytics(
#                 u1=s_dict["Unit-1"],
#                 u2=s_dict["Unit-2"],
#                 u3=s_dict["Unit-3(1)"],
#                 obj=s_dict["Obj-1(A)"],
#                 assign=s_dict["Assignment-1(A)"],
#                 mid2_u1=s_dict["Unit-3(2)"],
#                 mid2_u2=s_dict["Unit-4"],
#                 mid2_u3=s_dict["Unit-5"],
#                 mid2_obj=s_dict["Obj-2(A)"],
#                 mid2_assign=s_dict["Assignment-2(A)"],
#             )

#             s_dict.update(analytics)

#             row_warnings = []
#             w1 = check_section_consistency(
#                 [s_dict["Unit-1"], s_dict["Unit-2"], s_dict["Unit-3(1)"],
#                  s_dict["Obj-1(A)"], s_dict["Assignment-1(A)"]],
#                 s_dict["Internal-I total"], f"{s} / Internal-I",
#             )
#             if w1:
#                 row_warnings.append(w1)
#             w2 = check_section_consistency(
#                 [s_dict["Unit-3(2)"], s_dict["Unit-4"], s_dict["Unit-5"],
#                  s_dict["Obj-2(A)"], s_dict["Assignment-2(A)"]],
#                 s_dict["Internal-II total"], f"{s} / Internal-II",
#             )
#             if w2:
#                 row_warnings.append(w2)

#             if row_warnings:
#                 s_dict["_needs_review"] = True
#                 all_warnings.extend(row_warnings)

#             final_rows.append(s_dict)

#         if all_warnings:
#             _save_flagged_upload(image_bytes, file.filename, extracted_grid, all_warnings)

#         gc.collect()
#         response = {"rows": final_rows}
#         if all_warnings:
#             response["warnings"] = all_warnings
#         return jsonify(response)

#     except Exception as e:
#         gc.collect()
#         return jsonify({"error": str(e)}), 500
#     finally:
#         _ocr_lock.release()

# if __name__ == "__main__":
#     port = int(os.environ.get("PORT", 5000))
#     # threaded=True only helps a little for local testing; for real
#     # multi-user traffic, run this behind gunicorn instead (see below).
#     app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
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
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
import numpy as np
from PIL import Image
import pytesseract
from pytesseract import Output

app = Flask(__name__)
CORS(app)

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

@app.route("/guide")
def guide_page():
    return render_template("bs.html")

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
