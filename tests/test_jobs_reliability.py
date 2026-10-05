import time
from backend.jobs import JobManager, JOB_DONE, JOB_FAILED


def test_job_updates_have_monotonic_event_sequence():
    manager = JobManager()
    job = manager.create('scan', 't', 'x', {})
    manager.update(job['id'], message='one', force=True)
    manager.update(job['id'], message='two', force=True)
    current = manager.get(job['id'])
    assert current['event_seq'] >= 3


def test_terminal_state_cannot_be_overwritten():
    manager = JobManager()
    job = manager.create('scan', 't', 'x', {})
    manager.update(job['id'], status=JOB_DONE, force=True)
    manager.update(job['id'], status=JOB_FAILED, force=True)
    assert manager.get(job['id'])['status'] == JOB_DONE
