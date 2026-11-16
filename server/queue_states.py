"""Request-queue states, named once (#173).

The admin queue (Store) cancels and retries whole jobs; a person's requests
(UserLibrary) withdraw only that person. Both act on the same states.
"""

# Work still to do or under way.
ACTIVE = ("waiting_for_sources", "searching", "downloading", "validating", "importing")
# Active states a person can pause a request in (#228): importing is
# publishing a file that has already passed validation, and finishes on its
# own.
PAUSABLE = ("waiting_for_sources", "searching", "downloading", "validating")
# Paused by a person (#228): neither active nor finished. Nothing claims or
# schedules it, a restart keeps it, and Resume puts it back to
# waiting_for_sources.
PAUSED = "paused"
# Not finished: work under way, still to do, or paused. What holds a song as
# requested (it is still wanted) while ACTIVE counts only work in progress.
PENDING = ACTIVE + (PAUSED,)
# States a request can be cancelled in: the pausable ones, and paused.
CANCELLABLE = PAUSABLE + (PAUSED,)
# Finished: Clear removes these (never a paused request).
FINISHED = ("completed", "downloaded", "failed", "cancelled")
# Finished with a file. Asked for again once the song has left the library,
# one is queued again (#211).
DONE = ("completed", "downloaded")
# Finished without a file: Retry queues these again.
RETRYABLE = ("failed", "cancelled")


def sql(states):
    """An SQL list of fixed state names: ('a','b')."""
    return "(" + ",".join(f"'{state}'" for state in states) + ")"
