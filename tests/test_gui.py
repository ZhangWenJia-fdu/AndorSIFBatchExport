"""GUI 参数、核心调用和主线程消息行为测试。"""

from pathlib import Path
import queue
import sys
import tkinter as tk

import numpy as np
import pytest
import tifffile

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import gui
import batch_export_sif as batch


def test_gui_requires_complete_valid_parameters(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    common = [str(input_dir), str(tmp_path / "out"), "0", "10"]
    for args, expected in [
        (["", common[1], "0", "10", True, False, True], "input folder"),
        ([common[0], "", "0", "10", True, False, True], "output folder"),
        ([*common[:2], "", "10", True, False, True], "both Display"),
        ([*common[:2], "NaN", "10", True, False, True], "finite"),
        ([*common[:2], "10", "10", True, False, True], "greater than"),
        ([*common, False, False, True], "at least one export format"),
    ]:
        with pytest.raises(ValueError, match=expected):
            gui.build_options(*args)


@pytest.mark.parametrize("tiff8,tiff16,expected", [(True, False, [8]), (False, True, [16]), (True, True, [8, 16])])
def test_gui_exports_selected_tiff_formats(tmp_path, monkeypatch, tiff8, tiff16, expected):
    source_dir = tmp_path / "input"
    source_dir.mkdir()
    source = source_dir / "sample.sif"
    source.write_bytes(b"test source")
    frame = np.array([[[0, 5], [10, 15]]], dtype=np.float32)
    monkeypatch.setattr(batch.sif_parser, "np_open", lambda _: (frame, {}))
    monkeypatch.setattr(batch, "CALCULATE_SHA256", False)
    options = gui.build_options(str(source_dir), str(tmp_path / "out"), "0", "10", tiff8, tiff16, True)
    events = []
    options["_files"] = batch.discover_sif_files(source_dir, options["recursive"])
    result = batch.run_batch(options, progress_callback=events.append)
    assert result[:3] == (1, 1, 0)
    assert [event["phase"] for event in events] == ["start", "file", "finished"]
    assert events[1]["processed"] == 1 and events[1]["succeeded"] == 1
    paths = {8: tmp_path / "out/display_tiff_8bit/sample_8bit_custom_0-10.tif",
             16: tmp_path / "out/display_tiff_16bit/sample_16bit_custom_0-10.tif"}
    for bits, path in paths.items():
        assert path.exists() is (bits in expected)
        if bits in expected:
            pixels = tifffile.imread(path)
            assert pixels.dtype == (np.uint8 if bits == 8 else np.uint16)
            assert tifffile.TiffFile(path).pages[0].tags["BitsPerSample"].value == bits
            mapped = batch.scale_to_display_dtype(frame[0], 0, 10, bits)
            assert np.array_equal(pixels, np.flipud(mapped))
    assert not (tmp_path / "out/display/sample.png").exists()


def test_gui_options_keep_solis_orientation_and_recursive_choice(tmp_path):
    source_dir = tmp_path / "in"
    source_dir.mkdir()
    options = gui.build_options(str(source_dir), str(tmp_path / "out"), "-1.5", "3.2", True, False, False)
    assert options["recursive"] is False
    assert options["display_tiff_orientation"] == "solis-compatible"
    assert options["png"] is False and options["raw_tiff"] is False
    assert options["display_min"] == -1.5 and options["display_max"] == 3.2


def test_gui_defaults_are_blank_and_tiff_formats_unchecked(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display is unavailable: {exc}")
    root.withdraw()
    app = gui.ExportApp(root)
    errors = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args, **kwargs: errors.append(args[1]))
    try:
        assert [app.input_var.get(), app.output_var.get(), app.min_var.get(), app.max_var.get()] == ["", "", "", ""]
        assert app.tiff8_var.get() is False and app.tiff16_var.get() is False
        assert app.recursive_var.get() is False
        assert app.range_var.get() == ""
        assert not app.raw_var.get() and not app.png_var.get()
        source = tmp_path / "input"
        source.mkdir()
        app.input_var.set(str(source))
        app.output_var.set(str(tmp_path / "output"))
        app.min_var.set("0")
        app.max_var.set("1")
        app.start_export()
        assert not app.active and app.worker is None
        assert errors and "Select at least one export format" in errors[-1]
    finally:
        root.destroy()


def test_browse_buttons_and_open_output_folder(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display is unavailable: {exc}")
    root.withdraw()
    app = gui.ExportApp(root)
    opened = []
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(gui.os, "startfile", lambda path: opened.append(path), raising=False)
    errors = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args, **kwargs: errors.append(args[1]))
    try:
        app._browse_input()
        app._browse_output()
        assert app.input_var.get() == str(tmp_path)
        assert app.output_var.get() == str(tmp_path)
        app.open_output_folder()
        assert opened == [str(tmp_path)]
        app.output_var.set("")
        app.open_output_folder()
        assert errors and "Select an output folder" in errors[-1]
    finally:
        root.destroy()


def test_empty_folder_is_rejected_before_batch_processing(tmp_path):
    source_dir = tmp_path / "empty"
    source_dir.mkdir()
    options = gui.build_options(str(source_dir), str(tmp_path / "out"), "0", "1", True, False, True)
    root_queue = queue.Queue()
    app = object.__new__(gui.ExportApp)
    app.messages = root_queue
    app._run_export(options)
    message = root_queue.get_nowait()
    assert message["type"] == "error"
    assert "No .sif files" in message["text"]


@pytest.mark.parametrize("recursive,expected_files", [(False, 1), (True, 2)])
def test_gui_recursive_discovery_option(tmp_path, monkeypatch, recursive, expected_files):
    source_dir = tmp_path / "in"
    source_dir.mkdir()
    (source_dir / "child").mkdir()
    (source_dir / "top.sif").touch()
    (source_dir / "child" / "nested.sif").touch()
    frame = np.array([[[0, 1], [2, 3]]], dtype=np.float32)
    monkeypatch.setattr(batch.sif_parser, "np_open", lambda _: (frame, {}))
    monkeypatch.setattr(batch, "CALCULATE_SHA256", False)
    options = gui.build_options(str(source_dir), str(tmp_path / f"out_{recursive}"), "0", "3", True, False, recursive)
    files = batch.discover_sif_files(source_dir, recursive)
    assert len(files) == expected_files
    options["_files"] = files
    assert batch.run_batch(options)[:3] == (expected_files, expected_files, 0)


def test_gui_conflicting_output_does_not_overwrite_existing_file(tmp_path, monkeypatch):
    source_dir = tmp_path / "in"
    source_dir.mkdir()
    (source_dir / "sample.sif").write_bytes(b"fake SIF")
    output = tmp_path / "out"
    target = output / "display_tiff_8bit" / "sample_8bit_custom_0-3.tif"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"preserve existing export")
    monkeypatch.setattr(batch.sif_parser, "np_open", lambda _: (np.array([[[0, 1], [2, 3]]], dtype=np.float32), {}))
    monkeypatch.setattr(batch, "CALCULATE_SHA256", False)
    options = gui.build_options(str(source_dir), str(output), "0", "3", True, False, True)
    assert batch.run_batch(options)[:3] == (1, 0, 1)
    assert target.read_bytes() == b"preserve existing export"
