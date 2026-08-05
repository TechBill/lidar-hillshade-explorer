#!/usr/bin/env python3
"""
Script to bundle PDAL/GDAL binaries and their DLL dependencies from a conda
environment for standalone Windows distribution.

This script:
1. Locates pdal.exe and gdaldem.exe in a conda environment
2. Recursively traces all DLL dependencies using PE import tables
3. Copies only the required DLLs (skipping Windows system DLLs)
4. Copies PDAL plugin DLLs
5. Copies GDAL_DATA and PROJ_DATA files
6. Places everything in bundle_bins/windows-x86_64/

Usage:
    # With conda 'lidar' environment active:
    python bundle_dependencies_windows.py

    # Or specify the conda env path explicitly:
    python bundle_dependencies_windows.py --conda-env C:\\Users\\techb\\.conda\\envs\\lidar

Prerequisites:
    pip install pefile
"""

import argparse
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


SCRIPT_DIR = Path(__file__).parent

# Required binaries to bundle
REQUIRED_BINARIES = ["pdal.exe", "gdaldem.exe"]

# Windows system DLL prefixes - never bundle these
SYSTEM_DLL_PREFIXES = (
    "api-ms-win-",
    "ext-ms-",
    "kernel32",
    "kernelbase",
    "ntdll",
    "user32",
    "advapi32",
    "shell32",
    "ole32",
    "oleaut32",
    "combase",
    "rpcrt4",
    "sechost",
    "bcrypt",
    "bcryptprimitives",
    "ws2_32",
    "wsock32",
    "crypt32",
    "gdi32",
    "shlwapi",
    "msvcrt",
    "comdlg32",
    "setupapi",
    "cfgmgr32",
    "devobj",
    "wintrust",
    "imagehlp",
    "powrprof",
    "profapi",
    "imm32",
    "version",
    "winmm",
    "iphlpapi",
    "dbghelp",
    "psapi",
    "netapi32",
    "wldp",
    "umpdc",
)

# Exact system DLL names to skip
SYSTEM_DLLS_EXACT = {
    "kernel32.dll",
    "kernelbase.dll",
    "ntdll.dll",
    "user32.dll",
    "advapi32.dll",
    "shell32.dll",
    "ole32.dll",
    "oleaut32.dll",
    "gdi32.dll",
    "gdi32full.dll",
    "combase.dll",
    "rpcrt4.dll",
    "sechost.dll",
    "bcrypt.dll",
    "ws2_32.dll",
    "wsock32.dll",
    "crypt32.dll",
    "msvcrt.dll",
    "comdlg32.dll",
    "winspool.drv",
    "mswsock.dll",
    "secur32.dll",
    "userenv.dll",
    "netutils.dll",
    "sspicli.dll",
    "nsi.dll",
    "dnsapi.dll",
}

# DLLs that are part of VC++ redistributable - bundle these as they may not be on target
VCREDIST_DLLS = {
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "vcruntime140_threads.dll",
    "vcomp140.dll",
    "vcamp140.dll",
    "vccorlib140.dll",
    "concrt140.dll",
    "ucrtbase.dll",
}

# Large DLLs we can skip (MKL, TBB) - not needed by PDAL/GDAL CLI tools
SKIP_DLLS = {
    "mkl_avx2.2.dll",
    "mkl_avx512.2.dll",
    "mkl_blacs_ilp64.2.dll",
    "mkl_blacs_intelmpi_ilp64.2.dll",
    "mkl_blacs_intelmpi_lp64.2.dll",
    "mkl_blacs_lp64.2.dll",
    "mkl_blacs_msmpi_ilp64.2.dll",
    "mkl_blacs_msmpi_lp64.2.dll",
    "mkl_cdft_core.2.dll",
    "mkl_core.2.dll",
    "mkl_def.2.dll",
    "mkl_intel_thread.2.dll",
    "mkl_mc3.2.dll",
    "mkl_rt.2.dll",
    "mkl_scalapack_ilp64.2.dll",
    "mkl_scalapack_lp64.2.dll",
    "mkl_sequential.2.dll",
    "mkl_tbb_thread.2.dll",
    "mkl_vml_avx2.2.dll",
    "mkl_vml_avx512.2.dll",
    "mkl_vml_cmpt.2.dll",
    "mkl_vml_def.2.dll",
    "mkl_vml_mc3.2.dll",
    "tbb12.dll",
    "tbbbind_2_5.dll",
    "tbbmalloc.dll",
    "tbbmalloc_proxy.dll",
}


def is_system_dll(dll_name: str) -> bool:
    """Check if a DLL is a Windows system DLL that should not be bundled."""
    name_lower = dll_name.lower()

    # Check exact matches
    if name_lower in SYSTEM_DLLS_EXACT:
        return True

    # Check prefixes
    for prefix in SYSTEM_DLL_PREFIXES:
        if name_lower.startswith(prefix):
            return True

    return False


def is_skippable_dll(dll_name: str) -> bool:
    """Check if a DLL should be skipped (large/unnecessary)."""
    return dll_name.lower() in {s.lower() for s in SKIP_DLLS}


def get_pe_imports_pefile(pe_path: Path) -> List[str]:
    """Get DLL imports using the pefile library."""
    try:
        import pefile
    except ImportError:
        return []

    try:
        pe = pefile.PE(str(pe_path), fast_load=True)
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]]
        )

        imports = []
        if hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
            for entry in pe.DIRECTORY_ENTRY_IMPORT:
                dll_name = entry.dll.decode("utf-8", errors="replace")
                imports.append(dll_name)

        pe.close()
        return imports
    except Exception as e:
        print(f"  Warning: Could not parse PE imports for {pe_path.name}: {e}")
        return []


def get_pe_imports_dumpbin(pe_path: Path) -> List[str]:
    """Get DLL imports using dumpbin (Visual Studio tool)."""
    try:
        result = subprocess.run(
            ["dumpbin", "/dependents", str(pe_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return []

        imports = []
        in_deps = False
        for line in result.stdout.splitlines():
            line = line.strip()
            if "Image has the following dependencies" in line:
                in_deps = True
                continue
            if in_deps:
                if line == "" or line.startswith("Summary"):
                    break
                if line.lower().endswith(".dll"):
                    imports.append(line)

        return imports
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def get_pe_imports_minimal(pe_path: Path) -> List[str]:
    """
    Minimal PE import parser using only the struct module.
    Reads the PE import directory to find DLL names.
    """
    try:
        with open(pe_path, "rb") as f:
            # Check MZ signature
            mz = f.read(2)
            if mz != b"MZ":
                return []

            # Get PE header offset
            f.seek(0x3C)
            pe_offset = struct.unpack("<I", f.read(4))[0]

            # Check PE signature
            f.seek(pe_offset)
            pe_sig = f.read(4)
            if pe_sig != b"PE\x00\x00":
                return []

            # Read COFF header
            machine = struct.unpack("<H", f.read(2))[0]
            num_sections = struct.unpack("<H", f.read(2))[0]
            f.read(12)  # skip timestamp, symbol table pointer, symbol count
            optional_header_size = struct.unpack("<H", f.read(2))[0]
            f.read(2)  # characteristics

            # Read optional header magic to determine PE32 or PE32+
            opt_start = f.tell()
            magic = struct.unpack("<H", f.read(2))[0]

            if magic == 0x10B:  # PE32
                f.seek(opt_start + 96)  # NumberOfRvaAndSizes offset for PE32
            elif magic == 0x20B:  # PE32+
                f.seek(opt_start + 112)  # NumberOfRvaAndSizes offset for PE32+
            else:
                return []

            num_data_dirs = struct.unpack("<I", f.read(4))[0]

            if num_data_dirs < 2:
                return []

            # Skip export directory
            f.read(8)

            # Read import directory RVA and size
            import_rva = struct.unpack("<I", f.read(4))[0]
            import_size = struct.unpack("<I", f.read(4))[0]

            if import_rva == 0:
                return []

            # Read section headers to map RVA to file offset
            section_headers_offset = opt_start + optional_header_size
            f.seek(section_headers_offset)

            sections = []
            for _ in range(num_sections):
                name = f.read(8)
                virtual_size = struct.unpack("<I", f.read(4))[0]
                virtual_address = struct.unpack("<I", f.read(4))[0]
                raw_data_size = struct.unpack("<I", f.read(4))[0]
                raw_data_ptr = struct.unpack("<I", f.read(4))[0]
                f.read(16)  # skip rest of section header
                sections.append(
                    (virtual_address, virtual_size, raw_data_ptr, raw_data_size)
                )

            def rva_to_offset(rva):
                for va, vs, raw_ptr, raw_size in sections:
                    if va <= rva < va + max(vs, raw_size):
                        return raw_ptr + (rva - va)
                return None

            # Read import directory
            import_offset = rva_to_offset(import_rva)
            if import_offset is None:
                return []

            imports = []
            f.seek(import_offset)

            while True:
                # Read IMAGE_IMPORT_DESCRIPTOR (20 bytes)
                entry = f.read(20)
                if len(entry) < 20:
                    break

                # Fields: OriginalFirstThunk, TimeDateStamp, ForwarderChain,
                #         Name, FirstThunk
                name_rva = struct.unpack("<I", entry[12:16])[0]

                if name_rva == 0:
                    break

                name_offset = rva_to_offset(name_rva)
                if name_offset is None:
                    continue

                # Save current position
                current_pos = f.tell()

                # Read DLL name
                f.seek(name_offset)
                name_bytes = b""
                while True:
                    ch = f.read(1)
                    if ch == b"\x00" or ch == b"":
                        break
                    name_bytes += ch

                dll_name = name_bytes.decode("ascii", errors="replace")
                if dll_name:
                    imports.append(dll_name)

                # Restore position
                f.seek(current_pos)

            return imports

    except Exception as e:
        print(f"  Warning: Minimal PE parser failed for {pe_path.name}: {e}")
        return []


def get_pe_imports(pe_path: Path) -> List[str]:
    """Get DLL imports using the best available method."""
    # Try pefile first (most reliable)
    imports = get_pe_imports_pefile(pe_path)
    if imports:
        return imports

    # Try dumpbin (Visual Studio)
    imports = get_pe_imports_dumpbin(pe_path)
    if imports:
        return imports

    # Fall back to minimal parser
    return get_pe_imports_minimal(pe_path)


def find_conda_env() -> Path:
    """Find the conda 'lidar' environment."""
    # Check if we're running inside a conda env
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        conda_path = Path(conda_prefix)
        if (conda_path / "Library" / "bin" / "pdal.exe").exists():
            return conda_path

    # Check common conda env locations
    home = Path.home()
    candidates = [
        home / ".conda" / "envs" / "lidar",
        home / "miniconda3" / "envs" / "lidar",
        home / "anaconda3" / "envs" / "lidar",
        Path("C:/ProgramData/miniconda3/envs/lidar"),
        Path("C:/ProgramData/anaconda3/envs/lidar"),
    ]

    for candidate in candidates:
        if (candidate / "Library" / "bin" / "pdal.exe").exists():
            return candidate

    # Try conda info command
    try:
        result = subprocess.run(
            ["conda", "info", "--envs"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if "lidar" in line.lower():
                parts = line.split()
                for part in parts:
                    p = Path(part)
                    if p.is_dir() and (p / "Library" / "bin" / "pdal.exe").exists():
                        return p
    except Exception:
        pass

    return None


def trace_dependencies(
    binary_path: Path,
    search_dir: Path,
    processed: Set[str],
    found_dlls: Dict[str, Path],
) -> None:
    """
    Recursively trace DLL dependencies for a binary.

    Args:
        binary_path: Path to the PE file to analyze
        search_dir: Directory to search for DLLs (conda Library/bin)
        processed: Set of already-processed DLL names (lowercase)
        found_dlls: Dict mapping DLL name -> source path
    """
    name_lower = binary_path.name.lower()
    if name_lower in processed:
        return

    processed.add(name_lower)

    imports = get_pe_imports(binary_path)
    for dll_name in imports:
        dll_lower = dll_name.lower()

        # Skip if already processed
        if dll_lower in processed:
            continue

        # Skip system DLLs
        if is_system_dll(dll_name):
            continue

        # Skip unnecessary large DLLs
        if is_skippable_dll(dll_name):
            continue

        # Try to find the DLL in the conda env
        dll_path = search_dir / dll_name
        if not dll_path.exists():
            # Try case-insensitive search
            for item in search_dir.iterdir():
                if item.name.lower() == dll_lower:
                    dll_path = item
                    break

        if dll_path.exists():
            found_dlls[dll_path.name] = dll_path
            # Recursively trace this DLL's dependencies
            trace_dependencies(dll_path, search_dir, processed, found_dlls)
        else:
            # DLL not in conda env - might be a system DLL we didn't filter
            processed.add(dll_lower)


def main():
    parser = argparse.ArgumentParser(
        description="Bundle PDAL/GDAL binaries from conda for standalone Windows builds"
    )
    parser.add_argument(
        "--conda-env",
        type=Path,
        help="Path to conda environment (auto-detected if not specified)",
    )
    parser.add_argument(
        "--all-dlls",
        action="store_true",
        help="Copy ALL non-system DLLs instead of tracing dependencies (larger but safer)",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("Bundling PDAL/GDAL binaries for standalone Windows distribution")
    print("=" * 70)

    # Find conda environment
    if args.conda_env:
        conda_env = args.conda_env
    else:
        conda_env = find_conda_env()

    if not conda_env or not conda_env.exists():
        print("\nERROR: Could not find conda 'lidar' environment.")
        print("Please either:")
        print("  1. Activate the conda env: conda activate lidar")
        print("  2. Specify the path: python bundle_dependencies_windows.py --conda-env <path>")
        sys.exit(1)

    conda_bin_dir = conda_env / "Library" / "bin"
    print(f"\nConda environment: {conda_env}")
    print(f"Binary directory: {conda_bin_dir}")

    # Verify required binaries exist
    for binary_name in REQUIRED_BINARIES:
        binary_path = conda_bin_dir / binary_name
        if not binary_path.exists():
            print(f"\nERROR: Required binary not found: {binary_path}")
            print("Please install it: conda install -c conda-forge pdal gdal")
            sys.exit(1)

    # Set up output directories
    bundle_dir = SCRIPT_DIR / "bundle_bins" / "windows-x86_64"
    libs_dir = bundle_dir / "libs"
    gdal_data_dir = bundle_dir / "gdal_data"
    proj_data_dir = bundle_dir / "proj_data"

    # Clean existing bundle
    if bundle_dir.exists():
        print(f"\nCleaning existing bundle: {bundle_dir}")
        shutil.rmtree(bundle_dir)

    bundle_dir.mkdir(parents=True, exist_ok=True)
    libs_dir.mkdir(exist_ok=True)

    # Step 1: Copy required binaries
    print("\n" + "=" * 70)
    print("Step 1: Copying binaries")
    print("=" * 70)

    binaries = []
    for binary_name in REQUIRED_BINARIES:
        src = conda_bin_dir / binary_name
        dst = bundle_dir / binary_name
        print(f"  Copying: {binary_name} ({src.stat().st_size / 1024:.0f} KB)")
        shutil.copy2(src, dst)
        binaries.append(dst)

    # Step 2: Copy PDAL plugin DLLs
    print("\n" + "=" * 70)
    print("Step 2: Copying PDAL plugin DLLs")
    print("=" * 70)

    pdal_plugins = list(conda_bin_dir.glob("libpdal_plugin_*.dll"))
    for plugin in pdal_plugins:
        dst = libs_dir / plugin.name
        print(f"  Copying: {plugin.name} ({plugin.stat().st_size / 1024:.0f} KB)")
        shutil.copy2(plugin, dst)

    print(f"  Copied {len(pdal_plugins)} PDAL plugins")

    # Step 3: Copy key DLLs (pdalcpp.dll, gdal.dll, proj_*.dll)
    print("\n" + "=" * 70)
    print("Step 3: Copying core DLLs")
    print("=" * 70)

    core_dlls = ["pdalcpp.dll", "gdal.dll"]
    # Find proj DLL (may be proj_9.dll, proj.dll, etc.)
    for dll in conda_bin_dir.glob("proj*.dll"):
        if dll.name not in core_dlls:
            core_dlls.append(dll.name)

    for dll_name in core_dlls:
        src = conda_bin_dir / dll_name
        if src.exists():
            dst = libs_dir / dll_name
            print(f"  Copying: {dll_name} ({src.stat().st_size / 1024:.0f} KB)")
            shutil.copy2(src, dst)

    # Step 4: Trace and copy all DLL dependencies
    print("\n" + "=" * 70)
    print("Step 4: Tracing DLL dependencies")
    print("=" * 70)

    if args.all_dlls:
        print("  Mode: Copying ALL non-system DLLs from conda env")
        found_dlls = {}
        for dll in conda_bin_dir.glob("*.dll"):
            if not is_system_dll(dll.name) and not is_skippable_dll(dll.name):
                found_dlls[dll.name] = dll
    else:
        print("  Mode: Tracing dependencies recursively")
        processed = set()
        found_dlls = {}

        # Trace from each binary
        for binary in binaries:
            print(f"\n  Tracing: {binary.name}")
            trace_dependencies(binary, conda_bin_dir, processed, found_dlls)

        # Also trace from core DLLs and plugins
        for dll_name in core_dlls:
            src = conda_bin_dir / dll_name
            if src.exists():
                print(f"  Tracing: {dll_name}")
                trace_dependencies(src, conda_bin_dir, processed, found_dlls)

        for plugin in pdal_plugins:
            print(f"  Tracing: {plugin.name}")
            trace_dependencies(plugin, conda_bin_dir, processed, found_dlls)

    # Copy found DLLs to libs directory
    print("\n" + "=" * 70)
    print("Step 5: Copying dependency DLLs")
    print("=" * 70)

    copied_count = 0
    for dll_name, dll_path in sorted(found_dlls.items()):
        dst = libs_dir / dll_name
        if not dst.exists():
            shutil.copy2(dll_path, dst)
            copied_count += 1

    print(f"  Copied {copied_count} dependency DLLs")

    # Also copy VC++ redistributable DLLs if present
    print("\n  Checking VC++ redistributable DLLs...")
    vcredist_count = 0
    for vcr_dll in VCREDIST_DLLS:
        src = conda_bin_dir / vcr_dll
        dst = libs_dir / vcr_dll
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            vcredist_count += 1
            print(f"    + {vcr_dll}")
    print(f"  Copied {vcredist_count} VC++ redistributable DLLs")

    # Step 6: Copy GDAL data files
    print("\n" + "=" * 70)
    print("Step 6: Copying GDAL data files")
    print("=" * 70)

    gdal_data_src = conda_env / "Library" / "share" / "gdal"
    if gdal_data_src.exists():
        if gdal_data_dir.exists():
            shutil.rmtree(gdal_data_dir)
        shutil.copytree(gdal_data_src, gdal_data_dir)
        gdal_files = list(gdal_data_dir.rglob("*"))
        gdal_size = sum(f.stat().st_size for f in gdal_files if f.is_file())
        print(f"  Copied {len(gdal_files)} GDAL data files ({gdal_size / 1024 / 1024:.1f} MB)")
    else:
        print("  WARNING: GDAL data directory not found!")

    # Step 7: Copy PROJ data files
    print("\n" + "=" * 70)
    print("Step 7: Copying PROJ data files")
    print("=" * 70)

    proj_data_src = conda_env / "Library" / "share" / "proj"
    if proj_data_src.exists():
        if proj_data_dir.exists():
            shutil.rmtree(proj_data_dir)
        shutil.copytree(proj_data_src, proj_data_dir)
        proj_files = list(proj_data_dir.rglob("*"))
        proj_size = sum(f.stat().st_size for f in proj_files if f.is_file())
        print(f"  Copied {len(proj_files)} PROJ data files ({proj_size / 1024 / 1024:.1f} MB)")
    else:
        print("  WARNING: PROJ data directory not found!")

    # Summary
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)

    total_files = list(bundle_dir.rglob("*"))
    total_size = sum(f.stat().st_size for f in total_files if f.is_file())
    dll_count = len(list(libs_dir.glob("*.dll")))
    exe_count = len(list(bundle_dir.glob("*.exe")))

    print(f"Platform: windows-x86_64")
    print(f"Executables: {exe_count}")
    for b in binaries:
        print(f"  - {b.name}")
    print(f"DLLs (libs/): {dll_count}")
    print(f"PDAL plugins: {len(pdal_plugins)}")
    print(f"GDAL data files: {len(list(gdal_data_dir.rglob('*'))) if gdal_data_dir.exists() else 0}")
    print(f"PROJ data files: {len(list(proj_data_dir.rglob('*'))) if proj_data_dir.exists() else 0}")
    print(f"Total size: {total_size / 1024 / 1024:.1f} MB")
    print(f"\nBundle location: {bundle_dir}")
    print("\nBundling complete!")
    print("\nNext steps:")
    print("  1. Test locally: python app.py")
    print("  2. Build .exe:   pyinstaller lidar_explorer.spec")
    print("  3. Find output:  dist\\LiDARHillshadeExplorer\\")


if __name__ == "__main__":
    main()
