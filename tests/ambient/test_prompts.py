from mypalclara.ambient import prompts


def test_reflection_prompt_is_private_and_journal_framed():
    p = prompts.REFLECTION_PROMPT.lower()
    assert "not sent" in p or "no one" in p  # framed as private
    assert "journal" in p


def test_surface_gate_prompt_demands_json_and_high_bar():
    p = prompts.SURFACE_GATE_PROMPT
    assert '"decision"' in p
    assert "nothing" in p and "queue" in p and "urgent" in p
    assert "json" in p.lower()
