# ADR 0023: Dulapul pe familii se mută la vest de brațul 2

- **Status:** acceptat; înlocuiește așezarea din ADR 0018
- **Data:** 2026-10-26
- **Decizat de:** echipa de proiect
- **Pornește de la:** ADR 0018 (dulapul rotit 90°, înlocuit), ADR 0017 (dulapul cu trei
  rafturi), ADR 0014 (celula simplificată), [11-geaman-mujoco.md](../11-geaman-mujoco.md)

## Context

ADR 0018 a așezat dulapul la **est** de brațul 2, cu fața vestică deschisă spre braț. Așezarea
funcționa din punct de vedere geometric, dar în demonstrația live dulapul stătea între brațul 2 și
restul celulei: la filmare, brațul și dulapul se suprapuneau, iar privirea dinspre sud vedea
întîi carcasa, apoi mișcarea. Cerința este ca dulapul să stea în **partea opusă**, la vest de
brațul 2, ca latura estică să rămînă liberă și celula să se citească de la stînga la dreapta:
dulap → brațul 2 → banda 2 → brațul 1 și mașina.

Mutarea nu schimbă niciuna dintre constrîngerile care au dus la forma dulapului: trei rafturi
suprapuse, două compartimente pe raft, fața deschisă spre braț, intrare înclinată pe sub raftul de
deasupra și rotire de aproximativ 90° între preluarea de pe bandă și așezarea în compartiment.
ADR 0018 rămîne sursa pentru cotele rafturilor, pentru ordinea familiilor și pentru unghiurile de
apropiere; se înlocuiește doar așezarea în plan și orientarea feței deschise.

## Decizie

1. **Dulapul stă la vest de brațul 2, cu lungimea pe direcția nord-sud.** Gabaritul exterior este
   0,30 × 1,00 m, cu colțul sud-vest în (1,92, 0,79) și centrul în (2,07, 1,29), deci acoperă
   celulele `D2–E4` din plan. Capătul nordic se oprește la y = 1,79 m, adică la 1 cm de șina
   sudică a benzii 2, iar capătul sudic lasă 0,79 m pînă la peretele sudic al celulei.
2. **Fața estică, spre braț, rămîne deschisă**, la x = 2,22 m, adică 0,48 m vest de baza brațului
   2. Lateralele de nord și sud, spatele de vest, fundul și capacul formează carcasa. Cei 0,30 m
   de adâncime se întind spre vest, în afara anvelopei brațului.
3. **Podelele rămîn la 0,85 / 1,16 / 1,41 m**, fața de sus la 1,69 m, iar cele șase
   compartimente au 0,50 × 0,30 m. Punctele de așezare stau la 2 cm în spatele feței deschise,
   la x = 2,20 m, cu centrele la y = 1,04 m (sud) și y = 1,54 m (nord).
4. **Ordinea familiilor rămîne cea din generator**: raftul 1 — `wheel_100`, `gusset_72`;
   raftul 2 — `hex_68`, `link_68`; raftul 3 — `pinion_56`, `plate_84`. Planul, scena și
   clasificatorul împărtășesc aceeași ordine, verificată de test.
5. **Apropierea păstrează cele două unghiuri măsurate**: 50° în compartimentul sudic, cu abatere
   de 30° spre sud, și 30° drept în cel nordic. Punctul de trecere stă la x = 2,48 m, la 5 cm
   peste punctul de lucru și la cel puțin 0,35 m de axul brațului; raftul de sus se atinge
   direct, fără trecere.
6. **Desenul și tabelele ies din aceeași listă de coordonate** (`docs/diagrams/cell_layout.py`).
   Se schimbă doar `RACK_X`, `RACK_Y` și orientarea carcasei, apoi se regenerează planul cu
   `make cell-layout`.

## Consecințe

- `view.py --demo` încheie ciclul pentru toate cele șase familii, cu dulapul la vest. Eroarea
  măsurată a sculei este **7,7 mm și 0,9°** în compartimentul sudic (`wheel_100`, intrare la 50°)
  și **4,0 mm și 0,5°** în cel nordic (`gusset_72`, intrare la 30°), fără nicio verigă atinsă.
- Brațul 2 se rotește în continuare aproximativ 90° între preluarea de pe banda 2 și raft, doar
  sensul se schimbă: acum spre sud-vest, nu spre sud-est. Antebrațul rămîne în culoarul dintre
  dulap și bandă.
- Latura estică a brațului 2 rămîne liberă, iar în cadru brațul are drept fundal banda și mașina,
  nu carcasa dulapului.
- Dulapul ocupă culoarul vestic de la y = 0,79 m la 1,79 m; trecerea dintre celula de sud și
  banda 2 se face pe la est de braț, ca înainte.
- Planul, tabelul de coordonate și verificarea razei de atingere se regenerează din
  `cell_layout.py`; cifrele din documentul de geamăn nu se mai transcriu manual.
- ADR 0018 rămîne în istoric pentru cotele rafturilor și pentru măsurătorile de apropiere; orice
  cititor care îl ia drept stare curentă trebuie trimis aici. Distanța de 0,679 m din ADR 0020
  descrie așezarea de atunci: compartimentul nordic stă acum la 0,50 m de baza brațului 2.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Păstrarea dulapului la est, din ADR 0018 | Geometric validă, dar în cadru carcasa stă între braț și restul celulei, iar cerința este partea opusă. |
| Dulapul la nord de braț, ca în ADR 0017 | Brațul se rotește aproximativ 130° între bandă și raft, nu 90°. |
| Dulapul mai aproape de braț, cu fața la x = 2,10 m | Fața ajunge prea aproape de bază; la intrarea de 50° antebrațul intră în carcasă, iar compartimentele se îngustează. |
| Dulapul mai spre vest, cu fața la x = 2,00 m | Punctul sudic iese din raza de 0,855 m: 0,88 m în plan pînă la flanșă. |
| Dulapul paralel cu banda 2 | Brațul trebuie să se răsucească peste 120° între preluare și compartiment. |
