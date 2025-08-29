variable "project_id" { type = string }
variable "location" {
  type    = string
  default = "northamerica-northeast1"
}
variable "bucket_prefix" {
  type    = string
  default = "rna-dev"
}
variable "sa_name" {
  type    = string
  default = "airflow-bucket-rw"
}
