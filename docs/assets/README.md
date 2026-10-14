# docs/assets — materiale pentru README

Aici stau fișierele media folosite în documentație.

## Video demonstrativ

README-ul are, în partea de sus, un slot de video care trimite la o înregistrare externă.
Ca să îl activați:

1. Urcați înregistrarea (YouTube, Loom, S3 cu link public sau orice URL stabil) și copiați linkul.
2. În `README.md`, înlocuiți `https://REPLACE-CU-LINK-VIDEO` cu linkul.
3. Opțional, înlocuiți miniatura `docs/assets/demo-thumbnail.svg` cu un cadru din video
   (`.png` sau `.jpg`, ~1280×720). Dacă schimbați numele fișierului, actualizați calea din README.

Miniatura actuală este un `svg` scris de mână, ca slot-ul să arate intenționat până există filmarea.

## Reguli

- Fără capturi cu date reale de client sau credențiale în imagini.
- Fișierele mari (peste ~10 MB) se pun în S3, nu în git; în README se pune linkul.
