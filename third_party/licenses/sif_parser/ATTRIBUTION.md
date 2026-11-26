# sif_parser 0.3.6: sources and attribution

- Release source: https://pypi.org/project/sif-parser/0.3.6/
- Project: https://github.com/fujiisoup/sif_parser
- Published source archive SHA-256: `5faf1e156b1aef7835968bc3df651ad828c4d42f1c2c60a5bd26784519c2a111`.
- Distribution metadata identifies Keisuke Fujii as author and declares BSD 3-clause.
  This is author metadata, not an invented copyright notice or an exclusive ownership claim.
- The released README credits the original Python plugin to **soemraws**.
  The earlier project is https://github.com/soemraws/SifImagePlugin ; its commit
  history identifies Sumant Oemrawsingh. Its README credits Marcel Leutenegger's
  MATLAB script and includes that script's license.
- The upstream contributor listing includes fujiisoup, soemraws, bicarlsen,
  queezz, mruijzendaal and rjlopez2. This historical acknowledgment is not an
  exhaustive copyright register or a claim about which release contains each contribution.
- Original MATLAB program: https://www.mathworks.com/matlabcentral/fileexchange/11224-andor-sif-image-reader
- Original MATLAB license: https://addons.mathworks.com/downloads/e5/e5505d71-4a80-11e4-9553-005056977bd0/1.0.0/license.txt

## Preserved texts

- `LICENSE.sif_parser.txt`: verbatim LICENSE from the 0.3.6 release source.
  The existing `licenses/LICENSE` copy is also retained unchanged.
- `LICENSE.MATLAB.txt`: complete verbatim license from MathWorks, including
  `Copyright (c) 2006, Marcel Leutenegger` and the institutional non-endorsement clause.
- `LICENSE.MATLAB.from_sif_parser_README.txt`: complete original-script license
  block as published in the 0.3.6 README. Its formatting and wording differ slightly
  from the MathWorks text; both are preserved without rewriting either license.

The sif_parser LICENSE is preserved as published, including its reference to an
"above copyright notice"; no guessed holder or year has been inserted. The current
0.3.6 release declares BSD 3-clause. Original MATLAB license texts and historical
author acknowledgments are preserved separately.

AndorSIFBatchExport calls this dependency through `sif_parser.np_open`. The
public application source does not vendor the parser implementation. The Windows
application contains frozen dependency modules. No Andor SDK/parser source or
SDK DLL is distributed. These notices do not imply endorsement by Andor, the
parser authors, their institutions, or contributors.
