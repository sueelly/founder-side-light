import pytest
from test_terminal_contract import Clock, Provider, Immediate, Held, make_session, complete

@pytest.fixture
def session(tmp_path):
    return make_session(tmp_path)

@pytest.fixture
def completed(session):
    return complete(session, Clock())
