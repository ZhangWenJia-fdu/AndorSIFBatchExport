"""收集随包说明和许可证；不改变可执行程序，拒绝覆盖。"""
from pathlib import Path
import shutil, zipfile, os, subprocess
ROOT = Path(__file__).resolve().parents[1]
def main():
    built = ROOT / "build/v1.1.0/dist/AndorSIFBatchExport"
    target = Path(os.environ.get("ANDOR_CANDIDATE_ZIP", str(ROOT / "release/AndorSIFBatchExport_Windows_x64_v1.1.0.zip")))
    if target.exists():
        raise FileExistsError("Refusing to overwrite an existing ZIP")
    if not (built / "AndorSIFBatchExport.exe").is_file():
        raise FileNotFoundError("Build the executable first")
    staging = ROOT / "build/v1.1.0/package/AndorSIFBatchExport"
    if staging.exists():
        raise FileExistsError("Use a new build/package directory")
    shutil.copytree(built, staging)
    for name in ("README.md", "使用说明.txt", "THIRD_PARTY_NOTICES.md", "LICENSE", "MICROSOFT_RUNTIME_TERMS.txt"):
        shutil.copyfile(ROOT / name, staging / name)
    shutil.copytree(ROOT / "third_party", staging / "third_party")
    shutil.copytree(ROOT / "docs", staging / "docs")
    # 从获许可的 Visual Studio 官方 REDIST 目录收集原样 x64 CRT。
    crt_setting = os.environ.get("ANDOR_VC_REDIST_DIR")
    if crt_setting:
        crt = Path(crt_setting)
    else:
        vswhere = Path(os.environ["ProgramFiles(x86)"]) / "Microsoft Visual Studio/Installer/vswhere.exe"
        install = subprocess.check_output([str(vswhere), "-latest", "-products", "*",
                    "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                    "-property", "installationPath"], text=True).strip()
        if not install:
            raise FileNotFoundError("A licensed Visual Studio with x64 C++ tools is required")
        folders = list((Path(install) / "VC/Redist/MSVC").glob("[0-9]*/x64/Microsoft.VC*.CRT"))
        if not folders:
            raise FileNotFoundError("Official x64 CRT REDIST folder not found")
        crt = max(folders, key=lambda f: tuple(int(x) for x in f.parts[-3].split(".")))
    if "debug_nonredist" in str(crt).lower():
        raise ValueError("Debug runtimes must not be redistributed")
    for name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        if not (crt / name).is_file():
            raise FileNotFoundError(crt / name)
    for name in ("vcruntime140.dll", "vcruntime140_1.dll"):
        shutil.copyfile(crt / name, staging / "_internal" / name.upper().replace(".DLL", ".dll"))
    numpy_crt = list((staging / "_internal/numpy.libs").glob("msvcp140-*.dll"))
    if len(numpy_crt) != 1:
        raise RuntimeError("Expected exactly one NumPy MSVCP140 runtime file")
    shutil.copyfile(crt / "msvcp140.dll", numpy_crt[0])
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, "AndorSIFBatchExport/" + path.relative_to(staging).as_posix())
    print(target.name, target.stat().st_size)
if __name__ == "__main__":
    main()
