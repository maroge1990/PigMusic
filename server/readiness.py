"""The checks that can hang (#238, #256): the database read and the NAS
mount test behind /api/health/ready, and the mount test behind /api/health.

A read of an unresponsive SMB mount blocks its thread for as long as the mount
does, and a timeout only stops waiting for it. Asked every 30 s, or by every
app testing reachability, on the shared thread pool, each hung call would take
one more of the threads every endpoint uses. So each runs on a thread of its
own, and a check whose last call has not come back is not started again: at
most one thread per check. Those threads are daemons, so one still blocked
when the server stops never holds up its exit.
"""

import asyncio
import threading
from concurrent.futures import Future


def _run(call, function, args):
    if not call.set_running_or_notify_cancel():
        return
    try:
        call.set_result(function(*args))
    except BaseException as error:
        call.set_exception(error)


class ReadinessChecks:
    def __init__(self):
        # The newest call of each check, finished or not.
        self.calls = {}

    async def passes(self, name, function, *args, timeout):
        """Whether `function(*args)` returns within `timeout` seconds. False
        at once, without calling it, while the check's last call is still
        running; false when it raises."""
        previous = self.calls.get(name)
        if previous is not None and not previous.done():
            return False
        try:
            call = Future()
            threading.Thread(
                target=_run,
                args=(call, function, args),
                name="ready-" + name,
                daemon=True,
            ).start()
            # Only once started: a call that never ran must not stay
            # outstanding for ever.
            self.calls[name] = call
            # Shielded: giving up the wait leaves the call itself in place, so
            # the next poll sees it is still outstanding.
            await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(call)), timeout=timeout
            )
            return True
        except Exception:
            return False

    def outstanding(self):
        return sorted(name for name, call in self.calls.items() if not call.done())

    def close(self):
        """Nothing to wait for: a call still blocked is left on its daemon
        thread, and the next start sees it outstanding until it returns."""
