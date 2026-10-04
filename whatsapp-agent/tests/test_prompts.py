from app.prompts import build_system_prompt, load_knowledge_base


def test_build_system_prompt():
    prompt = build_system_prompt("Al-Madina Store", "Test Business Policy")
    assert "Al-Madina Store" in prompt
    assert "Test Business Policy" in prompt
    assert "Language Matching" in prompt
    assert "Never Invent Prices or Stock" in prompt
    assert "Prompt Injection Defense" in prompt
