from backend import database

def test_sqlite_logs_and_trash(tmp_path):
    db = tmp_path / 'filepilot.db'
    database.append_log(db, {'time':'now','type':'scan','result':'成功','path':'x','note':'n','job_id':'1'}, 10)
    assert database.read_logs(db, 10)[0]['job_id'] == '1'
    database.replace_trash(db, [{'id':'a','original':'x'}])
    assert database.read_trash(db)[0]['id'] == 'a'
