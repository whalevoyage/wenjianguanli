from backend.jobs import JobManager, JOB_DONE

def test_sse_replays_events_after_last_id():
    manager = JobManager()
    job = manager.create('scan', 't', 'x', {})
    manager.update(job['id'], message='one', force=True)
    manager.update(job['id'], status=JOB_DONE, force=True)
    current = manager.get(job['id'])
    events = list(manager.stream(job['id'], last_event_id=1))
    assert any('one' in event for event in events)
    assert any(f"id: {current['event_seq']}" in event for event in events)
