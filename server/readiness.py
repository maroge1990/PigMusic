"""The readiness checks that can hang (#238): the database read and the NAS
mount test behind /api/health/ready.

A read of an unresponsive SMB mount blocks its thread for as long as the mount
does, and a timeout only stops waiting for it. Asked every 30 s on the shared
thread pool, each hung call would take one more of the threads every endpoint
uses. So these run on two threads of their own, and a check whose last call
has not come back is not started again.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

THREADS = 2


class ReadinessChecks:
    def __init__(self):
        self.executor = None
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
            if self.executor is None:
                self.executor = ThreadPoolExecutor(
                    max_workers=THREADS, thread_name_prefix="ready"
                )
            call = self.calls[name] = self.executor.submit(function, *args)
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
        """Never waits for a call that is still blocked."""
        executor, self.executor = self.executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
