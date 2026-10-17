# ADR 0017: Dulap cu trei rafturi și două compartimente pe raft

- **Status:** acceptat
- **Data:** 2026-10-17
- **Decizat de:** echipa de proiect
- **Pornește de la:** ADR 0012 (geamănul MuJoCo), ADR 0014 (raftul cubic, înlocuit parțial),
  [11-geaman-mujoco.md](../11-geaman-mujoco.md)

## Context

ADR 0014 a redus raftul la cinci intrări cubice pe un singur nivel, ca să scoată antebrațul
brațului 2 din structură. Raftul real pe care îl reprezintă celula este însă un **dulap cu trei
rafturi suprapuse, fiecare despărțit în două compartimente**. Un singur nivel nu redă nici
geometria dulapului, nici secvența de sortare: toate piesele stau la aceeași cotă, iar brațul 2 nu
are cum să învețe ridicări și așezări la înălțimi diferite.

Trei constrângeri au fost măsurate în scenă, nu deduse din desen:

1. **Anvelopa mâinii coboară sub punctul de strângere**, iar antebrațul urcă peste el. La o
   apropiere de 30° față de verticală, cea mai de jos parte a mâinii stă la −15 mm față de punct,
   iar cea mai de sus la +171 mm; la 90° coboară la −62 mm. Un dulap adânc, încărcat de sus, nu
   poate fi deservit: raftul de deasupra intră exact prin anvelopa antebrațului.
2. **La 30°, veriga 5 atinge marginea raftului 2** când scula coboară spre raftul 1, după ce
   brațul vine din preluarea de pe banda 2. Contactul oprește brațul la **95 mm** de punct, cu
   11,8° de răsucire. Contactul este razant (0,1 mm), dar schimbă ramura cinematicii inverse.
3. **Adâncimea și unghiul sunt o pereche.** La 0,18 m adâncime și 45°, toate cele cinci ținte se
   ating cu eroare de cel mult **6,8 mm**, fără nicio verigă atinsă. Punctul de trecere trebuie să
   stea la 5 cm peste punctul de lucru; la 10 cm, compartimentul estic al raftului 2 nu-și atinge
   orientarea la trecere (46 mm eroare), fiindcă încheietura urcă prea mult.

## Decizie

1. **Dulapul are trei rafturi suprapuse, fiecare cu două compartimente**, despărțite de un panou
   vertical la mijloc. Gabaritul exterior este 1,00 × 0,18 m, cu colțul sud-vest în (2,20, 0,78),
   iar podelele rafturilor la 0,85 / 1,16 / 1,41 m. Fața de sus este la 1,69 m, iar fața nordică,
   spre brațul 2, rămâne deschisă. Lateralele, spatele, fundul și capacul formează carcasa.
2. **Fiecare compartiment are 0,50 × 0,18 m**, iar punctul de așezare stă la 2 cm în spatele feței
   deschise, la cota podelei plus jumătatea grosimii piesei. Discul cel mai mare (Ø104) are centrul
   pe blat și peste 0,2 m de joc lateral; marginea lui dinspre nord iese 3,2 cm peste față.
3. **Scula intră înclinat, la 45° față de verticală**, dinspre nord: direcția de apropiere este
   `(0, −√2/2, −√2/2)`. Un punct de trecere stă în aer liber, la `y = 1,22 m` (0,26 m în fața
   dulapului) și 5 cm peste punctul de lucru. Coborârea de sus este exclusă: ar trece prin raftul
   de deasupra.
4. **Familiile se așază raft cu raft, de la vest la est**: raftul 1 — `wheel_100`, `flange_72`;
   raftul 2 — `bushing_68`, `wheel_60`; raftul 3 — `pinion_56`, iar al doilea compartiment rămâne
   gol, ca în dulapul real. Planul (`docs/diagrams/cell_layout.py`) rămâne singura sursă pentru
   așezare și cote; scena desenează carcasa, rafturile și despărțitorul din
   `layout.rack_geometry()`.
5. **Din ADR 0014 rămân valabile** verdictul de calitate de la camera de pe flanșa brațului 2,
   mașina CNC simbolică și schema de cadre. Sunt înlocuite doar deciziile 1, 2 și 5 despre raft.

## Consecințe

- `make mj-demo` este verde pentru toate cele cinci familii: eroarea maximă în compartimente este
  **6,8 mm**, fără nicio verigă atinsă; eroarea maximă a ciclului rămâne 17,7 mm la așezarea pe
  banda 2. O familie durează 57–59 s simulați.
- Scena scade de la 36 de corpuri și 236 de geome la **31 de corpuri și 206 geome**: cele cinci
  corpuri de intrare, cele 12 picioare, panourile și buzele dispar, iar carcasa, cele trei rafturi
  și despărțitorul intră în corpul `rack`.
- În plan, cele trei rafturi se suprapun, deci desenul arată un singur dreptunghi de 1,00 × 0,18 m;
  cotele rafturilor stau în legendă și în tabelul de coordonate, nu în desen.
- Falcile pot atinge blatul când lasă piesa. Atingerea falcilor este raportată, nu tratată ca
  eșec, fiindcă falcile sunt scula; o verigă care atinge dulapul rămâne eșec.
- Prinderea nu este modelată, deci piesa nu se așază fizic pe raft după eliberare. Ținta este
  centrul piesei, ca la toate celelalte puncte de lucru.
- Adâncimea de 0,18 m este limita măsurată: un dulap mai adânc readuce veriga 5 peste marginea
  raftului de deasupra, iar o apropiere de 30° readuce contactul chiar la această adâncime.
- Rafturile nu sunt egal distanțate: 0,31 m între primele două și 0,25 m între ultimele două.
  Ambele intervale lasă piesa și falcile să intre, iar podeaua de jos rămâne la nivelul benzii.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Apropiere la 30° cu același dulap | Veriga 5 atinge marginea raftului 2 și brațul se oprește la 95 mm de punct. |
| Apropiere la 45° cu trecerea la 10 cm peste punct | Compartimentul estic al raftului 2 nu-și atinge orientarea la trecere (46 mm eroare). |
| Dulap adânc, de 0,24–0,30 m | Antebrațul intră în carcasă; mâna nu mai are loc sub raftul de deasupra. |
| Rafturi aliniate, cu brațul retras | Scoate brațul 2 din raza benzii 2 ori a lăzii de rebut; baza lui este acolo din măsurători de atingere. |
| Apropiere verticală, de sus | Mâna ar trece prin raftul de deasupra; anvelopa coboară sub punct. |
| Al șaselea compartiment cu o familie | Generatorul are cinci familii; un compartiment etichetat suplimentar ar cere o clasă care nu există. |
| Păstrarea raftului cubic din ADR 0014 | Nu redă dulapul real și ține toate piesele la aceeași cotă, deci nu apare variația de înălțime în date. |
