# Geamăn digital MuJoCo: telemetrie pentru antrenare

Plan de implementare pentru al doilea geamăn al celulei CNC. Decizia arhitecturală stă în
[ADR 0012](adr/0012-geaman-mujoco-pentru-generare-de-telemetrie.md); documentul de față spune
**ce** se construiește, în ce ordine și cum se verifică.

Stare: faza 0 (documentația) s-a terminat, iar din faza 2 există scena MJCF din
`simulation/mujoco/`, cu piesa generată, cele două brațe, cele patru puncte de vedere și raftul pe
familii, plus transportul pe benzi (`belt.py`, `cell.py`) cu linie de oprire în plan. Verificarea
scenei,
`view.py --check`, este **verde**: țintele cad în raza brațelor, camera de inspecție are piesa în
cadru la 3,6 px/mm și rămâne la peste 10 cm de brațe pe 2000 de configurații eșantionate, iar
amândouă piesele parcurg banda la viteza de suprafață și se opresc la 13 mm peste linia de oprire a
benzii. `view.py --demo` rulează ciclul brațelor până la capăt pentru toate cele cinci familii
(**verde**), cu eroarea maximă a sculei sub 18 mm și fără nicio verigă atinsă. Colectarea datelor
există: `make mj-record` scrie sesiuni întregi — telemetrie pe contract, metadatele cadrelor de
inspecție, PNG-urile celor patru puncte de vedere și etichetele pieselor — pentru toate cele cinci
familii (**verde**, cinci cadre pe ciclu). Din faza 2 lipsesc prinderea (falcile doar ating piesa)
și stările de așchiere ale mașinii CNC: `CUTTING` nu are încă sursă, iar mărimile axului vin din
scenariul sesiunii. Starea amănunțită stă în `simulation/mujoco/README.md`.
Uzura, detectorul, interfața de control și randarea degradată sunt deocamdată doar pe hârtie.

## Cuprins

1. [Scop și non-scop](#1-scop-și-non-scop)
2. [Scena: componente și reprezentarea în MJCF](#2-scena-componente-și-reprezentarea-în-mjcf)
3. [Motorul de uzură](#3-motorul-de-uzură)
4. [Telemetrie și contracte](#4-telemetrie-și-contracte)
5. [Detecția anomaliilor și timpul de anticipare](#5-detecția-anomaliilor-și-timpul-de-anticipare)
6. [Interfața de control](#6-interfața-de-control)
7. [Structura de fișiere](#7-structura-de-fișiere)
8. [Faze și criterii de acceptare](#8-faze-și-criterii-de-acceptare)
9. [Riscuri și capcane](#9-riscuri-și-capcane)
10. [Decizii lăsate deschise](#10-decizii-lăsate-deschise)

## 1. Scop și non-scop

**Scop.** O celulă CNC simulată care rulează headless, pe CPU, ore întregi, și scoate două
lucruri: telemetrie brută compatibilă cu hot path-ul și ferestre gata de antrenare pentru un
detector de anomalii. Uzura este non-liniară, produce eșecuri mecanice reale în simulare, iar
episoadele de eșec sunt etichetate, ca să se poată măsura un preaviz.

**Non-scop.** Fidelitatea vizuală, interfața operatorului (rămâne Isaac Sim, cu DCV și WebRTC),
fizica așchierii, înlocuirea pipeline-ului existent, orice resursă AWS nouă.

## 2. Scena: componente și reprezentarea în MJCF

Fiecare componentă din promptul de cerință are corespondent în MJCF. MuJoCo nu are bandă
transportoare nativă, deci banda este un geom de tip cutie cu frecare mică, peste care un modul de
forțe tangențiale împinge corpurile aflate în contact cu ea; banda își scoate forța la o cotă din
plan, chiar ținta de preluare, iar de acolo piesa alunecă 13 mm până o oprește frecarea. Capcana
măsurată: cu frecare mare pe platformă, frecarea statică anulează
forța aplicată și piesa stă pe loc, deci platforma are `priority="1"` și frecare mică, iar aderența
benzii o dă exclusiv forța exterioară.

| Componentă | Reprezentare MJCF | Senzori | Actuatori / forțe |
| --- | --- | --- | --- |
| Bandă 1 (alimentare) | cutie (platformă cu frecare mică și prioritară, șine cu frecare mare), fără opritor: piesa se așază în amonte și banda o împinge până la linia de oprire din plan | viteză de suprafață, alunecare, număr de piese | forță tangențială per pas, aplicată prin `data.xfrc_applied` corpurilor în contact, cu limita `mu * N` |
| Braț 1, deservirea mașinii | Franka Emika Panda (7 DoF + griper) din `mujoco-menagerie`; preia piesa de pe banda 1, o încarcă în CNC, apoi o descarcă și o așază pe banda 2 | efort/curent pe articulații, forță de strângere, contact cu piesa | `position`/`motor` pe articulații, actuator pe degetele griperului |
| Mașină CNC, 3 axe | 3 articulații prismatice (X, Y, Z) + corp de ax + piesă prinsă; stările din contract, fără așchiere | vibrație RMS și vârf, sarcină ax, temperatură, avans | profil de sarcină și vibrație aplicat ca forță pe corpul axului, în funcție de stare și uzură |
| Braț 2 (sortare) | același model Panda, pe latura sudică a benzii 2, la 0,25 m de șina ei sudică | ca la brațul 1 | ca la brațul 1 |
| Bandă 2 (evacuare) | ca banda 1, cu un singur sens de mers, spre vest, și linia ei de oprire la ținta de preluare a brațului 2 | viteza, numărul de piese | ca la banda 1 |
| Raft pe familii | o scară de 0,50 × 0,50 m în coloana E, cu trei trepte care urcă spre vest, de la podeaua de 0,85 m — nivelul suprafeței benzii — până la 1,09 m, cu pasul de 0,12 m; fiecare treaptă are câte două locuri despărțite pe latura scurtă, deci șase locuri pentru cinci familii, iar piesa se lasă de sus, culcată, în locul familiei ei | numărul de piese pe loc | — |
| Ladă rebut | o ladă pe podea, sub capătul de vest al benzii 2, cu trei pereți scurți și latura de est deschisă; piesa neconformă rămâne pe bandă, trece de linia de oprire a brațului 2 și cade de la capătul benzii | numărul de rebuturi | — |
| Camere | patru puncte de vedere: câte o cameră pe flanșa fiecărui braț, camera fixă de deasupra benzii 2 și camera liberă. Verdictul de calitate îl dă cadrul de pe flanșa brațului 2, care vede piesa ținută în falci — rolul `sorting` din `cnc.qa.snapshot.v1.json`; cadrul fix de deasupra benzii rămâne `inspection` | cadru RGB offscreen + scor de calitate | — |

Stările mașinii CNC sunt exact enumerația din `cnc.telemetry.v1.json`: `IDLE`, `LOADING`,
`CUTTING`, `UNLOADING`, `INSPECTING`. Ciclul complet — bandă 1 → braț 1 → CNC → braț 1 →
bandă 2 (inspecție) → braț 2 → compartimentul familiei — produce `cycle_id` și `part_id` cu
aceleași formate (`cyc-000001`, `part-000001`).

Randarea camerei se face cu `mujoco.Renderer`. Pentru sesiunile în care interesează doar
telemetria, randarea se oprește complet, iar degradarea imaginii se aplică doar cadrelor cerute
(inspecție), ca să nu plătească timp de generare. Verificat: randarea offscreen funcționează
fără fereastră, pe CPU, în afara Kit.

**Rezoluția nu poate rămâne la implicitul de 160×120.** Diferența cea mai grosieră de rezolvat
este adâncimea unui dinte ciobit, `chip_depth = 4 mm`, iar cea mai fină este dezaxarea radială a
unei găuri, 2,5 mm. Ca fiecare să treacă de câțiva pixeli, piesa de ~100 mm trebuie să ocupe
câteva sute de pixeli, adică ~3,6 px/mm. Cu camera la 2,20 m, cât prevede documentul pentru
celelalte camere, un obiectiv normal acoperă ~1,5 m din cadru: piesa ajunge la vreo 10 pixeli,
iar defectul sub un pixel, deci nicio clasă din catalogul de defecte nu ar fi separabilă. De
aceea acoperirea este fixată, iar distanța și obiectivul se aleg împreună: camera de inspecție
randează la 640×480, cu **5,6° pe verticală**, de la **1,35 m** deasupra suprafeței benzii 2,
adică de la cota documentată a camerelor, 2,20 m, și acoperă **176 × 132 mm** la nivelul benzii —
destul ca un dinte ciobit de 4 mm să iasă pe ~14 pixeli și o gaură dezaxată cu 2,5 mm pe ~9, la
~3,6 px/mm. Sub ~0,3 mm nu se mai măsoară nimic, iar blurul din secțiunea 3 se adaugă peste.

De ce nu mai jos: pe plaja articulațiilor, geomele unui braț urcă peste axa benzii 2 până la
~2,10 m în lume — cotul ajunge la ~1,15 m de bază, iar baza stă la 0,95 m. Camera se ridică deci
la cota documentată a camerelor, 2,20 m, iar acolo golul minim măsurat pe 2000 de configurații
eșantionate este +0,115 m față de brațul 1 și +0,095 m față de brațul 2. Sub 2,20 m geometria
refuză compunerea scenei (`camera_geometry` ridică `ValueError`): obiectivul ar vedea brațele, nu
piesa. Consola care ține capul optic este prinsă pe capacul mașinii, în `H6`, la 0,25 m vest de
axul ei, iar brațul consolei trece imediat deasupra capului optic.
Înălțimea camerelor este parametru, nu o constantă comună, iar intrinsics-urile camerei de
inspecție — înălțime, câmp vizual, rezoluție — se scriu în `simulation/mujoco/README.md`,
împreună cu ce poate și ce nu poate rezolva. Legătura cu uzura: degradarea camerei din secțiunea 3 aplică blur proporțional cu
`1 - h_camera`, deci o cameră proiectată exact la limita de rezoluție pierde piesa, nu doar
defectul.

**Camerele de pe flanșe.** Catalogul Menagerie al modelului Panda nu are cameră, deci celula o
adaugă pe ultima verigă (`link7`), la 5 cm de flanșă, cu **60° pe verticală** și **640×480** —
aceeași rezoluție ca a camerei fixe, ca două cadre să se poată pune unul peste altul. Obiectivul
privește punctul de strângere, iar orientarea se calculează la compunerea scenei, nu se scrie de
mână: așa cum vine din catalog, camera privea **înapoi**, spre cot, iar punctul de strângere cădea
la −143 mm adâncime, adică în spatele obiectivului. Măsurat acum cu `view.py --check`: punctul de
strângere e la 160 mm, abaterea față de axul optic e 0,0°, iar o piesă de 100 mm iese pe ~260 px
din 640, adică ~2,6 px/mm. Pragul care prinde o montare greșită sau o cameră prea depărtată este
`checks.MIN_WRIST_PART_PX = 150`.

**Cele patru puncte de vedere** sunt registrul din `cncdt_mj/povs.py`, care este pur: nume de
cameră, rezoluție, moment de captură și rol. Cadrul se ia la două feluri de momente — la un punct
de lucru, după ce brațul s-a așezat pe poziția de jos, ori când piesa trece pe bandă prin dreptul
camerei fixe. Pe ciclu ies cinci cadre: două de pe flanșa brațului 1 (blocul brut în falci și piesa
în universal), unul de pe flanșa brațului 2 (verdictul), unul al camerei fixe (cadrul de proces al
piesei mașinate) și unul general al celulei, cu piesa în locul familiei ei.

### Poziționarea în celulă: grid de 0,5 m

![Planul celulei CNC, văzut de sus](diagrams/cnc-cell-layout.png)

Celula are 6,0 × 4,0 m, cu originea (0, 0) în colțul sud-vest, **+X spre est** și **+Y spre
nord**. Gridul are celule de 0,5 m: **coloanele A–L** spre est, **rândurile 1–8** spre nord, deci
fiecare element are o referință scurtă — brațul 1 stă în `F6`, mașina ocupă `G6–J8`, banda 2 în
`B4–J5`, brațul 2 în `F4`, raftul pe familii în `E3–E4`, lada de rebut în `A4–B5`, iar axul optic
al camerei fixe în `G5`, cu consola prinsă pe capacul mașinii, în `H6`.
Conversia la lumea MuJoCo este identitatea: planul `(x, y)` devine lumea
`(x, y)`, cu +Z în sus, deci scena preia cifrele ca atare.

Desenul și tabelele de mai jos ies din aceeași listă de coordonate
(`docs/diagrams/cell_layout.py`), regenerată cu `make cell-layout`; nimic nu se transcrie manual,
ca planul și scena să nu se poată contrazice.

| Element | Centru (x, y) | Amprentă (m) | Celule de grid |
| --- | --- | --- | --- |
| Bandă 1 (alimentare) | (1,60, 3,30) | 2,4 × 0,4 | A7–F7 |
| Braț 1 (Panda #1) | (2,95, 2,65) | bază 0,25 Ø | F6 |
| Cameră pe brațul 1 (flanșă) | (3,17, 2,42) | punct | G5 |
| Mașină CNC 3 axe | (4,10, 3,15) | 1,4 × 1,0 | G6–J8 |
| Bandă 2 (evacuare, un singur sens) | (2,70, 2,00) | 3,8 × 0,4 | B4–J5 |
| Braț 2 (Panda #2) | (2,70, 1,55) | bază 0,25 Ø | F4 |
| Cameră pe brațul 2 (flanșă) | (2,45, 1,72) | punct | E4 |
| Raft pe familii (3 niveluri × 2 locuri) | (2,25, 1,50) | 0,5 × 0,5 | E3–E4 |
| Ladă rebut | (0,45, 2,00) | 0,6 × 0,4 | A4–B5 |
| Cameră fixă de proces (consolă pe mașină) | (3,05, 2,00) | ax optic, cadru 0,18 m | G5 |

Fiecare țintă de prindere cade în raza de 0,855 m a brațului care o deservește, măsurată în plan:

| Braț | Țintă | Distanță în plan (m) | În raza de 0,855 m |
| --- | --- | --- | --- |
| Braț 1 | preluare de pe banda 1 | 0,70 | da |
| Braț 1 | încărcare/descărcare CNC | 0,72 | da |
| Braț 1 | așezare pe banda 2 | 0,72 | da |
| Braț 2 | preluare de pe banda 2 | 0,45 | da |
| Braț 2 | locul 1 (wheel_100) | 0,29 | da |
| Braț 2 | locul 2 (flange_72) | 0,33 | da |
| Braț 2 | locul 3 (bushing_68) | 0,46 | da |
| Braț 2 | locul 4 (wheel_60) | 0,48 | da |
| Braț 2 | locul 5 (pinion_56) | 0,62 | da |

Fluxul materialului, desenat punctat pe plan: banda 1 intră din `A7` spre est și lasă **blocul
brut** — un disc de metal, nu piesa finită — la capătul ei, în `F7`; brațul 1 îl ridică și îl
încarcă în mașină pe fața vestică. Abia după ce piesa este poziționată în dispozitiv intră
generatorul procedural: geometria de pe masa mașinii devine roata dințată a familiei, cu clasa ei
de defect, iar eticheta iese din același `GearParams` care a produs geometria, deci nu se pot
desincroniza. După ce mașina termină, brațul 1 o descarcă tot el și o așază pe banda 2, în `G5`,
la o rotație aleatoare. Banda 2 merge într-un singur sens, spre vest, iar pe drum piesa trece pe
sub camera fixă de proces, al cărei ax optic stă peste bandă în `G5`. Brațul 2, aflat pe latura
sudică a benzii, în `F4`, o ridică din `F5` și o lasă în locul familiei ei, pe raftul din `E3–E4`,
cu podeaua treptei de jos la 0,85 m, adică la nivelul suprafeței benzii.

Raftul este o scară, nu un dulap cu rafturi suprapuse, și asta iese din geometria brațului.
Fiecare treaptă are podeaua ei și câte două locuri despărțite pe latura scurtă, iar treptele urcă
**spre vest, departe de braț**: 0,85 m, 0,97 m, 1,09 m, cu pasul de 0,12 m. Ordinea nu e o
preferință estetică. Măsurat în scenă, cu baza brațului 2 la 0,95 m, placa lui de bază coboară la
0,82 m, antebrațul mătură amprenta raftului la ~1,20 m în timpul apropierii, iar încheietura trece
la ~0,153 m peste podeaua locului în care coboară scula. Lângă braț are deci cel mai puțin loc,
exact acolo unde scula trebuie să coboare cel mai adânc, așa că treapta de lângă braț stă la
nivelul benzii, iar scara urcă spre vest. Fiecare treaptă e un bloc plin până la podeaua ei, deci
fața de est a treptei de dedesubt ține locul de deasupra ca un perete; peste blocuri vin buzele,
scunde cât să țină piesa culcată fără să stea în calea mâinii, și despărțitorul dintre cele două
locuri ale treptei. Despărțitorul se retrage 3,5 cm de la marginea de est a treptei: scula se lasă
în locul treptei vecine, iar încheietura brațului rămâne deasupra muchiei dintre trepte, chiar la
colțul lui; cu despărțitorul până în margine, veriga se sprijină pe el.

Raftul înlocuiește cutiile adânci de dinainte, și înlocuirea rezolvă o măsurătoare, nu doar o
preferință. Cutiile aveau pereți care urcau până la 0,85 m, nivelul benzii, iar antebrațul brațului
2 trecea prin ei: la cota de dinainte a piedestalului, 0,55 m, punctele de așezare rămâneau la
41 mm, respectiv 106 mm eroare. Un raft deschis nu opune nimic antebrațului, iar cele cinci locuri
cad între 0,29 m și 0,62 m de baza brațului 2 în plan. Locul are 0,25 m lățime, iar treapta
0,167 m adâncime, deci cea mai mare piesă, Ø104, se lasă culcată cu loc de falci înainte și după
ea.

Fiecare loc ține o familie de piese, în ordinea din plan (`docs/diagrams/cell_layout.py`) și din
generator (`cnc_cell/parts/gear.py`): `wheel_100`, `flange_72`, `bushing_68`, `wheel_60`,
`pinion_56`; locurile se umplu de la treapta de lângă braț spre vest, iar în treaptă de la nord la
sud, iar al șaselea loc, cel de la vest, în treapta de sus, rămâne gol. Un test verifică potrivirea
celor două ordini, ca raftul să nu ajungă să adune familiile în altă ordine decât le învață
clasificatorul. Codurile citite de pe raft sunt amânate deliberat: vezi
`docs/10-dataset-piese.md`.

Rebutul are o cale fără braț, ca în plan: piesa neconformă rămâne pe bandă, trece de linia de
oprire a brațului 2 și cade de la capătul de vest al benzii 2 în lada din `A4–B5`. Marginea lăzii
stă la 0,62 m, sub nivelul benzii, ca piesa să nu se sprijine pe perete, iar latura de est nu are
perete deloc: e chiar sub capătul benzii, iar un perete acolo ar prinde piesa. Pistonul de
rejecție din scena Isaac Sim nu se dublează aici — banda duce rebutul până la capăt, fără ca
vreun braț să-l atingă.

Rotația aleatoare de pe banda 2 nu este un detaliu de fizică, ci o decizie despre dataset: dă
clasificatorului de calitate aceeași piesă din unghiuri diferite, iar defectele din catalog sunt
diferențe relative între dinți și cercul de găuri, deci rămân vizibile la orice rotire. Yaw-ul
așezării se loghează însă împreună cu cadrul și cu eticheta, altfel nu se poate construi o
partiție de evaluare pe unghiuri nevăzute și nu se poate distinge invarianța la rotire de noroc.

Înălțimile propuse, pentru când se scrie MJCF-ul: suprafețele de lucru ale benzilor la 0,85 m,
masa mașinii la 0,75 m, capacul de protecție la 1,90 m, podeaua raftului pe familii la 0,85 m,
adică la nivelul suprafeței benzii 2, nivelurile raftului urcând unele peste altele la 0,12 m,
marginea
lăzii de rebut la 0,62 m, adică sub nivelul benzii, ca piesa să treacă peste ea, baza brațelor pe
piedestal la 0,95 m, iar camerele la 2,20 m, orientate în jos — camera de inspecție a calității
prin aceeași cotă, cu capul optic deasupra axei benzii 2, pe o consolă care se așază pe capacul
mașinii. Culoarele
libere: 0,60 m între capătul benzii 1 și fața vestică a mașinii, 0,45 m între marginea nordică a
benzii 2 și fața sudică a mașinii (brațul 1 lucrează pe deasupra benzii, nu prin culoar), 0,25 m
între marginea sudică a benzii 2 și axul brațului 2, adică 0,13 m până la piedestalul lui, iar
raftul se oprește la 0,05 m de șina sudică a benzii 2. Brațul 1 are
0,72 m până la axul mașinii și 0,72 m până la locul de așezare pe banda 2, iar brațul 2 are 0,45 m
până la punctul de preluare și între 0,29 m și 0,62 m până la locurile raftului. Ambele brațe
lucrează pe deasupra benzilor, nu prin culoarele
dintre ele; distanța mică dintre axul brațului 2 și banda 2 se verifică la montaj, în scenă, nu
din plan.

Ce nu spune planul: înălțimile relative dintre brațe și ținte (raza de 0,855 m este proiectată în
plan, deci o țintă poate părea la îndemână și să fie prea sus) și geometria interioară a mașinii.
Amândouă se verifică abia în scenă, la faza 2. Verificarea din scenă prinde o atingere, nu și o
traiectorie care trece razant pe deasupra.

Piedestalul brațelor este cota care nu se poate decide din plan, iar scena o măsoară. Cutiile
adânci de dinainte dădeau sentința „0,55 m nu se poate": pereții lor urcau până la 0,85 m, iar
antebrațul trecea prin ei (`arm2_link3↔bin_ng`), cu 41 mm eroare rămasă la cutia de piese bune.
Raftul deschis scoate exact acea piedică: nu mai are pereți înalți în care să intre antebrațul. Nu
scoate însă piedica de la preluare: ca să ridice piesa de pe banda 2, brațul 2 trebuie să treacă cu
antebrațul peste platformă, iar la 0,55 m platforma este deasupra lui — măsurat,
`arm2_link3↔belt_out` la punctul de deasupra, cu 106 mm eroare rămasă, iar locurile din raft se
ating cu peste 1 m eroare la al cincilea (`arm2_link3↔rack`). La 0,95 m `view.py --demo` încheie
ciclul pentru toate cele cinci familii, cu eroarea maximă a sculei sub 18 mm și fără nicio verigă
atinsă; în schimb anvelopa brațelor urcă peste axa benzii 2 până la ~2,10 m în lume, deci camera
fixă se ridică la cota documentată a camerelor, 2,20 m, cu gol minim eșantionat de 0,095 m față de
brațul 2 pe 2000 de configurații. Cota documentată a piedestalului este 0,95 m (secțiunea 10),
iar `view.py --base-z` o poate coborî pentru măsurători.

## 3. Motorul de uzură

Cod pur, fără MuJoCo, ca să poată fi testat și reproductibil (`cncdt_mj/wear.py`). Cinci mecanisme:

**a) Sănătate cu rată de uzură crescătoare.** Pentru fiecare componentă `i`, sănătatea
`h_i ∈ (0, 1]` pornește de la 1 și scade cu

```
dh_i/dt = -k_i · (1 + α_i · u_i) · h_i^{p_i},   u_i = sarcina normalizată, p_i ∈ (0, 1)
```

Exponentul `p_i < 1` face uzura lentă la început și accelerată spre avarie, adică exact
comportamentul non-liniar cerut. Constantele `k_i` se aleg pe timpi nominali, nu pe valori de
catalog: la sarcină nominală, CNC-ul ajunge la „degradat" în ~30 de minute de așchiere, brațele
în ~90 de minute de mișcare, benzile în câteva ore. Ca timpul nominal să fie chiar cel scris,
`k_i` este împărțit la factorul de sarcină, `1 + α_i u_i`: altfel factorul ar scurta viața sub
cea documentată.

Ecuația este o ecuație Bernoulli, iar motorul o integrează **exact**: cu `q = 1 - p`,
`d(h^q)/dt = -q c`, deci `h^q(t) = h^q(0) - q c t`. Timpul până la degradat nu depinde astfel de
pasul de integrare — același rezultat la pasul de 2 ms al sesiunii și la un pas de o oră. Toate
timpii se scalează dintr-o singură variabilă, care comprimă viața mașinii fără să atingă ceasul
(ADR 0007): `--rate`.

**Uzura curge cu timpul de lucru, nu cu ceasul.** Fiecare interval al ciclului are un `Work`, adică
cine lucrează: banda cât cară, brațul cât se mișcă, scula, axul și răcirea cât taie. Ce nu lucrează
nu se uzează. Uzura se acumulează **peste cicluri**, fiindcă scena se recompune la fiecare ciclu,
iar modelul de uzură este al sesiunii.

**b) Drift de calibrare.** `drift_i = d_i · (1 - h_i)`, aplicat ca abatere lentă: viteza benzii
scade cu până la 8%, articulațiile capătă un offset de poziție, axele CNC capătă eroare de
poziționare. Driftul este cel care face uzura vizibilă în medii și deviații, nu doar în nivel.

**c) Zgomot dependent de uzură.** `σ_i = σ0_i + s_i · (1 - h_i)`, adăugat peste mărimile
senzorilor (vibrație, sarcină de ax, debit de răcire, temperatură), cu sămânță fixă.

**d) Eșecuri discrete.** Hazard crescător `λ_i(t) = λ0_i · exp(γ_i · (1 - h_i))`, evaluat per
pas, cu generator cu sămânță. Evenimentele sunt cele cerute, câte unul pe componentă: piesa
scăpată din griper (`gripper_slip`), alunecarea de bandă (`belt_slip`), scula ruptă
(`tool_break`, ține până la reparație), rulmentul axului (`spindle_bearing`) și pierderea răcirii
(`coolant_loss`). Fiecare eveniment scrie o linie în `events.jsonl` **la momentul lui** (`ts`,
componentă, tip, `h`, durată), care este **eticheta** pentru detector; intervențiile de operator
intră în același fișier, cu `event: "action"`.

**e) Degradarea camerei.** Blur gaussian cu rază proporțională cu `1 - h_camera`, zgomot aditiv
pe pixel, ocazional cadru lipsă. Scorul de calitate (`1 - (1 - h_camera)` ponderat cu blurul
aplicat) intră în metadatele cadrului. **Neimplementat**: cadrele ies deocamdată curate.

**Legătura cu fizica**. Uzura nu rămâne o cifră în telemetrie, ci schimbă simularea:

| Componentă | Efectul în geamăn azi | Ce se vede în date |
| --- | --- | --- |
| Bandă | scala vitezei de suprafață scade cu driftul (`speed_scale` pe fiecare bandă), iar banda care alunecă stă pe loc cât ține evenimentul | `belt_speed_mps` în diagnostic, timpi de ciclu mai lungi, mai puține eșantioane pe secundă de ceas |
| Brațe | efortul măsurat în scenă se scalează cu `1 + 0,8 (1 - h)` | `joint_effort` mai mare, efort pe braț în diagnostic |
| Griper | — (prinderea nu este modelată) | rată de piese scăpate, evenimente |
| CNC | amplitudinea vibrației și sarcina axului cresc cu uzura rulmentului și a sculei, debitul de răcire scade, avansul scade, temperatura urcă | `vibration`, `spindle_load`, `axis_feed`, `coolant_flow_lpm`, `temp_c`, `tool_wear_index`, `surface_quality` |
| Cameră | — | scor de calitate, verzi la inspecția vizuală |

**Mașina nu se animează**: nu are axe în scenă, iar prelucrarea intră în dataset ca **fereastră de
timp** cu starea `CUTTING` (`--machining-s`, implicit 12 s pe ciclu), în care se uzează scula, axul
și răcirea și cresc mărimile de proces. Când scenei îi vor crește axele, aceleași cifre se citesc
din motor și nimic din contract nu se schimbă.

**Acțiuni de operator** (în oglindă cu `degradation/model.py`): `inject_tool_wear`,
`inject_joint_friction`, `replace_tool`, `repair_joints` — numele care există deja în
`cnc.control.v1.json` —, plus altele locale geamănului, fiindcă schema de control este închisă:
`inject_wear(componentă, cât)`, `repair(componentă)`, `inject_fault_burst(componentă, magnitudine,
durată)` — scade sănătatea brusc și ridică temporar rata de uzură de 25 de ori, scenariul pe care
detectorul trebuie să îl prindă —, `set_wear_rate(multiplier)` și `reset_wear`. Din linia de comandă
se ajung cu `--rate` (compresia vieții) și `--inject componentă:cât` (pornește sesiunea cu o
componentă deja uzată), iar în sesiune fiecare acțiune lasă o linie în `events.jsonl`.

## 4. Telemetrie și contracte

Două ieșiri, amândouă pe contracte existente.

**Eșantion brut, ~10 Hz**, scris în JSONL (și publicabil pe MQTT, pentru paritate cu calea
live), cu exact câmpurile din `telemetry/schemas/cnc.telemetry.v1.json`. Nu se adaugă câmpuri:
schema are `additionalProperties: false`, iar Lambda-ul de feature engineering oricum aruncă
câmpurile din afara contractului.

**Ferestre de 60 s**, scrise în Parquet (sau CSV pentru inspecție rapidă), cu exact cele 26 de
coloane din `cncdt_ml.schema.ENRICHED_COLUMNS`, produse de funcțiile existente
`cncdt_ml/features.py::compute_window_features` și `enriched_record`. Partiționarea pe disc
respectă `dt_year/dt_month/dt_day/dt_hour`, ca fișierele să poată fi citite cu aceleași
interogări Athena ca datele din lake.

Două mărimi nu au coloană în contract: uzura benzii de alimentare și calitatea camerei. Pentru
bandă se emit eșantioane brute normale, cu `device_id` propriu
(`cnc-cell-01-conveyor`), pe câmpurile care există deja. Calitatea camerei rămâne deocamdată în
metadatele locale ale sesiunii, pentru că nu are nicio coloană în contract. Dacă modelul le cere
în lake, urmează bump `1.0.0 → 1.1.0` simultan în tabelul din Terraform, în
`telemetry/schemas/` și în `cncdt_ml/schema.py` — cele trei se schimbă împreună, altfel
Firehose cade la conversia Parquet.

Fișierele unei sesiuni, așa cum le scrie `make mj-record` în `simulation/mujoco/runs/<familie>-<sămânță>/`:

```
runs/wheel_100-7/
  samples.jsonl        # eșantioane brute, 10 Hz, exact câmpurile din cnc.telemetry.v1.json
  snapshots.jsonl      # metadatele cadrelor de inspecție, exact câmpurile din cnc.qa.snapshot.v1.json
  frames.jsonl         # indexul cadrelor: cale, punct de vedere, moment, eticheta piesei
  readings.jsonl       # diagnostic: efort pe braț, forță de strângere, poziția piesei, contacte
  wear.jsonl           # sănătatea fiecărei componente, aliniată pe sim_time_s cu eșantioanele
  events.jsonl         # etichetele: eșecurile componentelor și intervențiile de operator
  frames/cyc-000001/   # PNG-urile, unul pe punct de vedere și moment
  session.json         # parametrii, versiunea mujoco, sămânța, uzura, totalurile, etichetele ciclurilor
```

`frames.jsonl` este datasetul de antrenare al vederii: fiecare rând leagă un PNG de familia,
defectul și verdictul piesei din el, plus identificatorii de trasabilitate ai ciclului. Cadrele de
inspecție apar în două locuri, fiindcă au două întrebuințări: în `snapshots.jsonl` intră doar cele
două roluri din contract (`sorting` — flanșa brațului 2, `inspection` — camera fixă), exact ce s-ar
publica pe MQTT și în bucketul de date, iar în `frames.jsonl` intră toate, inclusiv cele de
prindere, care sunt pentru antrenare, nu pentru verdict. `readings.jsonl` ține mărimile care n-au
coloană în contract, ca să nu se strecoare în eșantionul de pe hot path.

`wear.jsonl` și `events.jsonl` sunt perechea pentru antrenarea detectorului: primul dă traiectoria
sănătății pe fiecare componentă (singurele urme din contract sunt `tool_wear_index`,
`joint_friction_index` și `surface_quality`), al doilea dă momentele: `event: "failure"` cu
`kind`, `health`, `severity` și `duration_s`, respectiv `event: "action"` cu comanda de operator.
`session.json` scrie și tabelul componentelor cu timpii nominali, rata folosită și sănătatea de la
sfârșit, ca o sesiune să se poată reproduce fără să ghicească parametrii.

Lipsește încă `windows.parquet`: ferestrele de 60 s se exportă în faza 4, când detectorul are ce
evalua, iar atunci primesc fiecare și eticheta ei din `events.jsonl`.

### Colectarea și urcarea în bucket

```bash
eval "$(./scripts/assume-operator.sh)"     # credențialele rolului de operator
make mj-collect                            # 5 cicluri pe familie, apoi urcare în bucket
make mj-collect-plan                       # doar planul: ce ar urca, fără să atingă bucketul
make mj-publish                            # urcă sesiunile deja scrise în simulation/mujoco/runs
```

`make mj-collect` scrie fiecare familie într-un director de rulare propriu
(`runs/run-<UTC>/<familie>-<sămânță>/`, cu `MJ_COLLECT_CYCLES` cicluri pe familie), apoi urcă tot
sub prefixul corpusului:

```
training-data/mujoco/dt=<zi>/run-<UTC>/manifest.json
training-data/mujoco/dt=<zi>/run-<UTC>/<familie>-<sămânță>/{samples.jsonl, frames.jsonl, …, frames/}
```

**Colectarea de uzură rulează fără camere.** Modelul de uzurare citește senzori tabulari, nu poze:
`make mj-collect MJ_COLLECT_FRAMES=false` pune `--no-frames`, iar o sesiune scade de la ~1,9 MB pe
ciclu la ~0,9 MB. Cadrele rămân pentru clasificatorul de piese, care chiar are nevoie de ele.

`datasets/` este un prefix propriu, nu `raw/`. `raw/telemetry/` este Parquet de ferestre de 60 s,
scris de Firehose și citit de tabelul Glue (secțiunea „Cum ajunge același conținut pe calea live"),
iar `raw/qa-snapshots/` este calea live: fiecare scriere acolo cheamă Lambda-ul de inspecție. Un
corpus de antrenament nu are ce căuta nici în tabel, nici în declanșator — se citește direct, cu
`cncdt_ml.offline` ori cu `ml/feature_engineering/build_features.py`, care știu să deschidă JSON
Lines. Partiționarea pe `dt=` păstrează convenția din `curated/qa-verdicts/dt=<zi>/`, iar numele
rulării face ca două colectări să nu se amestece niciodată.

**Transportul este AWS CLI** (`aws s3 cp <sesiune> s3://<bucket>/<prefix>/<sesiune> --recursive`,
plus manifestul), nu un SDK: credențialele vin din rolul de operator, iar așa nu se adaugă `boto3`
în niciunul dintre cele două venv-uri. Bucketul se ia din `DATA_LAKE_BUCKET` (`.env`, valoarea
ieșirii `data_lake_bucket` din stack-ul `platform`), iar `--run-dir`, `--prefix`, `--run` și `--dt`
se pot da direct lui `simulation/mujoco/publish.py`.

**Verificarea dinaintea urcării** stă în `cncdt_mj/export.py` (pur, testat în
`tests/unit/test_mj_export.py`): fișiere lipsă ori goale, numărul de rânduri din `frames.jsonl` și
numărul de PNG-uri de pe disc față de totalul din `session.json`, numărul de rânduri de uzură față
de eșantioane. Un corpus cu găuri se refuză înainte de urcare, când se vede ieftin; altfel s-ar
vedea la antrenare. Singurele fișiere goale tolerate sunt `events.jsonl` (o sesiune fără eșecuri)
și cele de cadre ale unei sesiuni care declară zero cadre în `session.json`; o sesiune care a
pierdut cadrele declarate este în continuare refuzată. Fără `--upload`, comanda doar citește corpusul și tipărește planul, deci se
poate verifica o colectare fără să atingă bucketul.

**`manifest.json`** este contractul cu antrenarea: lista fișierelor sesiunii, sesiunile cu familia,
sămânța, ciclurile, eșantioanele, cadrele, octeții și numărul de piese pe defect, totalurile,
familiile, versiunile de schemă și parametrii de uzură din `session.json`, plus revizia git din
care s-a generat corpusul. Un job nou află din el unde sunt fișierele și cu ce contract au fost
scrise, fără să ghicească din numele de directoare. Tot în manifest intră și acoperirea de uzură
(secțiunea de mai jos).

### Ce poate învăța un model din corpus

Întrebarea la care răspunde secțiunea asta este „de unde știm că datele ajung ca să învățăm uzura pe
serii de timp?". Răspunsul are două părți: ce garantează **formatul** sesiunii și ce garantează
**colectarea**. Amândouă se măsoară, iar cifrele de mai jos sunt citite din sesiuni reale, nu
presupuse.

**Formatul.** `wear.jsonl` are un rând la fiecare eșantion, aliniat cu `samples.jsonl` pe `ts_ms` și
pe `cycle_id` (verificat: aliniere exactă pe toată sesiunea), iar rândul conține sănătatea tuturor
celor șapte componente (`<componentă>_health`) plus indicii derivați din contract
(`tool_wear_index`, `joint_friction_index`, `surface_quality`), scala de viteză a benzilor
(`belt_speed_scale`) și temperatura. Eșantionul însuși are exact coloanele din
`cnc.telemetry.v1.json`, inclusiv cele care se mișcă cu uzura: `spindle_load`, `vibration`,
`temp_c`, `coolant_flow_lpm`, `axis_feed`, `joint_effort`. Diagnosticul (`readings.jsonl`) adaugă
mărimile care n-au coloană în contract — viteza fiecărei benzi, efortul și forța de strângere pe
fiecare braț, poziția și viteza pieselor, contactele.

Ceasul seriei este regulat: pas median **100 ms** (10 Hz, ca hot path-ul), cu pași mai mari la
hotarul dintre cicluri — **124–190 ms** măsurat — fiindcă acolo scena se recompune. Pașii mari sunt
numărați, nu ascunși: `coverage.gaps` din manifest spune câți sunt.

Uzura se vede în măsurători, nu doar în etichete. Măsurat pe o sesiune cu uzură reală: viteza benzii
coboară de la **0,12000** la **0,11652 m/s** când benzile ajung la `h = 0,638`, iar efortul brațului
1 urcă până la **38,6 Nm** la `h = 0,435` (față de 30,2 Nm cu brațul sănătos). Sarcina axului,
vibrația, temperatura, avansul și debitul de răcire vin din același motor de uzură.

**Colectarea.** Formatul este necesar, dar nu suficient: la rata nominală (`--rate 1`) un ciclu
mișcă sănătatea cu **0,006** (1,000 → 0,994), deci un corpus de rutină ar fi complet și fără nimic
de învățat. De aceea `make mj-collect` comprimă viața mașinii: `MJ_COLLECT_RATE` (implicit 20), iar
rata folosită se scrie în fiecare `session.json`, ca traiectoria să poată fi rescalată. Cu
implicitul, o colectare de 5 cicluri pe familie ajunge la `h ≈ 0,48`: benzile și brațul 1 „worn",
scula „degraded".

**Etichetele de eșec** sunt stohastice (hazard crescător, tras cu sămânța sesiunii), deci nu vin
singure dintr-o colectare scurtă. Rețetele de mai jos au fost rulate, ca să nu se ghicească:

| Combinație | Ce iese |
| --- | --- |
| `--rate 20 --cycles 5` (implicitul) | degradare pe 6 componente, `h_min ≈ 0,48`, **0 eșecuri** |
| `--rate 50 --cycles 10` | `h_min = 0,02`, **6 eșecuri** (`belt_slip` ×5, `tool_break`) |
| `--rate 50 --cycles 5 --inject cnc_tool:0.2 --inject arm1:0.2 --inject belt_out:0.2` | `h_min = 0,03`, **4 evenimente** (3 injecții și `tool_break`) |
| `--rate 200 --cycles 5` cu aceleași trei injecții | **10 evenimente**, 4 tipuri de eșec (`gripper_slip`, `tool_break`, `belt_slip`, `spindle_bearing`) |

Fiecare eșec are `kind`, `health`, `severity` și `duration_s`, deci un episod se poate delimita, iar
preavizul se poate măsura — exact metrica din secțiunea 5. Injecțiile apar ca `action`
(`inject_wear`), nu ca eșec: cine citește corpusul vede ce a fost cerut de operator și ce s-a
stricat singur.

**Poarta.** `make mj-collect` urcă prin `publish.py --require-wear`: un corpus în care nicio
componentă nu a coborât sub `h = 0,9` și în care nu s-a înregistrat niciun eșec este refuzat, cu
motivul scris în clar. Iar cifrele intră în manifest, deci răspunsul la întrebare se citește direct
din bucket: `coverage` pe fiecare sesiune (`rows`, `dt_ms`, `gaps`, `health_min`,
`health_min_by_component`, `worn`, `failures`, `actions`) și `totals.health_min`, `totals.failures`,
`totals.carries_wear`.

Ce **nu** conține încă corpusul: ferestrele de 60 s (`windows.parquet`, faza 4) și degradarea
cadrelor de cameră — cadrele ies curate, fără blur proporțional cu `1 - h_camera`.

### Cum ajunge același conținut pe calea live

Ce scrie sesiunea local este exact ce s-ar publica: rândurile din `samples.jsonl` sunt sarcinile
MQTT, cu `topic` inclus, iar metadatele din `snapshots.jsonl` sunt sidecar-ul care declanșează
inspecția. Geamănul nu are încă publicator — `run.py` scrie fișiere —, iar legarea la IoT Core se
face din aceleași trei piese ca la extensia Isaac Sim:

- **Eșantioanele** pleacă pe `cncdt/<env>/cnc/<cell>/telemetry`, cu `paho-mqtt` peste mTLS, prin
  `cnc_cell/telemetry/publisher.py`; aceeași clasă are transportul local în JSONL, deci o sesiune
  fără rețea arată exact ca una live. Regula IoT Core selectează mesajul întreg, adaugă topicul și
  marcajul de timp MQTT, iar Firehose îl convertește în Parquet (Snappy), partiționat pe oră, sub
  `raw/telemetry/dt_year=.../dt_month=.../dt_day=.../dt_hour=.../`, cu microbatch de 60 s și
  Lambda-ul de transformare în zbor (secțiunea 4 din `docs/01-architecture.md`).
- **Cadrele de inspecție** devin pereche PNG + JSON în bucketul data lake, sub `raw/qa-snapshots/`:
  scrierea fișierului JSON este chiar evenimentul care cheamă Lambda-ul de inspecție (notificarea
  S3 filtrează pe `filter_prefix = "raw/qa-snapshots/"`), iar Lambda-ul citește fotografia cu cheia
  din `image_key`, deci numele de fișier al geamănului (`<punct de vedere>-<moment>.png`) intră ca
  atare. Verdictul se scrie în `curated/qa-verdicts/` și se publică pe
  `cncdt/<env>/cnc/<cell>/qa-verdict`, de unde celula acționează pistonul de rejecție.
- **Identitatea** dispozitivului vine din modulul `iot-ingestion` (lucrul `cnc-cell-01`, certificat
  mTLS, politică), iar geamănul citește din mediu aceleași nume ca extensia: `CNCDT_ENVIRONMENT`,
  `CNCDT_DEVICE_ID`, `CNCDT_CELL_ID`, `CNCDT_TELEMETRY_TOPIC`, `CNCDT_QA_PREFIX`.

Două piese lipsesc de pe calea live, amândouă în afara geamănului: urcarea perechii PNG + JSON în
`raw/qa-snapshots/` și un publicator MQTT pentru eșantioane. Sunt subțiri — fișierul JSON urcat sub
`raw/qa-snapshots/`, respectiv un client `paho-mqtt` care trimite rândurile existente —, iar
contractele sunt deja identice, deci fișierele sesiunii nu se schimbă când se scriu. Urcarea
**offline**, a corpusului de antrenare, există deja și merge pe alt prefix (`datasets/`, mai sus):
cele două căi au cititori diferiți și rămân separate.

## 5. Detecția anomaliilor și timpul de anticipare

**Vectorul de caracteristici** este cel din contract, plus mărimi strict locale, disponibile
pentru experiment: `spindle_load_mean/p95/std`, `vibration_rms/peak/std`, `coolant_flow_lpm`,
`temp_c_mean`, `axis_feed_mean`, `joint_effort_mean/max`, `sample_count`, `machine_state`,
plus `cycle_time_s` și `belt_speed_std`, care există în simulare și nu în schema publicată.

`tool_wear_index` și `joint_friction_index` **nu** intră în vector, deși rămân în contract și
în dashboard-ul de degradare: sunt `1 - h`, adică starea internă a motorului de uzură, nu o
citire de senzor. Un detector care le primește are răspunsul în intrare, iar pe linia reală
coloanele nu există (ADR 0013). `MODEL_FEATURE_ORDER` din `lambdas/feature_engineering/core.py`
este lista care decide.

**Antrenare**: RCF local, doar pe ferestre din sesiuni sănătoase, cu aceleași hiperparametri
pentru toate componentele. **Scor**: fiecare fereastră nouă primește un scor de anomalie.
**Alarmă**: scor peste percentila 99 a distribuției sănătoase, pe două ferestre consecutive.

**Evaluare pe episoade etichetate**, raportată ca tabel, nu ca impresie:

| Metrică | Definiție | Țintă |
| --- | --- | --- |
| Preaviz median | `t(eșec) - t(alarmă)`, în secunde de ceas real | ≥ 120 s |
| Rată de detecție | episoade cu alarmă înainte de eșec / episoade totale | ≥ 0,9 |
| Alarmă falsă | alarme pe oră în sesiuni sănătoase | ≤ 1 / 8 h |

Două precizări de onestitate, care se repetă și în `ml/models/`:

1. **RCF nu prezice.** Este un detector nesupervizat de puncte anormale; preavizul vine din
   faptul că traiectoria de uzură este monotonă și lentă, deci fereastra devine anormală cu
   minute înainte ca mașina să cedeze, plus din regula de alarmă. „Cu câteva minute înainte" se
   obține prin proiectarea semnalului de uzură, nu prin algoritm.
2. **Dacă se cere predicție în sens strict**, adică `P(eșec în următoarele H minute)`, modelul
   potrivit este supervizat, pe aceleași ferestre, cu eticheta din `events.csv`. Infrastructura
   de date este aceeași; `ml/src/cncdt_ml/labels.py` există deja pentru partea de etichetare.

Pentru AWS, `mujoco` se înlocuiește cu un export de ferestre în S3: aceleași coloane, același
`$schema`, deci antrenarea SageMaker RCF și inferența din `ml/training/predictive_maintenance/`
și `ml/inference/predictive_maintenance/` se scriu fără să atingă simularea.

## 6. Interfața de control

Vocabularul reutilizează `cnc.control.v1.json` acolo unde există deja comanda
(`inject_tool_wear`, `inject_joint_friction`, `replace_tool`, `repair_joints`, `pause`,
`resume`, `reset`). Comenzile noi — `inject_fault_burst`, `set_wear_rate`, `reset_wear` — sunt
locale geamănului, pentru că schema de control este închisă (`additionalProperties: false`) și
un nume nou acolo cere bump de versiune plus alinierea Lambda-ului de comenzi.

Trei intrări, toate locale:

1. **Consolă (REPL)** — merge headless, în CI, în rulări de noapte: `fault grip cnc 0.4`,
   `repair all`, `rate 20`, `status`.
2. **Viewer MuJoCo** — când există display (macOS de dezvoltare, sesiunea DCV a instanței):
   panou cu butoane pentru injectare, reparație, rată de uzură. Necesită fereastră, deci nu
   rulează în CI.
3. **Endpoint HTTP local** (`127.0.0.1`), pentru scenarii scriptate: aceleași comenzi ca în
   REPL, plus un `GET /status` care întoarce sănătatea componentelor și ultimul scor de anomalie.

Panoul rămâne o unealtă de dezvoltare. Operarea se uită în dashboard-urile CloudWatch (ADR 0005).

## 7. Structura de fișiere

```
simulation/mujoco/          # există
  README.md                # instalare, comenzi, structură, intrinsics, stare cunoscută
  requirements.txt         # mujoco==3.15.0, mujoco-menagerie==2026.10.1, numpy, mapbox_earcut
  view.py                   # --check / --render / --demo / --view, peste scena compusă de cncdt_mj.scene
  run.py                    # sesiunea de colectare: cicluri, telemetrie, cadre, fișierele sesiunii
  publish.py                # urcă în bucket corpusul scris de run.py, cu AWS CLI; plan fără --upload
  runs/                     # sesiunile scrise de `make mj-record`; ignorate de git
  scene/cell.xml            # șablonul celulei; cifrele se pun prin substituție, nu stau în el
  cncdt_mj/
    __init__.py             # căile pachetului
    layout.py               # așezarea în plan (din docs/diagrams/cell_layout.py) și înălțimile; fără MuJoCo
    part.py                 # blocul brut și piesa din generatorul procedural; fără MuJoCo
    belt.py                 # direcțiile benzilor, viteza de suprafață, forța cu limita de frecare; fără MuJoCo
    checks.py               # regulile verificării pe nume de corpuri (contacte normale); fără MuJoCo
    moves.py                # punctele de lucru ale fiecărui braț: ordine, apropiere, treceri; fără MuJoCo
    povs.py                 # cele patru puncte de vedere: cameră, rezoluție, moment, rol; fără MuJoCo
    telemetry.py            # eșantionul pe contract, metadatele cadrului, fișierele sesiunii; fără MuJoCo
    wear.py                 # motorul de uzură: sănătate, efecte în contract, eșecuri, acțiuni; fără MuJoCo
    export.py               # corpusul de antrenare: chei în bucket, manifest, verificări; fără MuJoCo
    scene.py                # compunerea: cell.xml plus cele două brațe; importă mujoco
    cell.py                 # contactul cu banda, viteza piesei, forța scrisă în data.xfrc_applied; importă mujoco
    arms.py                 # cinematica inversă, urmărirea căii, strângerea; importă mujoco
    sensors.py              # din MjData în mărimi de proces: efort, falci, piese, contacte; importă mujoco
    camera.py               # randare offscreen (PNG scris de mână) și cadrele sesiunii; importă mujoco
```

```
simulation/mujoco/cncdt_mj/  # de scris, în fazele 4-5
  detector.py               # RCF local + regula de alarmă (pur, numpy)
  report.py                 # preaviz, rată de detecție, alarme false (pur)
  control.py                # REPL, endpoint local, punte către viewer
  cell.py                   # are transportul pe benzi și ciclul brațelor; lipsesc prinderea și stările mașinii
tests/unit/
  test_mujoco_scene.py      # există: layout/part/checks fără MuJoCo, înălțimile față de acest document, camera de inspecție; partea care atinge motorul se rulează cu `make mj-test`
  test_mj_telemetry.py      # există: contractele eșantionului și ale cadrului, planul capturii, fișierele sesiunii
  test_mj_wear.py           # există: ecuația și independența de pas, efectele în contract, eșecurile, acțiunile
  test_mj_export.py         # există: cheile din bucket, manifestul, refuzul unui corpus cu găuri
  test_mj_detector.py       # prag, alarmă susținută, preaviz pe episod sintetic
```

Injecția de defecțiuni, reparația și rata de uzură nu mai au un modul propriu (`faults.py`):
trăiesc în `wear.py`, ca acțiuni care își lasă eticheta în `events.jsonl` — aceeași listă de
comenzi pe care o va chema consola din `control.py`.

**De ce scena nu se poate scrie declarativ.** MJCF nu poate aduce singur modelele din
`mujoco-menagerie`: `<attach model="...">` nu funcționează în MuJoCo 3.15.0 — încercat cu patru
forme de cale, toate sfârșite cu „could not find model" — iar `modeldir` nu există pe `<compiler>`.
`scene/cell.xml` ține deci doar componentele celulei, iar cele două brațe se atașează programatic,
cu `MjSpec.attach(child, prefix=..., frame=body.to_frame())` și `copy_during_attach = True` pentru
al doilea exemplar. Celula își scrie propriile poziții nominale, fiindcă cele ale modelului copil
se importă și s-ar ciocni între cele două brațe.

Regula din `AGENTS.md` se respectă: identificatorii în engleză, comentariile de design în
română, logica pură separată de cea care atinge motorul de fizică — `layout.py`, `part.py`,
`belt.py`, `moves.py`, `checks.py`, `povs.py`, `telemetry.py` și `export.py` se importă din
`.venv`-ul proiectului, unde `mujoco` nu există, iar `tests/unit/test_mujoco_scene.py` verifică
exact ce module ating motorul: `scene.py`, `cell.py`, `arms.py`, `sensors.py`, `camera.py`.

## 8. Faze și criterii de acceptare

| Fază | Ce se livrează | Criteriu de acceptare |
| --- | --- | --- |
| 0 | ADR 0012 + documentul de față | revizuite, fără cod |
| 1 | `wear.py` + teste pure | `pytest` verde fără MuJoCo instalat; uzura e monotonă, mărginită și se accelerează la sănătate mică; ferestrele au exact coloanele din contract |
| 2 | `scene/cell.xml`, `belt.py`, `cell.py`, `povs.py`, `sensors.py`, `camera.py`, `run.py` | o sesiune de 10 minute simulează un ciclu complet (bandă 1 → braț 1 → CNC → braț 1 → bandă 2 → braț 2 → cutie) fără piese pierdute în geometrie; timpul de generare se măsoară și se notează în README |
| 3 | scrierea sesiunii (JSONL cu eșantioane, cadre, metadate, uzură și evenimente; apoi Parquet) | aceeași sămânță produce fișiere identice; un test de paritate compară vectorii de caracteristici cu cei din scena Isaac Sim pe scenariu nominal |
| 4 | `detector.py`, `report.py` + evaluare | tabelul de metrici din secțiunea 5, generat de o comandă, pe minimum 20 de episoade de eșec și 8 ore sănătoase |
| 5 | `control.py` + degradarea camerei | injectarea și reparația din REPL se văd în telemetrie în aceeași fereastră; cadrul de cameră degradat păstrează piesa detectabilă până la `h` critic |
| 6 | (opțional) SageMaker RCF + card de model | antrenare din ferestrele exportate, card în `ml/models/`, cost justificat în PR |

Starea de azi: din faza 2 există partea de scenă — `layout.py`, `part.py`, `checks.py`, `scene.py`,
`scene/cell.xml` și `view.py` —, transportul pe benzi (`belt.py`, `cell.py`, cu linia de oprire din
plan și viewer cu benzile pornite) și **brațele în mișcare**: `moves.py` ține punctele de lucru ale
fiecărui braț, iar `arms.py` le atinge cu cinematică inversă, urmărind linii drepte în spațiul
sculei. `view.py --check` este verde, `view.py --demo` rulează ciclul și tipărește cifrele fiecărei
opriri, iar testele geamănului acoperă partea pură (`tests/unit/test_mujoco_scene.py`) și
contractele colectării (`tests/unit/test_mj_telemetry.py`).

Din faza 1 există motorul de uzură: `cncdt_mj/wear.py` ține sănătatea celor șapte componente,
o integrează exact pe timpul de lucru al fiecăreia, compune mărimile de așchiere, trage eșecurile
discrete și primește acțiunile de operator. Testele pure sunt `tests/unit/test_mj_wear.py`.

Din faza 3 există scrierea sesiunii: `make mj-record` parcurge un ciclu pe fiecare familie și scrie
cele șapte ieșiri (cinci cadre pe ciclu), iar `samples.jsonl` respectă întocmai contractul.
Există și **colectarea cu urcare în bucket**: `make mj-collect` scrie sesiunile într-un director de
rulare propriu și le urcă sub `training-data/mujoco/dt=<zi>/run-<UTC>/`, cu manifestul corpusului
lângă ele; `cncdt_mj/export.py` verifică sesiunile înainte, iar `publish.py` urcă prin AWS CLI
(secțiunea 4, „Colectarea și urcarea în bucket").
Criteriul **nu este atins**: ciclul atinge punctele, dar nu prinde piesa (falcile doar o ating,
fiindcă deschiderea mâinii Panda, 83 mm, este mai mică decât discul de 104 mm — prinderea se
modelează ca legătură rigidă, ca în celula din Isaac Sim), mașina nu are axe, iar un ciclu ține
~65 s simulați, deci „o sesiune de 10 minute" cere zeci de cicluri. Cadrul fix de proces se ia la
începutul ciclului, fiindcă în geamăn piesa mașinată stă deja sub cameră când începe ciclul, iar
banda o duce întâi la linia de preluare a brațului 2. Când prinderea există, ordinea devine cea
reală: piesa ajunge sub cameră după ce brațul 1 o lasă pe bandă.

Fiecare fază se poate opri fără să lase ceva rupt: faza 1 nu are dependențe de MuJoCo, faza 2 nu
atinge contractele publicate.

## 9. Riscuri și capcane

- **Banda cere forțe proprii.** MuJoCo nu are bandă nativă; modulul de forțe tangențiale trebuie
  să fie stabil la pas mare, altfel piesele tremură sau cad prin geometrie. Se validează cu un
  test de transport: o piesă parcurge banda în timpul așteptat, la trei pași de integrare
  diferiți.
- **Determinismul are limite.** Aceeași versiune de MuJoCo, același nucleu, aceeași sămânță dau
  rezultate identice; o altă versiune poate schimba ultimele zecimale. De aceea versiunea se
  fixează în `requirements.txt`, iar `session.json` o notează.
- **Două Python-uri.** Kit (Isaac Sim) și proiectul rulează pe versiuni diferite de Python.
  Geamănul MuJoCo nu se importă din extensia Isaac Sim, nici invers; comunicarea, dacă va fi
  nevoie, se face prin fișiere sau prin rețea.
- **Licențele modelelor.** Modelul Panda din `mujoco-menagerie` este Apache-2.0; fiecare model
  folosit se verifică la adoptare, iar atribuirea se păstrează.
- **Preavizul se măsoară în ceas real.** La 60 s per fereastră, „câteva minute" înseamnă cel
  puțin două ferestre; sesiunile de evaluare trebuie să dureze real sau să folosească o rată de
  uzură mărită, cu mențiunea explicită în raport.
- **Divergența dintre geamănuri.** Cel mai probabil mod de a strica proiectul este să ajustezi
  uzura într-un geamăn și să uiți celălalt. Testul de paritate din faza 3 este plasa de
  siguranță; dacă el cade des, semnul este că motoarele trebuie unificate.
- **Volumul de date.** 10 Hz × 6 dispozitive ≈ 5,2 milioane de eșantioane pe zi. Parquet cu
  compresie și partiționare pe oră ține lucrurile rezonabile, dar sesiunile de noapte se
  planifică pe disc, nu pe bunăvoință.

## 10. Decizii lăsate deschise

1. **Unificarea motoarelor de uzură** (geamăn MuJoCo și extensia Isaac Sim) — se decide după
   faza 3, când contractul de caracteristici e dovedit stabil.
2. **Bump de schemă pentru uzura benzii și calitatea camerei** — se decide după faza 4, în
   funcție de dacă detectorul le cere în lake.
3. **Predicție supervizată peste RCF** — se decide după faza 4, când se vede cât preaviz dă
   detecția nesupervizată.
4. **Paralelizare pe GPU (MJX sau MuJoCo Warp)** — se decide când timpul de generare devine
   limita reală a antrenării.
5. **Cota piedestalului brațelor** — închisă la 0,95 m. Raftul a scos piedica pe care o puneau
   pereții cutiilor, dar a rămas banda 2: ca să ridice piesa de pe ea, brațul 2 trebuie să treacă
   cu antebrațul peste platformă, iar la 0,55 m platforma este deasupra lui (`arm2_link3↔belt_out`,
   106 mm eroare), iar locurile din raft devin de neatins. La 0,95 m ciclul se încheie pentru toate
   cele cinci familii, fără nicio verigă atinsă; prețul este că anvelopa brațelor urcă peste axa
   benzii 2, deci camera fixă se ridică la cota documentată a camerelor, 2,20 m (gol minim
   eșantionat: 0,115 m față de brațul 1, 0,095 m față de brațul 2, pe 2000 de configurații).
   Verdictul de calitate rămâne al camerei de pe flanșa brațului 2, care fotografiază piesa ținută,
   iar camera fixă privește banda de sus, pentru cadrul de proces.
