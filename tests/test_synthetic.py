"""合成数据测试：验证映射、类型保留和帧导出，不代表 SIF 读取正确性。"""

import importlib.util
import csv
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import pytest
import tifffile

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "batch_export_sif.py"
spec = importlib.util.spec_from_file_location("batch_export_sif", SCRIPT)
batch = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = batch
spec.loader.exec_module(batch)


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16, np.float32, np.float64])
def test_fixed_mapping_and_boundaries(dtype):
    pixels = np.array([0, 25, 50, 75, 100, 101], dtype=dtype)
    assert batch.scale_to_uint8(pixels, 0, 100).tolist() == [0, 63, 127, 191, 255, 255]


def test_uint16_mapping_uses_full_resolution_directly():
    pixels = np.array([0, 25, 50, 75, 100, 101], dtype=np.uint16)
    result = batch.scale_to_uint16(pixels, 0, 100)
    assert result.dtype == np.uint16
    assert result.tolist() == [0, 16383, 32767, 49151, 65535, 65535]
    assert int(result[2]) != int(batch.scale_to_uint8(pixels, 0, 100)[2]) * 257


def test_display_mapping_rejects_nonfinite_pixels_and_ranges():
    with pytest.raises(ValueError, match="有限数值"):
        batch.scale_to_uint16(np.ones((2, 2)), np.nan, 10)
    with pytest.raises(ValueError, match="NaN 或 Inf"):
        batch.scale_to_uint16(np.array([[np.inf]]), 0, 10)


def test_below_min_clips_to_black():
    assert batch.scale_to_uint8(np.array([-1, 0], dtype=np.float32), 0, 100).tolist() == [0, 0]


def test_bad_range():
    with pytest.raises(ValueError):
        batch.scale_to_uint8(np.ones((2, 2)), 2, 2)


def test_tiff_preserves_dtype_and_png_roundtrip(tmp_path):
    frame = np.array([[0, 1000], [5000, 6000]], dtype=np.uint16)
    png, display_tif, raw_tif = (tmp_path / "image.png", tmp_path / "image_display.tif",
                                 tmp_path / "image_raw.tif")
    display_tif16 = tmp_path / "image_16bit.tif"
    checks = batch._export_frame(frame, png, display_tif, raw_tif, 0, 5000, True,
                                 display_tiff16_path=display_tif16)
    assert checks == ("PASS", "PASS", "PASS", "PASS")
    assert np.array_equal(np.asarray(Image.open(png)), [[255, 255], [0, 51]])
    display_restored = tifffile.imread(display_tif)
    assert display_restored.dtype == np.uint8
    with tifffile.TiffFile(display_tif) as tif:
        assert tif.pages[0].tags["BitsPerSample"].value == 8
    assert np.array_equal(display_restored, np.asarray(Image.open(png)))
    display16 = tifffile.imread(display_tif16)
    assert display16.dtype == np.uint16
    with tifffile.TiffFile(display_tif16) as tif:
        assert tif.pages[0].tags["BitsPerSample"].value == 16
    assert np.array_equal(display16, [[65535, 65535], [0, 13107]])
    restored = tifffile.imread(raw_tif)
    assert restored.dtype == frame.dtype and np.array_equal(restored, frame)


def test_display_tiff_orientation_modes_are_single_flip_and_raw_is_unchanged(tmp_path):
    frame = np.array([[1, 2, 3], [11, 12, 13]], dtype=np.float32)
    original = frame.copy()
    compatible = tmp_path / "compatible.tif"
    native = tmp_path / "native.tif"
    batch._export_frame(frame, None, compatible, None, 1, 13, True,
                        display_tiff_orientation="solis-compatible")
    batch._export_frame(frame, None, native, None, 1, 13, True,
                        display_tiff_orientation="native")
    assert np.array_equal(tifffile.imread(compatible), [[212, 233, 255], [0, 21, 42]])
    assert np.array_equal(tifffile.imread(native), [[0, 21, 42], [212, 233, 255]])
    assert np.array_equal(frame, original)
    with pytest.raises(ValueError, match="方向"):
        batch.orient_display_tiff(frame, "flip-twice")


def test_nonfinite_and_collision(tmp_path):
    png = tmp_path / "image.png"
    with pytest.raises(ValueError, match="NaN 或 Inf"):
        batch._export_frame(np.array([[np.nan]]), png, None, None, 0, 1, True)
    png.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        batch._export_frame(np.ones((1, 1)), png, None, None, 0, 1, True)
    assert png.read_bytes() == b"keep"


def test_discover_paths_and_recursive(tmp_path):
    (tmp_path / "a.sif").write_bytes(b"")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.SIF").write_bytes(b"")
    assert len(batch.discover_sif_files(tmp_path, True)) == 2
    assert len(batch.discover_sif_files(tmp_path, False)) == 1
    with pytest.raises(FileNotFoundError):
        batch.discover_sif_files(tmp_path / "missing", True)


def test_read_rejects_ambiguous_dimensions(monkeypatch, tmp_path):
    monkeypatch.setattr(batch.sif_parser, "np_open", lambda _: (np.zeros((2, 3, 4, 5)), {}))
    with pytest.raises(ValueError, match="无法可靠解释"):
        batch.read_sif_frames(tmp_path / "x.sif")


def test_multiframe_axis_order(monkeypatch, tmp_path):
    stack = np.arange(24, dtype=np.float32).reshape(3, 2, 4)
    monkeypatch.setattr(batch.sif_parser, "np_open", lambda _: (stack, {"SifVersion": 1}))
    frames, _ = batch.read_sif_frames(tmp_path / "stack.sif")
    assert len(frames) == 3
    assert [frame.shape for frame in frames] == [(2, 4)] * 3
    assert np.array_equal(frames[2], stack[2])


def test_failed_frame_is_logged(tmp_path, monkeypatch):
    source_dir = tmp_path / "in"
    source_dir.mkdir()
    source = source_dir / "bad.sif"
    source.write_bytes(b"synthetic")
    monkeypatch.setattr(batch.sif_parser, "np_open", lambda _: (np.array([[[np.inf]]]), {}))
    monkeypatch.setattr(batch, "CALCULATE_SHA256", False)
    options = {"input": source_dir, "output": tmp_path / "out", "display_min": 0,
               "display_max": 1, "recursive": False, "png": True,
               "display_tiff": False, "raw_tiff": False}
    files, passed, failed, report = batch.run_batch(options)
    assert (files, passed, failed) == (1, 0, 1)
    with report.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["status"] == "FAIL_EXPORT" and "NaN 或 Inf" in row["error"]


def test_analysis_reports_global_and_per_frame_without_normal_exports(tmp_path, monkeypatch, capsys):
    source_dir = tmp_path / "input"
    source_dir.mkdir()
    (source_dir / "a.sif").write_bytes(b"a")
    (source_dir / "b.sif").write_bytes(b"b")
    frame_a = np.array([[0, 1], [2, 3]], dtype=np.float32)
    frame_b = np.array([[4, 5], [6, 7]], dtype=np.float32)
    monkeypatch.setattr(batch.sif_parser, "np_open", lambda path: (frame_a[None] if path.endswith("a.sif") else frame_b[None], {}))
    monkeypatch.setattr(batch, "CALCULATE_SHA256", False)
    options = {"input": source_dir, "input_file": None, "output": tmp_path / "out",
               "display_min": 0, "display_max": 100, "recursive": True, "preview_count": 1}
    result = batch.analyze_batch(options)
    assert result["pixel_count"] == 8
    assert result["stats"]["min"] == 0 and result["stats"]["max"] == 7
    assert len(result["previews"]) == 1
    assert result["histogram_image"].is_file()
    assert np.array_equal(np.asarray(Image.open(result["previews"][0])),
                          batch.scale_to_uint8(frame_a, 0, 100))
    assert not (tmp_path / "out" / "display").exists()
    assert not (tmp_path / "out" / "display_tiff").exists()
    assert not (tmp_path / "out" / "quantitative").exists()
    with result["file_report"].open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2 and all(row["status"] == "PASS_ANALYSIS" for row in rows)
    with result["histogram_report"].open(encoding="utf-8-sig", newline="") as stream:
        histogram = list(csv.DictReader(stream))
    assert sum(int(row["pixel_count"]) for row in histogram) == 8
    with result["candidate_report"].open(encoding="utf-8-sig", newline="") as stream:
        candidates = list(csv.DictReader(stream))
    assert len(candidates) == 4 and all(row["selected_automatically"] == "否" for row in candidates)
    assert "候选范围未自动应用" in capsys.readouterr().out


def test_single_file_mode_and_format_defaults(tmp_path, monkeypatch):
    source = tmp_path / "one.sif"
    source.write_bytes(b"sample")
    frame = np.array([[[0, 10], [20, 30]]], dtype=np.float32)
    monkeypatch.setattr(batch.sif_parser, "np_open", lambda _: (frame, {}))
    monkeypatch.setattr(batch, "CALCULATE_SHA256", False)
    options = {"input": tmp_path / "missing", "input_file": source, "output": tmp_path / "out",
               "display_min": 0, "display_max": 30, "recursive": True, "png": True,
               "display_tiff": True, "tiff16": True, "raw_tiff": True}
    files, passed, failed, report = batch.run_batch(options)
    assert (files, passed, failed) == (1, 1, 0)
    png_pixels = np.asarray(Image.open(tmp_path / "out/display_png/one_8bit_custom_0-30.png"))
    assert np.array_equal(png_pixels,
                          tifffile.imread(tmp_path / "out/display_tiff_8bit/one_8bit_custom_0-30.tif"))
    assert tifffile.imread(tmp_path / "out/display_tiff_16bit/one_16bit_custom_0-30.tif").dtype == np.uint16
    assert tifffile.imread(tmp_path / "out/raw_tiff/one_raw.tif").dtype == np.float32


def test_analysis_reports_full_white_warning(tmp_path, monkeypatch, capsys):
    source = tmp_path / "white.sif"
    source.write_bytes(b"sample")
    monkeypatch.setattr(batch.sif_parser, "np_open", lambda _: (np.full((1, 2, 2), 10, dtype=np.float32), {}))
    monkeypatch.setattr(batch, "CALCULATE_SHA256", False)
    result = batch.analyze_batch({"input": tmp_path, "input_file": None, "output": tmp_path / "out",
                                  "display_min": 0, "display_max": 5, "recursive": False, "preview_count": 0})
    assert "全白" in capsys.readouterr().out
    with result["file_report"].open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["all_white"] == "True" and "全白" in row["warning"]


def test_noninteractive_does_not_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("不应等待输入"))
    args = batch.build_parser().parse_args(["--no-interactive", "--input", str(tmp_path)])
    options = batch.resolve_options(args)
    assert options["input"] == tmp_path
    assert options["png"] is True
    assert options["display_tiff"] is True and options["tiff16"] is True and options["raw_tiff"] is False
    assert options["display_tiff_orientation"] == "solis-compatible"


def test_cli_input_takes_priority_over_interactive_file_prompt(monkeypatch, tmp_path):
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: (prompts.append(prompt) or "n"))
    args = batch.build_parser().parse_args([
        "--interactive", "--input", str(tmp_path), "--output", str(tmp_path / "out"),
        "--min", "0", "--max", "10", "--recursive", "y", "--png", "y",
        "--tiff8", "n", "--tiff16", "y", "--raw-tiff", "n", "--preview-count", "0",
    ])
    options = batch.resolve_options(args)
    assert options["input"] == tmp_path and options["input_file"] is None
    assert options["display_tiff"] is False and options["tiff16"] is True
    assert not any("Single SIF file" in prompt for prompt in prompts)


def test_invalid_batch_options():
    with pytest.raises(ValueError, match="Display Max"):
        batch.run_batch({"input": Path("."), "output": Path("out"), "display_min": 2,
                         "display_max": 1, "recursive": False, "png": True, "raw_tiff": False})
