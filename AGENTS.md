# AGENTS.md

Notes for AI coding agents (Claude Code, etc.) working in this repo. This is
project-specific operational knowledge that isn't obvious from reading the
code alone — read this before touching build/version/GDAL-PROJ code.

## What this project is

A Tkinter desktop app (Python 3.12) that downloads USGS LiDAR, generates DEMs
via PDAL, and renders hillshades via GDAL, packaged as a standalone app with
PyInstaller (no Python/Homebrew/PDAL/GDAL required by end users). Entry point
is [`app.py`](app.py); GUI code lives in `src/main_gui.py` (main window +
Advanced Settings dialog) and `src/hillshade_viewer.py` (viewer window,
GeoTIFF/KMZ export). Core processing is under `src/lidar_core/`.

## Building the standalone app

```bash
venv/bin/python3 -m PyInstaller lidar_explorer.spec --noconfirm
```

**Do not use `venv/bin/pyinstaller` directly** — its shebang bakes in the
absolute path of whatever machine created the venv. If the repo folder was
ever renamed/moved (it was: originally
`/Users/techbill/Desktop/LidarHIllshadeExplorer-main`), that script fails
with `bad interpreter: ... no such file or directory` even though the venv
itself works fine. Always invoke via `python3 -m PyInstaller` instead of the
`pyinstaller` console-script. Same caution applies to any other console
scripts in `venv/bin/`.

Output: `dist/LiDAR Hillshade Explorer.app` (~488 MB, onedir + BUNDLE).
`dist/` and `build/` are gitignored — nothing there is source of truth.

Package for release:

```bash
cd dist
ditto -c -k --sequesterRsrc --keepParent \
  "LiDAR Hillshade Explorer.app" \
  "LiDAR-Hillshade-<version>-Mac-ARM64.zip"
```

Use `ditto`, not `zip`/Finder compress — it preserves the bundle's resource
forks, symlinks (e.g. `Contents/Frameworks/gdal_data` → `../Resources/gdal_data`),
and permissions.

The current build machine is Apple Silicon (`arm64`), so only
`bundle_bins/macos-arm64/` is populated and only an ARM64 `.app` can be
produced here. Intel Mac and Windows builds require running the analogous
`bundle_dependencies*.py` + PyInstaller build **on that target platform** —
native binaries can't be cross-compiled or copied between architectures. See
`BUILD.md`.

`bundle_bins/{platform}/` (pdal, gdaldem, `libs/*.dylib`, `gdal_data/`,
`proj_data/`) is git-ignored except for `.gitkeep`; only the machine that ran
`bundle_dependencies.py` has it populated. Check it exists before building —
if missing, PyInstaller still "succeeds" but produces an app with no
PDAL/GDAL binaries.

## Version numbers — where they all live

There is no single source of truth; bumping the version means updating all
of these by hand:

- `app.py` → `APP_VERSION = "X.Y"` (drives the Tk window title)
- `lidar_explorer.spec` → `CFBundleShortVersionString` and `CFBundleVersion`
  (drives `Info.plist`, verify post-build with
  `/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "dist/LiDAR Hillshade Explorer.app/Contents/Info.plist"`)
- `README.md`, `USER_GUIDE.md`, `BUILD.md`, `BUNDLING_GUIDE.md`,
  `README-build.md` — version appears in headings, prose, and release
  filenames (`LiDAR-Hillshade-X.Y-Mac-ARM64.zip`,
  `LiDAR-Hillshade-Explorer-X.Y-macOS-arm64.dmg`)

`git grep -n "3\.2"` (or whatever the current version is) before a bump to
find every occurrence; watch for false positives — coordinate examples like
`-93.292299` contain a "3.2" substring.

**Do not bulk `sed -i 's/OLD_VERSION/NEW_VERSION/g'` across the docs without
checking the diff afterward.** This has actually corrupted coordinate
examples twice: bumping `3.2` → `3.2.1` with a plain substring replace turned
`-93.292299` into `-93.2.292299` (silently, since `3.2` matches inside the
digits `...93.292...`) in both `README.md` and `USER_GUIDE.md`, and it went
unnoticed for a whole round because the check after the *previous* bump
(`3.0`→`3.2`) doesn't cover a *later* one. After any version-string sed pass,
run `git diff <file> | grep '^[+-]'` and actually read every changed line,
not just grep for the version string itself.

The `"version": 1` key in `get_default_config()` in `src/utils/config.py` is
an unrelated config-schema version, not the app version — don't touch it
when bumping app version.

## GDAL/PROJ/rasterio environment — this took three rounds to get right

`src/utils/binary_paths.py` has two *different* environment-setup functions
that are easy to conflate:

- `setup_bundled_environment()` — called once at startup in `app.py`, sets
  vars on `os.environ` for the **main process** (in-process `rasterio`/
  `pyproj` imports, used by `src/lidar_core/kmz_export.py` and
  `dem_generator.py`).
- `get_bundled_lib_env()` — called per-call from `dem_generator.py`,
  `hillshade_engine.py`, `aws_operations.py` to build an `env=` dict passed
  only to `subprocess.run`/`Popen` when invoking the bundled `pdal`/
  `gdaldem` CLI binaries.

**Current (verified-correct) state:** `setup_bundled_environment()`'s macOS
frozen branch sets `GDAL_DATA`/`PROJ_LIB`/`PROJ_DATA` on `os.environ` to
`bundle_dir / "gdal_data"` and `bundle_dir / "proj_data"` — the same
Homebrew-derived data `get_bundled_lib_env()` uses for the pdal/gdaldem
subprocess (bundled from `bundle_bins/{platform}/`, always present
regardless of what's on the target machine). It deliberately does **not**
set `DYLD_LIBRARY_PATH` for the main process.

**Why, in order of what was actually tried and disproven** (don't repeat
this without re-reading this section):

1. *First guess (wrong):* the bug looked like a library-version-hijack —
   `DYLD_LIBRARY_PATH` forcing the Homebrew-derived `bin/libs` dylibs to
   load in-process, conflicting with `rasterio`/`pyproj`'s own bundled,
   version-matched GDAL/PROJ. Fix applied: stop setting
   `GDAL_DATA`/`PROJ_LIB`/`PROJ_DATA`/`DYLD_LIBRARY_PATH` for the main
   process at all, on the theory that the self-contained wheels would just
   find their own bundled data.
2. *This broke KMZ export on the build machine itself* (previously "worked"
   there) — reported as `CRSError: The EPSG code is unknown. PROJ:
   internal_proj_create_from_database: Cannot find proj.db`, from inside
   `rasterio.warp.transform_bounds()`.
3. *Root-caused with an in-app self-test hook*, not guesswork: temporarily
   added an `if os.environ.get("LHE_SELFTEST"):` block at the top of
   `app.py` (right after `setup_bundled_environment()`) that imports
   `pyproj`/`rasterio`, runs the exact `transform_bounds()` call KMZ export
   makes against a real hillshade `.tif`, and a full `tifs_to_kmz()` call,
   writing results to a file (stdout from a windowed/`console=False`
   PyInstaller app is not reliably visible). Ran the **actual built
   `Contents/MacOS/LiDARHillshadeExplorer` binary** directly (not `open`)
   with that env var set, e.g.:

   ```bash
   LHE_SELFTEST=/tmp/result.txt "dist/LiDAR Hillshade Explorer.app/Contents/MacOS/LiDARHillshadeExplorer"
   cat /tmp/result.txt
   ```

   This proved: `pyproj.Transformer` resolves its own bundled `proj_dir`
   fine on its own, with or without any env var. But `rasterio`'s CRS layer
   (`rasterio.crs.CRS.from_epsg`, which `transform_bounds()` calls into) goes
   through **GDAL's own, separate** OSR/PROJ context, and *that* context's
   auto-detection does not reliably find a usable proj.db once PyInstaller
   has relocated everything under `Contents/Frameworks` — regardless of
   whose Mac it is. Confirmed by literally toggling the fix off/on in two
   consecutive builds and reproducing/resolving the identical error text
   both times (see git history around the 3.2.2 version bump).
4. Fix: explicitly set `GDAL_DATA`/`PROJ_LIB`/`PROJ_DATA` to the bundled
   (not system-Homebrew) data — real, present in every build, no dependency
   on the target machine having anything installed. A newer/older PROJ
   database than what `pyproj` itself ships is fine in practice; verified a
   Homebrew 9.8-derived `proj.db` (`DATABASE.LAYOUT.VERSION` 1.6) transforms
   correctly under `pyproj`'s bundled 9.5 library (`DATABASE.LAYOUT.VERSION`
   1.4). Still deliberately **not** setting `DYLD_LIBRARY_PATH` for the main
   process — only the *data* lookup needed pointing, not which
   libgdal/libproj actually loads, and there's no evidence the latter was
   ever the problem.

A second, independent bug was found and fixed the same round: don't recreate
`aws_operations.py`'s old `clip_aws_aoi_to_ground_laz()` pattern of checking
`/opt/homebrew/share/proj` and calling `pyproj.datadir.set_data_dir()` on it
when present — that's a **sticky, process-wide** override (it stays in
effect for the rest of the run) based on whatever happens to be on the
*current* machine, layered on top of whatever `setup_bundled_environment()`
already set correctly. If you see code reaching for
`/opt/homebrew/share/{gdal,proj}` or `/usr/local/share/{gdal,proj}` anywhere
in `src/`, it's the same anti-pattern — remove it in favor of the bundled
data path above.

If GDAL/PROJ-related bugs come up again: reach for the `LHE_SELFTEST` hook
technique above before guessing — it tests the *actual frozen binary*, which
is the only environment where this class of bug reproduces. Also verify any
bundled-data path checks actually match what's shipped (`gdal_data`/
`proj_data` at the bundle root — confirmed via `find "dist/LiDAR Hillshade
Explorer.app" -iname gdal_data -o -iname proj_data`), not a `share/gdal`
layout that doesn't exist in this PyInstaller layout.

## Output/config locations (not inside the app bundle)

`src/utils/config.py::get_output_dir()` / `get_config_dir()` intentionally
write outside the `.app` (macOS: `~/Library/Application Support/
LiDARHillshadeExplorer`; Windows: `%APPDATA%/LiDARHillshadeExplorer`) because
a bundle installed in `/Applications` isn't writable. Don't "fix" code that
looks like it should write next to the executable — that would break signed
installs.

**`output/{laz,dem,hillshades}/` is a shared, unbounded scratch folder, not
scoped per-session.** It's only cleared by `cleanup_output_folder()`
(`src/utils/cleanup.py`), called from `_on_viewer_closed()` /
`_on_close()` in `main_gui.py` — i.e. only when the viewer window is
returned from normally or the app quits normally. Anything short of that
(killing the process, a crash, generating a second location without closing
the viewer first, or - as happened while verifying a fix - driving the
frozen binary directly for a self-test and `kill`ing it) leaves the
previous location's `.tif`/`.laz`/DEM files sitting there. Confirmed live:
`output/hillshades/` ended up holding two locations' `_classic.tif` /
`_arch_*.tif` at once this way. `hillshade_viewer.py`'s export dialogs
(`_show_kmz_export_dialog`, `_show_tif_export_dialog`) now filter
`hillshades_dir.glob("*.tif")` down to `p.stem.startswith(f"{self.dem_path.stem}_")`
for exactly this reason - don't remove that filter or re-introduce an
unfiltered `glob("*.tif")` over that folder anywhere else without the same
guard, or stale files from an earlier location reappear as confusing
duplicate-looking entries (same display name, since the display-name logic
strips the location prefix).

## Testing without a full app run

There's no GUI test harness. For quick verification of Tkinter dialog
changes, a headless smoke test works locally (real display available, no
`mainloop()` needed):

```bash
python3 -c "
import tkinter as tk
root = tk.Tk(); root.withdraw()
# ... build the widget tree, call dialog.update(), assert on winfo_* geometry ...
root.destroy()
"
```

Used this to confirm the Advanced Settings scroll fix keeps the button bar
within the dialog bounds without needing to drive the whole app.

`tests/test_selection.py` (`python3 -m unittest`) covers dataset
ranking/config logic in `lidar_core.aws_operations` and `utils.config` — no
GUI or GDAL/PDAL dependency, safe to run anywhere.

To sanity-check the *built* app actually launches:

```bash
open "dist/LiDAR Hillshade Explorer.app"
sleep 4 && pgrep -fl LiDARHillshadeExplorer   # confirm it's running
kill <pid>                                    # clean shutdown
```

(`osascript`/System Events window inspection is not available in this
sandbox — no assistive-access permission — so process presence is the
practical liveness check.)

## Misc

- Architecture-sensitive: `platform.machine()` (`arm64` vs `x86_64`) selects
  `bundle_bins/{macos-arm64,macos-x86_64,windows-x86_64}/` in both
  `lidar_explorer.spec` and `src/utils/binary_paths.py::get_platform_dir()`.
  Keep these two independent implementations in sync if the platform list
  changes.
- `pyinstaller-hooks-contrib` (a transitive dep pulled in by `pip install
  pyinstaller`, itself not in `requirements.txt` — see `BUILD.md`) provides
  the `hook-pyproj.py` that makes `pyproj/proj_dir` get collected into the
  build. It's what makes the self-contained GDAL/PROJ bundling above work.
  If a future `pip install pyinstaller` ends up without it, `pyproj`'s data
  won't be bundled and CRS transforms will break in the frozen app —
  re-verify with the `find ... -iname proj_data` check above after any
  PyInstaller/venv rebuild.
