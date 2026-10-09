# GPLv3 license change audit — 2026-10-09

This records evidence and unresolved questions, not a legal opinion or a certification of every binary dependency. Seekdex-owned code now uses **GPL-3.0-only**. Third-party notices and weights are not relicensed.

## Copyright and earlier grants

The current clean Git history records `ciqing wesley` as author. No other author or copied-code copyright header was found in application source. This is limited evidence: Git authorship does not prove exclusive ownership or the provenance of every fragment. The owner's instruction authorizes the change for their own code; any undisclosed external contributor or copied work must retain its rights and requires review before claiming it is project-owned.

Project-created screenshots use synthetic images and fictional paths. The icon is a pre-existing project SVG, without an external attribution recorded; its original authorship cannot be independently established from Git alone. No private photographs or RAW files are distributed.

Earlier MIT grants are not revoked. `docs/licenses/LEGACY-MIT.txt` preserves the earlier project's original notice; it is historical attribution, not a dual-license option for this GPL version. No old commit is amended or deleted.

## Dependency evidence and compatibility scope

The build exports installed versions and original notices in `licenses/dependencies.json`. The following are the actual local build versions, not a claim about future wheels.

| Component | Build version / original license | Review |
| --- | --- | --- |
| PySide6 / Shiboken6 / Qt | 6.11.2; LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only; module-specific notices retained | GPLv3 / LGPLv3 paths are available upstream. Keep dynamic DLL replacement and the LGPL source / notice obligations; do not select a GPLv2-only path for combination with GPLv3. |
| rawpy / LibRaw | rawpy 0.27.1 MIT; LibRaw LGPL-2.1 / CDDL alternatives | Keep original LibRaw texts; use the LGPL alternative, not an assertion that CDDL is GPL-compatible. Applicable native-library source and relinking obligations remain. |
| Pillow | 11.3.0 MIT-CMU | Keep PIL and codec notices. |
| NumPy | 2.5.3; BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | Retain bundled numerical-library notices. |
| ExifRead / PyTorch | BSD-3-Clause; torch 2.8.0+cpu | Preserve all bundled native notices; no CUDA weights are included. |
| OpenVINO | 2026.4.1 Apache-2.0 | Upstream and bundled plugin notices retained. |
| Transformers / Hub / Safetensors | Apache-2.0, individual upstream files retained | Library license does not supply model-weight authorization. |
| RapidOCR | 3.9.2 Apache-2.0 | Installed wheel does not expose a license file through metadata. The unmodified LICENSE from the official v3.9.2 tag is included separately in `packaging/notices/`. |
| ONNX Runtime | 1.30.0 MIT | Keep its full third-party notices. |
| Python | PSF and included third-party notices | Python / SQLite are not relicensed as GPL by this change. |
| PyInstaller | 6.22.3 GPL-2.0-or-later plus bootloader exception | Preserve its exception; using the tool does not change third-party or application licensing by itself. |
| Inno Setup | 6.7.3 original Inno Setup License | Unmodified build tool; retain its original text, no MIT / GPL relabeling. |
| Chinese-CLIP code | upstream MIT | Keep its OFA-Sys / OpenCLIP author notices. |
| Chinese-CLIP weights | fixed snapshot, independent redistribution authorization unconfirmed | No GPL conversion or redistribution authorization is inferred. Standard packages carry no weights. Do not publicly upload preinstalled weights until separately resolved. |
| OCR weights | upstream PaddleOCR Apache-2.0 attribution, RapidAI fixed mirror | Keep model provenance and hashes; code GPL does not change these terms. This task does not upload weights. |

MIT / revised BSD / permissive components can generally be combined with GPLv3 when their notices and applicable conditions are preserved. [FSF compatibility guidance](https://www.gnu.org/licenses/license-list.html) and the [Apache Software Foundation's GPL compatibility statement](https://www.apache.org/licenses/GPL-compatibility) describe the license families; this is not proof that every compiled payload is covered by that general statement. See [Qt for Python licenses](https://doc.qt.io/qtforpython-6/licenses.html), [LibRaw license choices](https://www.libraw.org/docs) and the [PyInstaller exception](https://pyinstaller.org/en/stable/license.html).

## Source and packaging

New standard Portable / Setup packages include the complete unchanged GNU GPLv3 text, COPYRIGHT.md, both third-party notice documents, installed-wheel notices, and the exact tracked application source / build scripts / workflow / documentation. `source-provenance.json` identifies the Git commit and source archive SHA256. There is no written blanket source offer that the maintainer has not verified they can fulfil.

The application archive provides Seekdex's modifiable source, not the full corresponding source of every bundled native dependency. Exact upstream dependency versions, source locations and rebuild / dynamic replacement guidance remain in THIRD_PARTY_LICENSES.md. Before distributing binaries, the distributor must verify the applicable LGPL / GPL source-delivery, installation-information and native component requirements for the actual payload and their chosen distribution method. This has **not** been independently certified here; keeping notices alone is not a substitute for those obligations.

## Unconfirmed items

1. Exclusive copyright / contributor authorization and the original icon's authorship cannot be proved from Git metadata alone.
2. Chinese-CLIP independent weight redistribution authorization remains unresolved; do not publish preinstalled weights.
3. Complete native-library corresponding-source / rebuild / installation-information compliance for every bundled wheel is not independently confirmed. Review this before publishing binaries; this audit does not declare legal compliance.
4. Existing MIT binaries in any draft are still MIT binaries. A new GPL tag does not retroactively relabel them; use the newly rebuilt, checksum-verified GPL artifacts when preparing that draft.
