# Third-party software notices

Third-party components retain their own copyrights and licenses. The
Non-Commercial Research Use License for AndorSIFBatchExport applies only to
original project content owned by Wenjia Zhang. It does not remove any rights
granted by third-party licenses, including rights to modify, redistribute or use
third-party components commercially where their licenses permit this.

## SIF reader: sif_parser 0.3.6

The application imports the external dependency `sif_parser` and calls
`sif_parser.np_open`. The application source does not vendor a parser implementation;
the Windows executable contains frozen dependency modules. It neither uses nor
bundles Andor SDK/parser source or SDK DLLs at runtime.

- Official project: https://github.com/fujiisoup/sif_parser
- Exact release: https://pypi.org/project/sif-parser/0.3.6/
- Declared license: BSD 3-clause (BSD-3-Clause).
- Release metadata author: Keisuke Fujii. This is not a replacement copyright statement.
- History: soemraws's Python SifImagePlugin, based on Marcel Leutenegger's MATLAB script.
  The earlier Python repository is https://github.com/soemraws/SifImagePlugin
  (Sumant Oemrawsingh in commit history).
- Original script: [Andor SIF image reader](https://www.mathworks.com/matlabcentral/fileexchange/11224-andor-sif-image-reader).
  Its license states `Copyright (c) 2006, Marcel Leutenegger` and allows source and
  binary redistribution with notice, disclaimer and non-endorsement conditions.
- Upstream contributions are acknowledged in [ATTRIBUTION.md](third_party/licenses/sif_parser/ATTRIBUTION.md).
  Contributor names are not asserted to be exclusive copyright holders.

Preserved license texts:

- [sif_parser release LICENSE](third_party/licenses/sif_parser/LICENSE.sif_parser.txt)
- [Original MATLAB license from MathWorks](third_party/licenses/sif_parser/LICENSE.MATLAB.txt)
- [Original MATLAB license block from the release README](third_party/licenses/sif_parser/LICENSE.MATLAB.from_sif_parser_README.txt)

The parser's own LICENSE is retained verbatim, including its reference to an
"above copyright notice" although no specific copyright holder/year is stated
there. The published text and author attribution are preserved without inventing
a replacement copyright statement. No endorsement is claimed.

## Other application components

| Component | Version | License / notice location |
| --- | --- | --- |
| Python / Tkinter | Python 3.13.5 | PSF and historical licenses; Python-LICENSE.txt and Python-third-party-notices.txt |
| Tcl/Tk | 8.6 family | Tcl-Tk-license.terms and component notices |
| NumPy | 2.5.4 | BSD and bundled component licenses under numpy/licenses |
| OpenBLAS / LAPACK | OpenBLAS reports 0.3.34.106.0 | Included in NumPy's full binary LICENSE.txt |
| Pillow | 12.3.0 | Full combined Pillow LICENSE, including bundled codecs and fonts |
| tifffile | 2026.9.20 | BSD-3-Clause, full supplied text |

All collected license texts are in [third_party/licenses](third_party/licenses).
Preserve full component texts, including additional notices within combined files.
Python's main license does not replace licenses of OpenSSL, libffi, zlib, Expat or
other bundled components.

## NumPy / OpenBLAS and GCC runtime

The Windows build contains
`numpy.libs/libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll`.
NumPy's binary license declares statically linked GCC runtime library code under
GPL-3.0-or-later WITH GCC-exception-3.1. The complete GPL and exception texts
remain in [NumPy's binary license](third_party/licenses/numpy/licenses/LICENSE.txt).

The GCC Runtime Library Exception 3.1 permits qualifying target-code combinations
with independent modules under their own terms. This release retains the unmodified
NumPy-wheel OpenBLAS binary and relies on that exception for the embedded GCC
runtime combination. No standalone GCC runtime library is separately supplied.
The full NumPy/OpenBLAS/LAPACK/GCC copyright, license and exception texts are
retained; the project's research-use restrictions do not apply to these components.
See [GCC's exception text](https://gcc.gnu.org/onlinedocs/libstdc++/manual/license.html).

## Microsoft runtime binaries and Windows requirements

Windows 10/11 x64 supplies the Universal CRT and API-set contracts. The release
does not bundle ucrtbase.dll or api-ms-win-*.dll from unrelated tool directories.
See [Microsoft UCRT deployment](https://learn.microsoft.com/en-us/cpp/windows/universal-crt-deployment).

The release supplies Microsoft x64 VCRUNTIME140.dll, VCRUNTIME140_1.dll and
MSVCP140.dll, file version 14.52.37029.0, copied without byte changes from the
Visual Studio Community 2026 release REDIST CRT folder. The MSVCP140 file uses
the filename expected by the NumPy wheel:
numpy.libs/msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll.
Only its filename differs; the Microsoft file bytes and signature are unchanged.
No debug, preview or Insiders runtime files are supplied.

Distribution follows the licensed Visual Studio user's Distributable Code grant
and the [Visual Studio 2026 REDIST list](https://learn.microsoft.com/en-us/visualstudio/releases/2026/redistribution).
The application adds its own primary functionality. Microsoft notices and
signatures are retained. These components are not relicensed under PSF, BSD or
the project's research-use license. Before use, recipients must accept the
Microsoft runtime terms as directed by MICROSOFT_RUNTIME_TERMS.txt.
The full [Microsoft runtime license](third_party/licenses/Microsoft-VC-Runtime-LICENSE.txt)
is included. Independent rights and applicable legal exceptions remain intact.
No Microsoft endorsement is claimed.

## Indirect, build and test dependencies

pandas 3.0.6, python-dateutil 2.9.0.post0, six 1.17.0 and tzdata 2026.5 satisfy
declared installation dependencies. The executable archive does not
contain pandas. Installed packages are not all runtime-distributed components.

PyInstaller 6.22.3 is a build tool; its bootloader exception permits applications
under other licenses subject to dependency terms. hooks-contrib, altgraph,
packaging, pefile, pywin32-ctypes and setuptools are build dependencies; pytest,
pluggy, iniconfig, Pygments and colorama are test dependencies. Collected licenses
for these tools and their vendored components remain available in the license tree.
Their licenses do not become the license of the project's original code.
