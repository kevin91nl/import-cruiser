import risk_like.config.settings


def is_ready() -> bool:
    return bool(risk_like.config.settings.DB_DSN)
