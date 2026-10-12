"""Extract artifacts/katrag.sqlite3.gz -> artifacts/katrag.sqlite3.

Skipped when the extracted database is already up to date. When the archive is newer than the
extracted file (for example after `git pull` brought a rebuilt database), the old file is moved to
katrag.sqlite3.bak and the new one is extracted, so a machine that ran the system before does not
keep answering from stale data.
"""
import gzip
import shutil
import sys
from pathlib import Path

ART = Path(__file__).resolve().parent / "artifacts"
SRC = ART / "katrag.sqlite3.gz"
DST = ART / "katrag.sqlite3"


def main() -> int:
    if not SRC.exists():
        if DST.exists() and DST.stat().st_size > 0:
            print(f"[OK] database already present: {DST}")
            return 0
        print(f"[ERROR] {SRC} not found - make sure the repo was cloned completely (git pull)")
        return 1
    if DST.exists() and DST.stat().st_size > 0:
        if DST.stat().st_mtime >= SRC.stat().st_mtime:
            print(f"[OK] database already present: {DST}")
            return 0
        backup = DST.with_suffix(".sqlite3.bak")
        DST.replace(backup)
        print(f"[INFO] newer database archive found - old database moved to {backup.name}")
    print("[INFO] extracting database (~110 MB) ...")
    tmp = DST.with_suffix(".tmp")
    with gzip.open(SRC, "rb") as f_in, open(tmp, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    tmp.replace(DST)
    print(f"[OK] database ready: {DST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
