from datetime import datetime, timezone, timedelta
from backend import database


def test_schedule_round_trip(tmp_path):
    db = tmp_path / 'filepilot.db'
    schedule = {
        'id': 's1', 'name': '每周扫描', 'kind': 'scan', 'params': {'path': 'C:\\Temp'},
        'enabled': True, 'next_run_at': '2026-10-03T10:00:00+08:00'
    }
    database.upsert_schedule(db, schedule)
    result = database.list_schedules(db)
    assert result[0]['id'] == 's1'
    assert result[0]['params']['path'] == 'C:\\Temp'


def test_schedule_delete(tmp_path):
    db = tmp_path / 'filepilot.db'
    database.upsert_schedule(db, {'id':'s1','name':'x','kind':'scan','params':{},'enabled':True,'next_run_at':'x'})
    database.delete_schedule(db, 's1')
    assert database.list_schedules(db) == []
