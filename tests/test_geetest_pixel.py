"""Test GeeTest pixel gap detection with synthetic images + real screenshots."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.services.geetest import GeetestSolver
from unittest.mock import MagicMock

WIDTH, HEIGHT = 340, 200
GAP_X = 187
GAP_W, GAP_H = 45, 130
GAP_Y = (HEIGHT - GAP_H) // 2


def _make_synthetic_geetest(gap_x: int = GAP_X) -> bytes:
    """Create a realistic GeeTest-like puzzle image with a gap."""
    img = np.ones((HEIGHT, WIDTH, 3), dtype=np.uint8) * 200

    for y in range(HEIGHT):
        shade = int(180 + 40 * np.sin(y * 0.05))
        img[y, :, :] = [shade, shade, min(255, shade + 10)]

    noise = np.random.randint(-15, 15, (HEIGHT, WIDTH, 3), dtype=np.int8)
    img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    piece_x, piece_w, piece_h = 15, 45, 130
    piece_y = (HEIGHT - piece_h) // 2
    cv2.rectangle(img, (piece_x, piece_y), (piece_x + piece_w, piece_y + piece_h),
                  (220, 180, 120), -1)
    cv2.rectangle(img, (piece_x, piece_y), (piece_x + piece_w, piece_y + piece_h),
                  (180, 140, 80), 2)

    # Gap area (dark shadow/cutout)
    cv2.rectangle(img, (gap_x, GAP_Y), (gap_x + GAP_W, GAP_Y + GAP_H),
                  (80, 75, 70), -1)
    cv2.rectangle(img, (gap_x, GAP_Y), (gap_x + GAP_W, GAP_Y + GAP_H),
                  (50, 45, 40), 2)

    # Slider track
    track_y = HEIGHT - 25
    cv2.rectangle(img, (10, track_y), (WIDTH - 10, track_y + 15),
                  (180, 185, 190), -1)
    cv2.rectangle(img, (10, track_y), (WIDTH - 10, track_y + 15),
                  (140, 145, 150), 1)

    # Slider handle
    handle_x = 12
    cv2.circle(img, (handle_x + 15, track_y + 7), 12, (240, 240, 240), -1)
    cv2.circle(img, (handle_x + 15, track_y + 7), 12, (100, 100, 100), 1)

    success, encoded = cv2.imencode(".png", img)
    assert success, "Failed to encode test image"
    return encoded.tobytes()


def _solver() -> GeetestSolver:
    config = MagicMock()
    return GeetestSolver(config)


async def test_synthetic():
    png_bytes = _make_synthetic_geetest(GAP_X)
    solver = _solver()
    result = await solver._detect_gap_pixel(png_bytes)
    assert result is not None, f"Pixel analysis returned None for known gap at {GAP_X}"
    assert abs(result - GAP_X) <= 5, (
        f"Gap detection off by {abs(result - GAP_X)}px "
        f"(expected ~{GAP_X}, got {result})"
    )
    print(f"  ✅ Synthetic test: gap={result}px (expected ≈{GAP_X}px)")


async def test_multi_gaps():
    for gap_x in [80, 120, 160, 200, 240]:
        png_bytes = _make_synthetic_geetest(gap_x)
        solver = _solver()
        result = await solver._detect_gap_pixel(png_bytes)
        assert result is not None, f"Pixel analysis returned None for gap at {gap_x}"
        assert abs(result - gap_x) <= 8, (
            f"Gap at {gap_x}px → {result}px (Δ={abs(result - gap_x)}px)"
        )
        print(f"  ✅ Gap at {gap_x}px → detected {result}px (Δ={abs(result - gap_x)}px)")


async def test_no_gap():
    img = np.ones((200, 340, 3), dtype=np.uint8) * 200
    noise = np.random.randint(-10, 10, (200, 340, 3), dtype=np.int8)
    img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    success, encoded = cv2.imencode(".png", img)
    assert success
    solver = _solver()
    result = await solver._detect_gap_pixel(encoded.tobytes())
    assert result is None, f"No-gap image returned {result}px instead of None"
    print("  ✅ No-gap test: returned None")


async def test_empty_input():
    solver = _solver()
    assert await solver._detect_gap_pixel(b"") is None
    assert await solver._detect_gap_pixel(b"short") is None
    print("  ✅ Empty/trash input: returned None")


async def test_perf():
    png_bytes = _make_synthetic_geetest(GAP_X)
    solver = _solver()
    times = []
    for _ in range(10):
        start = time.perf_counter()
        result = await solver._detect_gap_pixel(png_bytes)
        elapsed = time.perf_counter() - start
        times.append(elapsed)
        assert result is not None
    avg = sum(times) / len(times)
    print(f"  ✅ Performance: avg {avg*1000:.1f}ms, min {min(times)*1000:.1f}ms, "
          f"max {max(times)*1000:.1f}ms (10 runs)")


async def test_real_screenshots():
    screenshots_dir = Path(__file__).parent.parent / "test_screenshots"
    if not screenshots_dir.exists():
        print("  ⏭️  No real screenshots dir, skipping")
        return
    solver = _solver()
    for p in sorted(screenshots_dir.glob("geetest_*.png")):
        data = p.read_bytes()
        start = time.perf_counter()
        result = await solver._detect_gap_pixel(data)
        elapsed = time.perf_counter() - start
        status = f"gap={result}px" if result else "no gap detected"
        print(f"  📷 {p.name}: {status} ({elapsed*1000:.1f}ms)")


async def main():
    print("=" * 60)
    print("GeeTest Pixel Analysis Tests")
    print("=" * 60)
    tests = [
        ("Synthetic known gap", test_synthetic()),
        ("Various gap positions", test_multi_gaps()),
        ("No-gap image", test_no_gap()),
        ("Empty input", test_empty_input()),
        ("Performance", test_perf()),
        ("Real screenshots", test_real_screenshots()),
    ]
    passed = 0
    failed = 0
    for name, coro in tests:
        print(f"\n  ── {name} ──")
        try:
            await coro
            passed += 1
        except Exception as e:
            print(f"  ❌ FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{'=' * 60}")
    print(f"Results: {passed} passed, {failed} failed, of {len(tests)}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
