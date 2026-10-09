# Security policy

Seekdex is local-first. This does not imply immunity to malicious files, dependency defects or unsafe file operations.

Please report vulnerabilities privately through GitHub's **Report a vulnerability** interface if enabled. If unavailable, contact the maintainer using the email in `pyproject.toml` and request a private reporting channel. Do not post exploit details, private paths, database contents, photos, tokens or signing keys in a public issue. Provide version, reproduction with synthetic files, impact and relevant redacted logs.

The current `0.5.0-rc1` binaries are unsigned pre-releases. Windows SmartScreen / Smart App Control may warn or block them. Do not disable security policies to run a build. Model download integrity checks protect against accidental corruption; they do not establish an independent code-signing identity.

If a real credential ever appears in Git history, revoke/rotate it first. Removing a current file or rewriting history does not undo exposure. History cleanup and public releases require maintainer approval.
