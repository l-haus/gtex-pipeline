```mermaid
flowchart LR
  %% --- SOURCES ---
  subgraph SRC[Sources]
    M1[GTEx / recount3 metadata]
    R1[GCS raw reads\n gs://<bucket>/raw/...]
  end

  %% --- AIRFLOW DAG ---
  subgraph AF[Airflow DAG: rnaseq_mvp]
    A0([start])
    A1[fetch_metadata]
    A2[stage_reads]
    subgraph TG[TaskGroup: per-sample mapping]
      Q1[qc (KPO: FastQC)]
      Q2[quant (KPO: Salmon)]
    end
    A3[aggregate]
    A4[track (W&B runs + artifacts)]
    A5[report (KPO: MultiQC)]
    A6([end / SLA 2h])
  end

  %% --- STORAGE & TRACKING ---
  subgraph STG[Storage & Tracking]
    W1[GCS processed/qc/]
    W2[GCS counts/]
    W3[GCS markers/*.done]
    W4[GCS lineage/<run_id>.json]
    WB[Weights & Biases (project/runs)]
    DLQ[GCS dead-letter/]
  end

  %% control-flow
  A0 --> A1 --> A2 --> TG --> A3 --> A4 --> A5 --> A6
  Q1 --> Q2

  %% data-flow (dashed)
  M1 -.-> A1
  R1 -.-> A2
  Q1 -.-> W1
  Q2 -.-> W2
  A2 -.-> W3
  A3 -.-> W2
  A4 -.-> W4
  A4 -.-> WB
  Q1 -.-> DLQ
  Q2 -.-> DLQ

  %% note
  note right of A6: Retries on qc/quant; idempotency via markers; Workload Identity (no keys)
```
