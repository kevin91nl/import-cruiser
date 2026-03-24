from sdk_core.session import get_session


def enrich_product(name: str) -> str:
    session = get_session()
    return f"{name}:{session['api_url']}"
