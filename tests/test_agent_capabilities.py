"""2026-10-13「Agent 能力放开」：新动作登记 / 本地规则识别 / 流水线与方案 / 风险等级。

放开范围：相似图片、大文件、全文检索、快照、备份核对、隔离区彻底删除，
以及编排放开（执行目标从 3 个扩到全部）。安全模型不变：plan-first + 二次确认 +
删除先进隔离区 + 只读档拒发；trash_purge 是唯一物理删除口，风险标高。
"""
from backend.services import agent, agent_intent, agent_planner, agent_task, agent_tools
from backend.services import clean as clean_svc


# ---- 一、新动作确实登记进来了 ----

def test_new_actions_are_registered():
    # large_scan 是"动作"但不是一个独立工具——它映射到 large_files 查询工具
    for action in ("similar_scan", "similar_clean", "large_clean",
                   "search_index", "trash_purge"):
        assert action in agent_tools.TOOL_REGISTRY, action
    for action in ("similar_scan", "similar_clean", "large_scan", "large_clean",
                   "search_index", "trash_purge"):
        assert action in agent_tools.ACTION_PARAMS, action


def test_new_write_actions_are_marked_write():
    for action in ("similar_clean", "large_clean", "trash_purge"):
        assert action in agent_tools.WRITE_ACTIONS, action
    # 查询/建索引不算写：不动用户文件
    for action in ("similar_scan", "large_scan", "search_index"):
        assert action not in agent_tools.WRITE_ACTIONS, action


def test_new_query_tools_dispatch(tmp_path, monkeypatch):
    # 用干净的 DB：共享数据目录可能被别的用例建过索引，那样断言就不成立了
    monkeypatch.setattr(agent_tools.config, "DATABASE_FILE", tmp_path / "fresh.db")
    # 没建过索引 → 如实说"还没有建立任何索引"，不假装搜过了
    out = agent_tools.run_tool("search", {"keyword": "hello"})
    assert out.get("ok") is False
    assert "索引" in (out.get("note") or "")
    # 模型不能凭空发明工具
    assert agent_tools.run_tool("not_a_tool", {}).get("ok") is False


# ---- 二、本地规则能认出新说法 ----

def test_local_rules_recognize_new_intents():
    assert agent.parse_local("找出 D:\\Downloads 里的相似图片")["action"] == "similar_scan"
    assert agent.parse_local("清理 D:\\Downloads 的相似图片")["action"] == "similar_clean"
    assert agent.parse_local("D 盘有哪些大文件")["action"] == "large_scan"
    assert agent.parse_local("删掉 D 盘上的大文件")["action"] == "large_clean"
    assert agent.parse_local("清空隔离区")["action"] == "trash_purge"
    assert agent.parse_local("给 D:\\Downloads 建立索引")["action"] == "search_index"


# ---- 三、流水线与方案 ----

def test_pipelines_for_new_actions():
    similar = agent_planner.plan_steps({"action": "similar_clean", "scope": {"path": "D:\\X"}})
    assert [s["tool"] for s in similar] == ["drive_status", "similar_scan", "", "estimate_task", ""]
    assert similar[-1]["kind"] == "confirm"

    purge = agent_planner.plan_steps({"action": "trash_purge"})
    assert [s["tool"] for s in purge] == ["trash_contents", "", "estimate_task", ""]

    # large_scan 是"查一下"：单步 + 查询 kind，不套 Job
    single = agent_planner.plan_steps({"action": "large_scan", "scope": {"path": "D:\\X"}})
    assert len(single) == 1
    assert single[0]["kind"] == "query"
    assert single[0]["tool"] == "large_files"


def test_proposal_builds_items_for_similar():
    intent = {"action": "similar_clean", "scope": {"path": "D:\\X"}, "constraints": {}}
    steps = {"similar": {"result": {"groups": [{"keep": "a.png", "files": [
        {"path": "a.png", "size": 100, "mtime": "2026-01-01"},
        {"path": "b.png", "size": 200, "mtime": "2026-01-02"},
    ]}]}}}
    proposal = agent_planner.build_proposal(intent, steps)
    assert [i["path"] for i in proposal["items"]] == ["b.png"]     # 只保留 a.png
    assert proposal["items"][0]["rule"] == "similar"
    # 相似图是用户数据 → 记 confirm 而不是 safe
    assert proposal["items"][0]["level"] == "confirm"
    assert proposal["freed_bytes"] == 200
    assert proposal["executable"] is True


def test_proposal_builds_items_for_large():
    intent = {"action": "large_clean", "scope": {"path": "D:\\X"}, "constraints": {}}
    steps = {"large": {"data": {"rows": [{"path": "big.bin", "size": 500}]}}}
    proposal = agent_planner.build_proposal(intent, steps)
    assert [i["path"] for i in proposal["items"]] == ["big.bin"]
    assert proposal["items"][0]["rule"] == "large"
    assert proposal["executable"] is True


def test_proposal_carries_trash_ids_for_purge():
    intent = {"action": "trash_purge", "scope": {}, "constraints": {}}
    steps = {"trash": {"data": {"entries": [{"id": "t1", "path": "x.txt", "size": 10}]}}}
    proposal = agent_planner.build_proposal(intent, steps)
    assert [i["id"] for i in proposal["items"]] == ["t1"]
    assert proposal["items"][0]["level"] == "danger"
    assert proposal["executable"] is True


def test_empty_results_are_not_executable():
    intent = {"action": "large_clean", "scope": {"path": "D:\\X"}, "constraints": {}}
    proposal = agent_planner.build_proposal(intent, {"large": {"data": {"rows": []}}})
    assert proposal["items"] == []
    assert proposal["executable"] is False
    assert proposal["blocked"]


# ---- 四、风险等级本地算 ----

def test_risk_levels_for_new_actions():
    # 彻底删除隔离区 = 唯一物理删除口、不可回滚 → high
    level, why = agent_intent.compute_risk("trash_purge", {}, {}, {"file_count": 3})
    assert level == "high" and "不可恢复" in why
    # 相似图 / 大文件动的是用户数据 → medium 且措辞说清"不是垃圾"
    level, why = agent_intent.compute_risk("similar_clean", {"path": "D:\\X"}, {}, {"file_count": 3})
    assert level == "medium" and "不是垃圾" in why
    level, _ = agent_intent.compute_risk("large_clean", {"path": "D:\\X"}, {}, {"file_count": 3})
    assert level == "medium"
    # 只读动作一律 low
    assert agent_intent.compute_risk("similar_scan", {}, {}, {})[0] == "low"
    assert agent_intent.compute_risk("search_index", {}, {}, {})[0] == "low"


def test_execution_steps_for_purge_is_high_risk():
    steps = agent_planner.execution_steps({"action": "trash_purge"}, {"action": "trash_purge"})
    assert steps[0]["tool"] == "trash_purge"
    assert steps[0]["risk"] == "high"
    assert "不可恢复" in steps[0]["title"]


# ---- 五、编排放开：自由多步 + 多执行目标 ----

def test_normalize_steps_drops_unknown_tools():
    steps = agent_planner.normalize_steps(
        [{"tool": "drive_status"}, {"tool": "invented_by_model"}, {"tool": "junk_scan"}],
        {"scope": {"path": "D://X"}},
    )
    assert [s["tool"] for s in steps] == ["drive_status", "junk_scan"]


def test_normalize_steps_forces_a_confirm_before_the_first_write():
    steps = agent_planner.normalize_steps(
        [{"tool": "junk_scan"}, {"tool": "junk_clean"}],
        {"scope": {"path": "D://X"}},
    )
    kinds = [s["kind"] for s in steps]
    assert kinds == ["job", "confirm", "job"]        # confirm 夹在扫描和清理之间
    assert steps[1]["id"] == "confirm"


def test_plan_steps_uses_explicit_steps_when_present():
    intent = {"action": "free", "scope": {"path": "D://X"},
              "steps": [{"tool": "drive_status"}, {"tool": "similar_scan"}]}
    steps = agent_planner.plan_steps(intent)
    assert [s["tool"] for s in steps] == ["drive_status", "similar_scan"]


def test_proposal_aggregates_multiple_goals():
    intent = {"action": "junk_clean", "actions": ["junk_clean", "large_clean"],
              "scope": {"path": "D://X"}, "constraints": {}}
    steps = {
        "junk": {"result": {"levels": [{"level": "safe", "items": [
            {"path": "a.tmp", "size": 10, "rule": "temp"}]}]}},
        "large": {"data": {"rows": [{"path": "big.bin", "size": 500}]}},
    }
    proposal = agent_planner.build_proposal(intent, steps)
    rules = sorted(i["rule"] for i in proposal["items"])
    assert rules == ["large", "temp"]
    assert proposal["freed_bytes"] == 510             # 10 + 500，两个目标累加
    assert proposal["item_count"] == 2
    assert proposal["executable"] is True


def test_execution_steps_emit_one_per_goal_and_a_single_report():
    steps = agent_planner.execution_steps(
        {"actions": ["junk_clean", "large_clean"]}, {"action": "junk_clean"})
    assert [s["tool"] for s in steps if s["id"] == "execute"] == ["clean", "large_clean"]
    assert [s["id"] for s in steps].count("report") == 1      # 汇报只加一次


# ---- 六、function calling（说人话就能识别，不必命中固定话术）----

def test_tool_schemas_cover_every_registered_tool():
    schemas = agent_tools.tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert set(agent_tools.TOOL_REGISTRY) <= names
    assert set(agent_tools.QUERY_TOOLS) <= names
    assert set(agent_tools.HELPER_TOOLS) <= names
    for item in schemas:
        assert item["type"] == "function"
        assert item["function"]["description"]          # 没有说明模型不会用
    junk = next(s for s in schemas if s["function"]["name"] == "junk_clean")
    assert "path" in junk["function"]["parameters"]["required"]


def test_readonly_tool_calls_answer_directly(monkeypatch):
    monkeypatch.setattr(agent, "_call_deepseek_tools", lambda m, c: {
        "tool_calls": [{"name": "trash_contents", "arguments": {"limit": 5}}], "tokens": 42})
    plan, answer = agent.plan_from_tools("隔离区里现在有啥", {})
    assert plan is None
    assert answer["tokens"] == 42 and answer["type"] == "query"
    assert "trash_contents" in answer["data"]        # 当场跑掉了，不用计划卡


def test_write_tool_calls_produce_a_confirmed_plan(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "_call_deepseek_tools", lambda m, c: {
        "tool_calls": [
            {"name": "junk_scan", "arguments": {"path": str(tmp_path)}},
            {"name": "junk_clean", "arguments": {"path": str(tmp_path)}},
        ], "tokens": 88})
    plan, answer = agent.plan_from_tools("把那儿没用的清一下", {})
    assert answer is None
    assert plan["action"] == "junk_clean"
    assert plan["write"] is True and plan["needs_confirm"] is True
    # confirm 是**本地**插进去的，模型绕不过
    assert "confirm" in [s["kind"] for s in agent.steps_for(plan)]


def test_invented_tools_are_discarded(monkeypatch):
    monkeypatch.setattr(agent, "_call_deepseek_tools", lambda m, c: {
        "tool_calls": [{"name": "rm_rf_everything", "arguments": {}}], "tokens": 5})
    plan, answer = agent.plan_from_tools("删掉所有文件", {})
    assert plan is None and answer is None          # 退回老路，不执行


def test_falls_back_to_the_json_plan_path(monkeypatch):
    monkeypatch.setattr(agent, "_call_deepseek_tools", lambda m, c: {"tool_calls": [], "tokens": 0})
    monkeypatch.setattr(agent, "_call_deepseek", lambda m, c: {
        "plan": {"action": "junk_clean", "reason": "老路", "params": {"path": "C://"}}, "tokens": 7})
    plan, answer, tokens = agent._deepseek_plan_or_answer("清垃圾", {})
    assert answer is None and plan["action"] == "junk_clean" and tokens == 7


# ---- 六、function calling（说人话就能识别，不必命中固定话术）----

def test_tool_schemas_cover_every_registered_tool():
    schemas = agent_tools.tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert set(agent_tools.TOOL_REGISTRY) <= names
    assert set(agent_tools.QUERY_TOOLS) <= names
    assert set(agent_tools.HELPER_TOOLS) <= names
    for item in schemas:
        assert item["type"] == "function"
        assert item["function"]["description"]          # 没有说明模型不会用
    junk = next(s for s in schemas if s["function"]["name"] == "junk_clean")
    assert "path" in junk["function"]["parameters"]["required"]


def test_readonly_tool_calls_answer_directly(monkeypatch):
    monkeypatch.setattr(agent, "_call_deepseek_tools", lambda m, c: {
        "tool_calls": [{"name": "trash_contents", "arguments": {"limit": 5}}], "tokens": 42})
    plan, answer = agent.plan_from_tools("隔离区里现在有啥", {})
    assert plan is None
    assert answer["tokens"] == 42 and answer["type"] == "query"
    assert "trash_contents" in answer["data"]        # 当场跑掉了，不用计划卡


def test_write_tool_calls_produce_a_confirmed_plan(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "_call_deepseek_tools", lambda m, c: {
        "tool_calls": [
            {"name": "junk_scan", "arguments": {"path": str(tmp_path)}},
            {"name": "junk_clean", "arguments": {"path": str(tmp_path)}},
        ], "tokens": 88})
    plan, answer = agent.plan_from_tools("把那儿没用的清一下", {})
    assert answer is None
    assert plan["action"] == "junk_clean"
    assert plan["write"] is True and plan["needs_confirm"] is True
    # confirm 是**本地**插进去的，模型绕不过
    assert "confirm" in [s["kind"] for s in agent.steps_for(plan)]


def test_invented_tools_are_discarded(monkeypatch):
    monkeypatch.setattr(agent, "_call_deepseek_tools", lambda m, c: {
        "tool_calls": [{"name": "rm_rf_everything", "arguments": {}}], "tokens": 5})
    plan, answer = agent.plan_from_tools("删掉所有文件", {})
    assert plan is None and answer is None          # 退回老路，不执行


def test_falls_back_to_the_json_plan_path(monkeypatch):
    monkeypatch.setattr(agent, "_call_deepseek_tools", lambda m, c: {"tool_calls": [], "tokens": 0})
    monkeypatch.setattr(agent, "_call_deepseek", lambda m, c: {
        "plan": {"action": "junk_clean", "reason": "老路", "params": {"path": "C:\\"}}, "tokens": 7})
    plan, answer, tokens = agent._deepseek_plan_or_answer("清垃圾", {})
    assert answer is None and plan["action"] == "junk_clean" and tokens == 7


# ---- 七、清理类「确认即永久删」：隔离后立刻清空同一批，不破铁律 ----

def test_execute_clean_isolates_then_purges_same_batch(monkeypatch):
    captured = {}
    calls = {"n": 0}

    def fake_run_clean(sub):
        captured["params"] = sub._params
        return {"mode": "isolate", "requested_count": 2, "cleaned_count": 2,
                "freed_size": 100, "freed_size_text": "100 B", "trash_dir": "X", "errors": []}

    def fake_list_trash():
        calls["n"] += 1
        # 第一次（隔离前）= 空；第二次（隔离后）= 刚进隔离区的两条
        if calls["n"] == 1:
            return {"entries": [], "count": 0}
        return {"entries": [{"id": "a1"}, {"id": "a2"}], "count": 2}

    purged_ids = []
    def fake_purge(ids):
        purged_ids.extend(ids)
        return {"purged_count": len(ids), "freed_size_text": "100 B", "errors": []}

    monkeypatch.setattr(clean_svc, "run_clean", fake_run_clean)
    monkeypatch.setattr(clean_svc, "list_trash", fake_list_trash)
    monkeypatch.setattr(clean_svc, "purge_trash", fake_purge)

    class _Sub:
        _params = None
    result = agent_task._execute_clean(_Sub(), [{"path": "x", "size": 50}], "isolate")

    assert captured["params"]["mode"] == "isolate"      # 仍先走隔离通道
    assert purged_ids == ["a1", "a2"]                  # 刚隔离的 2 条被立刻清空
    assert result["permanent"] is True                  # 对外标记：已彻底删除
    assert result["purged_count"] == 2
    assert result["cleaned_count"] == 2


def test_execute_clean_does_not_purge_pre_existing_trash(monkeypatch):
    # 隔离区里本来就有别的条目，不能被这次清理误清空
    calls = {"n": 0}

    def fake_list_trash():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"entries": [{"id": "old1"}], "count": 1}   # 隔离前已有旧条目
        return {"entries": [{"id": "old1"}, {"id": "new1"}], "count": 2}

    purged_ids = []
    monkeypatch.setattr(clean_svc, "run_clean", lambda sub: {
        "mode": "isolate", "requested_count": 1, "cleaned_count": 1,
        "freed_size": 10, "freed_size_text": "10 B", "trash_dir": "X", "errors": []})
    monkeypatch.setattr(clean_svc, "list_trash", fake_list_trash)
    monkeypatch.setattr(clean_svc, "purge_trash", lambda ids: purged_ids.extend(ids) or {"purged_count": len(ids), "errors": []})

    class _Sub:
        _params = None
    agent_task._execute_clean(_Sub(), [{"path": "y"}], "isolate")
    assert purged_ids == ["new1"]                        # 只清 new1，old1 不被误删


# ---- 七、清理类「确认即永久删」：隔离后立刻清空同一批，不破铁律 ----

def test_execute_clean_isolates_then_purges_same_batch(monkeypatch):
    captured = {}
    calls = {"n": 0}

    def fake_run_clean(sub):
        captured["params"] = sub._params
        return {"mode": "isolate", "requested_count": 2, "cleaned_count": 2,
                "freed_size": 100, "freed_size_text": "100 B", "trash_dir": "X", "errors": []}

    def fake_list_trash():
        calls["n"] += 1
        # 第一次（隔离前）= 空；第二次（隔离后）= 刚进隔离区的两条
        if calls["n"] == 1:
            return {"entries": [], "count": 0}
        return {"entries": [{"id": "a1"}, {"id": "a2"}], "count": 2}

    purged_ids = []
    def fake_purge(ids):
        purged_ids.extend(ids)
        return {"purged_count": len(ids), "freed_size_text": "100 B", "errors": []}

    monkeypatch.setattr(clean_svc, "run_clean", fake_run_clean)
    monkeypatch.setattr(clean_svc, "list_trash", fake_list_trash)
    monkeypatch.setattr(clean_svc, "purge_trash", fake_purge)

    class _Sub:
        _params = None
    result = agent_task._execute_clean(_Sub(), [{"path": "x", "size": 50}], "isolate")

    assert captured["params"]["mode"] == "isolate"      # 仍先走隔离通道
    assert purged_ids == ["a1", "a2"]                  # 刚隔离的 2 条被立刻清空
    assert result["permanent"] is True                  # 对外标记：已彻底删除
    assert result["purged_count"] == 2
    assert result["cleaned_count"] == 2


def test_execute_clean_does_not_purge_pre_existing_trash(monkeypatch):
    # 隔离区里本来就有别的条目，不能被这次清理误清空
    calls = {"n": 0}

    def fake_list_trash():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"entries": [{"id": "old1"}], "count": 1}   # 隔离前已有旧条目
        return {"entries": [{"id": "old1"}, {"id": "new1"}], "count": 2}

    purged_ids = []
    monkeypatch.setattr(clean_svc, "run_clean", lambda sub: {
        "mode": "isolate", "requested_count": 1, "cleaned_count": 1,
        "freed_size": 10, "freed_size_text": "10 B", "trash_dir": "X", "errors": []})
    monkeypatch.setattr(clean_svc, "list_trash", fake_list_trash)
    monkeypatch.setattr(clean_svc, "purge_trash", lambda ids: purged_ids.extend(ids) or {"purged_count": len(ids), "errors": []})

    class _Sub:
        _params = None
    agent_task._execute_clean(_Sub(), [{"path": "y"}], "isolate")
    assert purged_ids == ["new1"]                        # 只清 new1，old1 不被误删
