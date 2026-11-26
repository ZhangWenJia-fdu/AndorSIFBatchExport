# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for the Windows GUI release."""

from pathlib import Path

project_root = Path(SPECPATH)
scripts_dir = project_root / "scripts"

a = Analysis(
    [str(scripts_dir / "gui.py")],
    pathex=[str(scripts_dir)],
    binaries=[],
    datas=[],
    hiddenimports=["sif_parser._sif_open"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "IPython", "jupyter", "matplotlib", "scipy", "xarray", "dask"],
    noarchive=False,
    optimize=0,
)
# Windows 10/11 使用系统 UCRT，排除误收集的系统组件。
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() != "ucrtbase.dll"
              and not Path(entry[0]).name.lower().startswith("api-ms-win-")]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AndorSIFBatchExport",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AndorSIFBatchExport",
)
