import logging

from celery import shared_task

from apps.datasets.exceptions import DatasetIngestionError
from apps.datasets.models import Dataset
from apps.datasets.services.ingestion import ingest_csv_file, ingest_xlsx_file
from apps.datasets.services.webhooks import send_webhook

logger = logging.getLogger(__name__)


@shared_task
def ingest_dataset_file(dataset_id: int) -> None:
    """Runs in a Celery worker, independent of the request that queued it.

    ingest_csv_file/ingest_xlsx_file already record a FAILED status + reason on the
    dataset for any DatasetIngestionError, so there's nothing further to do with that
    exception here — it's expected input-data failure, not a task/worker failure.
    """
    try:
        dataset = Dataset.objects.get(pk=dataset_id)
    except Dataset.DoesNotExist:
        logger.warning("ingest_dataset_file: no Dataset with id %s (deleted?)", dataset_id)
        return

    is_xlsx = dataset.source_file.name.lower().endswith(".xlsx")
    try:
        if is_xlsx:
            ingest_xlsx_file(dataset, dataset.source_file, sheet_name=dataset.sheet_name or None)
        else:
            ingest_csv_file(dataset, dataset.source_file)
    except DatasetIngestionError:
        logger.info("Ingestion failed for dataset %s: %s", dataset_id, dataset.failure_reason)

    if dataset.webhook_url:
        send_webhook(dataset)
