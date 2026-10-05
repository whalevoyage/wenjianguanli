from fastapi.testclient import TestClient
from backend.main import app

def test_agent_memory_api_round_trip():
    client = TestClient(app)
    response = client.post('/api/agent/chat', json={'message': '现在磁盘和任务怎么样', 'context': {'conversation_id': 'api-test'}})
    assert response.status_code == 200
    memory = client.get('/api/agent/memory/api-test')
    assert memory.status_code == 200
    assert len(memory.json()['messages']) >= 2
    cleared = client.delete('/api/agent/memory/api-test')
    assert cleared.status_code == 200
    assert client.get('/api/agent/memory/api-test').json()['messages'] == []
