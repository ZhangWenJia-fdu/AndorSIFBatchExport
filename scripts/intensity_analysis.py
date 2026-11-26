"""独立、只读的原始 SIF 逐帧强度分析；不调用显示映射。"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import numpy as np
import batch_export_sif as batch

STAT_KEYS = ("min", "max", "mean", "median", "p1", "p99")
CORE_KEYS = ("lowest_minimum", "highest_minimum", "lowest_maximum", "highest_maximum")


def analyze_intensity(input_dir: Path, recursive: bool = False, progress_callback=None) -> dict[str, Any]:
    """一次仅保留一个源文件的像素。无效帧整帧排除，保留错误及非有限像素数。"""
    source_dir = Path(input_dir).expanduser().resolve()
    files = batch.discover_sif_files(source_dir, recursive)
    rows = []
    succeeded = failed = files_analyzed = frames_found = 0

    def notify(**event):
        if progress_callback is not None:
            progress_callback(event)

    notify(phase="start", total=len(files), processed=0, succeeded=0, failed=0)
    for processed, source in enumerate(files, 1):
        relative = source.relative_to(source_dir).as_posix()
        notify(phase="reading", total=len(files), processed=processed - 1,
               succeeded=succeeded, failed=failed, source=relative)
        file_rows = []
        try:
            frames, _ = batch.read_sif_frames(source)
            frames_found += len(frames)
            for number, frame in enumerate(frames, 1):
                row = dict(file=relative, frame=number, pixel_count=int(frame.size),
                           invalid_pixels=int(np.count_nonzero(~np.isfinite(frame))),
                           status="OK", error="")
                if row["invalid_pixels"]:
                    row.update(status="INVALID_FRAME", error="NaN/Inf pixels; entire frame excluded")
                else:
                    # 统计临时副本使用 float64，避免 NumPy float32 百分位插值舍入。
                    row.update(batch._intensity_stats(frame.astype(np.float64)))
                file_rows.append(row)
        except Exception as exc:
            file_rows = [dict(file=relative, frame="", status="READ_ERROR", error=str(exc))]
        valid_count = sum(row["status"] == "OK" for row in file_rows)
        files_analyzed += bool(valid_count)
        if all(row["status"] == "OK" for row in file_rows):
            succeeded += 1
        else:
            failed += 1
        rows.extend(file_rows)
        notify(phase="file", total=len(files), processed=processed, succeeded=succeeded,
               failed=failed, source=relative, status="OK" if valid_count == len(file_rows) else "ERROR")
    valid = [row for row in rows if row["status"] == "OK"]
    summary = dict(files_found=len(files), files_analyzed=files_analyzed, files_succeeded=succeeded,
                   files_failed=failed, frames_found=frames_found, frames_analyzed=len(valid),
                   frames_excluded=frames_found - len(valid),
                   invalid_pixels=sum(row.get("invalid_pixels", 0) for row in rows))
    # 没有有效帧时用 None，禁止以 0 伪装成统计结果。
    summary.update(dict.fromkeys((*CORE_KEYS, "median_of_image_minima", "median_of_image_maxima")))
    if valid:
        minima = [row["min"] for row in valid]
        maxima = [row["max"] for row in valid]
        summary.update(lowest_minimum=min(minima), highest_minimum=max(minima),
                       lowest_maximum=min(maxima), highest_maximum=max(maxima),
                       median_of_image_minima=float(np.median(minima)),
                       median_of_image_maxima=float(np.median(maxima)))
    return dict(input_folder=str(source_dir), recursive=bool(recursive), summary=summary, rows=rows)


def export_csv(result: dict[str, Any], path: Path) -> None:
    """单一 CSV 分区保存摘要、逐帧数据及错误；保留浮点精度。拒绝写入 SIF。"""
    path = Path(path)
    if path.suffix.lower() != ".csv":
        raise ValueError("Select a .csv file; source SIF files cannot be overwritten.")
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["Analysis summary"])
        writer.writerow(["input_folder", result["input_folder"]])
        writer.writerow(["include_subfolders", result["recursive"]])
        writer.writerow(["invalid_data_policy", "Exclude entire nonfinite frame; retain valid sibling frames. Files analyzed and failed may overlap for partial files."])
        for key, value in result["summary"].items():
            writer.writerow([key, "" if value is None else value])
        summary = result["summary"]
        writer.writerow(["overall_intensity_range", "" if summary["lowest_minimum"] is None else
                         f"{summary['lowest_minimum']} – {summary['highest_maximum']}"])
        writer.writerow([])
        writer.writerow(["Per-image statistics"])
        columns = ("file", "frame", *STAT_KEYS, "pixel_count", "invalid_pixels", "status", "error")
        records = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        records.writeheader()
        records.writerows(result["rows"])
