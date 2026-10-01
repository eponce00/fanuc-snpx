# Releasing

Releases are GitHub releases with the sdist and wheel attached (`.github/workflows/release.yml`). Nothing
is published to PyPI. The first release, 0.1.0, waits until the read-only hardware validation (Phase 3 in
[TRACKER.md](TRACKER.md)) is logged in [VALIDATION_LOG.md](VALIDATION_LOG.md).

1. On a branch: set the version in `pyproject.toml` **and** `src/fanuc_snpx/__init__.py` (`__version__`).
2. In `CHANGELOG.md`, rename `[Unreleased]` to `[x.y.z] - YYYY-MM-DD` and start a new empty `[Unreleased]`.
3. PR, CI green, merge.
4. Tag `main` and push the tag:

   ```bash
   git tag -a v0.1.0 -m "fanuc-snpx 0.1.0"
   git push origin v0.1.0
   ```

5. The workflow checks the tag matches the version, builds, tests the wheel, refuses to continue if the
   source archive contains `vendor-docs/`, and creates the release.

Installing a release from the private repository (needs access to it):

```bash
pip install "fanuc-snpx @ git+https://github.com/eponce00/fanuc-snpx.git@v0.1.0"
```

## Before any public release

`vendor-docs/` holds third-party copyrighted manuals and the owner's notes. Making the repository public
requires removing it from the whole history first (see `vendor-docs/README.md`), or publishing from a fresh
repository without it. The source archive never contains it (`MANIFEST.in` prunes it).
