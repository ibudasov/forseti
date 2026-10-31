terraform {
  required_version = ">= 1.17.0, < 2.0.0"
  backend "gcs" {
    prefix = "main/"
  }
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 8.0, < 9.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.7, < 4.0"
    }
  }
}
