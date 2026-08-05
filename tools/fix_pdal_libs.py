#!/usr/bin/env python3
"""
Post-build script to fix library symlinks for PDAL compatibility.

Problem: PDAL's libpdalcpp looks for libproj and libgdal at @rpath, which resolves
to Frameworks/. The symlinks there point to rasterio's bundled copies, but those
are incompatible with PDAL (different symbol versions).

Solution: Replace the symlinks at Frameworks root to point to our bundled copies
in bin/libs/ instead of rasterio's copies. This allows:
- PDAL to find compatible libs via Frameworks/ symlinks
- Rasterio to still use its own copies in __dot__dylibs/

Usage:
    python3 tools/fix_pdal_libs.py

Run this after: pyinstaller lidar_explorer.spec
"""

import os
import sys
from pathlib import Path


def find_app_bundle() -> Path:
    """Find the built .app bundle in dist/."""
    dist_dir = Path("dist")
    if not dist_dir.exists():
        raise FileNotFoundError("dist/ directory not found. Run pyinstaller first.")
    
    app_bundles = list(dist_dir.glob("*.app"))
    if not app_bundles:
        raise FileNotFoundError("No .app bundle found in dist/")
    
    return app_bundles[0]


def fix_framework_symlinks(app_path: Path) -> None:
    """
    Replace Frameworks root symlinks to point to our bundled libs.
    
    This redirects libproj and libgdal symlinks from rasterio's copies
    to our bundled copies in bin/libs/.
    """
    frameworks_dir = app_path / "Contents" / "Frameworks"
    bin_libs_dir = frameworks_dir / "bin" / "libs"
    
    if not frameworks_dir.exists():
        raise FileNotFoundError(f"Frameworks directory not found: {frameworks_dir}")
    
    if not bin_libs_dir.exists():
        raise FileNotFoundError(f"bin/libs directory not found: {bin_libs_dir}")
    
    # Libraries to redirect
    libs_to_fix = {
        "libproj": "libproj.25.9.7.1.dylib",
        "libgdal": "libgdal.38.3.12.2.dylib",  # Adjust version if needed
    }
    
    fixed_count = 0
    
    for lib_prefix, target_name in libs_to_fix.items():
        # Check if our bundled lib exists
        bundled_lib = bin_libs_dir / target_name
        if not bundled_lib.exists():
            # Try finding any version
            matches = list(bin_libs_dir.glob(f"{lib_prefix}*.dylib"))
            if matches:
                # Use the actual file (not symlink)
                bundled_lib = next((m for m in matches if not m.is_symlink()), matches[0])
                target_name = bundled_lib.name
            else:
                print(f"Warning: No bundled {lib_prefix} found in bin/libs/")
                continue
        
        # Find and replace symlinks at Frameworks root
        for item in frameworks_dir.iterdir():
            if item.name.startswith(lib_prefix) and item.is_symlink():
                # Check if it points to rasterio
                try:
                    link_target = os.readlink(item)
                    if "rasterio" in link_target or "pyproj" in link_target:
                        # Remove old symlink
                        item.unlink()
                        
                        # Create new symlink pointing to our bundled lib
                        # Use relative path: bin/libs/libproj.25.9.7.1.dylib
                        rel_target = f"bin/libs/{target_name}"
                        item.symlink_to(rel_target)
                        
                        print(f"  Fixed: {item.name} -> {rel_target}")
                        fixed_count += 1
                except Exception as e:
                    print(f"  Error fixing {item.name}: {e}")
    
    # Also ensure the short symlinks exist (e.g., libproj.25.dylib)
    short_symlinks = {
        "libproj.25.dylib": "libproj.25.9.7.1.dylib",
        "libgdal.38.dylib": "libgdal.38.3.12.2.dylib",
    }
    
    for short_name, full_name in short_symlinks.items():
        short_link = frameworks_dir / short_name
        # Check if the target exists in bin/libs
        if (bin_libs_dir / full_name).exists():
            if short_link.exists() or short_link.is_symlink():
                if short_link.is_symlink():
                    link_target = os.readlink(short_link)
                    if "rasterio" in link_target or "pyproj" in link_target:
                        short_link.unlink()
                        short_link.symlink_to(f"bin/libs/{full_name}")
                        print(f"  Fixed: {short_name} -> bin/libs/{full_name}")
                        fixed_count += 1
            else:
                # Create new symlink
                short_link.symlink_to(f"bin/libs/{full_name}")
                print(f"  Created: {short_name} -> bin/libs/{full_name}")
                fixed_count += 1
    
    return fixed_count


def main():
    print("=" * 60)
    print("PDAL Library Symlink Fix")
    print("=" * 60)
    
    try:
        app_path = find_app_bundle()
        print(f"\nFound app bundle: {app_path}")
        
        print("\nFixing Frameworks symlinks...")
        fixed = fix_framework_symlinks(app_path)
        
        print(f"\nDone! Fixed {fixed} symlinks.")
        print("\nThe app should now work with PDAL processing.")
        
    except FileNotFoundError as e:
        print(f"\nError: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
