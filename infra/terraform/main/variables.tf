variable "project_id" {
  type    = string
  default = "forseti-505116"
}

variable "region" {
  type    = string
  default = "europe-west4"
}

variable "owner_email" {
  type      = string
  sensitive = true
}

variable "credentials_version" {
  description = "Bump to rotate the database passwords; all write-only credentials rotate together."
  type        = number
  default     = 1
}

variable "housekeeping_bootstrap_image" {
  description = "Placeholder image for the first creation of the housekeeping job; CD replaces it with the API image."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/job:latest"
}
