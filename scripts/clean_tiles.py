"""
Download KMIA ramp satellite tiles at zoom 19, detect aircraft via YOLOv8,
and inpaint them out using OpenCV.

Usage:  python clean_tiles.py
Output: tiles/original/  — raw Esri tiles
        tiles/masks/     — detection masks (white = plane)
        tiles/cleaned/   — inpainted tiles with planes removed
"""

import os
import time
from pathlib import Path

import cv2
import mercantile
import numpy as np
import requests
from ultralytics import YOLO

# ── Configuration ─────────────────────────────────────────────────────────────
TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
ZOOM = 19

# KMIA ramp area bounding box (west, south, east, north)
BOUNDS = (-80.295, 25.790, -80.270, 25.805)

# Directories
SCRIPT_DIR = Path(__file__).parent
ORIGINAL_DIR = SCRIPT_DIR / "tiles" / "original"
MASK_DIR = SCRIPT_DIR / "tiles" / "masks"
CLEANED_DIR = SCRIPT_DIR / "tiles" / "cleaned"

# Inpainting radius — larger = smoother fill but slower
INPAINT_RADIUS = 7

# Mask dilation — expand detection boxes to cover shadows/gear
DILATE_PIXELS = 15

# YOLOv8 confidence threshold
CONFIDENCE = 0.25

# COCO class ID for "aeroplane" (class 4 in COCO)
AIRPLANE_CLASS_ID = 4


def download_tile(tile: mercantile.Tile) -> Path:
    """Download a single tile and save as PNG. Returns the file path."""
    out_path = ORIGINAL_DIR / f"{tile.z}_{tile.x}_{tile.y}.png"
    if out_path.exists():
        return out_path

    url = TILE_URL.format(z=tile.z, y=tile.y, x=tile.x)
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    out_path.write_bytes(resp.content)
    return out_path


def detect_planes(model: YOLO, img: np.ndarray) -> np.ndarray:
    """
    Run YOLOv8 on the image and return a binary mask where
    white (255) = detected airplane pixels.
    """
    mask = np.zeros(img.shape[:2], dtype=np.uint8)

    results = model.predict(img, conf=CONFIDENCE, verbose=False)
    for result in results:
        if result.boxes is None:
            continue
        for box, cls_id in zip(result.boxes.xyxy.cpu().numpy(),
                               result.boxes.cls.cpu().numpy().astype(int)):
            if cls_id == AIRPLANE_CLASS_ID:
                x1, y1, x2, y2 = box.astype(int)
                # Fill the bounding box region in the mask
                mask[y1:y2, x1:x2] = 255

    # Dilate mask to cover shadows, landing gear, wing tips
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (DILATE_PIXELS * 2, DILATE_PIXELS * 2)
    )
    mask = cv2.dilate(mask, kernel, iterations=1)

    return mask


def inpaint_tile(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Inpaint masked regions using OpenCV Telea algorithm."""
    return cv2.inpaint(img, mask, INPAINT_RADIUS, cv2.INPAINT_TELEA)


def main():
    # Ensure output dirs exist
    for d in (ORIGINAL_DIR, MASK_DIR, CLEANED_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # Get tiles covering the bounding box
    tiles = list(mercantile.tiles(*BOUNDS, zooms=ZOOM))
    print(f"[info] {len(tiles)} tiles to process at zoom {ZOOM}")

    # Load YOLOv8 model (downloads yolov8n.pt on first run, ~6MB)
    print("[info] Loading YOLOv8 model...")
    model = YOLO("yolov8n.pt")

    processed = 0
    planes_found = 0

    for i, tile in enumerate(tiles):
        name = f"{tile.z}_{tile.x}_{tile.y}"
        print(f"[{i+1}/{len(tiles)}] Processing tile {name}...", end=" ")

        # 1. Download
        tile_path = download_tile(tile)
        img = cv2.imread(str(tile_path))
        if img is None:
            print("SKIP (unreadable)")
            continue

        # 2. Detect planes
        mask = detect_planes(model, img)
        plane_pixels = cv2.countNonZero(mask)

        if plane_pixels == 0:
            # No planes — just copy original to cleaned
            cv2.imwrite(str(CLEANED_DIR / f"{name}.png"), img)
            print("no planes")
        else:
            planes_found += 1
            # Save mask for inspection
            cv2.imwrite(str(MASK_DIR / f"{name}_mask.png"), mask)

            # 3. Inpaint
            cleaned = inpaint_tile(img, mask)
            cv2.imwrite(str(CLEANED_DIR / f"{name}.png"), cleaned)
            print(f"INPAINTED ({plane_pixels} mask pixels)")

        processed += 1

        # Be polite to Esri servers
        time.sleep(0.1)

    print(f"\n[done] Processed {processed} tiles, found planes in {planes_found}")
    print(f"  Originals: {ORIGINAL_DIR}")
    print(f"  Masks:     {MASK_DIR}")
    print(f"  Cleaned:   {CLEANED_DIR}")


if __name__ == "__main__":
    main()
