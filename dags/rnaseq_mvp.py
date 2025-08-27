import os
import tempfile
from datetime import timedelta
from pendulum import datetime
from airflow.decorators import dag, task
from airflow.exceptions import AirflowSkipException
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.utils.task_group import TaskGroup
from google.cloud.storage import Client as GCSClient
from kubernetes.client import V1ResourceRequirements, V1SecurityContext, V1Capabilities

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
    params={
        "GCS_BUCKET": "rna-dev-9c91",
        "GCS_RAW_SAMPLE": "raw/demo/test.fastq",
        "GCS_QC_PREFIX": "processed/qc/demo",
    },
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
            kubernetes_conn_id="k8s_gke",
            name="fastqc-gke",
            namespace="rnaseq",
            service_account_name="airflow-runner",
            image="gcr.io/google.com/cloudsdktool/google-cloud-cli:slim",  # has gsutil; amd64
            cmds=["bash", "-lc"],
            arguments=[r"""
        set -euo pipefail
        echo "Bucket={{ params.GCS_BUCKET }}  Sample={{ params.GCS_RAW_SAMPLE }}"

        # Install minimal deps + FastQC
        apt-get update
        apt-get install -y --no-install-recommends ca-certificates curl unzip openjdk-17-jre-headless perl
        rm -rf /var/lib/apt/lists/*
        curl -fsSL -o /tmp/fastqc.zip https://www.bioinformatics.babraham.ac.uk/projects/fastqc/fastqc_v0.12.1.zip
        unzip -q /tmp/fastqc.zip -d /opt && chmod +x /opt/FastQC/fastqc

        # I/O
        mkdir -p /work/in /work/out
        gsutil cp "gs://{{ params.GCS_BUCKET }}/{{ params.GCS_RAW_SAMPLE }}" /work/in/test.fastq

        # Run
        /opt/FastQC/fastqc /work/in/test.fastq --outdir /work/out --quiet

        # Upload results
        gsutil -m cp /work/out/* "gs://{{ params.GCS_BUCKET }}/{{ params.GCS_QC_PREFIX }}/"
        gsutil ls -l "gs://{{ params.GCS_BUCKET }}/{{ params.GCS_QC_PREFIX }}/"
        """],
            get_logs=True,
            is_delete_operator_pod=False,
            container_resources=V1ResourceRequirements(
                requests={"cpu": "500m", "memory": "1Gi", "ephemeral-storage": "2Gi"},
                limits={"cpu": "1", "memory": "2Gi", "ephemeral-storage": "4Gi"},
            ),
            labels={"pipeline": "rnaseq", "step": "qc", "sample": "demo"},
    ),
    keys = list_tmp_keys(GCS_BUCKET, PREFIX_TMP)
    marker = ensure_marker.expand(key=keys)
    #log_to_wandb(keys)
    
    marker >> qc_on_gke >> log_to_wandb(keys)

rnaseq_mvp()
