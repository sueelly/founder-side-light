import pytest
from harness.finalize import prepare_final
from harness.storage import Session
from test_terminal_contract import Provider

def test_final_ranking_waits_for_four_actual_feedbacks(session):
    p=Provider()
    with pytest.raises(ValueError, match="네 명"): prepare_final(session,p)
    assert not p.calls

def test_ranking_output_crash_recovers_committed_result(completed, monkeypatch):
    import harness.storage as storage
    real=storage.atomic_write
    def crash(path,text):
        if path.name=='ranking.md': raise OSError('합성 결과 출력 실패')
        return real(path,text)
    p=Provider()
    monkeypatch.setattr(storage,'atomic_write',crash)
    with pytest.raises(OSError): prepare_final(completed,p)
    monkeypatch.setattr(storage,'atomic_write',real)
    resumed=Session(completed.directory)
    result=prepare_final(resumed,p)
    assert len(p.calls)==1
    assert result==resumed.state['final_result']
    assert (resumed.directory/'ranking.md').exists()

def test_confirmed_feedback_corruption_blocks_ranking(completed):
    iv=completed.state['interviews'][0]
    (completed.interview_dir(iv)/'feedback.confirmed.md').write_text('modified')
    with pytest.raises(ValueError, match="변경|손상"): prepare_final(completed,Provider())
