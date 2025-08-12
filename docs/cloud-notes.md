# Cloud Notes — GTEx Airflow Project (Template)

> Purpose: reproducible lab log of infra choices and commands.

## 0) Context Snapshot
- Date: 2025-08-12
- Goal (this week): {{e.g., Airflow local + MinIO; TF plan for GCS}}
- Current state: Airflow + MinIO

## 1) Decisions Log
- ADR-001 — Local object store = **MinIO (brew)** → GCS in Week 2.  
  *Why*: fast local dev; *Alt rejected*: S3 (not needed), local FS (no S3 API).
- ADR-002 — Airflow runtime = **Astronomer runtime via astro CLI**.  
  *Why*: reproducible images; *Alt rejected*: raw docker-compose.

## 2) Environments & Versions
macOS 15.6 (24G84)
Docker Desktop 4.43.2 (199162)
astro 1.35.1
Astronomer runtime image: astrocrpublic.azurecr.io/runtime:3.0-7
Python 3.12.11
Airflow 3.0.4+astro.1
MinIO server RELEASE.2025-07-23T15-54-02Z (go1.24.5 darwin/arm64)
apache-airflow-providers-amazon==9.12.0
boto3==1.40.7

## 3) Secrets Policy
- No secrets in repo. Use `.env` locally; commit `.env.example` only.

## 4) MinIO (local)
**Start (foreground):**
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin
minio server /opt/homebrew/var/minio --console-address ":9001"

**Buckets:**
mc alias set local http://127.0.0.1:9000 minioadmin minioadmin
mc mb local/rna-raw
mc mb local/rna-processed

**ILM (7-day tmp/):**
mc ilm rule add --expire-days 7 local/rna-raw --prefix tmp/
mc ilm rule add --expire-days 7 local/rna-processed --prefix tmp/
mc ilm rule ls local/rna-raw
mc ilm rule ls local/rna-processed

**Notes / gotchas:**
- versioning unsupported locally; use ILM only

## 5) Airflow (astro)
**Bootstrap & deps:**
astro dev init

**requirements.txt additions**
apache-airflow-providers-amazon
boto3

**Connections (.env):**
AIRFLOW__CORE__LOAD_EXAMPLES=False

WANDB_API_KEY=<your_key>
AIRFLOW_CONN_MINIO_S3="aws://minioadmin:minioadmin@/?region_name=us-east-1&endpoint_url=http%3A%2F%2Fhost.docker.internal%3A9000&verify=False"

**Run & logs:**
astro dev start
astro dev logs --scheduler

## 6) Connectivity Sanity Checks
**Inside scheduler container:**

astro dev bash --scheduler
python - <<'PY'
import boto3
s3=boto3.client('s3',endpoint_url='http://host.docker.internal:9000',
aws_access_key_id='minioadmin',aws_secret_access_key='minioadmin',
region_name='us-east-1')
print([b['Name'] for b in s3.list_buckets().get('Buckets',[])])
PY

Failures & fixes:
- `InvalidAccessKeyId` → endpoint must be `host.docker.internal:9000` (not localhost); optional `AWS_S3_ADDRESSING_STYLE=path`.

## 7) DAGs — What’s Green Today
- `list_minio` — proves S3Hook connectivity; screenshot: `docs/airflow-green.png`.
- `purge_minio_tmp` — maintenance ILM safety net; schedule: @daily.

## 8) Proof Artifacts (links)
- MinIO console (ILM rule) screenshot: `docs/minio-ilm.png`
- Airflow DAG graph: `docs/airflow-green.png`

## Docker FastQC
mkdir -p images/fastqc samples
cat > samples/test.fastq <<'EOF'
@TEST_READ_1
GATTTGGGGTTCAAAGCAGTATCGATCAAATAGTAA
+
IIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII
EOF

**First Build**
make build
9.03 real         0.25 user         0.25 sys

**Second Build**
make build
0.71 real         0.19 user         0.12 sys

**Image Size**
make size
fastqc       0.12.1    10f38fa0fbcc   2 minutes ago   385MB

**Run**
make run

### Push to Registry
Create a PAT with write:packages, read:packages

echo "$GHCR_TOKEN" | docker login ghcr.io -u <your_github_username> --password-stdin

docker tag fastqc:0.12.1 ghcr.io/<your_github_username>/fastqc:0.12.1

docker push ghcr.io/<your_github_username>/fastqc:0.12.1

ghcr.io/l-haus/fastqc:0.12.1

## 9) GCP (Week 2 Plan)
**Terraform intents:** GKE Autopilot (small), GCS bucket (versioned), Artifact Registry, Workload Identity.
**Guardrails:** smallest tiers; `terraform destroy` documented; cost notes.
**Placeholder commands:**
terraform init && terraform plan
terraform apply
terraform destroy

## 10) Troubleshooting Log (append daily)
- 2025-08-12 — Task is hitting AWS S3 instead of MinIO: InvalidAccessKeyId → Point the S3 client to the MinIO endpoint inside Docker (.env file).
- 2025-08-12 - Make build error, unable to prepare context → Use absolute paths in Makefile.

## 11) Teardown (local)
brew services stop minio   # or Ctrl-C if foreground
astro dev stop

