# Pscmr Mid Calci

Upload a photo of your internal-marks table and get your mid marks calculated automatically.

**Live demo:** <your-render-url>
*(Hosted on Render's free plan, so the first load after a quiet period can take a while while the server wakes up.)*

<!-- Add a screenshot or GIF here: ![Calculator screenshot](static/images/screenshot.png) -->

## Why I built this

Working out mid marks by hand from a marks sheet is slow and easy to get wrong. This tool reads the table straight from a photo, extracts every subject's marks, and applies the college's mid-marks scheme for you. About 60 classmates use it.

## Features

- **Photo to marks:** upload an image of the marks table and the app detects the grid, reads each cell, and fills in every subject.
- **Automatic calculation:** Mid 1 and Mid 2 scores, the best-mid (80%) and other-mid (20%) weighting, the final mid average, and the semester marks you still need.
- **Self-check for misreads:** the app adds up the extracted components and compares them with the totals printed on the sheet. If they don't match, that subject is flagged so you can double-check it.
- **Works when Mid 2 is pending:** if Mid 2 marks are blank, only Mid 1 is calculated.
- **Light on resources:** tuned to run on a throttled free-tier CPU.

## How it works

1. **Preprocess:** resize the image (max width 1200 px), convert to grayscale, and apply Otsu thresholding.
2. **Find the table:** morphological opening with horizontal and vertical kernels isolates the grid lines, and contour detection finds each cell. Cells are grouped into rows by y-coordinate.
3. **Clean:** the grid lines are erased so they aren't read as characters.
4. **Read:** each row is cropped, upscaled 2x, and passed to Tesseract (LSTM engine, single-line mode). Words are mapped to columns by their horizontal position.
5. **Handle OCR noise:**
   - an ink-density check skips empty cells
   - tolerant regex normalises messy row labels (for example `l`, `i` and `1` confusion)
   - values are aligned to subject headers by horizontal overlap
   - numeric parsing corrects common character mix-ups (for example `O` to `0`)
6. **Verify:** the arithmetic self-check compares the extracted marks with the printed totals and warns about any mismatch.
7. **Calculate:** the marks scheme is applied (see below).

## Marks scheme (as implemented)

- **Internal score** = round-half-up((Unit A + Unit B + Unit C) / 2) + Objective + Assignment
- **Final mid average** = round-half-up(best mid x 0.8) + round-half-up(other mid x 0.2)
- **Required semester marks** = max(24, 40 - final mid average)

See `calculate_analytics()` in `app.py`. If your college uses a different scheme, change it there.

## Tech stack

- **Backend:** Python, Flask, Flask-CORS
- **Vision and OCR:** OpenCV, Tesseract (via pytesseract), Pillow, NumPy
- **Frontend:** HTML, CSS, JavaScript
- **Hosting:** Render (free plan)
- **Contact form:** Web3Forms

## Run locally

1. Install [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (for example `sudo apt install tesseract-ocr` on Ubuntu or `brew install tesseract` on macOS).
2. Install the Python dependencies:
   ```bash
   pip install flask flask-cors opencv-python-headless numpy pillow pytesseract
   ```
3. Start the app:
   ```bash
   python app.py
   ```
4. Open `http://localhost:5000`.

## Routes

| Route | Purpose |
| --- | --- |
| `/` | Choose your gender (landing page) |
| `/girl`, `/boy` | Welcome pages leading to the calculator |
| `/calculator` | Upload, preview and calculate |
| `POST /process-image` | Takes an image file, returns JSON with `rows` and optional `warnings` |

`/process-image` responds with `400` for a missing file or undetectable table, `503` if the server is busy with another upload, and `500` for unexpected errors.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `5000` | Port the server listens on |
| `FLAGGED_UPLOADS_DIR` | `flagged_uploads` | Where uploads that fail the self-check are saved for review |
| `OMP_THREAD_LIMIT` | `1` (set in code) | Forces single-threaded Tesseract, which is faster on fractional-CPU hosts |

## Privacy

Uploaded images are processed in memory. Only an upload that **fails the self-check** is saved (the image, the extracted grid, and the warnings) so misreads can be reviewed and the reader improved. These files can contain a student's marks, so the `flagged_uploads/` folder is excluded from the repository (see `.gitignore`) and should be cleared regularly.

## Limitations

- Built for one specific marks-sheet layout. Other layouts will need changes to the row labels and columns.
- Works best with a clear, straight, well-lit photo. Blurry or heavily tilted images can cause misreads.
- Always check any subject the app flags before relying on the result.

## Roadmap

- Attendance calculator
