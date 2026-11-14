"""Queue states are named once (#173 part 1)."""

import re
from pathlib import Path

from server.queue_states import ACTIVE, CANCELLABLE, FINISHED, RETRYABLE, sql


def test_the_state_sets_relate_as_the_queue_expects():
    assert set(CANCELLABLE) == set(ACTIVE) - {"importing"}
    assert set(RETRYABLE) <= set(FINISHED)
    assert not set(ACTIVE) & set(FINISHED)
    assert sql(RETRYABLE) == "('failed','cancelled')"


def test_no_module_writes_its_own_list_of_queue_states():
    # mix_cache's busy check also counts the legacy 'queued' state on purpose.
    spelled = re.compile(r"""['"]waiting_for_sources['"],\s*['"]searching['"]""")
    offenders = [
        str(path)
        for path in Path("server").rglob("*.py")
        if path.name not in {"queue_states.py", "mix_cache.py"}
        and spelled.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
