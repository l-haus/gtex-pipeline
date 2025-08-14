provider "google" {
  project = var.project_id
  region  = var.location
}

# Enable required APIs (idempotent)
resource "google_project_service" "apis" {
  for_each           = toset(["storage.googleapis.com", "iam.googleapis.com"])
  service            = each.value
  disable_on_destroy = false
}

resource "random_id" "suffix" { byte_length = 2 }
locals { bucket_name = "${var.bucket_prefix}-${random_id.suffix.hex}" }

resource "google_storage_bucket" "data" {
  name                        = local.bucket_name
  location                    = var.location
  storage_class               = "STANDARD"
  force_destroy               = true
  uniform_bucket_level_access = true
  labels                      = { project = "gtex", env = "dev" }

  versioning { enabled = true }

  lifecycle_rule {
    condition {
      age            = 7
      matches_prefix = ["tmp/"]
    }
    action { type = "Delete" }
  }
}

resource "google_service_account" "airflow" {
  account_id   = var.sa_name
  display_name = "Airflow bucket RW (dev)"
}

# Least privilege: objectAdmin on this bucket only
resource "google_storage_bucket_iam_member" "sa_rw" {
  bucket = google_storage_bucket.data.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.airflow.email}"
}
