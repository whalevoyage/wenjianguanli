from pydantic import ValidationError
from backend.main import AgentRequest, RecipeRequest, CleanItem
import pytest
from fastapi.testclient import TestClient
from backend import main


def test_api_missing_job_does_not_serialize_exception_context(monkeypatch):
    def missing(_job_id):
        raise KeyError("internal sentinel must stay private")

    monkeypatch.setattr(main.manager, "rollback", missing)
    with TestClient(main.app) as client:
        response = client.post("/api/jobs/missing/rollback")
    assert response.status_code == 404
    assert response.json() == {"detail": "任务不存在"}
    assert "Traceback" not in response.text
    assert "internal sentinel" not in response.text


def test_agent_message_has_maximum_length():
    with pytest.raises(ValidationError):
        AgentRequest(message='x' * 4001)


def test_recipe_requires_bounded_fields():
    with pytest.raises(ValidationError):
        RecipeRequest(name='', message='ok')


def test_clean_item_rejects_negative_size():
    with pytest.raises(ValidationError):
        CleanItem(path='x', size=-1)
