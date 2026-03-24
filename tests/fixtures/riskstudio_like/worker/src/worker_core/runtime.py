from sdk_core.session import get_session
from sdk_modules.events.models import Event


def run_job(name: str) -> Event:
    session = get_session()
    return Event(name=name, session=session)
