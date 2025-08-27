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

from airflow.models import Variable
def _v(key, default):
    try: return Variable.get(key)
    except Exception: return default

GCS_BUCKET     = _v("GCS_BUCKET",     "rna-dev-xxxx")
GCS_RAW_SAMPLE = _v("GCS_RAW_SAMPLE", "raw/demo/test.fastq")
GCS_QC_PREFIX  = _v("GCS_QC_PREFIX",  "processed/qc/demo")

PREFIX_TMP = "tmp/"
MARKERS_PREFIX = "processed/markers/"


common_params = dict(
    GCS_BUCKET=GCS_BUCKET,
    GCS_RAW_SAMPLE=GCS_RAW_SAMPLE,
    GCS_QC_PREFIX=GCS_QC_PREFIX,
)

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
    def ensure_marker(key: str, bucket_name: str) -> str:
        """Create a processed marker for idempotency; skip if exists."""
        client = _gcs_client()
        bucket = client.bucket(bucket_name)
        marker_name = f"{MARKERS_PREFIX}{key.replace('/', '_')}.done"
        blob = bucket.blob(marker_name)
        if blob.exists():
            raise AirflowSkipException(f"already processed: {key}")
        blob.upload_from_string("ok")
        return marker_name

    @task
    def log_tmp_to_wandb(keys: list[str]) -> None:
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
            params=common_params,
            get_logs=True,
            on_finish_action="keep_pod",
            is_delete_operator_pod=False,
            reattach_on_restart=True,
            container_resources=V1ResourceRequirements(
                requests={"cpu": "500m", "memory": "1Gi", "ephemeral-storage": "2Gi"},
                limits={"cpu": "1", "memory": "2Gi", "ephemeral-storage": "4Gi"},
            ),
            labels={"pipeline": "rnaseq", "step": "qc", "sample": "demo"},
        )

        multiqc_kpo = KubernetesPodOperator(
            task_id="multiqc_gke",
            kubernetes_conn_id="k8s_gke",
            name="multiqc",
            namespace="rnaseq",
            service_account_name="airflow-runner",
            image="google/cloud-sdk:471.0.0",
            image_pull_policy="IfNotPresent",
            cmds=["bash","-lc"],
            arguments=[r"""
                set -euo pipefail
                echo "Bucket={{ params.GCS_BUCKET }}  Prefix={{ params.GCS_QC_PREFIX }}"

                # venv to avoid PEP 668
                apt-get update
                apt-get install -y --no-install-recommends python3-venv ca-certificates
                rm -rf /var/lib/apt/lists/*
                python3 -m venv /opt/venv
                /opt/venv/bin/pip install --no-cache-dir multiqc==1.29 kaleido==0.2.1

                mkdir -p /work/qc /work/report
                gsutil -m cp "gs://{{ params.GCS_BUCKET }}/{{ params.GCS_QC_PREFIX }}/*" /work/qc/ || true

                PATH="/opt/venv/bin:${PATH}" multiqc /work/qc -o /work/report || true

                if [ -f /work/report/multiqc_report.html ]; then
                  gsutil cp /work/report/multiqc_report.html "gs://{{ params.GCS_BUCKET }}/{{ params.GCS_QC_PREFIX }}/multiqc_report.html"
                fi

                echo ok | gsutil cp - "gs://{{ params.GCS_BUCKET }}/processed/markers/qc_${SAMPLE_ID:-demo}.done"
            """],
            params=common_params,
            get_logs=True,
            on_finish_action="keep_pod",
            is_delete_operator_pod=False,
            reattach_on_restart=True,
            container_resources=V1ResourceRequirements(
                requests={"cpu": "250m", "memory": "1Gi", "ephemeral-storage": "1Gi"},
                limits={"cpu": "1", "memory": "2Gi", "ephemeral-storage": "1Gi"},
            ),
            labels={"pipeline":"rnaseq","step":"multiqc","sample":"demo"},
        )

    @task
    def emit_qc_manifest(bucket_name: str, prefix: str) -> str:
        """List QC outputs in GCS and write a manifest file to GCS; return gs:// path."""
        client = _gcs_client()
        bucket = client.bucket(bucket_name)
        blobs = client.list_blobs(bucket_name, prefix=prefix)

        lines = []
        total_bytes = 0
        for b in blobs:
            if b.name.endswith("/") or b.name == prefix:
                continue
            total_bytes += (b.size or 0)
            lines.append(f"{b.updated.isoformat()}Z\t{b.size}\tgs://{bucket_name}/{b.name}")

        if not lines:
            lines = ["<no files>"]
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
            f.write("\n".join(lines))
            local_path = f.name

        manifest_key = f"{prefix.rstrip('/')}/_manifest.txt"
        bucket.blob(manifest_key).upload_from_filename(local_path)
        os.unlink(local_path)
        return f"gs://{bucket_name}/{manifest_key}"
    
    @task
    def log_qc_manifest_to_wandb(gs_uri: str, bucket_name: str, prefix: str) -> None:
        import wandb, tempfile
        if not os.getenv("WANDB_API_KEY"):
            print("WANDB_API_KEY missing; skipping W&B")
            return
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
            f.write(gs_uri + "\n")
            p = f.name
        wandb.login()
        run = wandb.init(project="gtex-rnaseq", job_type="qc-manifest",
                         config={"bucket": bucket_name, "prefix": prefix})
        art = wandb.Artifact("qc-manifest-pointer", type="report")
        art.add_file(p, name="_manifest_uri.txt")
        run.log_artifact(art)
        run.finish()

    keys = list_tmp_keys(GCS_BUCKET, PREFIX_TMP)
    marker = ensure_marker.partial(bucket_name=GCS_BUCKET).expand(key=keys)

    marker >> qc_on_gke >> log_tmp_to_wandb(keys)
    fastqc_kpo >> multiqc_kpo
    m = emit_qc_manifest(GCS_BUCKET, GCS_QC_PREFIX) 
    multiqc_kpo >> m >> log_qc_manifest_to_wandb(m, GCS_BUCKET, GCS_QC_PREFIX)

rnaseq_mvp()
