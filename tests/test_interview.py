import pytest
from test_terminal_contract import Clock, Provider, Held, runner, feedback, finish
from harness.storage import Session, file_lock

def test_two_writers_are_rejected(session):
    with file_lock(session.directory / '.lock'):
        with pytest.raises(ValueError, match="다른 하네스"):
            with file_lock(session.directory / '.lock'): pass

def test_incomplete_turn_on_restart_waits_for_retry(session):
    c, held, p = Clock(), Held(), Provider()
    r = runner(session, c, p, held)
    r.step(); r.shutdown()
    again = runner(Session(session.directory), c, p, confirm=False)
    again.step()
    assert not p.calls
    again.retry_generation(); again.step(); again.step()
    assert len(p.calls) == 1
    again.shutdown()

def test_wrap_cancels_old_interviewer_question(session):
    c = Clock()
    finish(session, c); feedback(session); session.accept_feedback('unchanged')
    held, p = Held(), Provider()
    r = runner(session, c, p, held)
    r.step()
    held.futures[0].set_result({'text':'자기소개 답변'})
    r.step()
    old = held.futures[1]
    c.value = session.current()['deadline_at'] - 120
    r.step()
    old.set_result({'text':'지난 본 질문'})
    r.step()
    assert all(m['text'] != '지난 본 질문' for m in session.current()['messages'])
    assert p.cancelled
    r.shutdown()

def test_clock_reversal_does_not_grant_more_time(session):
    wall, mono = Clock(), Clock()
    from harness.interview import Interview
    from harness.storage import sha
    session.confirm_insights(sha(session.insights()), at=wall())
    r = Interview(session, Provider(), now=wall, monotonic=mono, executor=Held())
    r.step()
    wall.advance(-120)
    mono.advance(10)
    with pytest.raises(ValueError, match="시계"):
        r.step()
    assert r.remaining() <= 1790
    r.shutdown()

def test_explicit_record_repair_keeps_canonical_dialogue(session):
    c = Clock()
    r=runner(session,c); r.step(); r.step(); r.shutdown()
    path=session.interview_dir(session.current()) / 'transcript.md'
    original=path.read_text()
    path.write_text('edited')
    with pytest.raises(ValueError): Session(session.directory)
    fixed=Session(session.directory, repair=True)
    assert path.read_text()==original
    assert fixed.current()['messages']==session.current()['messages']
