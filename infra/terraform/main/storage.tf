# Trust reports. Daily-refresh raw documents stay with GCP-5; this bucket exists
# now because forseti-trust-check mounts it at /gcs.
resource "google_storage_bucket" "artifacts" {
  name                        = "${var.project_id}-artifacts"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  # Match the API default so a missing block does not become perpetual drift.
  soft_delete_policy {
    retention_duration_seconds = 604800
  }

  lifecycle_rule {
    condition {
      age            = 180
      matches_prefix = ["reports/"]
    }
    action {
      type = "Delete"
    }
  }
}
