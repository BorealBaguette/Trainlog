# Loaded by gunicorn on its own (it reads ./gunicorn.conf.py from the working
# directory, /code in the container). Hooks only, no settings: workers,
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
