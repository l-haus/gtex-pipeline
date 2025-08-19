import os
import tempfile
from datetime import timedelta
from pendulum import datetime
from airflow.decorators import dag, task
from airflow.exceptions import AirflowSkipException
from google.cloud.storage import Client as GCSClient

GCS_BUCKET = os.getenv("GCS_BUCKET")
PREFIX_TMP = "tmp/"
MARKERS_PREFIX = "processed/markers/"

def _gcs_client():
    return GCSClient()

@dag(
    dag_id="rnaseq_mvp",
    start_date=datetime(2024, 1, 1, tz="UTC"),
    schedule=None,
    catchup=False,
    tags=["dev"],
)
def rnaseq_mvp():
    @task(retries=3, retry_delay=timedelta(minutes=2))
    def list_tmp_keys(bucket: str, prefix: str) -> list[str]:
        client = _gcs_client()
        blobs = client.list_blobs(bucket, prefix=prefix)
        out = [b.name for b in blobs if b.name != prefix]
        return out

    @task(retries=2, retry_delay=timedelta(minutes=1))
    def ensure_marker(key: str) -> str:
        """Create a processed marker for idempotency; skip if exists."""
        client = _gcs_client()
        bucket = client.bucket(GCS_BUCKET)
        marker_name = f"{MARKERS_PREFIX}{key.replace('/', '_')}.done"
        blob = bucket.blob(marker_name)
        if blob.exists():
            raise AirflowSkipException(f"already processed: {key}")
        blob.upload_from_string("ok")
        return marker_name

    @task
    def log_to_wandb(keys: list[str]) -> None:
        import wandb
        api_key = os.getenv("WANDB_API_KEY")
        if not api_key:
            print("WANDB_API_KEY missing; skipping W&B")
            return
        wandb.login(key=api_key)
        run = wandb.init(project="gtex-rnaseq", job_type="dev",
                         config={"bucket": GCS_BUCKET, "prefix": PREFIX_TMP, "n_keys": len(keys)})
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
            f.write("\n".join(keys or ["<no_keys>"]))
            path = f.name
        art = wandb.Artifact("tmp-list", type="report")
        art.add_file(path, name="list.txt")
        run.log_artifact(art)
        run.finish()

    keys = list_tmp_keys(GCS_BUCKET, PREFIX_TMP)
    ensure_marker.expand(key=keys)
    log_to_wandb(keys)

rnaseq_mvp()
