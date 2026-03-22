import risk_like.infra.db
import risk_like.shared.types


def fetch_records() -> list[risk_like.shared.types.DomainModel]:
    if risk_like.infra.db.is_ready():
        return [risk_like.shared.types.DomainModel(name="record")]
    return []
