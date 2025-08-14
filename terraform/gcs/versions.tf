terraform {
  required_version = ">= 1.12.2"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.40" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}
