# ADR 0018: Dulapul brațului 2 este rotit 90°, cu fața vestică spre braț

- **Status:** înlocuit de [ADR 0023](0023-dulap-la-vest-de-bratul-2.md) pentru așezarea în plan și
  orientarea feței deschise; cotele rafturilor, ordinea familiilor și unghiurile de apropiere
  rămîn valabile
- **Data:** 2026-10-17
- **Decizat de:** echipa de proiect
- **Pornește de la:** ADR 0017 (dulapul cu trei rafturi și două compartimente),
  ADR 0012 (geamănul MuJoCo), ADR 0014 (raftul cubic, înlocuit parțial),
  [11-geaman-mujoco.md](../11-geaman-mujoco.md)

## Context

ADR 0017 a așezat dulapul la nord de brațul 2, cu fața nordică deschisă la y = 0,96 m și cu
lungimea de 1,00 m pe direcția est-vest. Dulapul era la îndemână, dar brațul 2 trebuia să se
rotească mult între preluarea de pe banda 2 și compartimentul familiei: yaw-ul bazei trecea de la
−5,1° la +125,4° pentru compartimentul vestic, adică aproximativ 130°, iar scula mai adăuga
răsucirea încheieturii.

Cerința este ca brațul să se rotească aproximativ **90°** între cele două puncte, cu dulapul
așezat la est de braț și cu fața deschisă spre vest. Varianta atinge limita de atingere pe raftul
de sus; limita a fost acceptată explicit.

## Decizie

1. **Dulapul stă la est de brațul 2, cu lungimea pe direcția nord-sud.** Gabaritul exterior este
   0,30 × 1,00 m, cu colțul sud-vest în (3,18, 0,79) și centrul în (3,33, 1,29), deci acoperă
   celulele `G2–G4` din plan. Capătul nordic se oprește la y = 1,79 m, adică la 1 cm de șina
   sudică a benzii 2.
2. **Fața vestică, spre braț, rămâne deschisă**, la x = 3,18 m, adică 0,48 m est de baza brațului
   2. Lateralele de nord și sud, spatele de est, fundul și capacul formează carcasa. Cei 0,30 m
   de adâncime se întind spre est, în afara anvelopei brațului.
3. **Trei rafturi suprapuse, fiecare cu două compartimente de 0,50 × 0,30 m**, despărțite de un
   panou vertical la mijloc. Podelele rămân la 0,85 / 1,16 / 1,41 m, iar fața de sus la 1,69 m.
   Punctele de așezare stau la 2 cm în spatele feței deschise, la x = 3,20 m, cu centrele
   compartimentelor la y = 1,04 m (sud) și y = 1,54 m (nord).
4. **Familiile se așază raft cu raft, de la sud la nord**: raftul 1 — `wheel_100`, `flange_72`;
   raftul 2 — `bushing_68`, `wheel_60`; raftul 3 — `pinion_56`, iar compartimentul nordic rămâne
   gol. Ordinea este aceeași cu a generatorului; planul rămâne singura sursă pentru așezare.
5. **Apropierea este înclinată, cu două unghiuri**: 50° față de verticală în compartimentul sudic
   și 30° în cel nordic. Direcțiile sunt `(sin θ, 0, −cos θ)`, adică dinspre vest, de sus.
   Compartimentul sudic are raftul 3 aproape de limită, iar la 45° punctul iese cu 13 mm din rază,
   la 30° cu 91 mm; compartimentul nordic stă aproape drept în fața bazei, iar la
   45° încheietura se răsucește până la limită (85,7° eroare).
6. **Punctul de trecere stă în fața feței, dar în afara zonei moarte a umărului.** La x = 2,92 m
   pentru compartimentul sudic și la x = 3,05 m pentru cel nordic, cu 5 cm peste punctul de lucru.
   Sub 0,35 m de baza brațului, trecerea nu mai poate fi atinsă cu orientarea cerută. Raftul de
   sus se atinge direct, fără trecere, iar retragerea de deasupra lui păstrează 4 cm până la
   limita de atingere; fără marjă, un punct chiar pe sferă nu se atinge din mers (112 mm eroare).
7. **Din ADR 0017 rămân valabile** numărul de rafturi și compartimente, cotele podelelor, carcasa
   formată din laterale, spate, fund și capac, ordinea familiilor și verdictul de calitate de la
   camera de pe flanșa brațului 2. Sunt înlocuite așezarea în plan, orientarea feței deschise,
   unghiul unic de 45° și poziția punctului de trecere.

## Consecințe

- `make mj-demo` este verde pentru toate cele cinci familii: eroarea maximă în compartimente este
  **7,0 mm**, răsucirea maximă **0,9°**, nicio verigă a brațului nu atinge dulapul, iar fiecare
  familie durează 57–59 s simulați.
- Brațul se rotește aproximativ 90° între preluarea de pe banda 2 și raft (măsurat 85–89° la
  raftul 1), în loc de aproximativ 130° în așezarea anterioară. Scula păstrează o răsucire
  proprie, fiindcă intrarea în raft este înclinată, nu verticală.
- Raftul 3 este la limita de atingere: 0,850 m în plan, aproximativ 0,849 m până la flanșă în
  poziția de lucru. Verificarea din scenă îl acceptă, iar retragerea de deasupra lui are 4 cm
  marjă. O creștere a cotei raftului sau o mutare a dulapului spre est iese din rază.
- Dulapul nu mai lasă 0,84 m de culoar sudic: capătul lui nordic ajunge la 1 cm de banda 2.
  Latura de est rămâne liberă până la peretele celulei, iar mașina CNC începe la y = 2,65 m.
- Planul are un singur dreptunghi de 0,30 × 1,00 m pentru dulap; cele trei rafturi se suprapun în
  desen, iar cotele lor stau în legendă și în tabelul de coordonate.
- Falcile pot atinge blatul când lasă piesa; atingerea falcilor este raportată, nu tratată ca
  eșec. O verigă care atinge dulapul rămâne eșec.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Păstrarea dulapului cu fața nordică din ADR 0017 | Brațul se rotește ~130° între bandă și raft, iar cerința este ~90°. |
| Un singur unghi de 45° pentru ambele compartimente | În compartimentul nordic încheietura se răsucește până la limită, cu 85,7° eroare la punct. |
| Un singur unghi de 30° pentru ambele compartimente | Raftul 3 iese din rază cu 91 mm, fiindcă flanșa urcă prea mult. |
| Dulapul mutat mai spre vest, ca să intre și raftul 3 la 30° | Fața ajunge prea aproape de bază; antebrațul intră în carcasă, iar compartimentele se îngustează. |
| Raftul 3 coborât sub 1,41 m | Se pierde treapta de 0,25 m și variația de înălțime pe care o învață modelul; cota a fost păstrată. |
| Trecere în fața raftului de sus | Brațul rămâne într-o ramură din care nu mai coboară pe punct (95 mm eroare); drumul direct urcă pe deasupra dulapului. |

## Notă (ADR 0021)

Ordinea rafturilor și unghiurile de apropiere rămîn valabile; s-au schimbat doar numele și
siluetele familiilor din compartimente, conform ADR 0021.
