"""模型配置与适配器路由服务。"""

from sqlalchemy.orm import Session
from app.integrations.model_adapters import (
    ModelAdapter,
    OllamaAdapter,
    OpenAICompatibleAdapter,
    RemoteEndpointAdapter,
)
from app.models.model_config import ModelConfig
from app.utils.crypto import decrypt_text, encrypt_text


def create_model_config(db: Session, user_id: int, payload: dict) -> ModelConfig:
    config = ModelConfig(
        user_id=user_id,
        model_type=payload["model_type"],
        model_name=payload["model_name"],
        api_key_encrypted=encrypt_text(payload.get("api_key")),
        base_url=payload.get("base_url"),
        model_params=payload.get("model_params", {}),
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def get_model_adapter(model_config: ModelConfig) -> ModelAdapter:
    params = model_config.model_params or {}
    temperature = float(params.get("temperature", 0.7))
    api_key = decrypt_text(model_config.api_key_encrypted)
    if model_config.model_type == "api":
        return OpenAICompatibleAdapter(
            model_config.model_name,
            model_config.base_url,
            api_key,
            temperature,
            model_params=params,
        )
    if model_config.model_type == "ollama":
        return OllamaAdapter(model_config.model_name, model_config.base_url, temperature)
    return RemoteEndpointAdapter(
        model_config.model_name,
        model_config.base_url,
        api_key,
        temperature,
        model_params=params,
    )
