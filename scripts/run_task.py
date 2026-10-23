"""Run one ordinary CLI operation with an exclusive, task-scoped API."""
from __future__ import annotations

import json
from pathlib import Path
import signal
import sys

ROOT = Path(__file__).resolve().parents[1]
# Direct script execution must not depend on the caller's current directory.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from local_scrape import cli
from local_scrape.lifecycle import task_service
from local_scrape.settings import load_settings
from local_scrape.workflows import WorkflowError, error_payload


def main(argv=None, *, root=None, operation=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(root or ROOT).resolve()
    previous_break = None
    if hasattr(signal, 'SIGBREAK'):
        def cancel(signum, frame):
            raise KeyboardInterrupt
        previous_break = signal.signal(signal.SIGBREAK, cancel)
    try:
        parsed = cli.parser().parse_args(argv)
        if parsed.command in ('start', 'stop', 'status'):
            raise WorkflowError('INVALID_ARGUMENT', 'Use start.cmd / stop.cmd for persistent lifecycle operations', 422)
        if hasattr(parsed, 'root') and Path(parsed.root).resolve() != root:
            raise WorkflowError('INVALID_ARGUMENT', 'Task mode is limited to this installation root', 422)
        settings = load_settings(root=root)
        with task_service(settings):
            return (operation or cli.main)(['--root', str(root), *argv])
    except KeyboardInterrupt:
        print(json.dumps({'ok': False, 'error': {'code': 'INTERRUPTED', 'message': 'Task interrupted; owned service cleanup completed', 'details': {}}}))
        return 130
    except WorkflowError as exc:
        print(json.dumps({'ok': False, 'error': error_payload(exc)}))
        return 2 if exc.code == 'INVALID_ARGUMENT' else 1
    except (ValueError, OSError):
        print(json.dumps({'ok': False, 'error': {'code': 'CONFIG_OR_RUNTIME_ERROR', 'message': 'Invalid configuration or unavailable runtime; see installation-local logs', 'details': {}}}))
        return 1
    finally:
        if previous_break is not None:
            signal.signal(signal.SIGBREAK, previous_break)


if __name__ == '__main__':
    raise SystemExit(main())
