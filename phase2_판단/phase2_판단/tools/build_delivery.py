"""Package only changed Phase 2 files, with base/current SHA-256 for safe review."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[2]


def git(*args):
    return subprocess.check_output(["git", "-c", "core.quotepath=false", *args], cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    names = set()
    for command in (("diff", "HEAD", "--name-only", "-z", "--", "phase2_판단"),
                    ("ls-files", "--others", "--exclude-standard", "-z", "--", "phase2_판단")):
        names.update(n.decode("utf-8") for n in git(*command).split(b"\0") if n)
    tracked = {n.decode("utf-8") for n in git("ls-tree", "-r", "--name-only", "-z", "HEAD", "--", "phase2_판단").split(b"\0") if n}
    entries = []
    for name in sorted(names):
        path = ROOT / name
        if not path.is_file() or any(part in {"__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        if path.suffix not in {".py", ".md", ".json", ".txt", ".ini"}:
            continue
        base = git("show", f"HEAD:{name}") if name in tracked else None
        entries.append({"path": name, "sha256": sha256(path.read_bytes()).hexdigest(),
                        "lf_sha256": sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
                        "baseline_sha256": sha256(base).hexdigest() if base is not None else None,
                        "baseline_lf_sha256": sha256(base.replace(b"\r\n", b"\n")).hexdigest() if base is not None else None})
    manifest = {"package": "phase2-chanwoo-v3", "base_commit": git("rev-parse", "HEAD").decode().strip(),
                "scope": "Changed Phase 2 files only; merge into the existing repository",
                "files": entries}
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for entry in entries:
            archive.write(ROOT / entry["path"], entry["path"])
        archive.writestr("PATCH_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        for entry in entries:
            assert sha256(archive.read(entry["path"])).hexdigest() == entry["sha256"]
    print(json.dumps({"zip": str(output), "files": len(entries), "sha256": sha256(output.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
