resource "google_artifact_registry_repository" "forseti" {
  location      = var.region
  repository_id = "forseti"
  description   = "Forseti application images"
  format        = "DOCKER"
  lifecycle {
    precondition {
      condition     = alltrue([for grant in local.resource_grants : contains(["secret", "service", "job", "bucket"], grant.kind)])
      error_message = "Unknown resource grant kind in identities.json."
    }
    precondition {
      condition = alltrue([
        for grant in local.resource_grants :
        grant.member == "owner" || contains(keys(local.identities.service_accounts), grant.member)
      ])
      error_message = "Resource grant members must be owner or a declared service account."
    }
  }

  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "delete-old-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "604800s"
    }
  }
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }
}
