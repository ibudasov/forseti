resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com", "sqladmin.googleapis.com", "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com", "cloudscheduler.googleapis.com",
    "aiplatform.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com",
    "sts.googleapis.com", "cloudtrace.googleapis.com", "monitoring.googleapis.com",
    "logging.googleapis.com", "storage.googleapis.com", "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com", "serviceusage.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
