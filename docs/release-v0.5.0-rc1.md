# Seekdex · 索星仪 v0.5.0-rc1

First Release Candidate under the Seekdex brand. **Unsigned pre-release**, not final 0.5.0.

Local search, beyond filenames.

## Source license

New RC builds use **GNU GPL v3.0 / GPL-3.0-only**, with full license text, copyright / third-party notices and an application source snapshot. Earlier MIT versions retain the permissions already granted under their original terms; the license change is not retroactive. Third-party components and model weights retain their own licenses. The RC tag and build provenance must identify the GPL commit. The GitHub draft is not published by the build scripts.

- Product, package, EXE and build names now use Seekdex / `seekdex`.
- Original installer AppId is retained for upgrade compatibility.
- Previous profiles are copied safely with SQLite backup; file_uid, embedding space, OCR identity and models remain unchanged. Old data is retained until the user confirms migration.
- Existing missing-output-stream and model-download fixes are included.
- Standard Portable and Setup contain runtimes but no model weights. Local preinstalled editions remain available; model redistribution licensing must be verified separately before public distribution.

## Known issues

Windows binaries currently unsigned.
SmartScreen / Smart App Control may warn or block unsigned builds.

Windows is the currently validated platform. Linux support is planned.
No clean Windows machine validation has yet been claimed. See the local RC verification report for actual results and limits.
The private repository's older commits contain personal path metadata. Current-file cleanup does not remove history; do not make it public before deciding history cleanup.

GitHub repository rename, visibility change, tags, pre-release publication and code signing are manual actions. They have not been performed by this preparation.
