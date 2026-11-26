"""Andor Solis SIF 批量固定显示范围导出工具。"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
from pathlib import Path
import sys
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
import tifffile
import sif_parser


# ====================== 用户参数（可直接修改） ======================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = PROJECT_ROOT / "testdata"       # SIF 文件所在目录
OUTPUT_DIR = PROJECT_ROOT / "output"        # 导出目录
DISPLAY_MIN = 0.0                           # 统一显示黑场阈值
DISPLAY_MAX = 5000.0                        # 统一显示白场阈值
RECURSIVE = True                            # 是否搜索子目录
EXPORT_PNG = True                           # 是否导出 8-bit 灰度显示 PNG
EXPORT_TIFF_8BIT = True                     # 是否导出 8-bit 固定范围灰度 TIFF
EXPORT_TIFF_16BIT = True                    # 是否导出 16-bit 固定范围灰度 TIFF
EXPORT_RAW_TIFF = False                     # 是否导出解析器返回的原始像素 TIFF
INPUT_FILE = None                           # 单文件模式路径；None 时使用文件夹批量模式
ANALYZE_ONLY = False                        # 只分析强度并写报告，不进行常规图像导出
ANALYSIS_PREVIEW_COUNT = 3                  # 分析模式最多生成多少张统一范围预览 PNG；0 表示不生成
HISTOGRAM_BINS = 1024                       # 全批次直方图等宽区间数
VERIFY_WRITES = True                        # 导出后回读并逐像素验证
CALCULATE_SHA256 = True                     # 报告中记录源文件 SHA-256
DISPLAY_TIFF_ORIENTATION = "solis-compatible"  # 与 Solis GUI/Display TIFF 一致：显示 TIFF/PNG 上下翻转
# ==================================================================


def scale_to_display_dtype(frame: np.ndarray, display_min: float, display_max: float,
                           bits: int) -> np.ndarray:
    """直接从原始像素按固定范围映射到 uint8 或 uint16；取整采用向下取整。"""
    if bits not in (8, 16):
        raise ValueError("显示 TIFF 位深仅支持 8 或 16")
    if not np.isfinite(display_min) or not np.isfinite(display_max) or display_max <= display_min:
        raise ValueError("Display Min/Max 必须为有限数值，且 Max 大于 Min")
    values = np.asarray(frame, dtype=np.float64)
    if not np.all(np.isfinite(values)):
        raise ValueError("像素包含 NaN 或 Inf，拒绝映射")
    maximum_gray = (1 << bits) - 1
    mapped = np.floor(np.clip((values - display_min) / (display_max - display_min), 0, 1) * maximum_gray)
    return mapped.astype(np.uint8 if bits == 8 else np.uint16)


def scale_to_uint8(frame: np.ndarray, display_min: float, display_max: float) -> np.ndarray:
    """8-bit 灰度显示映射（保留旧函数接口）。"""
    return scale_to_display_dtype(frame, display_min, display_max, 8)


def scale_to_uint16(frame: np.ndarray, display_min: float, display_max: float) -> np.ndarray:
    """16-bit 灰度显示映射，直接从输入像素计算，不经过 8-bit。"""
    return scale_to_display_dtype(frame, display_min, display_max, 16)


def mapping_range(frame: np.ndarray, mode: str, low: float, high: float) -> tuple[float, float]:
    if mode == "custom":
        return low, high
    if mode == "minmax":
        return float(frame.min()), float(frame.max())
    if mode == "0-65535":
        return 0.0, 65535.0
    raise ValueError("未知 Data Range 模式")


def map_display(frame: np.ndarray, low: float, high: float, bits: int, mode: str = "custom") -> np.ndarray:
    """Custom 保留 v0.2 算法；Solis Min-Max 16-bit 使用实测 float32 系数。"""
    if not np.all(np.isfinite(frame)):
        raise ValueError("像素包含 NaN 或 Inf，拒绝映射")
    low, high = mapping_range(frame, mode, low, high)
    if mode == "minmax" and high == low:
        raise ValueError("常量图像的 Solis Min-Max 行为尚未验证，请使用 Custom 或 0–65535")
    if mode == "minmax" and bits == 16:
        values = np.asarray(frame, dtype=np.float32)
        mapped = np.floor((values - low) * np.float32(65535 / (high - low)))
        # 全部五个剩余差异均为最大值端点；实测 Solis 将端点设置为满灰度。
        mapped = np.where(values >= high, 65535, mapped)
        return np.clip(mapped, 0, 65535).astype(np.uint16)
    return scale_to_display_dtype(frame, low, high, bits)


def range_label(mode: str, low: float, high: float) -> str:
    def encode(value: float) -> str:
        value = float(value)
        if not np.isfinite(value):
            raise ValueError("范围必须为有限数值")
        token = str(int(value)) if value.is_integer() and abs(value) < 1e20 else repr(value)
        return token.replace("-", "m").replace(".", "p").replace("+", "plus")
    return f"custom_{encode(low)}-{encode(high)}" if mode == "custom" else mode


def orient_display_tiff(image: np.ndarray, mode: str) -> np.ndarray:
    """调整 Display TIFF/PNG 行方向；Solis-compatible 为上下翻转，须显式选择。"""
    if mode == "solis-compatible":
        return np.flipud(image)
    if mode == "native":
        return image
    raise ValueError("Display TIFF 方向必须是 solis-compatible 或 native")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def discover_sif_files(input_dir: Path, recursive: bool) -> list[Path]:
    if not input_dir.is_dir():
        raise FileNotFoundError(f"输入目录不存在或不是目录: {input_dir}")
    paths = input_dir.rglob("*") if recursive else input_dir.iterdir()
    return sorted((p for p in paths if p.is_file() and p.suffix.lower() == ".sif"), key=lambda p: str(p).casefold())


def read_sif_frames(path: Path) -> tuple[list[np.ndarray], dict[str, Any]]:
    """读取 SIF；二维视为单帧，三维按 sif_parser 返回的首轴作为帧轴。"""
    data, info = sif_parser.np_open(str(path))
    data = np.asarray(data)
    if not np.issubdtype(data.dtype, np.number):
        raise TypeError(f"SIF 像素必须是数值类型，实际为 {data.dtype}")
    if np.issubdtype(data.dtype, np.complexfloating):
        raise TypeError(f"不支持复数像素数据: {data.dtype}")
    if data.ndim == 2:
        frames = [data]
    elif data.ndim == 3:
        # sif_parser 将多帧图像返回为 (帧, 高, 宽)；只接受可明确解释的常见布局。
        frames = [data[i] for i in range(data.shape[0])]
    else:
        raise ValueError(f"无法可靠解释解析数组轴，形状为 {data.shape}；仅支持二维单帧或三维帧堆栈")
    if not frames or any(frame.ndim != 2 or not frame.size for frame in frames):
        raise ValueError(f"无效帧尺寸: {data.shape}")
    return frames, info if isinstance(info, dict) else {"reader_info": repr(info)}


def _export_frame(frame: np.ndarray, png_path: Path | None, display_tiff_path: Path | None,
                  raw_tiff_path: Path | None,
                  display_min: float, display_max: float, verify: bool,
                  display_tiff16_path: Path | None = None,
                  display_tiff_orientation: str = "solis-compatible",
                  data_range: str = "custom") -> tuple[str, str, str, str]:
    if not np.all(np.isfinite(frame)):
        raise ValueError("像素包含 NaN 或 Inf，拒绝导出")
    png_check = "未导出"
    display_tiff8_check = "未导出"
    display_tiff16_check = "未导出"
    raw_tiff_check = "未导出"
    if png_path is not None:
        png_path.parent.mkdir(parents=True, exist_ok=True)
        if png_path.exists():
            raise FileExistsError(f"目标文件已存在，禁止覆盖: {png_path}")
        display = orient_display_tiff(map_display(frame, display_min, display_max, 8, data_range), display_tiff_orientation)
        Image.fromarray(display).save(png_path)
        if verify:
            with Image.open(png_path) as image:
                if not np.array_equal(display, np.asarray(image)):
                    raise IOError(f"PNG 回读像素不一致: {png_path}")
            png_check = "PASS"
    if display_tiff_path is not None:
        display_tiff_path.parent.mkdir(parents=True, exist_ok=True)
        if display_tiff_path.exists():
            raise FileExistsError(f"目标文件已存在，禁止覆盖: {display_tiff_path}")
        display = orient_display_tiff(map_display(frame, display_min, display_max, 8, data_range), display_tiff_orientation)
        tifffile.imwrite(display_tiff_path, display, photometric="minisblack")
        if verify:
            restored = tifffile.imread(display_tiff_path)
            if not np.array_equal(display, restored):
                raise IOError(f"Display TIFF 回读像素不一致: {display_tiff_path}")
            with tifffile.TiffFile(display_tiff_path) as tif:
                page = tif.pages[0]
                if page.tags["BitsPerSample"].value != 8 or page.tags["SamplesPerPixel"].value != 1:
                    raise IOError(f"8-bit TIFF 标签不符合单通道 8-bit: {display_tiff_path}")
            display_tiff8_check = "PASS"
    if display_tiff16_path is not None:
        display_tiff16_path.parent.mkdir(parents=True, exist_ok=True)
        if display_tiff16_path.exists():
            raise FileExistsError(f"目标文件已存在，禁止覆盖: {display_tiff16_path}")
        display16 = orient_display_tiff(map_display(frame, display_min, display_max, 16, data_range), display_tiff_orientation)
        tifffile.imwrite(display_tiff16_path, display16, photometric="minisblack")
        if verify:
            restored16 = tifffile.imread(display_tiff16_path)
            with tifffile.TiffFile(display_tiff16_path) as tif:
                page = tif.pages[0]
                if (restored16.dtype != np.uint16 or not np.array_equal(display16, restored16)
                        or page.tags["BitsPerSample"].value != 16
                        or page.tags["SamplesPerPixel"].value != 1):
                    raise IOError(f"16-bit TIFF 回读、dtype 或 BitsPerSample 验证失败: {display_tiff16_path}")
            display_tiff16_check = "PASS"
    if raw_tiff_path is not None:
        raw_tiff_path.parent.mkdir(parents=True, exist_ok=True)
        if raw_tiff_path.exists():
            raise FileExistsError(f"目标文件已存在，禁止覆盖: {raw_tiff_path}")
        tifffile.imwrite(raw_tiff_path, frame, photometric="minisblack")
        if verify:
            restored = tifffile.imread(raw_tiff_path)
            if restored.dtype != frame.dtype or not np.array_equal(frame, restored):
                raise IOError(f"Raw TIFF 回读像素或数据类型不一致: {raw_tiff_path}")
            raw_tiff_check = "PASS"
    return png_check, display_tiff8_check, display_tiff16_check, raw_tiff_check


def _bool_value(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"y", "yes", "true", "1"}:
        return True
    if normalized in {"n", "no", "false", "0"}:
        return False
    raise argparse.ArgumentTypeError("请输入 y/yes 或 n/no")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="使用固定 Display Min/Max 批量导出 SIF 图像")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--input", "--input-dir", dest="input", type=Path, help="SIF 输入目录（批量模式）")
    source.add_argument("--file", dest="input_file", type=Path, help="单文件模式：指定一个 SIF 文件")
    parser.add_argument("--output", type=Path, help="导出根目录")
    parser.add_argument("--min", dest="display_min", type=float, help="Display Min")
    parser.add_argument("--max", dest="display_max", type=float, help="Display Max")
    parser.add_argument("--data-range", choices=("custom", "minmax", "0-65535"), help="显示数据范围模式")
    parser.add_argument("--recursive", type=_bool_value, help="是否递归搜索（y/n）")
    parser.add_argument("--png", type=_bool_value, help="是否导出 PNG（y/n）")
    parser.add_argument("--display-tiff", "--tiff8", dest="display_tiff", type=_bool_value,
                        help="是否导出 8-bit 显示 TIFF（y/n）")
    parser.add_argument("--tiff16", type=_bool_value, help="是否导出 16-bit 显示 TIFF（y/n）")
    parser.add_argument("--raw-tiff", type=_bool_value, help="是否导出原始 TIFF（y/n）")
    parser.add_argument("--display-tiff-orientation", choices=("solis-compatible", "native"),
                        help="Display TIFF 方向：Solis 兼容（上下翻转）或解析数组原方向")
    parser.add_argument("--analyze-only", action="store_true", help="仅分析像素分布，不执行常规图像导出")
    parser.add_argument("--preview-count", type=int, help="分析模式最多生成的统一范围预览数，0 表示不生成")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--interactive", action="store_true", help="对未由命令行指定的参数进行交互询问")
    mode.add_argument("--no-interactive", action="store_true", help="禁止交互，适用于自动化运行")
    return parser


def _prompt(label: str, default: Any, converter: Any = str) -> Any:
    while True:
        raw = input(f"{label} [默认: {default}]: ").strip()
        if not raw:
            return default
        try:
            return converter(raw)
        except (ValueError, argparse.ArgumentTypeError) as exc:
            print(f"输入无效：{exc}")


def resolve_options(args: argparse.Namespace) -> dict[str, Any]:
    defaults = {"input": INPUT_DIR, "input_file": INPUT_FILE, "output": OUTPUT_DIR,
                "display_min": DISPLAY_MIN, "display_max": DISPLAY_MAX, "recursive": RECURSIVE,
                "png": EXPORT_PNG, "display_tiff": EXPORT_TIFF_8BIT,
                "tiff16": EXPORT_TIFF_16BIT, "raw_tiff": EXPORT_RAW_TIFF, "analyze_only": ANALYZE_ONLY,
                "preview_count": ANALYSIS_PREVIEW_COUNT, "data_range": "custom",
                "display_tiff_orientation": DISPLAY_TIFF_ORIENTATION}
    explicit = {"input": args.input, "input_file": args.input_file, "output": args.output,
                "display_min": args.display_min, "display_max": args.display_max,
                "recursive": args.recursive, "png": args.png, "display_tiff": args.display_tiff,
                "tiff16": args.tiff16, "raw_tiff": args.raw_tiff,
                "analyze_only": True if args.analyze_only else None,
                "preview_count": args.preview_count, "data_range": args.data_range,
                "display_tiff_orientation": args.display_tiff_orientation}
    interactive = args.interactive or (not args.no_interactive and not any(v is not None for v in explicit.values()) and sys.stdin.isatty())
    converters = {"input": Path, "input_file": Path, "output": Path, "display_min": float,
                  "display_max": float, "recursive": _bool_value, "png": _bool_value,
                  "display_tiff": _bool_value, "raw_tiff": _bool_value,
                  "tiff16": _bool_value, "data_range": str,
                  "display_tiff_orientation": str,
                  "analyze_only": _bool_value, "preview_count": int}
    labels = {"input": "Input folder", "input_file": "Single SIF file (留空使用输入文件夹)",
              "output": "Output folder", "display_min": "Display Min",
              "display_max": "Display Max", "recursive": "Recursive search (Y/n)",
              "png": "Export Display PNG (Y/n)", "display_tiff": "Export Display TIFF (Y/n)",
              "tiff16": "Export 16-bit Display TIFF (Y/n)", "data_range": "Data Range (custom/minmax/0-65535)",
              "raw_tiff": "Export Raw TIFF (Y/n)", "analyze_only": "Analysis only, no exports (y/N)",
              "display_tiff_orientation": "Display TIFF orientation (solis-compatible/native)",
              "preview_count": "Analysis preview count (0 disables previews)"}
    options: dict[str, Any] = {}
    for key, default in defaults.items():
        if key == "input_file" and args.input is not None:
            options[key] = None
        elif key == "input_file" and interactive and explicit[key] is None:
            options[key] = _prompt(labels[key], default or "", lambda value: Path(value) if value else default)
        elif key == "analyze_only" and interactive and explicit[key] is None:
            options[key] = _prompt(labels[key], default, _bool_value)
        elif explicit[key] is not None:
            options[key] = explicit[key]
        else:
            options[key] = _prompt(labels[key], default, converters[key]) if interactive else default
    if options["input_file"] is not None:
        options["input_file"] = Path(options["input_file"])
    return options


def _intensity_stats(values: np.ndarray) -> dict[str, float | int]:
    flat = np.asarray(values).reshape(-1)
    return {
        "pixel_count": int(flat.size), "min": float(np.min(flat)), "max": float(np.max(flat)),
        "mean": float(np.mean(flat, dtype=np.float64)), "median": float(np.percentile(flat, 50)),
        "p1": float(np.percentile(flat, 1)), "p5": float(np.percentile(flat, 5)),
        "p95": float(np.percentile(flat, 95)), "p99": float(np.percentile(flat, 99)),
        "p99_9": float(np.percentile(flat, 99.9)),
    }


def _csv_report_path(output_dir: Path, prefix: str, stamp: str) -> Path:
    path = output_dir / "reports" / f"{prefix}_{stamp}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"报告文件已存在，拒绝覆盖: {path}")
    return path


def _write_histogram_png(path: Path, edges: np.ndarray, counts: np.ndarray) -> None:
    """用 Pillow 绘制全批次直方图，纵轴 log10(count+1) 便于观察长尾。"""
    width, height = 1200, 650
    left, top, right, bottom = 105, 55, 1160, 565
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    maximum = max(1, int(np.max(counts)))
    max_log = np.log10(maximum + 1)
    draw.text((left, 15), "Pooled pixel intensity histogram (log10 bin count + 1)", fill="black")
    # 画水平网格及轴刻度。
    for tick in range(6):
        y = bottom - round((bottom - top) * tick / 5)
        draw.line((left, y, right, y), fill="#dddddd", width=1)
        draw.text((8, y - 7), f"{max_log * tick / 5:.1f}", fill="black")
    bin_width = (right - left) / len(counts)
    for index, count in enumerate(counts):
        value = np.log10(int(count) + 1)
        bar_height = round((bottom - top) * value / max_log) if max_log else 0
        x0 = left + round(index * bin_width)
        x1 = left + max(1, round((index + 1) * bin_width))
        if bar_height:
            draw.rectangle((x0, bottom - bar_height, min(x1, right), bottom), fill="#3569a8")
    draw.line((left, top, left, bottom, right, bottom), fill="black", width=2)
    draw.text((left, bottom + 12), f"{edges[0]:.6g}", fill="black")
    mid = (left + right) // 2
    draw.text((mid - 40, bottom + 12), f"{(edges[0] + edges[-1]) / 2:.6g}", fill="black")
    draw.text((right - 70, bottom + 12), f"{edges[-1]:.6g}", fill="black")
    draw.text((width // 2 - 35, height - 35), "Pixel intensity", fill="black")
    image.save(path)


def analyze_batch(options: dict[str, Any]) -> dict[str, Any]:
    """只读分析 SIF 像素并写统计/直方图报告；预览遵循同一个显示范围。"""
    output_dir = Path(options["output"]).expanduser()
    display_min, display_max = float(options["display_min"]), float(options["display_max"])
    if not np.isfinite(display_min) or not np.isfinite(display_max) or display_max <= display_min:
        raise ValueError("Display Min/Max 必须是有限数值，且 Display Max 必须大于 Display Min")
    preview_count = int(options["preview_count"])
    if preview_count < 0:
        raise ValueError("预览数量不能小于 0")
    input_file = options.get("input_file")
    if input_file is not None:
        source = Path(input_file).expanduser()
        if not source.is_file() or source.suffix.lower() != ".sif":
            raise FileNotFoundError(f"单文件必须是存在的 .sif 文件: {source}")
        input_dir, files = source.parent, [source]
    else:
        input_dir = Path(options["input"]).expanduser()
        files = discover_sif_files(input_dir, bool(options["recursive"]))
    if not files:
        raise FileNotFoundError(f"输入位置没有找到 SIF 文件: {input_dir}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    file_report = _csv_report_path(output_dir, "analysis_files", stamp)
    batch_report = _csv_report_path(output_dir, "analysis_batch", stamp)
    candidate_report = _csv_report_path(output_dir, "analysis_candidates", stamp)
    histogram_report = _csv_report_path(output_dir, "analysis_histogram", stamp)
    histogram_image = histogram_report.with_suffix(".png")
    file_columns = ["source_file", "source_sha256", "frame_number", "total_frames", "height", "width", "dtype",
                    "pixel_count", "min", "max", "mean", "median", "p1", "p5", "p95", "p99", "p99_9",
                    "display_min", "display_max", "below_min_pct", "above_max_pct", "all_black", "all_white",
                    "warning", "status", "error"]
    good_frames: list[np.ndarray] = []
    source_frames: list[tuple[Path, Path, int, int, np.ndarray]] = []
    errors = 0
    print(f"分析模式（不导出常规 PNG/TIFF）：找到 SIF 文件 {len(files)} 个")
    with file_report.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=file_columns)
        writer.writeheader()
        for file_index, source in enumerate(files, 1):
            print(f"[{file_index}/{len(files)}] {source}", flush=True)
            try:
                frames, _ = read_sif_frames(source)
                digest = sha256_file(source) if CALCULATE_SHA256 else ""
            except Exception as exc:
                writer.writerow({"source_file": str(source), "status": "FAIL_READ", "error": repr(exc)})
                stream.flush(); errors += 1
                continue
            relative = source.relative_to(input_dir)
            for index, frame in enumerate(frames, 1):
                row: dict[str, Any] = {"source_file": str(source), "source_sha256": digest,
                       "frame_number": index, "total_frames": len(frames), "height": frame.shape[0],
                       "width": frame.shape[1], "dtype": str(frame.dtype), "display_min": display_min,
                       "display_max": display_max}
                finite = np.isfinite(frame)
                if not np.all(finite):
                    row.update(pixel_count=int(frame.size), status="FAIL_NONFINITE",
                               error=f"包含 {int(frame.size - np.count_nonzero(finite))} 个 NaN/Inf")
                    writer.writerow(row); stream.flush(); errors += 1
                    continue
                row.update(_intensity_stats(frame))
                row["below_min_pct"] = 100 * np.count_nonzero(frame < display_min) / frame.size
                row["above_max_pct"] = 100 * np.count_nonzero(frame > display_max) / frame.size
                display = scale_to_uint8(frame, display_min, display_max)
                row["all_black"] = bool(np.all(display == 0))
                row["all_white"] = bool(np.all(display == 255))
                warning = ""
                if row["all_black"]:
                    warning = "警告：整张图像映射为全黑"
                    print(f"警告：{source.name} 第 {index} 帧映射结果全黑；请检查显示范围。")
                elif row["all_white"]:
                    warning = "警告：整张图像映射为全白"
                    print(f"警告：{source.name} 第 {index} 帧映射结果全白；请检查显示范围。")
                row["warning"], row["status"] = warning, "PASS_ANALYSIS"
                writer.writerow(row); stream.flush()
                good_frames.append(frame)
                source_frames.append((source, relative, index, len(frames), frame))

    if not good_frames:
        raise ValueError(f"没有可用于统计的有效帧；逐帧错误记录见 {file_report}")
    # 当前真实批次规模可直接合并计算精确全批次分位数。
    all_pixels = np.concatenate([frame.reshape(-1) for frame in good_frames])
    batch_stats = _intensity_stats(all_pixels)
    with batch_report.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["scope", "pixel_count", "min", "max", "mean", "median", "p1", "p5", "p95", "p99", "p99_9", "valid_frames", "failed_frames"])
        writer.writeheader()
        writer.writerow({"scope": "全批次有效像素合并统计（非逐文件平均）", **batch_stats,
                         "valid_frames": len(good_frames), "failed_frames": errors})
    candidate_rows = [
        {"candidate": "全批次观测 Min-Max", "display_min": batch_stats["min"], "display_max": batch_stats["max"], "basis": "保留全部有限观测值；极端值可能压缩显示对比"},
        {"candidate": "全批次 P0.1-P99.9", "display_min": float(np.percentile(all_pixels, 0.1)), "display_max": batch_stats["p99_9"], "basis": "两端各裁剪约 0.1% 全批次像素"},
        {"candidate": "全批次 P1-P99", "display_min": batch_stats["p1"], "display_max": batch_stats["p99"], "basis": "两端各裁剪约 1% 全批次像素"},
        {"candidate": "全批次 P5-P95", "display_min": batch_stats["p5"], "display_max": batch_stats["p95"], "basis": "两端各裁剪约 5% 全批次像素"},
    ]
    with candidate_report.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["candidate", "display_min", "display_max", "basis", "selected_automatically"])
        writer.writeheader()
        for row in candidate_rows:
            writer.writerow({**row, "selected_automatically": "否"})
    hist, edges = np.histogram(all_pixels, bins=HISTOGRAM_BINS, range=(batch_stats["min"], batch_stats["max"]))
    with histogram_report.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["batch_scope", "bin_left", "bin_right", "pixel_count", "fraction_of_valid_batch"])
        for left, right, count in zip(edges[:-1], edges[1:], hist):
            writer.writerow(["all valid pixels pooled", left, right, int(count), float(count / all_pixels.size)])
    _write_histogram_png(histogram_image, edges, hist)

    preview_paths: list[Path] = []
    if preview_count:
        preview_root = output_dir / "reports" / f"previews_{stamp}"
        for source, relative, index, total, frame in source_frames[:preview_count]:
            suffix = f"_frame{index:04d}" if total > 1 else ""
            preview_path = preview_root / relative.parent / f"{source.stem}{suffix}_preview.png"
            if preview_path.exists():
                raise FileExistsError(f"预览文件已存在，拒绝覆盖: {preview_path}")
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(scale_to_uint8(frame, display_min, display_max)).save(preview_path)
            preview_paths.append(preview_path)

    print(f"全批次统计基于 {all_pixels.size} 个有限像素（有效帧 {len(good_frames)}，失败帧 {errors}）。")
    print(f"Min={batch_stats['min']:.6g}, Max={batch_stats['max']:.6g}, Mean={batch_stats['mean']:.6g}, Median={batch_stats['median']:.6g}, P1={batch_stats['p1']:.6g}, P5={batch_stats['p5']:.6g}, P95={batch_stats['p95']:.6g}, P99={batch_stats['p99']:.6g}, P99.9={batch_stats['p99_9']:.6g}")
    print(f"逐图报告: {file_report}\n全批次报告: {batch_report}\n候选范围: {candidate_report}\n直方图 CSV: {histogram_report}\n直方图图像: {histogram_image}")
    print(f"仅按统一显示范围 {display_min}–{display_max} 生成预览 {len(preview_paths)} 张；候选范围未自动应用。")
    return {"files": len(files), "valid_frames": len(good_frames), "failed_frames": errors,
            "pixel_count": int(all_pixels.size), "stats": batch_stats, "candidates": candidate_rows,
            "file_report": file_report, "batch_report": batch_report,
            "candidate_report": candidate_report, "histogram_report": histogram_report,
            "histogram_image": histogram_image, "previews": preview_paths}


def run_batch(options: dict[str, Any], progress_callback: Any = None) -> tuple[int, int, int, Path]:
    output_dir = Path(options["output"]).expanduser()
    input_file = options.get("input_file")
    if input_file is not None:
        input_file = Path(input_file).expanduser()
        if not input_file.is_file() or input_file.suffix.lower() != ".sif":
            raise FileNotFoundError(f"单文件必须是存在的 .sif 文件: {input_file}")
        input_dir, files = input_file.parent, [input_file]
    else:
        input_dir = Path(options["input"]).expanduser()
        files = options.get("_files")
        files = list(files) if files is not None else discover_sif_files(input_dir, bool(options["recursive"]))
    mode = options.get("data_range", "custom")
    has_display = options["png"] or options["display_tiff"] or options.get("tiff16", False)
    if has_display and mode not in {"custom", "minmax", "0-65535"}:
        raise ValueError("请选择 Data Range")
    display_min, display_max = (float(options["display_min"]), float(options["display_max"])) if has_display and mode == "custom" else (0.0, 65535.0)
    if has_display and mode == "custom" and (not np.isfinite(display_min) or not np.isfinite(display_max) or display_max <= display_min):
        raise ValueError("Display Min/Max 必须是有限数值，且 Display Max 必须大于 Display Min")
    if not options["png"] and not options["display_tiff"] and not options.get("tiff16", False) and not options["raw_tiff"]:
        raise ValueError("至少选择一种导出格式")
    print(f"找到 SIF 文件：{len(files)} 个")
    if not files:
        raise FileNotFoundError(f"目录中未找到 .sif 文件: {input_dir}")
    print(f"输入目录: {input_dir}\n输出目录: {output_dir}\nDisplay Min/Max: {display_min} / {display_max}\n递归: {options['recursive']}；Display PNG: {options['png']}；8-bit TIFF: {options['display_tiff']}；16-bit TIFF: {options.get('tiff16', False)}；Raw TIFF: {options['raw_tiff']}")
    output_dir.mkdir(parents=True, exist_ok=True)
    report = output_dir / "reports" / f"audit_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    if progress_callback is not None:
        progress_callback({"phase": "start", "total": len(files), "processed": 0,
                           "succeeded": 0, "failed": 0, "report": report})
    orientation = options.get("display_tiff_orientation", DISPLAY_TIFF_ORIENTATION)
    if orientation not in {"solis-compatible", "native"}:
        raise ValueError("Display TIFF 方向必须是 solis-compatible 或 native")
    columns = ["data_range", "user_min", "user_max", "actual_min", "actual_max", "raw_mapping", "raw_orientation", "source_file", "source_sha256", "reader_version", "detector_type", "detector_temperature", "exposure_time", "accumulated_cycles", "experiment_time", "frame_number", "total_frames", "height", "width", "dtype", "pixel_min", "pixel_max", "pixel_mean", "nonfinite_count", "below_min_pct", "above_max_pct", "display_min", "display_max", "display_tiff_orientation", "png_path", "display_tiff_path", "display_tiff16_path", "raw_tiff_path", "png_verification", "display_tiff_verification", "display_tiff16_verification", "raw_tiff_verification", "all_black", "all_white", "display_warning", "status", "error"]
    successful_files = failed_files = exported = 0
    with report.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for file_index, source in enumerate(files, 1):
            print(f"[{file_index}/{len(files)}] {source}", flush=True)
            digest = ""
            try:
                if CALCULATE_SHA256:
                    digest = sha256_file(source)
                frames, info = read_sif_frames(source)
            except Exception as exc:
                writer.writerow({"source_file": str(source), "source_sha256": digest, "status": "FAIL_READ", "error": repr(exc)})
                stream.flush(); failed_files += 1
                if progress_callback is not None:
                    progress_callback({"phase": "file", "total": len(files), "processed": file_index,
                                       "succeeded": successful_files, "failed": failed_files,
                                       "source": source, "status": "FAIL_READ", "report": report})
                continue
            parent = source.relative_to(input_dir).parent
            file_failed = False
            for frame_index, frame in enumerate(frames, 1):
                suffix = f"_frame{frame_index:04d}" if len(frames) > 1 else ""
                stem = f"{source.stem}{suffix}"
                label = range_label(mode, display_min, display_max) if has_display else ""
                actual_low, actual_high = mapping_range(frame, mode, display_min, display_max) if has_display else ("", "")
                png = output_dir / "display_png" / parent / f"{stem}_8bit_{label}.png" if options["png"] else None
                display_tiff = output_dir / "display_tiff_8bit" / parent / f"{stem}_8bit_{label}.tif" if options["display_tiff"] else None
                display_tiff16 = output_dir / "display_tiff_16bit" / parent / f"{stem}_16bit_{label}.tif" if options.get("tiff16", False) else None
                raw_tiff = output_dir / "raw_tiff" / parent / f"{stem}_raw.tif" if options["raw_tiff"] else None
                row = {"data_range": mode if has_display else "none", "user_min": display_min if has_display and mode == "custom" else "",
                       "user_max": display_max if has_display and mode == "custom" else "", "actual_min": actual_low, "actual_max": actual_high,
                       "raw_mapping": "none", "raw_orientation": "native", "source_file": str(source), "source_sha256": digest,
                       "reader_version": str(info.get("SifVersion", info.get("sif_version", ""))),
                       "detector_type": str(info.get("DetectorType", "")),
                       "detector_temperature": info.get("DetectorTemperature", ""),
                       "exposure_time": info.get("ExposureTime", ""),
                       "accumulated_cycles": info.get("AccumulatedCycles", ""),
                       "experiment_time": info.get("ExperimentTime", ""),
                       "frame_number": frame_index, "total_frames": len(frames), "height": frame.shape[0], "width": frame.shape[1],
                       "dtype": str(frame.dtype), "display_min": actual_low, "display_max": actual_high,
                       "display_tiff_orientation": orientation,
                       "png_path": str(png) if png else "", "display_tiff_path": str(display_tiff) if display_tiff else "",
                       "display_tiff16_path": str(display_tiff16) if display_tiff16 else "",
                       "raw_tiff_path": str(raw_tiff) if raw_tiff else ""}
                png_existed = png.exists() if png is not None else False
                display_tiff_existed = display_tiff.exists() if display_tiff is not None else False
                display_tiff16_existed = display_tiff16.exists() if display_tiff16 is not None else False
                raw_tiff_existed = raw_tiff.exists() if raw_tiff is not None else False
                try:
                    finite = np.isfinite(frame)
                    row["nonfinite_count"] = int(frame.size - np.count_nonzero(finite))
                    if not np.all(finite):
                        raise ValueError("像素包含 NaN 或 Inf，拒绝导出")
                    row.update(pixel_min=float(frame.min()), pixel_max=float(frame.max()), pixel_mean=float(frame.mean(dtype=np.float64)),
                               below_min_pct=100 * np.count_nonzero(frame < actual_low) / frame.size if has_display else "",
                               above_max_pct=100 * np.count_nonzero(frame > actual_high) / frame.size if has_display else "")
                    if options["png"] or options["display_tiff"] or options.get("tiff16", False):
                        mapped = map_display(frame, display_min, display_max, 8, mode)
                        row["all_black"], row["all_white"] = bool(np.all(mapped == 0)), bool(np.all(mapped == 255))
                        if row["all_black"]:
                            row["display_warning"] = "警告：整张图像映射为全黑"
                            print(f"警告：{source.name} 第 {frame_index} 帧映射结果全黑；请检查显示范围。")
                        elif row["all_white"]:
                            row["display_warning"] = "警告：整张图像映射为全白"
                            print(f"警告：{source.name} 第 {frame_index} 帧映射结果全白；请检查显示范围。")
                    (row["png_verification"], row["display_tiff_verification"],
                     row["display_tiff16_verification"], row["raw_tiff_verification"]) = _export_frame(
                        frame, png, display_tiff, raw_tiff, display_min, display_max, VERIFY_WRITES,
                        display_tiff16_path=display_tiff16, display_tiff_orientation=orientation, data_range=mode)
                    row["status"] = "PASS_INTERNAL"
                    exported += int(png is not None) + int(display_tiff is not None) + int(display_tiff16 is not None) + int(raw_tiff is not None)
                except Exception as exc:
                    row["status"], row["error"] = "FAIL_EXPORT", repr(exc)
                    file_failed = True
                    # 失败也可能发生在一种格式已经写出之后，统计实际落盘的文件。
                    exported += (int(png is not None and not png_existed and png.exists())
                                 + int(display_tiff is not None and not display_tiff_existed and display_tiff.exists())
                                 + int(display_tiff16 is not None and not display_tiff16_existed and display_tiff16.exists())
                                 + int(raw_tiff is not None and not raw_tiff_existed and raw_tiff.exists()))
                writer.writerow(row); stream.flush()
            if file_failed:
                failed_files += 1
            else:
                successful_files += 1
            if progress_callback is not None:
                progress_callback({"phase": "file", "total": len(files), "processed": file_index,
                                   "succeeded": successful_files, "failed": failed_files,
                                   "source": source, "status": "FAIL_EXPORT" if file_failed else "PASS_INTERNAL",
                                   "report": report})
    with report.open(encoding="utf-8-sig", newline="") as audit_stream:
        audit_rows = list(csv.DictReader(audit_stream))
    format_report = report.with_name(report.stem + "_formats.csv")
    fields = ["source_file", "frame_number", "format", "data_range", "user_min", "user_max", "actual_min", "actual_max", "dtype", "BitsPerSample", "orientation", "pixel_min", "pixel_max", "pixel_mean", "output_path", "verification", "status", "error"]
    with format_report.open("x", encoding="utf-8-sig", newline="") as format_stream:
        fw = csv.DictWriter(format_stream, fieldnames=fields)
        fw.writeheader()
        for record in audit_rows:
            for fmt, path_key, check_key, dtype, bits in (
                ("png", "png_path", "png_verification", "uint8", 8),
                ("tiff8", "display_tiff_path", "display_tiff_verification", "uint8", 8),
                ("tiff16", "display_tiff16_path", "display_tiff16_verification", "uint16", 16),
                ("raw", "raw_tiff_path", "raw_tiff_verification", record.get("dtype", ""), 32)):
                if not record.get(path_key):
                    continue
                r = {k: record.get(k, "") for k in fields}
                r.update(format=fmt, dtype=dtype, BitsPerSample=bits, orientation="native" if fmt == "raw" else orientation,
                         output_path=record[path_key], verification=record.get(check_key, ""),
                         status="PASS_INTERNAL" if record.get(check_key) == "PASS" else record.get("status", ""))
                if fmt == "raw":
                    r.update(data_range="none", user_min="", user_max="", actual_min="", actual_max="")
                fw.writerow(r)
    print(f"批次结束：文件 {len(files)}，成功文件 {successful_files}，失败文件 {failed_files}，实际导出图像文件 {exported}。")
    print(f"报告位置: {report}\nPASS_INTERNAL 仅代表解析后内部处理/回读验证通过，不代表 Solis 交叉验证。")
    if progress_callback is not None:
        progress_callback({"phase": "finished", "total": len(files), "processed": len(files),
                           "succeeded": successful_files, "failed": failed_files, "report": report})
    return len(files), successful_files, failed_files, report


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        options = resolve_options(args)
        if options["analyze_only"]:
            result = analyze_batch(options)
            return 1 if result["failed_frames"] else 0
        _, _, failures, _ = run_batch(options)
        return 1 if failures else 0
    except (ValueError, FileNotFoundError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
