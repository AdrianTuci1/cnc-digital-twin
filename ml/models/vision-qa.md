# Card de model: vision-qa-v3

- **Stare:** candidat pentru celula MuJoCo; familia e curată, verdictul e limitat de date.
- **Data:** 2026-10-25
- **Job:** `cncdt-vision-qa-v3-20261025-030125`, `ml.g5.xlarge` on-demand, 12 epoci
- **Artefact:** `s3://cncdt-dev-datalake-864981730944/models/vision-qa/vision-qa-v3/model.tar.gz`
- **Pornire:** greutățile v2 (`SM_CHANNEL_INIT`), ca familia să nu se învețe de la zero.

## Ce face

Un ResNet18 cu două capete peste același backbone, antrenat pe **cadrele camerei de sortare**
(`arm2_wrist`, la `belt2_pick`, piesa ținută în falci):

- **familia** — șase clase, folosită la alegerea compartimentului;
- **verdictul** — PASS/REJECT, folosit la decizia dintre compartment și rebut.

Răspunsul endpoint-ului conține `defect_probability`, `family` și `family_probability`.

## Date

Cadrele din `simulation/mujoco/runs/cell-vision-20261025`: șase familii × 50 de cicluri, 293 de
cadre de sortare. Splitul este pe variantă, cu identitatea variantei compusă cu sesiunea: fiecare
sesiune își numerotează piesele de la același prefix, iar dedublarea doar pe `variant_id` ar fi
amestecat sesiuni în același split.

| Split | Imagini | Familii |
| --- | --- | --- |
| train | 201 | 6 |
| val | 46 | 6 |
| test | 46 | 6 |

## Metrice pe test

| Cap | Rezultat |
| --- | --- |
| familie | acuratețe 1.000, macro recall 1.000 |
| verdict, prag 0.91 | recall PASS 0.71, recall REJECT 0.31, precizie REJECT 0.71, acuratețe echilibrată 0.513 |

Matricea de confuzie a verdictului: `[[10, 4], [22, 10]]` (rând = real, coloană = prezis).

## Limite

- Verdictul este antrenat pe 201 imagini. Defectele evidente se prind
  (`BORE_MISSING` 0.83),
  cele fine aproape deloc (`HOLE_OFF_CIRCLE`, `KEYWAY_MISSING`). Pentru sortarea reală trebuie
  colectate mai multe cicluri pe fiecare combinație familie × defect.
- Pragul de 0,91 vine din regula „recall PASS de cel puțin 0,90" pe val. Coborârea lui prinde
  mai multe rebuturi, dar trimite piese bune la rebut.
- v2 (antrenat pe randări de sus, 8.000 de imagini) are verdict bun pe datasetul de piese, dar
  greșește familia pe cadrele celulei: de aceea v3 pornește din el și se adaptează pe această
  cameră.
