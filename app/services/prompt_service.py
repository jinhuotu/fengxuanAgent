"""提示词模板服务。"""

from sqlalchemy.orm import Session
from app.models.prompt import PromptTemplate


def render_prompt(template: PromptTemplate | None, question: str, context: str = "") -> str:
    if not template:
        return question
    return (
        template.template.replace("{{question}}", question).replace("{{context}}", context)
    )


def create_prompt(db: Session, user_id: int, name: str, template: str, tags: str | None) -> PromptTemplate:
    entity = PromptTemplate(user_id=user_id, name=name, template=template, tags=tags)
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return entity
