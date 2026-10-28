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

## Viewer and KMZ NoData rendering

The Advanced Settings preference `preferences.opaque_kmz_nodata` is off by
default. When enabled, both the Hillshade Viewer and KMZ export render
masked/NoData pixels as the same opaque neutral gray chosen from the hillshade
instead of alpha 0. This is a display operation only: never use this preference
to overwrite DEM elevations, remove GeoTIFF NoData metadata, or synthesize
underwater terrain. The source DEM and GeoTIFF must remain analytically
unchanged.

## Cloud Optimized GeoTIFF export

The viewer's **Export GeoTIFF** action calls
`src/lidar_core/geotiff_export.py::export_cog()`; it must not revert to a plain
file copy. Export uses Rasterio's in-process GDAL `COG` driver, so no external
or separately bundled `gdal_translate` executable is required. Creation
options are DEFLATE, `PREDICTOR=YES`, 512-pixel blocks, AVERAGE overview
resampling, and `BIGTIFF=IF_SAFER`.

The export is written to a temporary sibling file, validated, and only then
atomically replaces the chosen destination. Validation must continue checking
`LAYOUT=COG`, 512 x 512 blocks, internal overviews, raster/georeferencing and
band-property equality, source metadata and descriptions, and full-resolution
GDAL band checksums. The current hillshade remains one 8-bit grayscale band;
do not convert it to RGB. Keep the conversion off the Tk UI thread.

## Combined USGS/OpenTopography/NOAA/USIEI discovery and delivery

`discover_datasets()` in `src/lidar_core/discovery.py` coordinates the
providers concurrently and returns a reusable `DiscoverySnapshot` containing
the ranked candidates plus provider-specific status. Manual selection runs
this outside the Tk thread and passes the same snapshot into processing; do
not reintroduce a second discovery call after the user chooses a result.
`list_intersecting_datasets()` remains the compatibility list-only API.

The daily WESM CSV remains the authoritative USGS work-unit catalog and
metadata source.
The CSV has no geometry, while the WESM GeoPackage is several gigabytes, so the
app uses the official TNM Access products API as the lightweight spatial query
for intersecting OPR/source-DEM and LPC tiles. Results are joined back to WESM
by the work-unit component in official product paths.

The public OpenTopography `otCatalog` and raster STAC are also searched for
localized lidar-derived bare-earth DTMs. Catalog/STAC lookup and public COG
access do not require an API key. Only candidates whose real coverage and tile
union cover the complete AOI are admitted. Their native COG resolution is read
before ranking, and winning COGs are cropped directly without resampling or
submitting an OpenTopography processing job. Preserve dataset citation and
license metadata.

The HoBu coverage index is only an EPT availability/delivery index. It must not
become the primary USGS catalog again. USGS delivery priority remains AWS EPT,
then official source DEM, then official LPC/LAZ. Source DEMs and OpenTopography
DTMs are cropped and mosaicked without changing their elevation values;
point-cloud paths continue to retain classification 2 before DEM generation.

The official USIEI topographic-lidar ArcGIS layer is queried once per AOI.
Its real GeoJSON footprint must cover the complete AOI before a record is
admitted. `catalogs/noaa.py` accepts only complete, public `Lidar-Topo`
records with a public NOAA EPT URL and confirmed classification 2 ground
points. Topobathy/bathymetry remain excluded from normal terrain mode.

`catalogs/usiei.py` is also a discovery and metadata fallback. It may deliver
an otherwise-missing dataset only through a supported official USGS EPT URL;
unsupported external links remain metadata and must not become processing
candidates. Project-level USIEI records enrich matching WESM work units under
`usiei_*` fields without overwriting authoritative WESM values. Normalize the
project alias when deduplicating numbered work units (for example,
`MO_SE11County_B24` and `MO_SE11County_1_B24`). USGS delivery remains preferred
when the same survey is available from both USGS and NOAA.

OpenTopography and USIEI AOI responses use the short-lived JSON cache in
`catalogs/cache.py` (30 minutes by default, with a recent stale copy permitted
during an outage). Keep HTTP/GDAL calls bounded. A provider failure or timeout
must be reported in the snapshot and must not discard successful results from
other providers.

## GDAL/PROJ/rasterio environment — this took three rounds to get right

`src/utils/binary_paths.py` has two *different* environment-setup functions
that are easy to conflate:

- `setup_bundled_environment()` — called once at startup in `app.py`, sets
  vars on `os.environ` for the **main process** (in-process `rasterio`/
  `pyproj` imports, used by `src/lidar_core/kmz_export.py` and
  `dem_generator.py`).
- `get_bundled_lib_env()` — called per-call from `dem_generator.py`,
  `hillshade_engine.py`, and `delivery/point_cloud.py` to build an `env=` dict passed
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
the old `clip_aws_aoi_to_ground_laz()` pattern (now in
`delivery/point_cloud.py`) of checking
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

Scratch data is scoped per running process under
`output/session-<pid>-<random>/{laz,dem,hillshades,logs}`. This is intentional:
closing one viewer or app must never delete the DEM still used by another app
instance. `cleanup_output_folder()` removes only the current process's session
directory. Do not change it back to deleting the shared `output/` parent.

Anything short of normal cleanup (killing the process or a crash) can leave a
stale session directory. `hillshade_viewer.py`'s export dialogs
(`_show_kmz_export_dialog`, `_show_tif_export_dialog`) now filter
`hillshades_dir.glob("*.tif")` down to `p.stem.startswith(f"{self.dem_path.stem}_")`
for defensive isolation within a session. Don't remove that filter or
re-introduce an unfiltered `glob("*.tif")` over that folder without the same
guard.

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
catalog/ranking logic and `utils.config` — no
GUI or GDAL/PDAL dependency, safe to run anywhere.

The Hillshade Viewer binds wheel routing at its own window level so nested
sidebar controls can scroll the overflowing control canvas. Preserve the
pointer/ancestry check: wheel events over the image canvas must continue to
zoom, and a sidebar that does not overflow must not consume the event.

Live catalog checks are deliberately opt-in and separate from the fast suite:

```bash
LHE_RUN_NETWORK_TESTS=1 venv/bin/python3 -m unittest discover -s tests/integration -v
```

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
