import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / 'source' / 'backend'
os.chdir(BACKEND_DIR)
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.session import SessionLocal  # noqa: E402
from app.services.retention_service import cleanup_expired_files, cleanup_expired_task_events  # noqa: E402


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def main() -> int:
    parser = argparse.ArgumentParser(description='Run retention cleanup round.')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--days', type=int, default=180)
    parser.add_argument('--limit', type=int, default=500)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        file_summary = cleanup_expired_files(db, limit=args.limit, dry_run=args.dry_run)
        event_summary = cleanup_expired_task_events(db, days=args.days, dry_run=args.dry_run)
    finally:
        db.close()

    summary = {
        'timestamp': now_iso(),
        'dry_run': bool(args.dry_run),
        'file_cleanup': file_summary,
        'task_event_cleanup': event_summary,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
