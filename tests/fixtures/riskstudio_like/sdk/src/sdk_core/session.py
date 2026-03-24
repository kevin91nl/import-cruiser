from sdk_core.config import get_api_url


def get_session() -> dict[str, str]:
    return {"api_url": get_api_url()}
