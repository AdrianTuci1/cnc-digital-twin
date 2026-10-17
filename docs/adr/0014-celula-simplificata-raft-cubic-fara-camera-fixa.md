# ADR 0014: Celula se simplifică: raft cubic pe un nivel, verdictul de la braț, mașină simbolică

- **Status:** acceptat parțial; deciziile 1, 2 și 5 despre raft sunt înlocuite de
  [ADR 0017](0017-dulap-cu-trei-rafturi-si-doua-compartimente.md)
- **Data:** 2026-10-15
- **Decizat de:** echipa de proiect
- **Pornește de la:** ADR 0009 (inspecție vizuală), ADR 0012 (geamănul MuJoCo),
  [11-geaman-mujoco.md](../11-geaman-mujoco.md)

## Context

Geamănul MuJoCo nu trebuie să arate ca o celulă reală, ci să producă datele de care are nevoie
antrenarea: cicluri care se încheie, contacte curate, cadre cu piesa în ele. Trei părți din scenă
lucrau împotriva acestui scop.

**Raftul-scară.** Avea trei niveluri a câte două locuri, urcând spre vest până la 1,09 m, cu
trepte pline sub ele. La fiecare rulare a ciclului, brațul 2 se oprea în raft: la intrarea cea
mai depărtată scula rămânea cu zeci de milimetri de țintă, fiindcă antebrațul mătura structura
raftului. Măsurat: cu podeaua la 1,35 m, raza orizontală disponibilă scade la 0,69 m, iar
treptele pline și despărțitoarele urcau până la 0,85–0,97 m, chiar în anvelopa brațului.
Raftul era și greu de citit: șase locuri pentru cinci familii, unul gol.

**Camera fixă de proces.** Stătea la 2,20 m, pe o consolă prinsă de capacul mașinii, și
declanșa un al doilea verdict de calitate (`camera_quality`, rolul `inspection`), din alt punct
de vedere decât cel care ține piesa. Geamănul cheltuia pentru ea o cotă documentată, o consolă,
o verificare de degajare pe două mii de configurații eșantionate, un set de intrinsics și
discuția de rezoluție din document. În plus, verdictul din două locuri diferite dă două
răspunsuri diferite pentru aceeași piesă.

**Gabaritul mașinii.** Era desenat ca o mașină reală (1,4 × 1,0 m, cu capac și axe), deși nu e o
reprezentare reală: nu taie nimic, nu are așchii, iar starea ei `CUTTING` este o fereastră de
timp. Ocupa însă spațiu în celulă și în desen.

## Decizie

1. **Raftul are cinci intrări cubice de 0,50 × 0,50 × 0,50 m, toate pe un singur nivel**, cu
   podeaua la 0,85 m, cota suprafeței benzilor. Gabaritul este 1,50 × 1,00 m, așezat astfel încât
   baza brațului 2 să se ridice în golul din spatele mijlocului: rândul din față are trei intrări,
   cel din spate două, la capete. Fiecare familie are intrarea ei, în ordinea de umplere din plan.
2. **Intrările sunt sertare late, nu cutii închise**: buze de 2 cm pe marginea podelei, fără
   despărțitoare, fără plafon, fără pereți deasupra podelei. Sub ele stau 12 picioare și două
   panouri laterale, la capete, toate sub podea. Suportul e gri, intrarea e verde.
3. **Verdictul de calitate vine de la camera de pe flanșa brațului 2**, din cadrul în care piesa
   este ținută în falci (rolul `sorting`). **Camera fixă de proces iese din scenă**, împreună cu
   cota ei de 2,20 m, consola, verificarea de degajare și intrinsics-urile ei. Scena are **trei
   puncte de vedere** — `arm1_wrist`, `arm2_wrist` și camera liberă `cell` — și **patru cadre pe
   ciclu**. Rolul `inspection` rămâne definit în `cnc.qa.snapshot.v1.json`, pentru calea Isaac
   Sim; geamănul MuJoCo nu îl mai produce.
4. **Mașina CNC este un cub simbolic de 1,0 × 0,8 m**, cu colțul sud-vest și capacul la cotele
   de dinainte, fără pretenția unei reprezentări reale și fără axe animate. `CUTTING` rămâne o
   fereastră de timp pusă după ce brațul 1 ajunge în universal.
5. **Brațul nu are planificator de traiectorie, deci planul îi dă punctele de trecere.**
   `moves.VIA` primește, pentru fiecare intrare din raft, un punct chiar deasupra intrării, la
   `moves.RACK_VIA_Z = 1,10 m`, de unde scula coboară vertical. Cota este măsurată, nu aleasă:
   la 1,25 m flanșa ajunge la 0,846 m din raza de 0,855 m, iar servo-urile lasă scula cu 20–30 mm
   în urmă (peste pragul de 20 mm) și pot sări în altă ramură a cinematicii inverse.

## Consecințe

- Ciclul se încheie pentru toate cele cinci familii: `make mj-demo` este verde, cu eroarea maximă
  a sculei de 17,7 mm la așezarea pe banda 2 și 10,7 mm la intrarea din raft.
- Intrarea de 0,50 m lasă 0,35 m de jur-împrejurul piesei de ~0,10 m, deci locul cel mai mare
  (wheel_100) intră lejer, iar falcile au loc să intre de sus.
- Ordinea de umplere a intrărilor este ordinea familiilor din generator: planul, scena și
  clasificatorul rămân aliniate. Fără locuri goale, fiindcă intrările sunt exact câte familii.
- Un singur punct de vedere dă verdictul, deci corpusul de viziune are un singur izvor de adevăr,
  iar `snapshots.jsonl` se subțiază de la cinci cadre pe ciclu la patru.
- Schemele nu se schimbă: `cnc.qa.snapshot.v1.json` păstrează rolul `inspection`, deci un cadru
  vechi rămâne valid.
- Rezoluția clasificatorului scade puțin: camera de pe flanșă stă la 12 cm de punctul de strângere
  (consolă laterală, mutată ulterior acolo din axa sculei, vezi `simulation/mujoco/README.md`), cu
  60° pe verticală, deci piesa de ~100 mm iese la ~345 px (~3,4 px/mm), față de 3,6 px/mm cât
  avea camera fixă. Cel mai fin defect din catalog (gaură dezaxată de 2,5 mm) ajunge la ~9 px.
  Pragul `checks.MIN_WRIST_PART_PX = 150 px` prinde o cameră montată greșit ori prea depărtată.
- Desenul planului și documentul geamănului se subțiază: un tabel de coordonate, fără consolă, cu
  raftul citit dintr-o singură listă de intrări.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Raftul-scară cu trepte mai joase | Antebrațul brațului 2 mătură orice structură ridicată peste podea în raza lui; o scară fără structură nu mai e scară. |
| Raft cu două niveluri pe coloane mai înalte | O intrare la 1,35 m lasă cel mult 0,69 m de rază orizontală, iar acolo brațul nu poate intra cu scula. |
| Mutarea bazei brațului 2 din raft | Rezolvă accesul, dar scoate brațul din raza benzii 2 ori a lăzii de rebut; planul are baza acolo din măsurători de atingere. |
| Păstrarea camerei fixe ca sursă secundară | Două verdicte pentru aceeași piesă, care se pot contrazice, și o cotă documentată în plus pentru fiecare schimbare de scenă. |
| Raft cu pereți, ca sertarele să arate real | Pereții ridicați peste podea sunt exact ce oprește antebrațul; „sertarul” rămâne o podea cu buze. |
| Mașină CNC animată, cu așchiere | Cost de simulare fără folos pentru corpus: uzura mașinii se citește din senzori, nu din mișcarea axelor. |
