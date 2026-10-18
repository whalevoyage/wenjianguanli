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


def test_touched_empty_conversation_appears_in_list(tmp_path, monkeypatch):
    # 「新建任务」后空会话也落库：切走再回来，历史列表里得有它
    monkeypatch.setattr(agent.config, 'DATABASE_FILE', tmp_path / 'filepilot.db')
    agent.touch_conversation('chat-empty')
    rows = agent.list_conversations()
    assert [item['conversation_id'] for item in rows] == ['chat-empty']
    assert rows[0]['count'] == 0
    assert rows[0]['title'] == '新任务'      # 没有首条消息 → 回退标题


def test_touch_does_not_clobber_custom_title(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.config, 'DATABASE_FILE', tmp_path / 'filepilot.db')
    agent.save_memory('chat-3', 'user', '扫描 C:\\Temp')
    agent.set_conversation_title('chat-3', '我起的名字')
    agent.touch_conversation('chat-3')
    rows = {item['conversation_id']: item for item in agent.list_conversations()}
    assert rows['chat-3']['title'] == '我起的名字'


def test_deleting_empty_conversation_removes_it_from_list(tmp_path, monkeypatch):
    # 只 touch 过的空会话在 agent_memory 里没有行，删它必须连 meta 一起清，
    # 否则会永远赖在列表里删不掉
    monkeypatch.setattr(agent.config, 'DATABASE_FILE', tmp_path / 'filepilot.db')
    agent.touch_conversation('chat-empty')
    agent.clear_memory('chat-empty')
    assert agent.list_conversations() == []
