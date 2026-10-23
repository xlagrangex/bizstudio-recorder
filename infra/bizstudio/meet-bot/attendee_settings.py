from .production import *  # noqa: F401,F403

# Upstream caps run_bot at a 1h soft time limit, which would cut long meetings.
# Bots leave on their own via automatic_leave_settings.max_uptime_seconds (set
# by meet-bridge), so the Celery limit is only a backstop above that.
CELERY_TASK_ANNOTATIONS = {
    "bots.tasks.run_bot_task.run_bot": {"soft_time_limit": 4 * 3600, "time_limit": 4 * 3600 + 900},
}
