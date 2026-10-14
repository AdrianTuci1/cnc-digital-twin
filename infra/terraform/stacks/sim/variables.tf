variable "project_name" {
  description = "Prefixul resurselor. Folosit și la numele bucket-ului de state."
  type        = string
  default     = "cncdt"
}

variable "environment" {
  description = "Mediul instanței de simulare."
  type        = string
  default     = "dev"
}

variable "region" {
  description = "Regiunea AWS. g5.2xlarge este disponibilă în toate regiunile comerciale majore."
  type        = string
  default     = "eu-central-1"
}

variable "aws_profile" {
  description = "Profilul AWS CLI. Gol folosește lanțul standard de credențiale."
  type        = string
  default     = ""
}

variable "launch_confirmed" {
  description = <<-EOT
    Confirmarea explicită că lansarea resurselor plătite este deliberată. Implicit `false`, iar
    `terraform_data.launch_guard` refuză atât planul, cât și aplicarea, cât timp valoarea rămâne
    `false`. Se pune pe `true` cu `-var launch_confirmed=true`: `tools/twinctl/twinctl` o face
    singur pe căile de lansare, după ce `INFRA_LAUNCH_ENABLED=true` în `.env`. Pe căile de
    ștergere twinctl trimite tot `true`, deși precondiția nu se evaluează la `destroy`.
    Detalii: ops/runbooks/launch-guard.md.
  EOT
  type        = bool
  default     = false
}

variable "instance_type" {
  description = "Tipul instanței GPU."
  type        = string
  default     = "g5.2xlarge"
}

variable "use_spot" {
  description = <<-EOT
    Cumpără instanța de pe piața spot (implicit) în loc de on-demand. Spot costă aproximativ
    jumătate; la întrerupere AWS anunță cu două minute și termină instanța, fără să repornească
    nimic automat, deci revenirea este o decizie explicită (`make sim-up` din AMI-ul golden).
    `SIM_USE_SPOT=false` în `.env` readuce on-demand. Detalii în ADR 0010.
  EOT
  type        = bool
  default     = true
}

variable "ami_id" {
  description = "AMI explicit. Gol înseamnă detectare automată."
  type        = string
  default     = ""
}

variable "root_volume_size_gb" {
  description = "Dimensiunea volumului rădăcină."
  type        = number
  default     = 200
}

variable "root_volume_iops" {
  description = "IOPS provisionate pentru volumul gp3 (baseline gratuit: 3000)."
  type        = number
  default     = 3000
}

variable "root_volume_throughput" {
  description = "Debitul în MiB/s pentru volumul gp3 (baseline gratuit: 125)."
  type        = number
  default     = 125
}

variable "vpc_id" {
  description = "VPC-ul instanței. Gol folosește VPC-ul implicit."
  type        = string
  default     = ""
}

variable "subnet_id" {
  description = "Subnetul instanței. Gol alege automat un subnet public."
  type        = string
  default     = ""
}

variable "availability_zone" {
  description = <<-EOT
    Zona în care pornește instanța. Gol alege automat un subnet public. Cu un volum de date
    atașat, twinctl scrie aici zona volumului, pentru că un volum EBS aparține unei singure zone.
  EOT
  type        = string
  default     = ""
}

variable "data_volume_id" {
  description = <<-EOT
    ID-ul volumului EBS persistent (stack-ul `sim-data`) care se atașează instanței. Gol
    înseamnă doar discul rădăcină. Volumul nu se șterge cu `make sim-down`: trăiește în alt
    state și se șterge exclusiv cu `make sim-data-down`.
  EOT
  type        = string
  default     = ""
}

variable "allocate_eip" {
  description = "Alocă un Elastic IP stabil."
  type        = bool
  default     = true
}

variable "ssh_key_name" {
  description = "Cheie SSH existentă în AWS."
  type        = string
  default     = ""
}

variable "ssh_key_public_key_path" {
  description = "Cheia publică locală folosită pentru a crea o cheie nouă în AWS."
  type        = string
  default     = "~/.ssh/cncdt-sim.pub"
}

variable "ssh_allowed_cidrs" {
  description = "CIDR-uri permise pe portul 22. Valoarea specială `auto` adaugă IP-ul public al mașinii curente."
  type        = list(string)
  default     = ["auto"]
}

variable "enable_turn" {
  description = "Activează serverul TURN/STUN pe instanță."
  type        = bool
  default     = false
}

variable "webrtc_public_ports" {
  description = "Deschide porturile WebRTC către internet. Folosiți doar cu TURN și autentificare."
  type        = bool
  default     = false
}

variable "signaling_port" {
  description = "Portul TCP de semnalizare WebRTC (Isaac Sim 5.0: 49100)."
  type        = number
  default     = 49100
}

variable "stream_port" {
  description = "Port păstrat pentru compatibilitate; în Isaac Sim 5.0 media este UDP."
  type        = number
  default     = 49100
}

variable "media_port_range" {
  description = "Intervalul UDP pentru media WebRTC (Isaac Sim 5.0: 47998-48020)."
  type        = string
  default     = "47998-48020"
}

variable "isaac_sim_image" {
  description = "Imaginea de container Isaac Sim."
  type        = string
  default     = "nvcr.io/nvidia/isaac-sim:latest"
}

variable "isaac_sim_entrypoint_script" {
  description = "Scriptul de livestreaming din container."
  type        = string
  default     = "./isaac-sim.streaming.sh"
}

variable "assets_bucket_name" {
  description = "Bucket-ul S3 cu asset-urile de simulare."
  type        = string
  default     = ""
}

variable "data_lake_bucket_arns" {
  description = "Bucket-urile în care instanța poate scrie telemetrie și snapshot-uri."
  type        = list(string)
  default     = []
}

variable "autostart_sim" {
  description = "Pornește automat containerul Isaac Sim la boot."
  type        = bool
  default     = false
}

variable "install_cloudwatch_agent" {
  description = "Instalează CloudWatch Agent pentru metrice de GPU."
  type        = bool
  default     = true
}

variable "enable_dcv" {
  description = <<-EOT
    Instalează desktopul GNOME și serverul Amazon DCV. Implicit pornit: AMI-ul Amazon Deep
    Learning nu are server grafic, deci fără acest pas instanța rămâne fără interfață grafică.
  EOT
  type        = bool
  default     = true
}

variable "dcv_port" {
  description = "Portul TCP al serverului DCV. Accesul se face implicit prin tunel SSH."
  type        = number
  default     = 8443
}

variable "dcv_public_access" {
  description = "Deschide portul DCV către CIDR-urile de SSH. Nerecomandat."
  type        = bool
  default     = false
}

variable "isaac_content_mirror" {
  description = <<-EOT
    Oglindește local conținutul Isaac Sim la bootstrap (descărcare în fundal, reluabilă) și
    trimite browserele de asset-uri spre copia locală. Fără oglindă, browserele de conținut
    rămân goale, pentru că listarea directoarelor din bucketul public Omniverse nu funcționează.
  EOT
  type        = bool
  default     = true
}

variable "isaac_content_url" {
  description = "Rădăcina publică a conținutului Isaac Sim, cu versiunea inclusă."
  type        = string
  default     = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.0"
}

variable "isaac_content_sections" {
  description = <<-EOT
    Secțiunile (sau sub-căile) oglindite. Implicit cele necesare celulei CNC: Robots (brațe),
    Props (benzi, palete), Materials, Sensors și Environments/Simple_Warehouse (scena de hală).
    Se pot adăuga secțiuni întregi — Environments 18 GB, Samples 11 GB, People 10 GB,
    IsaacLab 7 GB — cu timp de descărcare pe măsură.
  EOT
  type        = list(string)
  default     = ["Robots", "Props", "Materials", "Sensors", "Environments/Simple_Warehouse"]
}

variable "extra_bootstrap_commands" {
  description = "Comenzi bash rulate la finalul bootstrap-ului."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Etichete suplimentare."
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------- Client VPN
variable "vpn_enabled" {
  description = <<-EOT
    Activează AWS Client VPN și închide porturile WebRTC către internet. Clientul se conectează
    în tunel și ajunge la instanță pe IP-ul privat. Se plătește doar cât timp endpoint-ul există.
  EOT
  type        = bool
  default     = false
}

variable "vpn_client_cidr_block" {
  description = "Pulul de adrese al clienților VPN (minim /22, fără suprapunere cu VPC-ul)."
  type        = string
  default     = "10.100.0.0/22"
}

variable "vpn_port" {
  description = "Portul endpoint-ului Client VPN."
  type        = number
  default     = 443
}

variable "vpn_transport_protocol" {
  description = "udp sau tcp pentru tunel."
  type        = string
  default     = "udp"
}

variable "vpn_client_common_name" {
  description = "CN-ul certificatului de client."
  type        = string
  default     = "operator"
}
