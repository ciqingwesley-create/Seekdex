# Windows native distribution evidence

Status: **BLOCKED for public binary distribution** until the remaining evidence in the packaged source plan and native inventory is verified. Technical consistency and passing tests are separate from license compliance.

## Reproduce the review

1. Install the project's build dependencies in a Windows Python 3.12 environment. Use the recorded wheel versions, not a silently updated environment.
2. Run `python scripts/fetch_native_sources.py`. This explicitly downloads hash-pinned official source inputs, not models. No source material is uploaded. Qt SHA256 values were verified against the official MirrorBrain metalinks. GitHub archives are pinned by immutable LibRaw gitlinks or release tags and their recorded archive hashes.
3. Commit source/documentation/configuration changes, then use `scripts/build_windows.ps1 -Edition Standard`. This requires a clean source commit, embeds the original LGPL texts, source plan and verified source archives, scans the actual native payload, runs the frozen EXE and records that commit.
4. Independently extract the Setup EXE with a trusted Inno unpacker. Run `python scripts/native_compliance.py --directory <Portable/Seekdex> --compare-directory <Setup/{app}> --output <report.json>`. The comparison checks every actual file; the native list includes SHA256, PE versions, installed-wheel provenance and normal/delay imports. Static embedded libraries and runtime LoadLibrary dependencies still need upstream build/SBOM evidence; PE imports alone cannot enumerate them all.
5. Run `python scripts/release_checks.py --public-ready` before public binary distribution. Unknown native obligations, unresolved imports, missing/changed sources or unverified build/replacement/source-delivery evidence must fail this gate. This command never publishes or creates a tag. Normal local build checks may pass while the stricter public gate remains BLOCKED.

## Selected license routes

Third-party original copyright notices and licenses remain unchanged. The application's GPL-3.0-only is not a license grant for every library or any model.

| Actual component | Selected route / evidence | Remaining evidence |
| --- | --- | --- |
| Qt Core, Gui, Widgets, Network, OpenGL 6.11.2 | LGPL-3.0-only; actual qtbase source SPDX headers and official LGPL text | Match wheel build configuration/patches and all statically included third-party code; matched runtime/bindings substitution passes 26 host checks; own modified Qt build remains unverified |
| Qt SVG, SVG icon/image plugins 6.11.2 | LGPL-3.0-only; qtsvg source SPDX headers | Same requirements as Qt above |
| Qt image plugins 6.11.2 | QtBase/QtImageFormats LGPL-3.0-only, plus TIFF/WebP/JPEG/etc. original licenses | Per-plugin embedded-code mapping, codec notices and exact upstream build inputs |
| Windows platform, style, TLS, network information, generic input plugins | QtBase LGPL-3.0-only, with upstream nested notices | Source, replacement and conditional installation-information review |
| PySide6 / Shiboken6 6.11.2 | LGPL-3.0-only selected from upstream alternatives, verified libpyside/libshiboken source headers | Qt for Python build recipe/toolchain and corresponding binding sources |
| LibRaw 0.22.1 | **LGPL-2.1** option selected; rawpy is separately MIT | Rawpy v0.27.1 gitlink `b860248a89d9082b8e0a1e202e516f46af9adb29`; LibRaw-cmake `6e26c9e73677dc04f9eb236a97c6a4dc225ba7e8`; source version header and build flags |
| GEOS 3.13.1 / Shapely 2.1.2 | GEOS LGPL-2.1 family, separate Shapely BSD; original COPYING and LICENSE_GEOS retained | Upstream release workflow fixes GEOS_VERSION=3.13.1; match exact wheel and build/repair inputs |
| Mesa software OpenGL (`opengl32sw.dll`) | Not the Qt LGPL license merely because it is in the Qt wheel | Exact Mesa/LLVM version and static dependency notices remain BLOCKED |
| Optional OpenCV FFmpeg video driver | Excluded from new still-image builds | Verify actual absence and still-image/OCR regression; do not claim old artifacts lack it |
| OpenVINO / TBB / PyTorch / numerical and image codecs | Wheel licenses plus nested native conditions | Use per-file inventory; a top-level Apache/BSD/MIT label does not establish all transitive grants |
| Microsoft C++/OpenMP runtimes | Microsoft redistribution terms | Verify the exact runtime redistributable rights, not GPL source assumptions |

GEOS is required: RapidOCR 3.9.2 `ch_ppocr_det/utils.py` imports `shapely.geometry.Polygon`; `unclip()` uses area and length for text box expansion. Excluding GEOS without replacing and validating that algorithm would break OCR. It is retained.

## Source delivery and rebuild inputs

`packaging/compliance/source-plan.json` pins each archive URL, SHA256 and component. Both standard packages carry them in `_internal/seekdex/resources/compliance/sources/`. This prepares a source-delivery mechanism under the distributor's control; it does not prove that unrecorded vendor patches, exact build flags or all static libraries have been found.

- Qt: QtBase, QtSVG, QtImageFormats 6.11.2 original archives plus Qt for Python 6.11.2 (includes Shiboken and binding generators). They include source, LICENSES and CMake/build controls. Use Windows MSVC x64, a compatible Windows SDK/CMake/Ninja and release shared builds. The exact official wheel compiler/configuration must be recovered from upstream release build records before marking that check PASS; do not assume a generic configure command reproduces every wheel feature.
- LibRaw: exact rawpy gitlinks above, original LibRaw/LibRaw-cmake sources and an explicitly derived rawpy build-input ZIP. The ZIP retains unmodified source/build/licensing files and excludes upstream camera photographs/tests/examples/logo; it is **not** represented as the full rawpy source archive. Restore LibRaw and LibRaw-cmake under `external/` as documented by rawpy's `.gitmodules`, and use its `setup.py`, `pyproject.toml` and wheel workflow. Windows builds use release MSVC, shared `raw_r.dll`, OpenMP where available, and default disabled GPL demosaic packs; verify actual `rawpy.flags` and official wheel before applying these facts to a binary.
- GEOS: original 3.13.1 archive plus original Shapely 2.1.2 source/build workflow. `ci/install_geos.cmd` builds release shared libraries with CMake/Ninja; `.github/workflows/release.yml` fixes the version. Wheel DLL repair adds hashed filenames/imports. Retain the repair/toolchain recipe when rebuilding; renaming only one DLL is insufficient.
- Qt source includes codec sources/notices, but standalone Mesa and other nested wheels need their own exact build/source mapping where applicable. Generic upstream homepages are only discovery pointers.

No unverified blanket three-year source offer is made. Public upload is not performed by these tools. Recipient access and any required installation information must be reviewed for the chosen distribution method.

## LGPL replacement procedure and scope

The repeatable harness is `scripts/verify_lgpl_replacement.py`. It clones a bundle into a new test directory, breaks hard links before replacing bytes, rejects identical-byte replacements and path escapes, removes development paths from the child environment, and launches the actual frozen EXE. `scripts/build_geos_replacement.py` builds a real modified GEOS (version marker plus a required MinGW symbol-export correction) for this test only; it never changes public payloads.

Use a **separate extracted application directory and a separate SEEKDEX_HOME**. Close that test process before replacing libraries. Do not touch the user's installed application, data, source cache or OS security settings.

- Qt: use ABI-compatible x64 runtime libraries and matching plugins. The core source modules and platform/image/TLS plugins must be considered together. Binding binaries also depend on the Qt ABI; test both import and actual GUI operations.
- A Core-only 6.11.1 replacement against the original 6.11.2 Widgets failed symbol resolution in an actual launch. This is a recorded incompatibility, not a valid compatible replacement. Replace a matched runtime/binding/plugin set; do not claim every individually mixed patch DLL is compatible.
- LibRaw: replace `_internal/rawpy/raw_r.dll` with a compatible x64 shared build; keep required runtime dependencies available. Test wrapper imports and RAW preview operations where a licensed test RAW is available. Import/version success is narrower evidence than full RAW decoding.
- GEOS: replace both the C API and underlying GEOS libraries; preserve expected wheel filenames/import names or rebuild/repair the binding. Check geometry operations and actual OCR, not just a DLL load.
- Use original notice texts and retain upstream copyrights in any modified source. Do not modify or bypass code-signing/Windows protection policies to make a test run.

An isolated directory/profile on this host is **not a clean Windows VM or Sandbox**. A library replaced by identical bytes proves neither modification nor ABI compatibility. A real alternate upstream build or a source-rebuilt modified library is required for the stronger replacement check; report each scope separately.

## Primary references

- [Qt obligations and source delivery](https://www.qt.io/faq/qt-open-source-licensing)
- [Qt LGPLv3 full terms, section 4](https://doc.qt.io/qt-6.11/lgpl.html)
- [Qt module / third-party licensing](https://doc.qt.io/qt-6.11/licensing.html)
- [LibRaw license alternatives](https://www.libraw.org/about)
- [rawpy original LGPLv2.1 text, sections 4 and 6](https://github.com/letmaik/rawpy/blob/v0.27.1/LICENSE.LibRaw)
- [GEOS original license](https://github.com/libgeos/geos/blob/3.13.1/COPYING)

Pending expert review also covers inherited icon/contributor provenance, nested component notices and model redistribution. Standard packages exclude AI/OCR weights; this change does not authorize preinstalled models.

## Recorded replacement evidence

`packaging/compliance/replacement-evidence.json` records distinct original/replacement SHA256 values and 26 successful frozen-GUI checks for each of: a matched Qt/PySide6/Shiboken6 6.11.1 set, a locally source-built modified GEOS 3.13.1, and an alternate upstream LibRaw DLL. These preliminary tests used commit `183d4b8`; repeat them against the new committed artifact. This establishes operational evidence on this host, not a clean Windows installation or completeness of corresponding source.
