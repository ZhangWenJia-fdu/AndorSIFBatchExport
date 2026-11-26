"""独立强度分析：数值、错误、递归、CSV 与真实 Tk 后台任务回归。"""
import csv
from pathlib import Path
import sys
import time
import tkinter as tk

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import batch_export_sif as batch
import intensity_analysis as analysis
import gui


def setup_sources(tmp_path, monkeypatch, arrays):
    for name in arrays:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'preserve source')
    def read(path):
        value = arrays[Path(path).relative_to(tmp_path).as_posix()]
        if isinstance(value, Exception):
            raise value
        return value, {}
    monkeypatch.setattr(batch.sif_parser, 'np_open', read)
    return tmp_path


def test_single_statistics_and_unchanged_pixels(tmp_path, monkeypatch):
    pixels = np.array([[1, 2], [3, 100]], dtype=np.float32)
    original = pixels.copy()
    setup_sources(tmp_path, monkeypatch, {'a.sif': pixels})
    result = analysis.analyze_intensity(tmp_path)
    row = result['rows'][0]
    assert row['min'] == 1 and row['max'] == 100
    assert row['mean'] == 26.5 and row['median'] == 2.5
    assert row['p1'] == pytest.approx(1.03)
    assert row['p99'] == pytest.approx(97.09)
    assert np.array_equal(original, pixels)
    assert (tmp_path / 'a.sif').read_bytes() == b'preserve source'


@pytest.mark.parametrize('ranges,expected', [
    ([(8900, 91000), (10010, 100300)], (8900, 10010, 91000, 100300)),
    ([(5, 9), (5, 9)], (5, 5, 9, 9)),
    ([(-1000, 3), (100, 1e8), (0, 1)], (-1000, 100, 1, 1e8)),
    ([(7, 7), (7, 7)], (7, 7, 7, 7)),
])
def test_batch_core_extrema(tmp_path, monkeypatch, ranges, expected):
    setup_sources(tmp_path, monkeypatch, {f'{i}.sif': np.array([pair]) for i, pair in enumerate(ranges)})
    summary = analysis.analyze_intensity(tmp_path)['summary']
    assert tuple(summary[k] for k in analysis.CORE_KEYS) == expected
    assert summary['median_of_image_minima'] == np.median([x[0] for x in ranges])
    assert summary['median_of_image_maxima'] == np.median([x[1] for x in ranges])


@pytest.mark.parametrize('bad', [np.nan, np.inf, -np.inf])
def test_nonfinite_frames_excluded(tmp_path, monkeypatch, bad):
    setup_sources(tmp_path, monkeypatch, {'a.sif': np.array([[[1, 2]], [[bad, 1000]]])})
    result = analysis.analyze_intensity(tmp_path)
    assert result['summary']['frames_analyzed'] == 1
    assert result['summary']['frames_excluded'] == 1
    assert result['summary']['files_failed'] == 1
    assert result['summary']['highest_maximum'] == 2
    assert result['rows'][1]['invalid_pixels'] == 1
    assert result['rows'][1]['status'] == 'INVALID_FRAME'


@pytest.mark.parametrize('arrays', [{}, {'a.sif': OSError('broken SIF')}, {'a.sif': np.array([[np.nan]])}])
def test_no_valid_data(tmp_path, monkeypatch, arrays):
    setup_sources(tmp_path, monkeypatch, arrays)
    result = analysis.analyze_intensity(tmp_path)
    assert result['summary']['frames_analyzed'] == 0
    assert all(result['summary'][k] is None for k in analysis.CORE_KEYS)
    assert result['summary']['files_failed'] == len(arrays)


def test_partial_read_failure(tmp_path, monkeypatch):
    setup_sources(tmp_path, monkeypatch, {'bad.sif': ValueError('bad header'), 'good.sif': np.array([[5, 9]])})
    result = analysis.analyze_intensity(tmp_path)
    assert result['summary']['files_succeeded'] == 1
    assert result['summary']['files_failed'] == 1
    assert result['summary']['lowest_minimum'] == 5
    assert result['rows'][0]['error'] == 'bad header'


def test_multiframe(tmp_path, monkeypatch):
    setup_sources(tmp_path, monkeypatch, {'a.sif': np.array([[[1, 10]], [[3, 20]]])})
    result = analysis.analyze_intensity(tmp_path)
    assert result['summary']['files_analyzed'] == 1
    assert result['summary']['frames_analyzed'] == 2
    assert [r['frame'] for r in result['rows']] == [1, 2]
    assert tuple(result['summary'][k] for k in analysis.CORE_KEYS) == (1, 3, 10, 20)


@pytest.mark.parametrize('recursive,count', [(False, 1), (True, 2)])
def test_recursive_and_csv(tmp_path, monkeypatch, recursive, count):
    setup_sources(tmp_path, monkeypatch, {'a.sif': np.array([[1, 2]]), 'child/a.SIF': np.array([[3, 4]])})
    events = []
    result = analysis.analyze_intensity(tmp_path, recursive, events.append)
    assert len(result['rows']) == count
    assert events[0]['total'] == count and events[-1]['processed'] == count
    path = tmp_path / 'result.csv'
    analysis.export_csv(result, path)
    with path.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.reader(stream))
    assert ['lowest_minimum', '1.0'] in rows
    table_index = rows.index(['Per-image statistics']) + 1
    records = list(csv.DictReader([','.join(row) for row in rows[table_index:]]))
    assert len(records) == count
    assert records[0]['file'] == 'a.sif' and float(records[0]['mean']) == 1.5
    if recursive:
        assert records[1]['file'] == 'child/a.SIF' and float(records[1]['max']) == 4
    with pytest.raises(ValueError, match='.csv'):
        analysis.export_csv(result, tmp_path / 'a.sif')


def test_gui_independent_analysis_and_export_afterwards(tmp_path, monkeypatch):
    setup_sources(tmp_path, monkeypatch, {'a.sif': np.array([[1, 2], [3, 4]], dtype=np.float32)})
    root = tk.Tk()
    root.withdraw()
    app = gui.ExportApp(root)
    monkeypatch.setattr(gui.messagebox, 'showinfo', lambda *a, **k: None)
    monkeypatch.setattr(gui.messagebox, 'showerror', lambda *a, **k: pytest.fail(str(a)))
    try:
        app.input_var.set(str(tmp_path))
        app.min_var.set('-12.5')
        app.max_var.set('98765.4321')
        app.analyze_button.invoke()
        first_worker = app.worker
        app.start_analysis()
        app.start_export()
        assert app.worker is first_worker
        ticks = 0
        deadline = time.monotonic() + 15
        while app.active and time.monotonic() < deadline:
            root.update()
            ticks += 1
            time.sleep(.01)
        assert not app.active and ticks > 0
        assert app.min_var.get() == '-12.5' and app.max_var.get() == '98765.4321'
        assert app.output_var.get() == '' and app.range_var.get() == ''
        assert not app.recursive_var.get()
        window = app.analysis_window
        assert len(window.tree.get_children()) == 1
        path = tmp_path / 'gui.csv'
        monkeypatch.setattr(gui.filedialog, 'asksaveasfilename', lambda **k: str(path))
        window.csv_button.invoke()
        assert path.exists() and 'a.sif' in path.read_text(encoding='utf-8-sig')
        window.sort('min')
        app.output_var.set(str(tmp_path / 'export'))
        app.range_var.set('custom')
        for variable in (app.tiff8_var, app.tiff16_var, app.raw_var, app.png_var):
            variable.set(True)
        app.start_export()
        deadline = time.monotonic() + 15
        while app.active and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        assert not app.active and app.success_var.get() == 'Successful: 1'
        assert len(list((tmp_path / 'export').rglob('*.tif'))) == 3
        assert len(list((tmp_path / 'export').rglob('*.png'))) == 1
    finally:
        root.destroy()


def test_float32_percentile_interpolation_precision(tmp_path, monkeypatch):
    pixels = np.array([[10000, 10001, 10002, 30001]], dtype=np.float32)
    setup_sources(tmp_path, monkeypatch, {'a.sif': pixels})
    row = analysis.analyze_intensity(tmp_path)['rows'][0]
    assert row['p99'] == pytest.approx(29401.03, abs=1e-9, rel=0)
