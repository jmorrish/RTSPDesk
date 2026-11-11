r"""Build on Windows: python build.py [--tools C:\path\to\tools]."""
import argparse
import importlib.metadata
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--tools", type=Path, default=root / "tools")
parser.add_argument("--work-dir", type=Path, default=root / "build")
parser.add_argument("--dist-dir", type=Path, default=root / "dist")
args = parser.parse_args()
if sys.platform != "win32":
    raise SystemExit("Build the Windows executable on Windows.")
for name in ("ffmpeg.exe", "ffprobe.exe", "mediamtx.exe"):
    if not (args.tools / name).is_file():
        raise SystemExit(f"Missing {name} in {args.tools}. See README.md.")
subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
                "--name", "RTSPDesk", "--distpath", str(args.dist_dir.resolve()), "--workpath", str(args.work_dir.resolve()),
                "--specpath", str(args.work_dir.resolve()), str(root / "run.py")], cwd=root, check=True)
destination = args.dist_dir.resolve() / "RTSPDesk"
shutil.copytree(args.tools, destination / "tools", dirs_exist_ok=True)
for name in ("README.md", "LICENSE", "THIRD-PARTY-NOTICES.md"):
    shutil.copy2(root / name, destination / name)
licenses = destination / "licenses"
licenses.mkdir(exist_ok=True)
shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", licenses / "Python-LICENSE.txt")
for path in (Path(sys.base_prefix) / "tcl").glob("*/license.terms"):
    shutil.copy2(path, licenses / (path.parent.name + "-license.terms"))
distribution = importlib.metadata.distribution("pyinstaller")
for path in distribution.files or []:
    if path.name.lower().startswith(("copying", "license")):
        shutil.copy2(distribution.locate_file(path), licenses / ("PyInstaller-" + path.name))
print(f"Ready: {destination / 'RTSPDesk.exe'}")
