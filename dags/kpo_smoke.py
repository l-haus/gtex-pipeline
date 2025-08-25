from airflow import DAG
from pendulum import datetime
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator

with DAG("kpo_smoke", start_date=datetime(2024,1,1), schedule=None, catchup=False) as dag:
    KubernetesPodOperator(
        task_id="echo",
        name="echo",
        namespace="rnaseq",
        image="busybox:1.36",
        cmds=["/bin/sh","-lc"],
        arguments=["echo hello-from-kpo && sleep 3"],
        get_logs=True,
        is_delete_operator_pod=True,
        kubernetes_conn_id="k8s_gke",
    )
