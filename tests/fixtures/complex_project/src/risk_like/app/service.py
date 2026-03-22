import risk_like.domain.model
import risk_like.infra.repo

SERVICE_NAME = "risk-service"


def compose_payload() -> dict[str, object]:
    return {
        "service": SERVICE_NAME,
        "model": risk_like.domain.model.MODEL,
        "records": risk_like.infra.repo.fetch_records(),
    }
