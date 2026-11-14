"""Request-queue states, named once (#173).

The admin queue (Store) cancels and retries whole jobs; a person's requests
(UserLibrary) withdraw only that person. Both act on the same states.
"""

# Work still to do or under way.
ACTIVE = ("waiting_for_sources", "searching", "downloading", "validating", "importing")
# Active states a request can be cancelled in: importing is publishing a
# file that has already passed validation, and finishes on its own.
CANCELLABLE = ("waiting_for_sources", "searching", "downloading", "validating")
# Finished: Clear removes these.
FINISHED = ("completed", "downloaded", "failed", "cancelled")
# Finished without a file: Retry queues these again.
RETRYABLE = ("failed", "cancelled")


def sql(states):
    """An SQL list of fixed state names: ('a','b')."""
    return "(" + ",".join(f"'{state}'" for state in states) + ")"
