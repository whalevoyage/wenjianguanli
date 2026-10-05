from pydantic import ValidationError
from backend.main import AgentRequest, RecipeRequest, CleanItem
import pytest


def test_agent_message_has_maximum_length():
    with pytest.raises(ValidationError):
        AgentRequest(message='x' * 4001)


def test_recipe_requires_bounded_fields():
    with pytest.raises(ValidationError):
        RecipeRequest(name='', message='ok')


def test_clean_item_rejects_negative_size():
    with pytest.raises(ValidationError):
        CleanItem(path='x', size=-1)
