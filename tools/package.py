"""配布用 ZIP を作る。Blender の拡張機能としてディスクからインストール可能。"""
from pathlib import Path
import zipfile
import tomllib

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "blender_manifest.toml").read_text(encoding="utf-8"))["version"]
out = root / "dist" / f"suketto-{version}.zip"
out.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
    for name in ("blender_manifest.toml", "__init__.py", "rpc.py", "runtime.py", "blender_tools.py", "README.md", "LICENSE"):
        archive.write(root / name, name)
print(out)
