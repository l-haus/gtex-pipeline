from airflow.decorators import dag, task
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from pendulum import datetime, duration, now

AWS_CONN_ID = "minio_s3"
BUCKETS = ["rna-raw", "rna-processed"]
PREFIX = "tmp/"
RETENTION_DAYS = 7


@dag(
    dag_id="purge_minio_tmp",
    start_date=datetime(2024, 1, 1, tz="UTC"),
    schedule="@daily",
    catchup=False,
    tags=["maintenance", "minio"],
)
def purge_minio_tmp():
    @task
    def purge_bucket(bucket: str) -> dict:
        """Delete objects under PREFIX older than RETENTION_DAYS; return stats."""
        hook = S3Hook(aws_conn_id=AWS_CONN_ID)
        s3 = hook.get_conn()
        cutoff = now("UTC") - duration(days=RETENTION_DAYS)

        paginator = s3.get_paginator("list_objects_v2")
        to_delete = []
        scanned = 0

        for page in paginator.paginate(Bucket=bucket, Prefix=PREFIX):
            for obj in page.get("Contents", []) or []:
                scanned += 1
                if obj["LastModified"] <= cutoff:
                    to_delete.append({"Key": obj["Key"]})
                    if len(to_delete) == 1000:
                        s3.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})
                        to_delete.clear()

        if to_delete:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})

        return {
            "bucket": bucket,
            "scanned": scanned,
            "deleted": scanned - len(to_delete) if scanned else 0,
        }

    purge_bucket.expand(bucket=BUCKETS)


purge_minio_tmp()
