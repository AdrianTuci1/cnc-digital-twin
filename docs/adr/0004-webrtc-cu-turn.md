# ADR 0004: WebRTC cu TURN, fără tunel TCP pentru media

- **Status:** acceptat
- **Data:** 2026-10-04
- **Decis de:** echipa de proiect

## Context

Isaac Sim transmite scena către browser prin WebRTC (`isaac-sim.streaming.sh`). Portul de
semnalizare (8211 implicit) este TCP și poate fi tunelat prin SSH sau SSM. Media WebRTC este
UDP și nu poate trece prin același tunel TCP: browserul nu poate deschide fluxul, chiar dacă
pagina se încarcă.

Documentația NVIDIA descrie două soluții: expunerea porturilor UDP direct pe internet sau un
server TURN care retransmite media prin TCP.

## Decizie

1. **Implicit: tunel pentru semnalizare și TURN pentru media.** Pornim coturn pe instanță
   (`simulation/docker/coturn/turnserver.conf`, port 3478 TCP/UDP, interval de releu
   49160–49200) și configurăm clientul WebRTC cu `iceTransportsType: relay`. Astfel media
   trece prin TURN pe TCP, deci nu este nevoie să deschidem UDP către internet.
2. **Alternativă pentru rețele de încredere: porturi UDP direct.** `twinctl` poate deschide
   intervalul de releu în security group, dar numai când `SIM_ENABLE_TURN=false` și
   `SIM_SSH_ALLOWED_CIDRS` este o listă explicită de CIDR-uri.
3. **Tunelul rămâne pentru administrare.** `twinctl connect` deschide tunelul pentru
   semnalizare și afișează link-ul; comportamentul este raportat explicit, inclusiv limita
   legată de media.

## Consecințe

- Tunelul nu este singura cale de operare: `twinctl url` afișează URL-ul, iar clientul poate
  fi folosit și din alt browser, atâta timp cât semnalizarea este accesibilă.
- Costul TURN este zero în afara instanței; coturn rulează pe GPU-ul deja plătit.
- Credențialele TURN nu stau în fișier: se citesc din SSM Parameter Store.
- Latența crește pe relay TCP; pentru demo este acceptabil, pentru producție se recomandă
  UDP direct, în rețea controlată.
- Intervalul de porturi de releu se poate schimba între versiunile Isaac Sim, deci este
  parametru în `.env`, nu constantă în cod.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Doar tunel SSH/SSM | Media UDP nu trece prin tunel TCP; pagina se încarcă, dar fluxul nu pornește. |
| Doar porturi UDP publice | Expune instanța GPU pe internet; risc de securitate și de cost. |
| Un server TURN separat (EC2 dedicat) | Cost suplimentar pentru un demo; coturn pe instanța existentă este suficient. |
| VPN între Mac și VPC | Funcționează, dar adaugă infrastructură și configurare pe postul client. |
