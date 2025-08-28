# gtex-pipeline (QC MVP)

## Quickstart (local dev with Astro)
1. Prereqs: Docker + Buildx, gcloud, Astronomer CLI (astro).
2. Configure Buildx once: ```docker buildx create –use –name xbuilder || true```
3. Start Airflow: ```astro dev start```
    - UI: http://localhost:8080 (default: admin/airflow)
4.	Set Airflow Variables (UI → Admin → Variables) or import:
```astro dev bash
airflow variables import /usr/local/airflow/dags/airflow_variables.example.json```
5.	Run DAG rnaseq_mvp manually.

## Build & push images to Artifact Registry

export REGION=northamerica-northeast1
export PROJECT_ID=gtex-pipeline
make login
make build-fastqc
make build-multiqc

### Notes

- FastQC image: google-cloud-cli base + FastQC 0.12.1
- MultiQC image: google-cloud-cli base + venv (MultiQC 1.29 + Kaleido)
- KPO images in DAG resolve to: northamerica-northeast1-docker.pkg.dev/gtex-pipeline/rnaseq/{fastqc|multiqc}:
