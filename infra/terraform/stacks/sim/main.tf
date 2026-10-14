# Gardul de lansare (ops/runbooks/launch-guard.md). Nu creează nimic în AWS: doar refuză planul
# și aplicarea care ar crea sau ar extinde resurse plătite, cât timp `launch_confirmed` nu este
# `true`. Îl pune pe `true` doar `tools/twinctl/twinctl`, pe căile de lansare, după ce a verificat
# `INFRA_LAUNCH_ENABLED` din `.env`; verificat pe viu, precondiția NU se evaluează la `destroy`,
# deci ștergerea rămâne permisă. Acesta e nivelul care închide ocolirea prin `terraform apply`
# rulat direct din acest director, de aceea mesajul conține comenzile complete de rulat.
resource "terraform_data" "launch_guard" {
  lifecycle {
    precondition {
      condition     = var.launch_confirmed
      error_message = <<-EOT
        Lansarea infrastructurii de simulare este blocată deliberat: `launch_confirmed` este `false`.

        Resursele plătite ale acestui stack (instanța GPU spot, discul rădăcină, IP-ul public,
        endpoint-ul Client VPN) nu se creează fără o confirmare explicită. Refuzul apare înainte
        de orice apel la AWS, deci NU este o eroare de credențiale, de profil sau de rețea.

        Ca să lansezi deliberat:
          pune în .env:  INFRA_LAUNCH_ENABLED=true
          apoi rulează:  make sim-up          (sau ./tools/twinctl/twinctl up)

        Ori, direct din acest director:
          terraform apply -var launch_confirmed=true

        Ștergerea nu este afectată: `terraform destroy` rămâne permis, cu sau fără
        `-var launch_confirmed=true`. Detalii: ops/runbooks/launch-guard.md
      EOT
    }
  }
}

locals {
  common_tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Repository  = "cnc-digital-twin"
      ManagedBy   = "terraform"
    },
    var.tags,
  )

  # `auto` în lista de CIDR-uri se rezolvă la IP-ul public al mașinii care rulează Terraform.
  wants_auto_ip = contains(var.ssh_allowed_cidrs, "auto")

  ssh_allowed_cidrs = compact(concat(
    [for cidr in var.ssh_allowed_cidrs : cidr if cidr != "auto"],
    local.wants_auto_ip ? ["${chomp(data.http.my_ip[0].response_body)}/32"] : [],
  ))

  # Porturile pe care instanța le acceptă din tunelul Client VPN.
  vpn_client_rules = var.vpn_enabled ? {
    ssh       = { from_port = 22, to_port = 22, ip_protocol = "tcp" }
    signaling = { from_port = var.signaling_port, to_port = var.signaling_port, ip_protocol = "tcp" }
    media = {
      from_port   = tonumber(split("-", var.media_port_range)[0])
      to_port     = tonumber(split("-", var.media_port_range)[1])
      ip_protocol = "udp"
    }
  } : {}
}

# Folosit doar când cineva cere explicit CIDR-ul „auto”.
data "http" "my_ip" {
  count = local.wants_auto_ip ? 1 : 0
  url   = "https://checkip.amazonaws.com"

  request_headers = {
    Accept = "text/plain"
  }
}

module "sim" {
  source = "../../modules/sim-instance"

  project_name            = var.project_name
  environment             = var.environment
  instance_type           = var.instance_type
  use_spot                = var.use_spot
  ami_id                  = var.ami_id
  root_volume_size_gb     = var.root_volume_size_gb
  root_volume_iops        = var.root_volume_iops
  root_volume_throughput  = var.root_volume_throughput
  vpc_id                  = var.vpc_id
  subnet_id               = var.subnet_id
  availability_zone       = var.availability_zone
  data_volume_id          = var.data_volume_id
  allocate_eip            = var.allocate_eip
  ssh_key_name            = var.ssh_key_name
  ssh_key_public_key_path = var.ssh_key_public_key_path
  ssh_allowed_cidrs       = local.ssh_allowed_cidrs
  enable_turn             = var.enable_turn
  # Cu VPN pornit, streaming-ul nu mai are nicio cale publică: porturile se deschid doar
  # către pulul de adrese al tunelului.
  webrtc_public_ports         = var.webrtc_public_ports && !var.vpn_enabled
  vpn_client_cidr_block       = var.vpn_enabled ? var.vpn_client_cidr_block : ""
  livestream_public_address   = !var.vpn_enabled
  signaling_port              = var.signaling_port
  stream_port                 = var.stream_port
  media_port_range            = var.media_port_range
  isaac_sim_image             = var.isaac_sim_image
  isaac_sim_entrypoint_script = var.isaac_sim_entrypoint_script
  assets_bucket_name          = var.assets_bucket_name
  data_lake_bucket_arns       = var.data_lake_bucket_arns
  autostart_sim               = var.autostart_sim
  install_cloudwatch_agent    = var.install_cloudwatch_agent
  enable_dcv                  = var.enable_dcv
  dcv_port                    = var.dcv_port
  dcv_public_access           = var.dcv_public_access
  isaac_content_mirror        = var.isaac_content_mirror
  isaac_content_url           = var.isaac_content_url
  isaac_content_sections      = var.isaac_content_sections
  extra_bootstrap_commands    = var.extra_bootstrap_commands
  tags                        = var.tags
}

# Endpoint-ul Client VPN stă în același stack efemer: `make sim-down` îl șterge, iar în repaus
# nu se plătește nimic pentru el.
module "vpn" {
  count  = var.vpn_enabled ? 1 : 0
  source = "../../modules/client-vpn"

  project_name       = var.project_name
  environment        = var.environment
  vpc_id             = module.sim.vpc_id
  subnet_id          = module.sim.subnet_id
  authorized_cidr    = module.sim.vpc_cidr_block
  client_cidr_block  = var.vpn_client_cidr_block
  allowed_cidrs      = length(local.ssh_allowed_cidrs) > 0 ? local.ssh_allowed_cidrs : ["0.0.0.0/0"]
  port               = var.vpn_port
  transport_protocol = var.vpn_transport_protocol
  client_common_name = var.vpn_client_common_name
  tags               = var.tags
}

# Pachetele care ies din tunel intră în VPC cu adresa interfețelor endpoint-ului, nu cu cea a
# clientului, deci instanța trebuie să accepte trafic de la security group-ul endpoint-ului.
# Regulile stau în stack, nu în `modules/sim-instance`: acolo ar cere ID-ul security group-ului
# din `module.vpn`, care la rândul lui are nevoie de VPC-ul din `module.sim` (dependență
# circulară între module).
resource "aws_vpc_security_group_ingress_rule" "vpn_client" {
  for_each = local.vpn_client_rules

  security_group_id            = module.sim.security_group_id
  referenced_security_group_id = module.vpn[0].security_group_id
  description                  = "Client VPN (${each.key}) catre instanta de simulare"
  from_port                    = each.value.from_port
  to_port                      = each.value.to_port
  ip_protocol                  = each.value.ip_protocol

  tags = var.tags
}
