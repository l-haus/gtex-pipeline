```mermaid
flowchart LR
  %% Lanes
  subgraph SRC[Sources]
    M1[GTEx/recount3 metadata]
    R1[(GCS raw reads\n gs://<bucket>/raw/...)]
  end

  subgraph AF[Airflow (DAG: rnaseq_mvp)]
    A0[[start]]
    A1[fetch_metadata\n(PythonOperator)]
    A2[stage_reads\n(PythonOperator)]
    subgraph TG[TaskGroup: per-sample mapping]
      Q1[qc\n(KubernetesPodOperator: FastQC)]
      Q2[quant\n(KubernetesPodOperator: Salmon)]
    end
    A3[aggregate\n(PythonOperator)]
    A4[track lineage & artifacts\n(PythonOperator → W&B)]
    A5[report (MultiQC or summary)\n(KubernetesPodOperator)]
    A6[[end (SLA 2h)]]
  end

  subgraph K8S[GKE Autopilot]
    P1[(Pod: fastqc)]
    P2[(Pod: salmon)]
    P3[(Pod: report)]
  end

  subgraph STG[Storage & Tracking]
    W1[(GCS processed\n gs://<bucket>/processed/qc/...)]
    W2[(GCS counts\n gs://<bucket>/processed/counts/...)]
    W3[(Markers\n gs://<bucket>/processed/markers/*.done)]
    W4[(Lineage JSON\n gs://<bucket>/processed/lineage/<run_id>.json)]
    WB[Weights & Biases\n runs + artifacts]
    DLQ[(Dead-letter\n gs://<bucket>/dlq/)]
  end

  %% Dependencies
  A0 --> A1 --> A2 --> TG --> A3 --> A4 --> A5 --> A6

  %% Fan-out (conceptual): each sample runs Q1->Q2
  TG --> Q1 --> Q2

  %% Data flows (dashed)
  R1 -. reads .-> A2
  A2 -. writes .-> W3
  Q1 -. reads .-> R1
  Q1 -. writes .-> W1
  Q2 -. reads .-> R1
  Q2 -. writes .-> W2
  A3 -. writes .-> W2
  A4 -. writes .-> W4
  A4 -. logs .-> WB
  Q1 -. on failure .-> DLQ
  Q2 -. on failure .-> DLQ

  %% K8s mapping
  Q1 -.runs on.-> P1
  Q2 -.runs on.-> P2
  A5 -.runs on.-> P3

  %% Notes
  note right of AF
    Retries: qc/quant = 2x (exp backoff)
    Idempotency: check marker exists → skip
    Backfill: date-range param; markers guard
    Auth: Workload Identity (no keys)
  end
```
