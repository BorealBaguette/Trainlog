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

# Workers grow by about 3 KB per request served and never give it back: prod
# workers reached ~2 GB each after 3 days (600k requests), 16 GB of 23.5 GB.
# Replace each worker after ~20k requests (a few hours at current traffic);
# with --preload a new worker is a cheap fork. The jitter keeps the 8 from
# restarting at the same moment.
max_requests = 20000
max_requests_jitter = 2000
