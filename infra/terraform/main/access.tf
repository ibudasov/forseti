locals {
  identities = jsondecode(file("${path.module}/../bootstrap/identities.json"))
  # Later milestones populate these maps with resource IDs, not user input.
  secret_resources = {
    "forseti-database-url"          = google_secret_manager_secret.database_url.id
    "forseti-owner-database-url"    = google_secret_manager_secret.owner_database_url.id
    "forseti-app-db-password"       = google_secret_manager_secret.app_db_password.id
    "forseti-alpha-vantage-api-key" = google_secret_manager_secret.alpha_vantage_api_key.id
  }
  service_resources = {
    "forseti-api" = google_cloud_run_v2_service.api.name
  }
  job_resources    = {}
  bucket_resources = {}
  resource_grants = {
    for grant in local.identities.resource_grants :
    "${grant.kind}/${grant.resource}/${grant.member}/${grant.role}" => grant
  }
  grant_members = {
    for key, grant in local.resource_grants : key => (
      grant.member == "owner" ? "user:${var.owner_email}" :
      "serviceAccount:${grant.member}@${var.project_id}.iam.gserviceaccount.com"
    )
  }
}

resource "google_secret_manager_secret_iam_member" "grants" {
  for_each  = { for key, grant in local.resource_grants : key => grant if grant.kind == "secret" }
  secret_id = local.secret_resources[each.value.resource]
  role      = "roles/${each.value.role}"
  member    = local.grant_members[each.key]
}

resource "google_cloud_run_v2_service_iam_member" "grants" {
  for_each = { for key, grant in local.resource_grants : key => grant if grant.kind == "service" }
  name     = local.service_resources[each.value.resource]
  location = var.region
  role     = "roles/${each.value.role}"
  member   = local.grant_members[each.key]
}

resource "google_cloud_run_v2_job_iam_member" "grants" {
  for_each = { for key, grant in local.resource_grants : key => grant if grant.kind == "job" }
  name     = local.job_resources[each.value.resource]
  location = var.region
  role     = "roles/${each.value.role}"
  member   = local.grant_members[each.key]
}

resource "google_storage_bucket_iam_member" "grants" {
  for_each = { for key, grant in local.resource_grants : key => grant if grant.kind == "bucket" }
  bucket   = local.bucket_resources[each.value.resource]
  role     = "roles/${each.value.role}"
  member   = local.grant_members[each.key]
}
