"""Rebuild the 見開きPDF分割 app from this directory."""
from pathlib import Path
import importlib.metadata
import os
import plistlib
import shutil
import subprocess
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / ".build"
DESTINATION = ROOT / "output" / "見開きPDF分割"
# Build/sign outside cloud-synced Documents: Finder can attach FinderInfo to
# bundles there between signing and verification. The archive is made from
# this clean staging tree, then the ready apps are copied to the workspace.
STAGING = tempfile.TemporaryDirectory(prefix="pdf-drop-build-")
OUTPUT = Path(STAGING.name) / "見開きPDF分割"
BUILD.mkdir(exist_ok=True)
OUTPUT.mkdir(parents=True, exist_ok=True)
ENV = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(BUILD / "pyinstaller-cache"))


def run(*args):
    subprocess.run([str(a) for a in args], cwd=ROOT, env=ENV, check=True)


run("xcrun", "swiftc", "-O", "-target", "arm64-apple-macosx13.0", "-module-cache-path", BUILD / "swift-cache", ROOT / "src/PDFDropApp.swift", ROOT / "src/main.swift", "-o", BUILD / "PDFDropApp", "-framework", "AppKit", "-framework", "UniformTypeIdentifiers")
run("xcrun", "swiftc", "-O", "-module-cache-path", BUILD / "swift-cache", ROOT / "src/make_icons.swift", "-o", BUILD / "make_icons", "-framework", "AppKit")
run(BUILD / "make_icons", BUILD / "icons")
run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console", "--name", "pdf-engine", "--target-arch", "arm64", "--distpath", BUILD / "engine-dist", "--workpath", BUILD / "engine-work", "--specpath", BUILD, "--collect-all", "pymupdf", ROOT / "src/pdf_engine.py")

for operation, name in [("split", "見開きPDF分割")]:
    app = OUTPUT / (name + ".app")
    if app.exists():
        shutil.rmtree(app)
    contents = app / "Contents"
    executable = contents / "MacOS"
    resources = contents / "Resources"
    executable.mkdir(parents=True)
    resources.mkdir(parents=True)
    shutil.copy2(BUILD / "PDFDropApp", executable / "PDFDropApp")
    shutil.copytree(BUILD / "engine-dist/pdf-engine", resources / "Engine", symlinks=True)
    # ICNS accepts PNG chunks directly; this also works without iconutil's GUI services.
    chunks = []
    for tag, filename in [("icp4", "icon_16x16.png"), ("icp5", "icon_32x32.png"), ("icp6", "icon_32x32@2x.png"),
                          ("ic07", "icon_128x128.png"), ("ic08", "icon_256x256.png"), ("ic09", "icon_512x512.png"),
                          ("ic10", "icon_512x512@2x.png"), ("ic11", "icon_16x16@2x.png"), ("ic12", "icon_32x32@2x.png"),
                          ("ic13", "icon_128x128@2x.png"), ("ic14", "icon_256x256@2x.png")]:
        payload = (BUILD / f"icons/{operation}.iconset" / filename).read_bytes()
        chunks.append(tag.encode("ascii") + struct.pack(">I", len(payload) + 8) + payload)
    payload = b"".join(chunks)
    (resources / "AppIcon.icns").write_bytes(b"icns" + struct.pack(">I", len(payload) + 8) + payload)
    info = dict(CFBundleName=name, CFBundleDisplayName=name, CFBundleExecutable="PDFDropApp",
                CFBundleIdentifier=f"local.pdfdroptools.{operation}", CFBundleVersion="1",
                CFBundleShortVersionString="1.0", CFBundlePackageType="APPL",
                CFBundleIconFile="AppIcon", NSHighResolutionCapable=True,
                LSMinimumSystemVersion="13.0", PDFOperation=operation,
                NSHumanReadableCopyright="PDF Drop Tools. Includes PyMuPDF and Python; see Licenses.",
                CFBundleDocumentTypes=[dict(CFBundleTypeName="PDF or Folder", CFBundleTypeRole="Viewer",
                    LSHandlerRank="None", LSItemContentTypes=["com.adobe.pdf", "public.folder"])])
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump(info, f)
    licenses = resources / "Licenses"
    licenses.mkdir()
    for package in ("pymupdf", "pyinstaller"):
        dist = importlib.metadata.distribution(package)
        for file in dist.files or []:
            if "license" in str(file).lower() or "copying" in str(file).lower():
                source = Path(dist.locate_file(file))
                if source.is_file():
                    destination = licenses / package / source.name
                    destination.parent.mkdir(exist_ok=True)
                    shutil.copy2(source, destination)
    python_license = Path(sys.base_prefix) / "lib/python3.12/LICENSE.txt"
    if python_license.exists():
        shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
    shutil.copy2(ROOT / "使い方.md", resources / "使い方.md")
    run("codesign", "--force", "--deep", "--sign", "-", app)
    run("codesign", "--verify", "--deep", "--strict", app)

shutil.copy2(ROOT / "使い方.md", OUTPUT / "使い方.md")
source_output = OUTPUT / "ソースコード"
if source_output.exists():
    shutil.rmtree(source_output)
shutil.copytree(ROOT / "src", source_output / "src", ignore=shutil.ignore_patterns("__pycache__"))
shutil.copytree(ROOT / "tests", source_output / "tests", ignore=shutil.ignore_patterns("__pycache__"))
for name in ("build_apps.py", "requirements-build.txt", "使い方.md", "divide_pdf.py"):
    shutil.copy2(ROOT / name, source_output / name)
DESTINATION.parent.mkdir(parents=True, exist_ok=True)
archive = DESTINATION.parent / "見開きPDF分割.zip"
if archive.exists():
    archive.unlink()
run("ditto", "-c", "-k", "--norsrc", "--noextattr", "--keepParent", OUTPUT, archive)
if DESTINATION.exists():
    shutil.rmtree(DESTINATION)
shutil.copytree(OUTPUT, DESTINATION, symlinks=True)
STAGING.cleanup()
print(f"Created: {DESTINATION}\nArchive: {archive}")
