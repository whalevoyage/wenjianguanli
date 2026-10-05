from backend.services import agent


def test_local_query_overview_returns_zero_token_result():
    result = agent.query_local('现在磁盘和任务怎么样')
    assert result['source'] == 'local'
    assert result['tokens'] == 0
    assert result['type'] == 'overview'
    assert 'data' in result


def test_explain_junk_rule_returns_reason():
    result = agent.query_local('为什么这个文件被判定为垃圾：C:\\Temp\\demo.tmp')
    assert result['source'] == 'local'
    assert result['type'] == 'explain'
    assert result['explanation']['rule_id'] == 'temp'
    assert '临时' in result['explanation']['reason']


def test_weather_question_is_not_misclassified_as_local_overview():
    result = agent.query_local('今天天气怎么样')
    assert result is None


def test_generic_junk_cleanup_starts_with_scan():
    result = agent.parse_local('帮我清理 C 盘垃圾文件')
    assert result['action'] == 'junk_scan'


def test_explicit_immediate_cleanup_can_create_write_plan():
    result = agent.parse_local('立即清理 C 盘垃圾文件')
    assert result['action'] == 'junk_clean'
