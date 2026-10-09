# Contributing to Seekdex

Seekdex · 索星仪 uses Python 3.10+; development and release validation currently target Windows. Linux support is planned, not officially validated.

## Development

Run commands from the checkout directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ai,openvino,ocr,build]"
.\.venv\Scripts\python.exe -m seekdex
.\.venv\Scripts\python.exe -m pytest -q
```

Normal tests use synthetic images, `tmp_path`, mock/fake inference backends and no downloaded model weights. Do not make CI depend on personal photos or model downloads. Keep slow disk work in workers; UI updates must use signals.

## Windows build

Install Inno Setup, then run `scripts/build_windows.ps1`. The script builds and validates the frozen EXE, creates standard Portable / Setup editions and `SHA256SUMS.txt` (no weights by default; `-Edition All` explicitly stages local preinstalled editions). Preinstalled editions require verified local Chinese-CLIP and OCR caches; never commit those weights. Standard RC names omit an edition suffix. See README for build options.

Only `src/seekdex/app_info.py` defines release metadata. PEP 440 package version `0.5.0rc1` corresponds to display/artifact version `0.5.0-rc1`.

## License and contributions

Seekdex-owned source is licensed under **GPL-3.0-only**, not GPL-3.0-or-later. By submitting a contribution intended for inclusion, you agree to license your contribution under GPL-3.0-only and confirm that you have the necessary rights. Retain applicable third-party copyright and license notices; identify adapted or vendored code and its provenance. This does not revoke licenses already granted for earlier MIT versions.

Distributions must preserve LICENSE, COPYRIGHT.md and third-party notices and provide the applicable corresponding source. The bundled application source archive is not a claim that every native dependency's source-delivery obligations have been independently certified; review `docs/licensing-audit.md` before redistributing binaries or models.

## Pull requests

Describe the change, verification and limitations. Keep scope focused. Preserve installer AppId, file_uid, model_id and embedding space unless an explicit compatibility migration is reviewed. Add meaningful regressions for identity, migration and file safety changes.

Never commit model weights, user databases, logs, config, credentials, signing keys or private photos. Public screenshots must be newly rendered using synthetic images and fictional paths; record provenance. Run `git diff --check`, pytest and a local secret scanner before submission.

Do not publish releases, rewrite history or change repository visibility as part of an ordinary code change.
