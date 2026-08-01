# PyInstaller specification shared by macOS and Windows.

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
datas = [
    (str(root / "app.py"), "."),
    (str(root / "migrations"), "migrations"),
    (str(root / ".streamlit" / "config.toml"), ".streamlit"),
]
binaries = []
hiddenimports = collect_submodules("plate_reader")
for package in ("streamlit", "plotly", "pyturso"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden
for distribution in ("streamlit", "plotly", "pyturso", "pandas", "numpy"):
    datas += copy_metadata(distribution)

analysis = Analysis(
    [str(root / "packaging" / "standalone_entry.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "matplotlib",
        "pyarrow",
        "pytest",
        "streamlit.hello",
        "streamlit.testing",
        "tensorflow",
    ],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="PlateReaderDatabase",
    console=True,
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="PlateReaderDatabase",
)

if sys.platform == "darwin":
    app = BUNDLE(
        collection,
        name="PlateReaderDatabase.app",
        bundle_identifier="org.plate-reader-database.app",
        info_plist={"NSHighResolutionCapable": True},
    )
