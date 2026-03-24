from sdk_core.session import get_session


class Event:
    def __init__(self, name: str, session: dict[str, str] | None = None) -> None:
        self.name = name
        self.session = session or get_session()
