#!/usr/bin/env python3
"""
Script to bundle binaries and their dependencies for standalone .app distribution.

This script:
1. Copies pdal and gdaldem from Homebrew (/opt/homebrew/bin/ or /usr/local/bin/)
2. Finds all dynamic library dependencies of the binaries
3. Copies them to bundle_bins/{platform}/libs/
4. Fixes library paths to use @executable_path relative paths

Usage:
    python3 bundle_dependencies.py

The binaries will be placed in:
    bundle_bins/macos-arm64/  (for Apple Silicon)
    bundle_bins/macos-x86_64/ (for Intel Macs)
"""

import os
import subprocess
import shutil
import sys
import platform
from pathlib import Path
from typing import Set, List, Dict, Optional

# Directories
SCRIPT_DIR = Path(__file__).parent

# Homebrew paths
HOMEBREW_ARM64 = Path("/opt/homebrew")
HOMEBREW_INTEL = Path("/usr/local")

# System libraries we should NOT bundle (they're guaranteed to exist on macOS)
SYSTEM_PREFIXES = (
    "/usr/lib/",
    "/System/",
)

# Required binaries
REQUIRED_BINARIES = ["pdal", "gdaldem"]


def get_platform_dir() -> str:
    """Get the platform-specific directory name."""
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "macos-arm64"
    else:
        return "macos-x86_64"


def get_homebrew_prefix() -> Path:
    """Get Homebrew prefix for current platform."""
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        if HOMEBREW_ARM64.exists():
            return HOMEBREW_ARM64
    else:
        if HOMEBREW_INTEL.exists():
            return HOMEBREW_INTEL
    
    # Fallback: try to detect from brew command
    try:
        result = subprocess.run(
            ["brew", "--prefix"],
            capture_output=True,
            text=True,
            check=True
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise RuntimeError("Could not find Homebrew installation")


def get_dependencies(binary_path: Path) -> List[str]:
    """Get list of dynamic library dependencies using otool."""
    try:
        result = subprocess.run(
            ["otool", "-L", str(binary_path)],
            capture_output=True,
            text=True,
            check=True
        )

        dependencies = []
        for line in result.stdout.splitlines()[1:]:  # Skip first line (binary itself)
            line = line.strip()
            if line:
                # Extract library path (before compatibility version info)
                lib_path = line.split("(")[0].strip()
                dependencies.append(lib_path)

        return dependencies
    except subprocess.CalledProcessError as e:
        print(f"Error getting dependencies for {binary_path}: {e}")
        return []


def should_bundle(lib_path: str) -> bool:
    """Check if library should be bundled (not a system library)."""
    return not any(lib_path.startswith(prefix) for prefix in SYSTEM_PREFIXES)


def find_library_path(lib_path: str) -> Optional[Path]:
    """Find actual library path (resolve symlinks if needed)."""
    path = Path(lib_path)
    if path.exists():
        return path.resolve()

    # Try to find it in Homebrew
    homebrew = get_homebrew_prefix()
    
    # Check various Homebrew library locations
    search_paths = [
        homebrew / "lib",
        homebrew / "opt",
    ]
    
    lib_name = Path(lib_path).name
    for search_path in search_paths:
        potential = search_path / lib_name
        if potential.exists():
            return potential.resolve()
        
        # Search recursively in opt
        if search_path.name == "opt":
            for lib_file in search_path.rglob(lib_name):
                if lib_file.exists():
                    return lib_file.resolve()

    return None


def copy_library(lib_path: str, libs_dir: Path) -> Optional[Path]:
    """Copy library to libs directory."""
    src_path = find_library_path(lib_path)

    if src_path is None or not src_path.exists():
        print(f"Warning: Library not found: {lib_path}")
        return None

    # Copy to libs directory with same name
    dest_path = libs_dir / src_path.name

    if not dest_path.exists():
        print(f"  Copying: {src_path.name}")
        shutil.copy2(src_path, dest_path)
        # Make writable so we can modify it later
        os.chmod(dest_path, 0o755)

    return dest_path


def fix_library_paths(binary_path: Path, lib_mapping: Dict[str, str], bins_dir: Path, libs_dir: Path):
    """Fix library paths in binary to use @executable_path relative paths."""
    print(f"\nFixing paths in: {binary_path.name}")

    # Only invoke install_name_tool for libraries this Mach-O file actually
    # links against. Trying every collected mapping for every file turns a
    # normal Homebrew dependency graph into tens of thousands of subprocesses.
    for old_path in get_dependencies(binary_path):
        new_name = lib_mapping.get(old_path)
        if new_name is None:
            continue
        # New path relative to executable
        if binary_path.parent == bins_dir:
            # Binary is in bundle_bins/{platform}/
            new_path = f"@executable_path/libs/{new_name}"
        else:
            # Library is in bundle_bins/{platform}/libs/
            new_path = f"@loader_path/{new_name}"

        try:
            subprocess.run(
                ["install_name_tool", "-change", old_path, new_path, str(binary_path)],
                check=True,
                capture_output=True
            )
            # Only print if we actually changed something
        except subprocess.CalledProcessError:
            # Ignore errors for libraries that aren't actually linked
            pass


def copy_runtime_data(homebrew: Path, bundle_dir: Path) -> None:
    """Copy GDAL and PROJ runtime databases required by bundled CLI tools."""
    for name in ("gdal", "proj"):
        src = homebrew / "share" / name
        dest = bundle_dir / f"{name}_data"
        if not src.is_dir():
            print(f"Warning: Runtime data directory not found: {src}")
            continue
        if dest.exists():
            shutil.rmtree(dest)
        print(f"Copying {name.upper()} runtime data from {src}")
        if name == "proj":
            # Homebrew installs hundreds of optional worldwide geodetic grid
            # files (roughly 750 MB). This app only projects geographic AOIs
            # to the dataset's horizontal CRS, for which PROJ's database and
            # text definitions are sufficient. PyProj also carries its own
            # compact runtime data for Python-side transformations.
            dest.mkdir(parents=True)
            grid_suffixes = {".tif", ".tiff", ".gtx", ".gsb"}
            for data_file in src.iterdir():
                if data_file.is_file() and data_file.suffix.lower() not in grid_suffixes:
                    shutil.copy2(data_file, dest / data_file.name)
        else:
            shutil.copytree(src, dest)


def fix_library_id(lib_path: Path):
    """Fix the library's own install name (id) to use @loader_path."""
    try:
        subprocess.run(
            ["install_name_tool", "-id", f"@loader_path/{lib_path.name}", str(lib_path)],
            check=True,
            capture_output=True
        )
    except subprocess.CalledProcessError:
        pass


def ad_hoc_codesign(path: Path) -> None:
    """Restore a valid ad-hoc signature after editing a Mach-O file."""
    subprocess.run(
        ["codesign", "--force", "--sign", "-", str(path)],
        check=True,
        capture_output=True,
    )


def process_binary(binary_path: Path, libs_dir: Path, processed: Set[str]) -> Dict[str, str]:
    """
    Process binary and recursively process its dependencies.
    Returns mapping of old paths to new library names.
    """
    if str(binary_path) in processed:
        return {}

    processed.add(str(binary_path))
    print(f"\nProcessing: {binary_path.name}")

    lib_mapping = {}
    dependencies = get_dependencies(binary_path)

    for dep in dependencies:
        if should_bundle(dep):
            # Copy library
            copied_lib = copy_library(dep, libs_dir)

            if copied_lib:
                lib_mapping[dep] = copied_lib.name

                # Recursively process this library's dependencies
                sub_mapping = process_binary(copied_lib, libs_dir, processed)
                lib_mapping.update(sub_mapping)

    return lib_mapping


def copy_binary_from_homebrew(name: str, dest_dir: Path) -> Optional[Path]:
    """Copy a binary from Homebrew to destination directory."""
    homebrew = get_homebrew_prefix()
    src_path = homebrew / "bin" / name
    
    if not src_path.exists():
        print(f"ERROR: Binary not found: {src_path}")
        print(f"  Please install it with: brew install {name.replace('dem', '')}")
        return None
    
    # Resolve symlink to get actual binary
    actual_path = src_path.resolve()
    dest_path = dest_dir / name
    
    print(f"Copying {name} from {actual_path}")
    shutil.copy2(actual_path, dest_path)
    os.chmod(dest_path, 0o755)
    
    return dest_path


def main():
    """Main bundling process."""
    print("=" * 70)
    print("Bundling binaries and dependencies for standalone .app distribution")
    print("=" * 70)

    # Determine platform-specific directory
    platform_dir = get_platform_dir()
    bundle_dir = SCRIPT_DIR / "bundle_bins" / platform_dir
    libs_dir = bundle_dir / "libs"
    
    print(f"\nPlatform: {platform_dir}")
    print(f"Bundle directory: {bundle_dir}")
    
    # Get Homebrew prefix
    try:
        homebrew = get_homebrew_prefix()
        print(f"Homebrew prefix: {homebrew}")
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # Create directories
    bundle_dir.mkdir(parents=True, exist_ok=True)
    libs_dir.mkdir(exist_ok=True)

    # Copy binaries from Homebrew
    print("\n" + "=" * 70)
    print("Step 1: Copying binaries from Homebrew")
    print("=" * 70)
    
    binaries = []
    for name in REQUIRED_BINARIES:
        binary = copy_binary_from_homebrew(name, bundle_dir)
        if binary:
            binaries.append(binary)
        else:
            print(f"\nMissing required binary: {name}")
            print("Please install required dependencies:")
            print("  brew install pdal gdal")
            sys.exit(1)

    print(f"\nCopied {len(binaries)} binaries")

    # Process each binary to collect dependencies
    print("\n" + "=" * 70)
    print("Step 2: Collecting library dependencies")
    print("=" * 70)
    
    processed = set()
    all_mappings = {}

    for binary in binaries:
        mapping = process_binary(binary, libs_dir, processed)
        all_mappings.update(mapping)

    # Fix paths in binaries
    print("\n" + "=" * 70)
    print("Step 3: Fixing library paths in binaries")
    print("=" * 70)
    
    for binary in binaries:
        fix_library_paths(binary, all_mappings, bundle_dir, libs_dir)

    # Fix paths in libraries (they need to reference each other correctly)
    print("\n" + "=" * 70)
    print("Step 4: Fixing library paths in libraries")
    print("=" * 70)
    
    for lib in libs_dir.glob("*.dylib"):
        fix_library_id(lib)
        fix_library_paths(lib, all_mappings, bundle_dir, libs_dir)

    # PDAL/GDAL need their coordinate-system and driver data on machines that
    # don't have Homebrew installed.
    print("\n" + "=" * 70)
    print("Step 5: Copying GDAL and PROJ runtime data")
    print("=" * 70)
    copy_runtime_data(homebrew, bundle_dir)

    print("\n" + "=" * 70)
    print("Step 6: Ad-hoc signing bundled Mach-O files")
    print("=" * 70)
    for lib in libs_dir.glob("*.dylib"):
        ad_hoc_codesign(lib)
    for binary in binaries:
        ad_hoc_codesign(binary)

    # Summary
    lib_count = len(list(libs_dir.glob("*.dylib")))
    total_size = sum(f.stat().st_size for f in bundle_dir.rglob("*") if f.is_file())
    
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"Platform: {platform_dir}")
    print(f"Binaries: {len(binaries)}")
    for b in binaries:
        print(f"  - {b.name}")
    print(f"Libraries: {lib_count}")
    print(f"Total size: {total_size / 1024 / 1024:.1f} MB")
    print(f"\nBundle location: {bundle_dir}")
    print("\nBundling complete!")
    print("\nNext steps:")
    print("  1. Test locally: python3 app.py")
    print("  2. Build .app:   pyinstaller lidar_explorer.spec")
    print("  3. Find output:  dist/LiDAR Hillshade Explorer.app")


if __name__ == "__main__":
    main()
