from app.services.prompt_service import render_prompt
from app.models.prompt import PromptTemplate


def test_render_prompt_with_placeholders():
    template = PromptTemplate(user_id=1, name="demo", template="Q: {{question}}\nC: {{context}}", tags=None)
    text = render_prompt(template, "你好", "上下文")
    assert "你好" in text
    assert "上下文" in text
