"""Build a sanitized source snapshot and archive using an explicit file allowlist."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

root = Path(__file__).resolve().parents[1]
files = set()
for name in ("README.md", "PROJECT_STATUS.md", ".env.example", ".gitignore", "docker-compose.yml", "render.yaml",
             "backend/Dockerfile", "backend/.dockerignore", "backend/requirements.txt", "backend/pytest.ini", "backend/.env.example",
             "backend/requirements-dev.txt", ".github/workflows/backend.yml",
             "frontend/Dockerfile", "frontend/.dockerignore", "frontend/.env.example", "frontend/.vercelignore",
             "frontend/package.json", "frontend/package-lock.json", "frontend/next.config.js", "frontend/vercel.json"):
    if (root / name).is_file():
        files.add(root / name)
for directory, extensions in (("backend/app", {".py"}), ("backend/tests", {".py"}),
                              ("frontend/app", {".js", ".css"}), ("frontend/public", {".json", ".md", ".mp4", ".vtt"}),
                              ("frontend/tests", {".cjs"}), ("docs", {".md"}), ("scripts", {".py"})):
    for path in (root / directory).rglob("*"):
        if path.is_file() and path.suffix in extensions and "__pycache__" not in path.parts:
            files.add(path)
for name in ("submission-evidence.json", "deployment.json", "production-desktop.jpg", "production-mobile.jpg", "current-demo.mp4", "revora-demo.mp4", "revora-demo.srt"):
    if (root / "docs/artifacts" / name).is_file():
        files.add(root / "docs/artifacts" / name)
manifest = {"file_count": len(files), "files": []}
snapshot = Path(tempfile.mkdtemp(prefix="revora-source-"))
for path in sorted(files):
    assert path.name not in {".env", ".env.local", ".env.production"}
    assert not path.is_symlink()
    relative = path.relative_to(root)
    destination = snapshot / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
    manifest["files"].append({"path": str(relative), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
(snapshot / "submission-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
(root / "output").mkdir(exist_ok=True)
archive = root / "output/revora-submission.zip"
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
    for path in snapshot.rglob("*"):
        if path.is_file():
            bundle.write(path, path.relative_to(snapshot))
checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
(root / "output/revora-submission.zip.sha256").write_text(checksum + "  revora-submission.zip\n")
(root / "output/submission-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps({"snapshot": str(snapshot), "archive": str(archive), "files": len(files), "sha256": checksum}))
