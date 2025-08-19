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

---

## 2025-08-14 — Day 4: Terraform GCS + SA (least privilege)

**Context**
IaC for a versioned GCS bucket with a **7-day `tmp/` rule**, and a **least-privilege** Service Account that has **objectAdmin on this bucket only** (no project-wide `buckets.list`).

### 0) One-time CLI setup

```bash
export PROJECT_ID=<your_gcp_project_id>
gcloud auth login --brief
gcloud auth application-default login    # ADC for Terraform
gcloud config set project "$PROJECT_ID"
```

### 1) Terraform scaffold (if not already in repo)

```bash
mkdir -p terraform/gcs && cd terraform/gcs
# Create files per repo guide (versions.tf, variables.tf, main.tf, outputs.tf) — see repo.
```

### 2) Enable required APIs (idempotent)

```bash
gcloud services enable storage.googleapis.com iam.googleapis.com
```

### 3) Plan & apply (capture logs)

```bash
cd terraform/gcs
terraform init
terraform plan -var project_id="$PROJECT_ID" | tee ../../docs/tf-plan.txt
terraform apply -auto-approve -var project_id="$PROJECT_ID" | tee ../../docs/tf-apply.txt
```

### 4) Pull outputs + sanity checks

```bash
# from terraform/gcs
export BUCKET=$(terraform output -raw bucket_name)
export SA_EMAIL=$(terraform output -raw sa_email)
echo "BUCKET=$BUCKET"; echo "SA_EMAIL=$SA_EMAIL"

# Versioning + lifecycle verify
gsutil versioning get gs://$BUCKET
gsutil lifecycle  get gs://$BUCKET

# Smoke write under tmp/ (should succeed with your user creds)
echo ok | gsutil cp - "gs://$BUCKET/tmp/test.txt"
gsutil ls -l "gs://$BUCKET/tmp/test.txt"
```

### 5) IAM sanity (least privilege)

```bash
# Bucket IAM shows objectAdmin for only this SA
gcloud storage buckets get-iam-policy "gs://$BUCKET" \
  --format='table(bindings.role, bindings.members)' | grep storage.objectAdmin

# (Expected) This SA cannot list project buckets; that's by design.
# If you try: gsutil ls gs://   → 403
```

**Proof**

* `docs/tf-plan.txt` and `docs/tf-apply.txt` committed.
* `gsutil versioning get` shows **Enabled**.
* `gsutil lifecycle get` shows **age=7** rule for prefix `tmp/`.
* `gs://$BUCKET/tmp/test.txt` exists.

**Issues → Fixes**

* Empty `terraform output` → run `terraform apply` (state was empty).
* 403 on `gs://` listing → expected; SA is bucket-scoped only.

**Next**
Bind this SA to a KSA via **Workload Identity** and prove a pod can write to `gs://$BUCKET` (done on Day 6).

---

## 2025-08-15 — Day 5: K8s Job (FastQC on kind)

**Context**
Run your `fastqc:0.12.1` container as a **Kubernetes Job** on a local **kind** cluster. Mount a toy FASTQ via **ConfigMap**, write results to **emptyDir**, and copy artifacts back. Handle Apple Silicon (arm64) and `kubectl cp` quirks.

### 0) Pre-reqs (host)

```bash
brew install kind kubectl
```

### 1) Create/refresh cluster

```bash
kind delete cluster --name rnaseq 2>/dev/null || true
kind create cluster --name rnaseq
kubectl get nodes
```

### 2) Ensure image arch matches the node (Apple Silicon = arm64)

```bash
# Check image arch
docker inspect --format '{{.Os}}/{{.Architecture}}' fastqc:0.12.1 || true

# If NOT linux/arm64, rebuild & load into kind
DOCKER_DEFAULT_PLATFORM=linux/arm64 \
  docker build -t fastqc:0.12.1 -f images/fastqc/Dockerfile images/fastqc
kind load docker-image fastqc:0.12.1 --name rnaseq
```

### 3) Namespace + sample input

```bash
kubectl create namespace rnaseq --dry-run=client -o yaml | kubectl apply -f -

# Create ConfigMap from your toy FASTQ
kubectl -n rnaseq create configmap fastq-sample \
  --from-file=samples/test.fastq \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 4) Job manifest (with sidecar to enable `kubectl cp`)

`k8s/job-fastqc.yaml`

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: fastqc
  namespace: rnaseq
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      volumes:
      - name: input
        configMap: { name: fastq-sample }
      - name: out
        emptyDir: {}
      containers:
      - name: fastqc
        image: fastqc:0.12.1
        # ENTRYPOINT=["fastqc"]; give args as a LIST (not a single string!)
        args: ["/input/test.fastq","--outdir","/out","--quiet"]
        resources:
          requests: { cpu: "250m", memory: "512Mi" }
          limits:   { cpu: "1",    memory: "1Gi" }
        volumeMounts:
        - { name: input, mountPath: /input, readOnly: true }
        - { name: out,   mountPath: /out }
      # Sidecar keeps pod Running so you can kubectl cp after fastqc exits
      - name: keeper
        image: alpine:3.20
        command: ["sh","-c","sleep 3600"]
        volumeMounts:
        - { name: out, mountPath: /out }
```

### 5) Apply, verify, copy artifacts

```bash
kubectl apply -f k8s/job-fastqc.yaml

# Wait until pod is Running (fastqc finishes quickly; keeper keeps it alive)
kubectl -n rnaseq get pods -l job-name=fastqc -w

# Logs from the terminated fastqc container (exitCode should be 0)
POD=$(kubectl -n rnaseq get pods -l job-name=fastqc -o jsonpath='{.items[0].metadata.name}')
kubectl -n rnaseq logs "$POD" -c fastqc | sed -n '1,120p'

# Copy results from the running sidecar
mkdir -p samples/out-k8s
kubectl -n rnaseq cp --container keeper "$POD":/out ./samples/out-k8s
ls -lh samples/out-k8s
```

### 6) Clean up (optional)

```bash
kubectl -n rnaseq delete job fastqc
# kind delete cluster --name rnaseq
```

**Proof**

* `samples/out-k8s/test_fastqc.html` **and** `.zip` present.
* `kubectl logs -c fastqc` shows normal FastQC output; container exit code **0**.
* You can explain **why**: ConfigMap → input, `emptyDir` → output, sidecar → artifact copy window.

**Issues → Fixes**

* **`exec format error`** on Apple Silicon → rebuild for `linux/arm64`, `kind load docker-image`.
* **No artifacts** → args were a single string; fix to list:
  `args: ["/input/test.fastq","--outdir","/out","--quiet"]`.
* **`cannot exec into a container in a completed pod` / `container not found ("fastqc")`** → copy from **sidecar** `keeper`, not the terminated `fastqc`.

**Next**
Stop using `kubectl cp` for pipelines; write `/out` to object storage (MinIO/GCS). You’ll do that as you switch to **GCS** and **GKE** in Week 2.

---

## 2025-08-18 — Day 6: GKE Autopilot + Workload Identity → GCS (smoke)

**Context**
Prove a pod on GKE Autopilot can read/write a **Terraform-provisioned** GCS bucket **without keys** (Workload Identity). Keep perms least-privilege: bucket-scoped **objectAdmin** only; no project-level `buckets.list`.

### 0) Setup: pull TF outputs and set project

```bash
export PROJECT_ID=<your_gcp_project_id>

# If needed, authenticate the CLI (user creds) and set project.
gcloud auth login --brief
gcloud config set project "$PROJECT_ID"

# Pull outputs from TF (must have been applied on Day 4)
export BUCKET=$(terraform -chdir=terraform/gcs output -raw bucket_name)
export SA_EMAIL=$(terraform -chdir=terraform/gcs output -raw sa_email)

echo "BUCKET=$BUCKET"
echo "SA_EMAIL=$SA_EMAIL"
# Sanity: as *user*, you should be able to list the bucket itself (not contents)
gsutil ls -d "gs://$BUCKET"
```

### 1) Create GKE Autopilot cluster (no `--workload-pool` flag on Autopilot)

```bash
gcloud services enable container.googleapis.com

gcloud container clusters create-auto rnaseq-dev \
  --region northamerica-northeast1 \
  --project "$PROJECT_ID"

# Wait until RUNNING (RECONCILING → RUNNING can take a few minutes)
until [ "$(gcloud container clusters describe rnaseq-dev \
  --region northamerica-northeast1 --project "$PROJECT_ID" \
  --format='value(status)')" = "RUNNING" ]; do date; sleep 10; done

# Install/use the GKE auth plugin (once per machine, if missing)
#   gcloud components install gke-gcloud-auth-plugin
export USE_GKE_GCLOUD_AUTH_PLUGIN=True

# Fetch kubeconfig creds and verify
gcloud container clusters get-credentials rnaseq-dev \
  --region northamerica-northeast1 --project "$PROJECT_ID"

kubectl get nodes
kubectl get ns
```

### 2) Bind Workload Identity (KSA ↔ GSA)

```bash
# Namespace + Kubernetes Service Account (KSA)
kubectl create namespace rnaseq --dry-run=client -o yaml | kubectl apply -f -
kubectl -n rnaseq create serviceaccount airflow-runner --dry-run=client -o yaml | kubectl apply -f -

# Allow the KSA to impersonate the GSA (Workload Identity)
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:${PROJECT_ID}.svc.id.goog[rnaseq/airflow-runner]"

# Annotate the KSA with the GSA email
kubectl -n rnaseq annotate serviceaccount airflow-runner \
  iam.gke.io/gcp-service-account="$SA_EMAIL" --overwrite

# Quick checks
kubectl -n rnaseq get sa airflow-runner -o yaml | grep -A2 iam.gke.io/gcp-service-account
gcloud iam service-accounts get-iam-policy "$SA_EMAIL" \
  --format='table(bindings.role, bindings.members)' | grep workloadIdentityUser || true
```

### 3) Smoke Job manifest (uses `google/cloud-sdk` with `gcloud` + `gsutil`)

`k8s/job-gcs-smoke.yaml`

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: gcs-smoke
  namespace: rnaseq
spec:
  backoffLimit: 0
  template:
    spec:
      serviceAccountName: airflow-runner
      restartPolicy: Never
      containers:
      - name: gcloud
        image: google/cloud-sdk:latest
        command: ["bash","-lc"]
        env:
        - { name: BUCKET, value: "{{BUCKET}}" }
        args:
        - |
          set -euo pipefail
          echo "BUCKET=$BUCKET"
          : "${BUCKET:?BUCKET not set}"
          /usr/bin/gsutil ls -d "gs://${BUCKET}"
          date | /usr/bin/gsutil cp - "gs://${BUCKET}/tmp/w2d1.txt"
          /usr/bin/gsutil ls -l "gs://${BUCKET}/tmp/w2d1.txt"
        resources:
          requests: { cpu: "100m", memory: "128Mi" }
          limits:   { cpu: "500m", memory: "512Mi" }
```

### 4) Apply with the bucket baked in; watch; read logs

```bash
# Render the placeholder → apply
sed "s/{{BUCKET}}/$BUCKET/g" k8s/job-gcs-smoke.yaml > /tmp/job.yaml
grep 'value:' -n /tmp/job.yaml   # should show your real bucket name

kubectl -n rnaseq delete job gcs-smoke --ignore-not-found
kubectl -n rnaseq apply --dry-run=client -f /tmp/job.yaml
kubectl -n rnaseq apply -f /tmp/job.yaml

# Watch pod status and then read logs
kubectl -n rnaseq get pods -l job-name=gcs-smoke -w
kubectl -n rnaseq logs job/gcs-smoke | sed -n '1,160p'
```

### 5) Verify from outside the cluster (ground truth)

```bash
gsutil ls -l "gs://${BUCKET}/tmp/w2d1.txt"
gsutil cat  "gs://${BUCKET}/tmp/w2d1.txt"   # should print a timestamp line
```

### 6) Acceptance (today)

* `kubectl logs job/gcs-smoke` shows:

  * `BUCKET=<name>`
  * `ls -d` on the bucket succeeds.
  * `gsutil cp` wrote `tmp/w2d1.txt` and `ls -l` shows size/time.
* No key files anywhere (only WI via KSA annotation + IAM binding).
* Note: **403** on `gs://` (all buckets) is expected; GSA lacks project-level `buckets.list` by design.

### 7) Cleanup (optional)

```bash
# Delete the Job only (keep cluster)
kubectl -n rnaseq delete job gcs-smoke

# Or tear down the cluster to avoid costs (you’ll recreate later)
# gcloud container clusters delete rnaseq-dev --region northamerica-northeast1 --quiet
```

### 8) Issues → Fixes (encountered)

* `gcloud: command not found` / `gsutil` missing → use `google/cloud-sdk:latest` (not slim).
* `container not found` / logs stuck in `ContainerCreating` → wait for Autopilot scale-up; switch context to GKE (not kind); use `kubectl describe pod` events.
* `gs:///tmp/...` URL error → `BUCKET` env was empty; inject via sed and guard with `: "${BUCKET:?...}"`.
* `stat gs://` error → `stat` is for objects; use `gsutil ls -d gs://$BUCKET` to check bucket exists.

**Next (tomorrow):** swap the Airflow DAG from MinIO to **GCS** (same idempotency markers) and add a `KubernetesPodOperator` that runs FastQC on **GKE** using this KSA (no keys).

## 2025-08-19 — Day 7: Swap DAG storage MinIO → GCS (ADC)

**Context**  
Airflow now reads `gs://$BUCKET/tmp/` and writes idempotency markers to `gs://$BUCKET/processed/markers/` using **Application Default Credentials**. Fixed two blockers: (1) wrong/typo’d ADC path; (2) missing project in env (user ADC often lacks project).

### Commands (copy–paste)

```bash
# 0) Vars (repo-scoped)
export PROJECT_ID=gtex-pipeline
export BUCKET=$(terraform -chdir=terraform/gcs output -raw bucket_name)
echo "PROJECT_ID=$PROJECT_ID  BUCKET=$BUCKET"

# 1) Put ADC into the project (bind-mounted into containers) and ignore it
gcloud auth application-default login   # if not done today
mkdir -p .secrets
install -m 600 ~/.config/gcloud/application_default_credentials.json .secrets/gcp-adc.json
grep -q '^\.secrets/$' .gitignore || printf "\n.secrets/\n" >> .gitignore

# 2) Airflow env (container paths)
grep -q '^GCS_BUCKET=' .env || printf "GCS_BUCKET=%s\n" "$BUCKET" >> .env
grep -q '^GOOGLE_APPLICATION_CREDENTIALS=' .env || \
  printf "GOOGLE_APPLICATION_CREDENTIALS=/usr/local/airflow/.secrets/gcp-adc.json\n" >> .env
# Explicit project to satisfy google-cloud-storage when ADC has no project field
grep -q '^GOOGLE_CLOUD_PROJECT=' .env || printf "GOOGLE_CLOUD_PROJECT=%s\n" "$PROJECT_ID" >> .env

# 3) Python deps for the GCS client
grep -q 'google-cloud-storage' requirements.txt || printf "\ngoogle-cloud-storage>=2.17.0\n" >> requirements.txt

# 4) Restart Airflow containers to pick up .env + mount .secrets
astro dev restart

# 5) Sanity from inside the scheduler
astro dev bash --scheduler -c 'ls -l /usr/local/airflow/.secrets/gcp-adc.json && echo "GCP=$GOOGLE_CLOUD_PROJECT"'
astro dev bash --scheduler -c "python - <<'PY'
from google.cloud.storage import Client
c = Client(project=None)  # uses GOOGLE_CLOUD_PROJECT if set
print('gcs_client_ok')
PY"

# 6) Trigger DAG and tail logs
astro dev bash --scheduler -c "airflow dags trigger rnaseq_mvp"
astro dev logs --scheduler | sed -n '1,200p'

# 7) Ground truth: markers in GCS
gsutil ls -l gs://$BUCKET/processed/markers/ | tail -n +1
```

### Code changes (minimal)

- Import the class, not the module; pass project explicitly:
    - from google.cloud.storage import Client as GCSClient
    - _gcs_client(): return GCSClient(project=os.getenv("GOOGLE_CLOUD_PROJECT"))
- Keep TaskFlow the same: list_tmp_keys(GCS_BUCKET, "tmp/") → ensure_marker (dynamic map) → log_to_wandb.

### Proof
- Airflow run Succeeded.
- Logs show keys from gs://$BUCKET/tmp/ and markers created/skipped.
- gsutil ls -l gs://$BUCKET/processed/markers/ returns .done files.

### Issues → Fixes (today)

- DefaultCredentialsError: ...gcp-adc.json was not found → copied ADC to .secrets/ and set GOOGLE_APPLICATION_CREDENTIALS=/usr/local/airflow/.secrets/gcp-adc.json.
- OSError: Project was not passed... / “No project ID could be determined” → added GOOGLE_CLOUD_PROJECT=$PROJECT_ID to .env and passed project= to Client().
- TypeError: 'module' object is not callable → stopped calling the storage module; imported Client class directly.
