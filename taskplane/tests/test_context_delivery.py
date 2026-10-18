"""Bounded command output transport; no attention or workflow authority."""
import pytest
from taskplane.context_delivery import CommandOutput

def test_command_output_waits_for_terminal_and_keeps_partial_chunks():
    collector = CommandOutput()
    assert collector.feed('{"remaining_') is None
    assert collector.feed('required":0}') is None
    response = collector.feed('', exit_code=0)
    assert response == {'remaining_required': 0}

def test_command_output_checks_combined_budget_and_terminal_errors():
    collector = CommandOutput(1024)
    collector.feed('x' * 700)
    with pytest.raises(ValueError, match='Combined'):
        collector.feed('y' * 400, exit_code=0)
    with pytest.raises(ValueError, match='failed'):
        CommandOutput().feed('{}', exit_code=2)
