resource "google_storage_bucket" "state" {
  name                        = local.state_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning {
    enabled = true
  }
  lifecycle {
    prevent_destroy = true
  }
  depends_on = [google_project_service.apis]
}

data "google_iam_policy" "state" {
  binding {
    role    = "roles/${local.identities.state_owner_role}"
    members = ["user:${var.owner_email}"]
  }
  # GCS backend lists workspaces at bucket scope. This reveals object names,
  # never bootstrap state contents; object reads/writes remain prefix-scoped.
  binding {
    role = "roles/${local.identities.state_listing_role}"
    members = [
      for grant in local.identities.state_grants :
      "serviceAccount:${local.account_emails[grant.member]}"
    ]
  }
  dynamic "binding" {
    for_each = local.identities.state_grants
    content {
      role    = "roles/${binding.value.role}"
      members = ["serviceAccount:${local.account_emails[binding.value.member]}"]
      condition {
        title      = "${binding.value.member}_main_state"
        expression = "resource.name.startsWith('projects/_/buckets/${local.state_bucket}/objects/${binding.value.prefix}')"
      }
    }
  }
}

resource "google_storage_bucket_iam_policy" "state" {
  bucket      = google_storage_bucket.state.name
  policy_data = data.google_iam_policy.state.policy_data
}
