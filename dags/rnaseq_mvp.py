import os
import tempfile
from datetime import timedelta
from pendulum import datetime
from airflow.decorators import dag, task
from airflow.exceptions import AirflowSkipException
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.utils.task_group import TaskGroup
from google.cloud.storage import Client as GCSClient
from kubernetes.client import V1ResourceRequirements

GCS_BUCKET = os.getenv("GCS_BUCKET")
PREFIX_TMP = "tmp/"
MARKERS_PREFIX = "processed/markers/"
GCS_RAW_SAMPLE = "raw/demo/test.fastq"
GCS_QC_PREFIX = "processed/qc/demo"

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

    with TaskGroup(group_id="qc_on_gke") as qc_on_gke:
        fastqc_kpo = KubernetesPodOperator(
            task_id="fastqc_gke",
            kubernetes_conn_id="k8s_gke",           # defined in .env
            name="fastqc-gke",
            namespace="rnaseq",
            service_account_name="airflow-runner",  # KSA → GSA via Workload Identity
            image="google/cloud-sdk:latest",        # includes gsutil + gcloud
            # Run a shell script inside the container
            cmds=["bash","-lc"],
            arguments=[f"""
            set -euo pipefail
            echo "Bucket={GCS_BUCKET}  Sample={GCS_RAW_SAMPLE}"

            # 1) Install FastQC at runtime (avoids private registry pulls)
            apt-get update
            apt-get install -y --no-install-recommends ca-certificates curl unzip openjdk-17-jre-headless perl
            rm -rf /var/lib/apt/lists/*
            curl -fsSL -o /tmp/fastqc.zip https://www.bioinformatics.babraham.ac.uk/projects/fastqc/fastqc_v0.12.1.zip
            unzip -q /tmp/fastqc.zip -d /opt && chmod +x /opt/FastQC/fastqc

            # 2) Get input from GCS
            mkdir -p /work/in /work/out
            gsutil cp "gs://{GCS_BUCKET}/{GCS_RAW_SAMPLE}" /work/in/test.fastq

            # 3) Run FastQC
            /opt/FastQC/fastqc /work/in/test.fastq --outdir /work/out --quiet

            # 4) Upload results to GCS
            gsutil -m cp /work/out/* "gs://{GCS_BUCKET}/{GCS_QC_PREFIX}/"
            gsutil ls -l "gs://{GCS_BUCKET}/{GCS_QC_PREFIX}/"
            """],
            get_logs=True,                          # stream pod logs back to Airflow
            is_delete_operator_pod=True,            # clean up pod after success
            container_resources=V1ResourceRequirements(
              requests={"cpu": "1", "memory": "3Gi", "ephemeral-storage": "4Gi"},
              limits={"cpu": "2", "memory": "4Gi", "ephemeral-storage": "4Gi"},
            ),
        )


    keys = list_tmp_keys(GCS_BUCKET, PREFIX_TMP)
    marker = ensure_marker.expand(key=keys)
    #log_to_wandb(keys)
    
    marker >> qc_on_gke >> log_to_wandb(keys)

rnaseq_mvp()
