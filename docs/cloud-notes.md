## Environments & Versions (snapshot - 2025-08-14)
- macOS: 15.6
- Docker: Docker version 28.3.2, build 578ccf6
- astro CLI: Astro CLI Version: 1.35.1
- Astronomer runtime image: astrocrpublic.azurecr.io/runtime:3.0-7
- Python 3.12.11
- Airflow 3.0.4+astro.1
- MinIO server RELEASE.2025-07-23T15-54-02Z (go1.24.5 darwin/arm64)
- apache-airflow-providers-amazon==9.12.0
- boto3==1.40.9
- wandb==0.21.1
---
## 2025-08-12 — Day 1: MinIO (brew) + Airflow connectivity

**Context**  
Local S3-compatible store for dev; Airflow (Astro) lists objects via S3Hook.

**Commands**
```
# MinIO (foreground)
MINIO_ROOT_USER=minioadmin \
MINIO_ROOT_PASSWORD=minioadmin \
minio server /opt/homebrew/var/minio --console-address ":9001"

# Buckets + 7‑day ILM on tmp/
mc alias set local http://127.0.0.1:9000 minioadmin minioadmin
mc mb local/rna-raw
mc mb local/rna-processed
mc ilm rule add --expire-days 7 local/rna-raw --prefix tmp/
mc ilm rule add --expire-days 7 local/rna-processed --prefix tmp/

# Airflow connection (Astro → host MinIO)
# .env (not committed):
AIRFLOW_CONN_MINIO_S3='aws://minioadmin:minioadmin@/?region_name=us-east-1&endpoint_url=http%3A%2F%2Fhost.docker.internal%3A9000&verify=False'
astro dev restart
```

**Proof**  
MinIO ILM rule listed; Airflow task logs show keys under `s3://rna-raw/tmp/`.

**Issues → Fixes**

- `InvalidAccessKeyId` → switched endpoint to `host.docker.internal:9000` inside containers
    
- `airflow.hooks.S3_hook` import error → use `from airflow.providers.amazon.aws.hooks.s3 import S3Hook` and add `apache-airflow-providers-amazon`

---
## 2025-08-13 — Day 2: Docker (FastQC) + optional push

**Context**  
Built a multi‑stage FastQC image; verified cache effectiveness; produced HTML report; (optional) pushed to GHCR.

**Commands**

```
# Build twice to show cache
docker build -t fastqc:0.12.1 -f images/fastqc/Dockerfile images/fastqc
docker build -t fastqc:0.12.1 -f images/fastqc/Dockerfile images/fastqc

# Run on toy FASTQ
mkdir -p samples/out
docker run --rm -v "$PWD/samples:/data" fastqc:0.12.1 /data/test.fastq --outdir /data/out --quiet
ls -lh samples/out

# (Optional) GHCR push
# docker login ghcr.io -u l-haus --password-stdin < <(printf %s "$GHCR_TOKEN")
# docker tag fastqc:0.12.1 ghcr.io/l-haus/fastqc:0.12.1
# docker push ghcr.io/l-haus/fastqc:0.12.1
```

**Proof**  
Second build faster than first; `samples/out/test_fastqc.html` present.

**Issues → Fixes**

- GHCR `permission_denied: token scopes` → regenerated fine‑grained PAT with **Packages: Read/Write** on the repo; re‑login and push
    
- GitHub push protection blocked leaked file (`images/fastqc/.secrets`) → removed from history with `git filter-repo`; revoked token; added `.gitignore` entry

---
## 2025-08-13 — Day 3: Airflow `rnaseq_mvp` + W&B artifact

**Context**  
TaskFlow DAG: (1) list keys in `rna-raw/tmp/`, (2) create idempotency markers in `rna-processed/markers/` via dynamic mapping, (3) log a W&B artifact.

**Commands**

```
# Deps + env (not committed)
printf "
apache-airflow-providers-amazon>=9.1.0
boto3
wandb
" >> requirements.txt
printf "
WANDB_CONSOLE=off
WANDB_SILENT=true
" >> .env
# export WANDB_API_KEY=<your_key>
astro dev restart
```

**Proof**  
Airflow UI shows green run; W&B run visible with `tmp-list` Artifact and `n_keys` in config; `.done` markers created under `rna-processed/markers/` for discovered keys (re‑run skips existing).

**Notes**  
W&B prints progress to stderr (Airflow shows as ERROR). Silenced with `WANDB_CONSOLE=off`, `WANDB_SILENT=true`.

**Next**  
Day 4: Terraform a versioned **GCS** bucket + least‑priv SA; record plan/apply/destroy; swap object store in code to GCS next week.
