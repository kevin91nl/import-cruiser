from worker_core.runtime import run_job
from sdk_modules.products.service import enrich_product


def handle_event(name: str) -> str:
    event = run_job(name)
    return enrich_product(event.name)
