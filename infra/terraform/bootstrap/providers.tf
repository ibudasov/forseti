provider "google" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}

provider "github" {
  owner = split("/", var.github_repository)[0]
}

data "google_project" "project" {
  project_id = var.project_id
}

locals {
  repository_name = split("/", var.github_repository)[1]
  state_bucket    = "${var.project_id}-tfstate"
  identities      = jsondecode(file("${path.module}/identities.json"))
  accounts        = local.identities.service_accounts
  account_emails  = { for name, account in google_service_account.accounts : name => account.email }
}
