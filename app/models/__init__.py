"""模型包。

这一句 import 是关键：只有被 import 过的模型类才会登记进 Base.metadata，
alembic 的 autogenerate 才看得见它。以后每新增一个模型文件，这里补一行。
"""

from app.models.rbac import Permission, Role, RolePermission, UserRole  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.chat import ChatGenerationGate, ChatMessage, ChatSession  # noqa: F401
from app.models.knowledge import KnowledgeBase  # noqa: F401
