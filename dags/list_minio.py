from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.amazon.aws.hooks.s3 import S3Hook


def list_keys():
    hook = S3Hook(aws_conn_id="minio_s3")
    keys = hook.list_keys(bucket_name="rna-raw", prefix="tmp/")
    print("MINIO_KEYS:", keys)


with DAG("list_minio", start_date=datetime(2024, 1, 1), schedule=None, catchup=False) as dag:
    PythonOperator(task_id="list", python_callable=list_keys)
