from backend import diagnostics

def test_diagnostics_reports_writable_state():
    result = diagnostics.check()
    assert 'ok' in result
    assert result['checks']['state_writable'] is True
