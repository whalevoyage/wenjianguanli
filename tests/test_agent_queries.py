from backend.services import agent
from backend.services import agent_intent
import pytest


@pytest.mark.parametrize("message", ["腾出20GB空间", "释放 30 GB 的容量", "腾     空间", "清出 1.5TB 地方"])
def test_space_goal_normalization_preserves_supported_phrases(message):
    assert agent_intent.canonical(message) == "清理垃圾"
    assert agent_intent.SPACE_INTENT_RE.search(message)


@pytest.mark.parametrize("message, expected", [
    ("为何这个文件是垃圾", True),
    ("垃圾的原因", False),
    ("为何\n垃圾", False),
    ("为何\n为何是垃圾", True),
])
def test_rule_explanation_requires_question_then_rule_on_same_line(message, expected):
    assert agent._asks_for_rule_explanation(message) is expected


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


def test_backup_without_paths_asks_for_source_and_destination():
    result = agent.parse_local('帮我备份一下')
    assert result['type'] == 'clarify'
    assert result['missing'] == ['source_dir', 'dest_dir']


def test_migration_without_destination_asks_for_destination():
    result = agent.parse_local('把 D 盘搬走')
    assert result['type'] == 'clarify'
    assert 'target_dir' in result['missing']


def test_agent_can_open_other_atlas_tabs():
    result = agent.parse_local('打开任务中心')
    assert result['action'] == 'open_tab'
    assert result['params']['route'] == '/jobs'


def test_agent_open_tab_hands_off_the_directory():
    # 「打开扫描分析 D:\Downloads」要带着目录过去（目标页预填），不是开个空白页
    result = agent.parse_local('打开扫描分析 D:\\Downloads')
    assert result['action'] == 'open_tab'
    assert result['params']['route'] == '/scan'
    assert result['params']['path'] == 'D:\\Downloads'


def test_agent_open_tab_without_directory_has_no_focus():
    result = agent.parse_local('打开扫描分析')
    assert result['action'] == 'open_tab'
    assert 'path' not in result['params']


def test_agent_can_start_project_radar():
    result = agent.parse_local('扫描项目 D 盘')
    assert result['action'] == 'projects_scan'


def test_chat_returns_structured_clarification_without_model_call():
    result = agent.chat('帮我备份一下', {'conversation_id': 'clarify-test'})
    assert result['type'] == 'clarify'
    assert result['source'] == 'local'
    assert result['tokens'] == 0


def test_local_query_can_read_jobs_without_model_call():
    result = agent.query_local('任务中心最近有什么任务')
    assert result['type'] == 'jobs'
    assert result['tokens'] == 0


def test_local_query_can_read_trash_without_model_call():
    result = agent.query_local('回收站里有什么')
    assert result['type'] == 'trash'
    assert result['tokens'] == 0


def test_local_query_can_read_logs_without_model_call():
    result = agent.query_local('查看操作日志')
    assert result['type'] == 'logs'
    assert result['tokens'] == 0
