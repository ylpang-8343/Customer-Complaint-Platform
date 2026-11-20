"""Sends the reminders by itself (demo feedback: the system should schedule the reminders, not only the Coordinator).

There is no always-on clock on a free host, and a free web service sleeps when nobody uses it, so the platform does not rely
on one. It checks at most once per ``REMINDER_CHECK_MINUTES`` (60 by default) while the site is awake: the first request after
the interval starts a background check that emails the reminders that are due. Several workers or servers never send twice,
because each check first claims the slot in the database (``JobRun``). Two other ways to start the same check:

* ``manage.py send_reminders``, from the host's own scheduler (cron, Render Cron Job), and
* ``/tasks/reminders/?token=...`` (``REMINDER_TOKEN``), for a free outside scheduler such as cron-job.org or UptimeRobot: it
  also wakes a sleeping service, so reminders go out at the time you choose even when nobody is signed in.

The Coordinator can still send the due reminders at once from the Notifications page.
"""

import logging
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, close_old_connections
from django.utils import timezone

from .models import JobRun

logger = logging.getLogger(__name__)

JOB = "reminders"
_last_look = 0.0  # when this process last looked, so a busy site does not ask the database on every request
_lock = threading.Lock()


def every():
    return timedelta(minutes=max(1, int(getattr(settings, "REMINDER_CHECK_MINUTES", 60))))


def enabled():
    return bool(getattr(settings, "AUTO_REMINDERS", False))


def claim(name=JOB, interval=None, now=None):
    """Take the slot for this run: True for exactly one caller in each interval, however many workers there are."""
    now = now or timezone.now()
    interval = interval or every()
    try:
        JobRun.objects.get_or_create(name=name, defaults={"last_run_at": now - interval - timedelta(seconds=1)})
    except IntegrityError:  # another worker created it at the same moment
        pass
    return JobRun.objects.filter(name=name, last_run_at__lte=now - interval).update(last_run_at=now) == 1


def run_if_due(now=None, interval=None):
    """Send the due reminders if this interval has not been taken. Returns the list of results, or None when it was not our turn."""
    from . import reminders

    if not claim(JOB, interval, now):
        return None
    results = reminders.send_due_reminders()
    sent = sum(1 for item in results if item["sent"])
    summary = f"{sent} sent" + (f", {len(results) - sent} failed" if len(results) > sent else "") if results else "none due"
    JobRun.objects.filter(name=JOB).update(last_result=summary)
    logger.info("Automatic reminders: %s", summary)
    return results


def _spawn(function):
    threading.Thread(target=function, name="auto-reminders", daemon=True).start()


def _background():
    try:
        run_if_due()
    except Exception:  # a failed check must never hurt the site; it is tried again next interval
        logger.exception("The automatic reminder check failed.")
    finally:
        close_old_connections()


def tick():
    """Called on requests: start a background check when this process has not looked for a while."""
    global _last_look
    if not enabled():
        return
    now = time.monotonic()
    with _lock:
        if now - _last_look < min(every().total_seconds(), 300):
            return
        _last_look = now
    _spawn(_background)


def status():
    """For the Notifications page: whether it is on, how often, and what the last check did."""
    run = JobRun.objects.filter(name=JOB).first()
    return {
        "enabled": enabled(),
        "minutes": int(every().total_seconds() // 60),
        "last_run_at": run.last_run_at if run and run.last_result else None,
        "last_result": run.last_result if run else "",
        "token_set": bool(getattr(settings, "REMINDER_TOKEN", "")),
    }
