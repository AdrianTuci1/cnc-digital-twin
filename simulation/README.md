# Simulare — NVIDIA Isaac Sim

Această parte produce datele: celula CNC, senzorii, camera de inspecție și degradarea
controlată. Tot ce ține de infrastructură trăiește în `infra/`, iar contractele de date în
`telemetry/schemas/`.

## Cuprins

| Cale | Rol |
| --- | --- |
| `docker/` | Rularea Isaac Sim în container, local sau pe instanța GPU. |
| `docker/coturn/` | Server TURN/STUN pentru acces la stream fără tunel. |
| `docker/docker-compose.local.yml` | Mediu local: broker MQTT și stocare compatibilă S3. |
| `scripts/` | Scripturi operaționale, identice cu cele instalate pe instanță. Inclusiv `build-scene.py`, scheletul scenei USD. |
| `extensions/cncdt.cnc_cell/` | Extensia Kit: panoul de control, degradarea, telemetria. |
| `assets/` | Asset-uri USD. Se urcă în S3, nu se comită. Directorul este montat read-only în container. |

Scena USD însăși lipsește la început. Contractul de căi pe care îl așteaptă extensia, de unde
vin props-urile și cum se asamblează sunt descrise în
[docs/09-scena-celulei-cnc.md](../docs/09-scena-celulei-cnc.md).

## Cum se obține ce ne trebuie

Trei lucruri pe care le cere proiectul, și metoda pentru fiecare:

### 1. Interfața de control

Interfața nu se construiește în HTML separat. Isaac Sim rulează peste Kit, care are propriul
sistem de UI (`omni.ui`), iar extensia noastră desenează un panou în fereastra aplicației.

- Local, cu interfață: `ui.Window` din `omni.ui` apare în fereastra Isaac Sim.
- Prin streaming: același panou este transmis în browser ca parte din viewport, deci nu e
  nevoie de un al doilea UI web.
- Butoanele de haos (uzură de sculă, frecare de articulație, defecte aleatoare) și afișarea
  stării sunt în `extensions/cncdt.cnc_cell/cncdt/cnc_cell/ui/panel.py`.

Vezi și `docs/08-isaac-sim-guide.md` pentru conceptele de bază (Kit, extensii, omni.ui).

### 2. Degradarea controlată

Degradarea este o modificare de parametri fizici plus o componentă sintetică în semnal:

- **Uzura sculei** crește amplitudinea vibrațiilor și scade calitatea suprafeței.
- **Frecarea articulațiilor** crește cuplul și întârzie ciclurile de mișcare.
- **Defectele aleatoare** marchează piese ca defecte la un interval configurabil.

Implementarea: `extensions/cncdt.cnc_cell/cncdt/cnc_cell/degradation/model.py`.

### 3. Datele

Datele vin din trei surse, toate citite de `telemetry/`:

| Sursă | Cum se citește | Ce produce |
| --- | --- | --- |
| Articulații robot și ax CNC | efortul și viteza per articulație | sarcină, cuplu, frecare |
| Senzori de proces | proprietăți pe prim, prin PhysX | vibrații RMS, temperatură, debit răcire |
| Cameră de inspecție | Replicator, writer de imagine | cadre QA pentru verdictul de calitate |

Implementarea: `extensions/cncdt.cnc_cell/cncdt/cnc_cell/telemetry/`.

## Rulare

Pe instanța GPU, prin `twinctl`:

```bash
make sim-up                                     # creează instanța + Isaac Sim, afișează datele de conectare
make sim-push                                   # urcă simulation/ pe instanță
./tools/twinctl/twinctl ssh /opt/cncdt/scripts/start-isaac.sh
make sim-logs                                   # urmărește logurile
make sim-connect                                # tunel + URL client WebRTC
```

Local, pe o mașină cu GPU NVIDIA:

```bash
export ISAAC_SIM_IMAGE=nvcr.io/nvidia/isaac-sim:latest
docker compose -f simulation/docker/docker-compose.isaac.yml up -d
docker compose -f simulation/docker/docker-compose.isaac.yml logs -f
```

Fără Isaac Sim, pentru testarea pipeline-ului de date:

```bash
make docker-local     # broker MQTT local + stocare compatibilă S3
make publish-dev      # publicator care simulează celula
```

## Stadiul extensiei

Codul extensiei este un schelet funcțional, scris pe API-urile documentate ale Isaac Sim.
Nu a fost încă rulat într-o instanță Isaac Sim. Pașii de validare, în ordine:

1. `scripts/selftest.sh` pe instanță — confirmă GPU, drivere și container.
2. Pornirea containerului și confirmarea `App ready` în loguri.
3. Încărcarea extensiei și verificarea că panoul apare.
4. Confirmarea că mesajele ajung în AWS IoT Core (`aws iot-data` sau CloudWatch Logs).
