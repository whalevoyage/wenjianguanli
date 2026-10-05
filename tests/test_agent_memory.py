from backend import database
from backend.services import agent


def test_agent_memory_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.config, 'DATABASE_FILE', tmp_path / 'filepilot.db')
    agent.save_memory('chat-1', 'user', '扫描 C:\\Temp')
    agent.save_memory('chat-1', 'assistant', '已生成扫描计划')
    messages = agent.load_memory('chat-1')
    assert [item['role'] for item in messages] == ['user', 'assistant']
    assert messages[0]['content'] == '扫描 C:\\Temp'


def test_chat_response_contains_conversation_id(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.config, 'DATABASE_FILE', tmp_path / 'filepilot.db')
    result = agent.chat('现在磁盘和任务怎么样', {'conversation_id': 'chat-2'})
    assert result['conversation_id'] == 'chat-2'
    assert result['tokens'] == 0
