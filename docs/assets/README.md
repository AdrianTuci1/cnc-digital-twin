# docs/assets — materiale pentru README

Aici stau fișierele media folosite în documentație.

## Video demonstrativ

`cnc-mujoco-demo.mp4` este copia comprimată a înregistrării buclei complete: 2 min 30 s,
1280×720, 30 fps, H.264 fără audio, 6,3 MB. Miniatura `cnc-mujoco-demo.jpg` este un cadru din
filmare, la secunda 30, când se văd simultan brațele, gauge-urile per braț și dashboard-ul.

GitHub nu redă un tag `<video>` care trimite la un fișier din repository, deci README-ul folosește
o miniatură care deschide filmarea. Fișierul `.mp4` se redă în vizualizatorul GitHub când este
deschis direct.

Editorul web din GitHub acceptă lipirea filmării direct în README: fișierul ajunge găzduit de
GitHub, iar linkul generat (`https://github.com/user-attachments/assets/...`) se redă inline.
Copia din `docs/assets/` rămâne rezerva din repository, iar originalul din `~/Movies/` nu se
urcă.

### Refacerea copiei comprimate

Originalul rămâne în afara repository-ului (`~/Movies/cnc-mujoco.mov`). Copia se face cu
`ffmpeg`, păstrând rezoluția și fără audio:

```bash
ffmpeg -i ~/Movies/cnc-mujoco.mov -an -c:v libx264 -preset slow -crf 22 \
  -pix_fmt yuv420p -movflags +faststart -map_metadata -1 \
  docs/assets/cnc-mujoco-demo.mp4
```

Un cadru pentru miniatură:

```bash
ffmpeg -ss 30 -i docs/assets/cnc-mujoco-demo.mp4 -frames:v 1 -q:v 2 \
  docs/assets/cnc-mujoco-demo.jpg
```

## Reguli

- Fără capturi cu date reale de client sau credențiale în imagini.
- Fișierele peste ~10 MB se pun în S3, nu în git; în README se pune linkul. Copia comprimată
  de aici stă sub acest prag.
