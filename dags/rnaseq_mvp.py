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
    try:
        return Variable.get(key)
    except Exception:
        return default


PROJECT_ID = _v("PROJECT_ID", "gtex-pipeline")
GCS_BUCKET = _v("GCS_BUCKET", "rna-dev-xxxx")
GCS_RAW_SAMPLE = _v("GCS_RAW_SAMPLE", "raw/demo/test.fastq")
GCS_RAW_PREFIX = _v("GCS_RAW_PREFIX", "raw/")
GCS_QC_PREFIX = _v("GCS_QC_PREFIX", "processed/qc/demo")
SAMPLE_ID = _v("SAMPLE_ID", "demo")

PREFIX_TMP = "tmp/"
MARKERS_PREFIX = "processed/markers/"

common_params = dict(
    PROJECT_ID=PROJECT_ID,
    GCS_BUCKET=GCS_BUCKET,
    GCS_RAW_SAMPLE=GCS_RAW_SAMPLE,
    GCS_QC_PREFIX=GCS_QC_PREFIX,
    SAMPLE_ID=SAMPLE_ID,
)


def _gcs_client():
    return GCSClient()


@dag(
    dag_id="rnaseq_mvp",
    start_date=datetime(2025, 1, 1, tz="UTC"),
    schedule=None,
    catchup=False,
    tags=["dev"],
    max_active_runs=1,
    max_active_tasks=6,
)
def rnaseq_mvp():
    @task(retries=3, retry_delay=timedelta(minutes=1))
    def discover_samples(bucket: str, raw_prefix: str) -> list[dict]:
        import re

        client = _gcs_client()
        blobs = client.list_blobs(bucket, prefix=raw_prefix)

        def infer_sample_id(obj_name: str) -> str:
            fn = obj_name.rsplit("/", 1)[-1]
            if fn.endswith(".fastq.gz"):
                stem = fn[:-9]
            elif fn.endswith(".fastq"):
                stem = fn[:-6]
            else:
                stem = fn
            # drop common read/lane suffixes at the END of the stem
            stem = re.sub(r"(?:[_\.]?R?[12])(?:_\d{3})?$|_L\d{3,}$", "", stem)
            # k8s/gcs-safe sample id
            sid = re.sub(r"[^A-Za-z0-9_.-]+", "-", stem)[:63].strip("-.")
            return sid or "sample"

        samples = []
        for b in blobs:
            n = b.name
            if n.endswith("/"):
                continue
            if not (n.endswith(".fastq") or n.endswith(".fastq.gz")):
                continue
            sid = infer_sample_id(n)
            samples.append(
                {
                    **common_params,
                    "GCS_BUCKET": bucket,
                    "GCS_RAW_SAMPLE": n,
                    "GCS_QC_PREFIX": f"processed/qc/{sid}",
                    "SAMPLE_ID": sid,
                }
            )
        return samples

    @task(retries=3, retry_delay=timedelta(minutes=2))
    def list_tmp_keys(bucket: str, prefix: str) -> list[str]:
        client = _gcs_client()
        blobs = client.list_blobs(bucket, prefix=prefix)
        out = [b.name for b in blobs if b.name != prefix]
        return out

    @task(retries=3, retry_delay=timedelta(minutes=1))
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
        run = wandb.init(
            project="gtex-rnaseq",
            job_type="dev",
            config={"bucket": GCS_BUCKET, "prefix": PREFIX_TMP, "n_keys": len(keys)},
        )
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
            f.write("\n".join(keys or ["<no_keys>"]))
            path = f.name
        art = wandb.Artifact("tmp-list", type="report")
        art.add_file(path, name="list.txt")
        run.log_artifact(art)
        run.finish()

    with TaskGroup(group_id="qc_on_gke") as qc_on_gke:
        sample_params = discover_samples(GCS_BUCKET, GCS_RAW_PREFIX)

        fastqc_kpo = KubernetesPodOperator.partial(
            task_id="fastqc_gke",
            kubernetes_conn_id="k8s_gke",
            name="fastqc-gke",
            namespace="rnaseq",
            service_account_name="airflow-runner",
            image="northamerica-northeast1-docker.pkg.dev/{{ params.PROJECT_ID }}/rnaseq/fastqc:0.12.1",
            cmds=["bash", "-lc"],
            arguments=[
                r"""
              set -euo pipefail
              echo ":: fastqc:start"
              echo "Bucket={{ params.GCS_BUCKET }}  Sample={{ params.GCS_RAW_SAMPLE }}"

              mkdir -p /work/in /work/out
              gsutil cp "gs://{{ params.GCS_BUCKET }}/{{ params.GCS_RAW_SAMPLE }}" /work/in/
              infile=$(basename "{{ params.GCS_RAW_SAMPLE }}")

              echo ":: fastqc:run"
              fastqc "/work/in/${infile}" --outdir /work/out --quiet

              echo ":: fastqc:upload"
              gsutil -m cp /work/out/* "gs://{{ params.GCS_BUCKET }}/{{ params.GCS_QC_PREFIX }}/"
              gsutil ls -l "gs://{{ params.GCS_BUCKET }}/{{ params.GCS_QC_PREFIX }}/"
              echo ":: fastqc:done"
            """
            ],
            get_logs=True,
            on_finish_action="delete_pod",
            reattach_on_restart=True,
            execution_timeout=timedelta(minutes=30),
            pool="gke-small",
            pool_slots=1,
            container_resources=V1ResourceRequirements(
                requests={"cpu": "500m", "memory": "1Gi", "ephemeral-storage": "2Gi"},
                limits={"cpu": "1", "memory": "2Gi", "ephemeral-storage": "4Gi"},
            ),
            labels={
                "pipeline": "rnaseq",
                "step": "qc",
                "sample_id": "{{ params.SAMPLE_ID }}",
                "run_id": "{{ ti.run_id }}",
            },
        ).expand(params=sample_params)

        multiqc_kpo = KubernetesPodOperator.partial(
            task_id="multiqc_gke",
            kubernetes_conn_id="k8s_gke",
            name="multiqc",
            namespace="rnaseq",
            service_account_name="airflow-runner",
            image="northamerica-northeast1-docker.pkg.dev/{{ params.PROJECT_ID }}/rnaseq/multiqc:1.29.0-kaleido",
            cmds=["bash", "-lc"],
            arguments=[
                r"""
              set -euo pipefail
              echo ":: multiqc:start"
              echo "Bucket={{ params.GCS_BUCKET }}  Prefix={{ params.GCS_QC_PREFIX }}"

              mkdir -p /work/qc /work/report
              echo ":: multiqc:download"
              gsutil -m cp "gs://{{ params.GCS_BUCKET }}/{{ params.GCS_QC_PREFIX }}/*" /work/qc/ || true

              echo ":: multiqc:run"
              multiqc /work/qc -o /work/report || true

              echo ":: multiqc:upload"
              if [ -f /work/report/multiqc_report.html ]; then
                gsutil cp /work/report/multiqc_report.html "gs://{{ params.GCS_BUCKET }}/{{ params.GCS_QC_PREFIX }}/multiqc_report.html"
              fi
              echo ok | gsutil cp - "gs://{{ params.GCS_BUCKET }}/processed/markers/multiqc_{{ params.SAMPLE_ID }}.done"
              echo ":: multiqc:done"
            """
            ],
            get_logs=True,
            on_finish_action="delete_pod",
            reattach_on_restart=True,
            execution_timeout=timedelta(minutes=30),
            pool="gke-small",
            pool_slots=1,
            container_resources=V1ResourceRequirements(
                requests={"cpu": "250m", "memory": "1Gi", "ephemeral-storage": "1Gi"},
                limits={"cpu": "1", "memory": "2Gi", "ephemeral-storage": "1Gi"},
            ),
            labels={
                "pipeline": "rnaseq",
                "step": "multiqc",
                "sample_id": "{{ params.SAMPLE_ID }}",
                "run_id": "{{ ti.run_id }}",
            },
        ).expand(params=sample_params)

    @task
    def emit_qc_manifest(bucket_name: str, prefix: str) -> str:
        """List QC outputs in GCS and write a manifest file to GCS; return gs:// path."""
        client = _gcs_client()
        bucket = client.bucket(bucket_name)
        blobs = client.list_blobs(bucket_name, prefix=prefix)

        lines = []
        total_bytes = 0
        count = 0
        for b in blobs:
            if b.name.endswith("/") or b.name == prefix:
                continue
            total_bytes += b.size or 0
            count += 1
            lines.append(
                f"{b.updated.isoformat()}Z\t{b.size}\tgs://{bucket_name}/{b.name}"
            )

        if not lines:
            lines = ["<no files>"]
            count = 0
        summary = f"# SUMMARY\tbytes={total_bytes}\tfiles={count}"
        lines = [summary] + lines

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
            f.write("\n".join(lines))
            local_path = f.name

        manifest_key = f"{prefix.rstrip('/')}/_manifest.txt"
        bucket.blob(manifest_key).upload_from_filename(local_path)
        os.unlink(local_path)
        return f"gs://{bucket_name}/{manifest_key}"

    @task
    def log_qc_manifest_to_wandb(gs_uri: str, bucket_name: str, prefix: str) -> None:
        import wandb
        import tempfile

        if not os.getenv("WANDB_API_KEY"):
            print("WANDB_API_KEY missing; skipping W&B")
            return
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt") as f:
            f.write(gs_uri + "\n")
            p = f.name
        wandb.login()
        run = wandb.init(
            project="gtex-rnaseq",
            job_type="qc-manifest",
            config={"bucket": bucket_name, "prefix": prefix},
        )
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
