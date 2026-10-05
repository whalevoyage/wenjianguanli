from fastapi.testclient import TestClient
from backend.main import app

def test_agent_query_endpoint_is_read_only():
    response = TestClient(app).post('/api/agent/query', json={'message': '现在磁盘和任务怎么样'})
    assert response.status_code == 200
    assert response.json()['source'] == 'local'
    assert response.json()['tokens'] == 0
