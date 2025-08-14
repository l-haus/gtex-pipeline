from datetime import timedelta
from pendulum import datetime
from airflow.decorators import dag, task
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.exceptions import AirflowSkipException
import os, tempfile

AWS_CONN_ID = "minio_s3"   # or "MINIO_S3" if you set via env var
BUCKET_RAW = "rna-raw"
PREFIX_TMP = "tmp/"
BUCKET_PROCESSED = "rna-processed"

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
        hook = S3Hook(aws_conn_id=AWS_CONN_ID)
        keys = hook.list_keys(bucket_name=bucket, prefix=prefix) or []
        return keys

    @task(retries=2, retry_delay=timedelta(minutes=1))
    def ensure_marker(key: str) -> str:
        """Create a processed marker for idempotency; skip if exists."""
        hook = S3Hook(aws_conn_id=AWS_CONN_ID)
        marker = f"markers/{key.replace('/', '_')}.done"
        if hook.check_for_key(marker, bucket_name=BUCKET_PROCESSED):
            raise AirflowSkipException(f"already processed: {key}")
        hook.load_string("ok", key=marker, bucket_name=BUCKET_PROCESSED, replace=True)
        return marker

    @task
    def log_to_wandb(keys: list[str]) -> None:
        import wandb
        api_key = os.getenv("WANDB_API_KEY")
        if not api_key:
            print("WANDB_API_KEY missing; skipping W&B")
            return
        wandb.login(key=api_key)
        run = wandb.init(project="gtex-rnaseq", job_type="dev",
                         config={"bucket": BUCKET_RAW, "prefix": PREFIX_TMP, "n_keys": len(keys)})
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
            f.write("\n".join(keys or ["<no_keys>"]))
            path = f.name
        art = wandb.Artifact("tmp-list", type="report")
        art.add_file(path, name="list.txt")
        run.log_artifact(art)
        run.finish()

    keys = list_tmp_keys(BUCKET_RAW, PREFIX_TMP)
    ensure_marker.expand(key=keys)  # dynamic mapping over discovered keys
    log_to_wandb(keys)

rnaseq_mvp()
