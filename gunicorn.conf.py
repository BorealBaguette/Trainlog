# Loaded by gunicorn on its own (it reads ./gunicorn.conf.py from the working
# directory, /code in the container). Hooks plus worker recycling: workers,
# timeout and bind stay on the command line / compose file.
#
# The hooks feed the /admin/health page and do nothing unless
# TRAINLOG_STATUS_DIR is set (see src/health/recorder.py).
from src.health.recorder import (  # noqa: F401
    child_exit,
    on_starting,
    post_request,
    post_worker_init,
    pre_request,
    worker_exit,
)

# No max_requests: workers grow ~3 KB per request (~2 GB each after 3 days),
# but recycling them was followed within 30 min by a corrupted auth.db
# (2026-11-26, first round of re-forks from a master up for hours). Off until
# forking later in the master's life is shown to be safe for SQLite.
