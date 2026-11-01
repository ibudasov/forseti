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

variable "api_bootstrap_image" {
  description = "Placeholder image for the first creation of the API service; CD owns the deployed image."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "agent_modes" {
  description = "Runtime agent modes; set any mode to off as an emergency kill switch."
  type        = map(string)
  default = {
    FUNDAMENTAL_AGENT_MODE = "enforced"
    TECHNICAL_AGENT_MODE   = "enforced"
    CRITIC_MODE            = "enforced"
  }
  validation {
    condition = (
      toset(keys(var.agent_modes)) == toset([
        "FUNDAMENTAL_AGENT_MODE",
        "TECHNICAL_AGENT_MODE",
        "CRITIC_MODE",
      ]) &&
      alltrue([for mode in values(var.agent_modes) : contains(["off", "shadow", "enforced"], mode)])
    )
    error_message = "agent_modes must set the fundamental, technical and critic modes to off, shadow or enforced."
  }
}

variable "gemini_model" {
  description = "Gemini model used by the API agents."
  type        = string
  default     = "gemini-2.0-flash-001"
}

variable "embedding_model" {
  description = "Vertex embedding model used by the API."
  type        = string
  default     = "text-embedding-004"
}

variable "edgar_user_agent" {
  description = "SEC EDGAR user-agent value used for filing requests."
  type        = string
  default     = "Forseti/0.1 (gazer-flair9o@icloud.com)"
}
