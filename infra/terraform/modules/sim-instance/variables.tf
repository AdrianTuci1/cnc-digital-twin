variable "project_name" {
  description = "Prefix scurt folosit la numele tuturor resurselor."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.project_name))
    error_message = "project_name trebuie să înceapă cu literă mică și să conțină doar litere mici, cifre și cratime."
  }
}

variable "environment" {
  description = "Numele mediului: dev, staging sau prod."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment trebuie să fie dev, staging sau prod."
  }
}

variable "instance_type" {
  description = "Tipul instanței GPU. g5.2xlarge oferă un A10G cu 24 GB VRAM."
  type        = string
  default     = "g5.2xlarge"
}

variable "use_spot" {
  description = <<-EOT
    Cumpără instanța de pe piața spot (cerere `one-time`) în loc de on-demand. Spot costă
    aproximativ jumătate; la întrerupere AWS anunță cu două minute și termină instanța, iar
    nimic nu o repornește automat: următoarea lansare este o decizie explicită, din AMI-ul
    golden (`make sim-up`). Fără spot, instanța rulează la tarif on-demand.
  EOT
  type        = bool
  default     = false
}

variable "ami_id" {
  description = "AMI explicit. Gol înseamnă detectarea automată a AMI-ului Amazon Deep Learning cu drivere NVIDIA."
  type        = string
  default     = ""
}

variable "ami_name_pattern" {
  description = <<-EOT
    Filtru de nume pentru AMI-ul detectat automat. Proprietarul este întotdeauna `amazon`.
    Varianta „Base OSS Nvidia Driver GPU" conține driverele NVIDIA, Docker și toolkit-ul de
    containere, fără framework-uri de ML: Isaac Sim își aduce PyTorch în container.
  EOT
  type        = string
  default     = "Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)*"
}

variable "root_volume_size_gb" {
  description = "Dimensiunea volumului rădăcină gp3. Cei ~50 GB folosiți efectiv lasă loc de cache-uri și asset-uri."
  type        = number
  default     = 200
}

variable "root_volume_iops" {
  description = "IOPS provisionate pentru volumul gp3. 3000 este baseline-ul gratuit; peste el se plătește lunar."
  type        = number
  default     = 3000
}

variable "root_volume_throughput" {
  description = "Debitul în MiB/s pentru volumul gp3. 125 este baseline-ul gratuit; peste el se plătește lunar."
  type        = number
  default     = 125
}

variable "root_volume_kms_key_id" {
  description = "Cheie KMS pentru criptarea volumului. Gol folosește cheia implicită a contului."
  type        = string
  default     = ""
}

variable "vpc_id" {
  description = "VPC-ul în care se lansează instanța. Gol folosește VPC-ul implicit."
  type        = string
  default     = ""
}

variable "subnet_id" {
  description = "Subnetul instanței. Gol alege automat un subnet public din VPC."
  type        = string
  default     = ""
}

variable "availability_zone" {
  description = <<-EOT
    Zona în care trebuie să pornească instanța. Se folosește când există un volum de date
    persistent: un volum EBS trăiește într-o singură zonă, iar o instanță din altă zonă nu îl
    poate atașa. Gol înseamnă că zona se alege singură (prima disponibilă).
  EOT
  type        = string
  default     = ""
}

variable "data_volume_id" {
  description = <<-EOT
    Volumul EBS de date, creat de stack-ul `sim-data` și atașat instanței la fiecare lansare.
    Gol înseamnă că instanța folosește doar discul rădăcină efemer. Volumul ține ce
    supraviețuiește instanței (oglinda de conținut Isaac Sim, cache-urile de shader, codul
    simulării); bootstrap-ul îl montează la /opt/cncdt.
  EOT
  type        = string
  default     = ""
}

variable "data_volume_device_name" {
  description = "Numele cerut la atașare. Nitro îl expune în guest ca /dev/nvme*, însă ID-ul volumului apare în /dev/disk/by-id."
  type        = string
  default     = "/dev/sdf"
}

variable "vpn_client_cidr_block" {
  description = <<-EOT
    Pulul de adrese al clienților Client VPN. Dacă este dat, porturile de streaming și SSH se
    deschid către acest CIDR, iar clientul ajunge la instanță prin tunel. Gol înseamnă fără VPN.
  EOT
  type        = string
  default     = ""
}

variable "livestream_public_address" {
  description = <<-EOT
    Adresa pe care o anunță Isaac Sim clienților. `true` folosește IP-ul public (porturile sunt
    deschise direct), `false` folosește IP-ul privat (accesul se face prin tunelul Client VPN).
  EOT
  type        = bool
  default     = true
}

variable "isaac_content_mirror" {
  description = <<-EOT
    Oglindește local conținutul Isaac Sim (Assets/Isaac/<versiune>) și trimite browserele de
    asset-uri spre copia locală. Fără oglindă, browserele rămân goale: listarea directoarelor
    din bucketul public Omniverse nu funcționează, deși fișierele individuale se descarcă.
  EOT
  type        = bool
  default     = true
}

variable "isaac_content_url" {
  description = "Rădăcina de conținut Isaac Sim de oglindit (URL public Omniverse, cu versiunea)."
  type        = string
  default     = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/5.0"
}

variable "isaac_content_sections" {
  description = <<-EOT
    Secțiunile (sau sub-căile) de conținut oglindite. Implicit cele necesare asamblării
    celulei: Robots (brațe), Props (benzi, palete), Materials, Sensors (~10 GB) și
    Environments/Simple_Warehouse (538 MB, scena de hală). Environments întreg (18 GB),
    Samples (11 GB), People (10 GB) și IsaacLab (7 GB) se adaugă doar când sunt folosite.
  EOT
  type        = list(string)
  default     = ["Robots", "Props", "Materials", "Sensors", "Environments/Simple_Warehouse"]
}

variable "associate_public_ip" {
  description = "Alocă un IP public instanței. Necesar pentru SSM și pentru accesul direct din browser."
  type        = bool
  default     = true
}

variable "allocate_eip" {
  description = "Alocă un Elastic IP stabil, ca să nu se schimbe adresa la fiecare stop/start."
  type        = bool
  default     = true
}

variable "ssh_key_name" {
  description = "Numele unei chei SSH existente în AWS. Mutual exclusiv cu ssh_key_public_key_path."
  type        = string
  default     = ""
}

variable "ssh_key_public_key_path" {
  description = "Calea locală către cheia publică folosită pentru a crea o cheie SSH nouă în AWS."
  type        = string
  default     = ""
}

variable "ssh_allowed_cidrs" {
  description = "CIDR-uri permise pe portul 22. Listă goală înseamnă că portul rămâne închis (acces doar prin SSM)."
  type        = list(string)
  default     = []
}

variable "enable_turn" {
  description = "Deschide porturile pentru serverul TURN/STUN de pe instanță (acces la stream fără tunel)."
  type        = bool
  default     = false
}

variable "turn_relay_port_range" {
  description = "Intervalul de porturi UDP pentru relay TURN."
  type        = string
  default     = "49160-49200"
}

variable "webrtc_public_ports" {
  description = "Deschide porturile de semnalizare și media WebRTC către internet. Nerecomandat fără TURN și fără autentificare."
  type        = bool
  default     = false
}

variable "signaling_port" {
  description = "Portul TCP de semnalizare WebRTC. Isaac Sim 5.0 îl ia din app.livestream.port (49100)."
  type        = number
  default     = 49100
}

variable "stream_port" {
  description = "Port păstrat pentru compatibilitate cu twinctl și ieșirile vechi. În Isaac Sim 5.0 media este UDP, în intervalul media_port_range."
  type        = number
  default     = 49100
}

variable "media_port_range" {
  description = "Intervalul UDP din care Isaac Sim alocă porturile media WebRTC (minHostPort-maxHostPort)."
  type        = string
  default     = "47998-48020"

  validation {
    condition     = can(regex("^[0-9]+-[0-9]+$", var.media_port_range))
    error_message = "media_port_range trebuie scris ca \"min-max\", de exemplu 47998-48020."
  }
}

variable "isaac_sim_image" {
  description = "Imaginea de container Isaac Sim."
  type        = string
  default     = "nvcr.io/nvidia/isaac-sim:latest"
}

variable "isaac_sim_entrypoint_script" {
  description = "Scriptul de livestreaming din interiorul containerului."
  type        = string
  default     = "./isaac-sim.streaming.sh"
}

variable "assets_bucket_name" {
  description = "Bucketul S3 din care instanța descarcă asset-urile de simulare."
  type        = string
  default     = ""
}

variable "data_lake_bucket_arns" {
  description = "Bucket-urile S3 cărora instanța le poate scrie telemetrie și snapshot-uri."
  type        = list(string)
  default     = []
}

variable "iot_publish_resource_arns" {
  description = "Topic-urile IoT pe care instanța le poate publica, în format ARN cu wildcard pe topic."
  type        = list(string)
  default     = []
}

variable "autostart_sim" {
  description = "Pornește containerul Isaac Sim automat la boot. Recomandat false: pornește explicit după verificarea driverului."
  type        = bool
  default     = false
}

variable "install_cloudwatch_agent" {
  description = "Instalează CloudWatch Agent pentru metrice de GPU și disc."
  type        = bool
  default     = true
}

variable "enable_dcv" {
  description = <<-EOT
    Instalează desktopul GNOME și serverul Amazon DCV. AMI-ul Amazon Deep Learning pornește
    fără server grafic (fără GDM3, fără Xorg), deci fără acest pas nu există interfață grafică
    și nici o sesiune DCV la care să te conectezi.
  EOT
  type        = bool
  default     = true
}

variable "dcv_port" {
  description = "Portul TCP al serverului DCV. Implicit se ajunge la el prin tunel SSH, nu direct."
  type        = number
  default     = 8443
}

variable "dcv_public_access" {
  description = "Deschide portul DCV către ssh_allowed_cidrs. Nerecomandat: portul expune login-ul pe internet."
  type        = bool
  default     = false
}

variable "extra_bootstrap_commands" {
  description = "Comenzi bash adăugate la finalul scriptului de bootstrap."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Etichete suplimentare aplicate tuturor resurselor."
  type        = map(string)
  default     = {}
}
