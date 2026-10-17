# Datasetul de piese: familii, generator, etichete

Piesele celulei sunt **roți dințate plane și o placă de montaj**, grupate în familii. Acest
document explică de unde vin variantele, cum se nasc etichetele și unde se trage linia între
antrenare și inferență.

## Ce produce generatorul

`simulation/extensions/cncdt.cnc_cell/cncdt/cnc_cell/parts/gear.py` construiește piesa
direct, din inele 2D, fără operații booleene: conturul exterior (dințat ori dreptunghiular cu
colțuri rotunjite), alezajul (cu sau fără pană de cheie) și fiecare gaură sunt câte un inel
propriu, fețele se triangulează cu `mapbox-earcut` (acceptă inele interioare), iar pereții se
ridică între cele două fețe.

Fiecare **defect este o schimbare de parametri**, nu o operație pe geometrie. De aici vine
proprietatea care contează: geometria randată și eticheta provin din același obiect, deci nu
se pot dezsincroniza.

```bash
# 220 de variante, clase echilibrate, împărțite train/val/test
./simulation/scripts/generate-parts.py --count 220 --balanced --split

# doar manifestul, eșantionare aleatoare (PASS 35%, restul egal)
./simulation/scripts/generate-parts.py --count 500

# completează o clasă rară
./simulation/scripts/generate-parts.py --count 40 --defect TOOTH_MISSING --seed0 100000
```

Ieșirea ajunge în `ml/artifacts/parts/` (ignorat de git). Modulul are nevoie de `numpy` și
`mapbox-earcut`, declarate în `simulation/extensions/cncdt.cnc_cell/requirements.txt`.

## Familiile de piese

Tabelul `FAMILIES` din generator este singura sursă de adevăr pentru familii. Cinci sunt roți
dințate plane, iar a șasea este o placă dreptunghiulară cu colțuri rotunjite — același proces,
aceeași sculă — dar diferă în plan, fiindcă de sus, din camera de inspecție, se vede silueta,
iar grosimea aproape deloc.

| Familie | Formă | Gabarit | Dinți | Alezaj | Pană | Găuri de buloane | Grosime | Prindere |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `wheel_100` | roată | Ø100 | 18 | Ø40 | 12 × 5 | 5 × Ø9 pe R35 | 15 | peste fălci (piesa de referință) |
| `flange_72` | roată | Ø72 | 24 | Ø28 | — | 3 × Ø6 pe R24 | 10 | între fălci |
| `bushing_68` | roată | Ø68 | 20 | Ø30 | — | 3 × Ø7 pe R22 | 14 | între fălci |
| `wheel_60` | roată | Ø60 | 18 | Ø20 | 6 × 3,5 | 5 × Ø5 pe R18,5 | 15 | între fălci |
| `pinion_56` | roată | Ø56 | 12 | Ø14 | 5 × 3 | 4 × Ø6 pe R15,5 | 12 | între fălci |
| `plate_84` | placă | 84 × 56, colțuri R8 | — | Ø18 | — | 4 × Ø6 pe R20 | 12 | pe latura de 56 mm |

Placa are diametrul cercului care o încadrează de 101 mm, dar se prinde pe latura scurtă, de
56 mm, deci încape între fălcile de 80 mm. Silueta dreptunghiulară este și motivul pentru care
apare în celulă: la o rulare, se vede imediat că piesa nu este o roată.

Regulile pe care tabelul le respectă, verificate în `tests/unit/test_part_generator.py`:

- **formele se deosebesc vizual**: diametrele roților diferă cu cel puțin 4 mm, iar placa are
  siluetă dreptunghiulară. Sub asta siluetele se confundă la rezoluția camerei de inspecție;
- **familiile trec validarea și rămân la distanță de fiecare defect**: alezajul mărit cu
  2 mm nu are voie să ajungă în cercul de găuri, gaura deplasată cu 2,5 mm nu are voie să
  iasă din margine, iar lățimile dinților trebuie să rămână valide și la 17 ori 19 dinți.
  Fără marje, o variantă iese din propria siluetă ori, mai rău, arată exact ca piesa nominală;
- **familiile mici încap între fălcile de 80 mm ale prinzătorului**, deci acolo prinderea
  poate fi reală. Piesa de referință, Ø100, e prea mare și rămâne prinsă prin constrângere
  (`grasping.py` în Isaac Sim, egalitatea `weld` în geamănul MuJoCo) sau mutată în poziția de
  strunjire fără prindere reală;
- **numărul greșit de dinți (17 ori 19) nu coincide cu nicio familie**, altfel un rebut ar
  arăta ca piesa nominală a altei familii.

Piesa nominală folosită implicit de unelte este `wheel_100`: Ø100 × 15 mm, 18 dinți, alezaj
Ø40 cu pană 12 × 5, 5 găuri Ø9 pe un cerc Ø70.

## Coduri pe raft: amânat

Pe raftul de la capătul benzii s-ar putea lipi coduri citite de model. Nu le punem: comanda
mașinii știe deja ce piesă produce, iar codul ar trebui să fie lizibil din camera de
inspecție. La 3,6 px/mm și un cod de 8 module, modulul ar avea 2–3 px, sub limita de ~5 px
de la care o diferență se învață. Identitatea piesei vine din programul mașinii, nu din
imagine.

## Catalogul de defecte

| Clasă | Ce se schimbă |
| --- | --- |
| `PASS` | nimic, doar abaterea în toleranță |
| `TOOTH_MISSING` | un dinte dispare din contur |
| `TOOTH_CHIPPED` | vârful unui dinte cade pe o pantă |
| `TOOTH_COUNT_WRONG` | 17 sau 19 dinți, în locul celor nominali |
| `BORE_OVERSIZE` / `BORE_UNDERSIZE` | alezajul central cu ±2 mm |
| `BORE_MISSING` | alezajul dispare |
| `KEYWAY_MISSING` | pana de cheie dispare |
| `HOLE_MISSING` | una din găurile de buloane dispare |
| `HOLE_OFF_CIRCLE` | o gaură iese 2,5 mm de pe cercul de buloane |
| `BOLT_PATTERN_ROTATED` | tot cercul de găuri e rotit cu 15° |

Magnitudinile sunt fixe, aceleași pe toate familiile: sunt dimensiuni de defect, nu fracțiuni
din piesă. `KEYWAY_MISSING` are sens doar pe familiile cu pană — `PartFamily.defect_classes()`
o scoate din liste pentru `flange_72` și `bushing_68` —, iar defectele `TOOTH_*` lipsesc la
`plate_84`, care nu are dinți. Generatorul refuză combinațiile, ca să nu apară o etichetă de
rebut pe geometrie corectă.

## Trei reguli de design care nu se văd din cod

**Rotirea cercului de găuri trebuie să nu fie o simetrie a piesei.** La piesa de referință,
cu 5 găuri la pas de 72°, asta cere ca numărul de dinți să nu fie multiplu de 5 (18 dinți,
pas 20°, cel mai mic multiplu comun 360°, deci simetrie de ordin 1). Regula generală este
însă despre deplasare: rotirea de 15° trebuie să mute găurile cu câțiva milimetri, iar
testul măsoară direct asta pe fiecare familie.

Aceeași proprietate decide și augmentarea imaginilor la antrenare: `BOLT_PATTERN_ROTATED` este
definit față de coroana dințată a piesei, deci o rotire în plan a întregului cadru lasă clasa
neschimbată și poate fi folosită. Scalarea nu poate: familia se citește din mărimea siluetei
(Ø56…Ø100), iar un zoom ar face o `pinion_56` să arate ca o `wheel_100`.

**Rezoluția dictează dimensiunea minimă a defectului.** Dacă roata umple ~700 px dintr-un cadru
de 1280, la Ø100 mm rezultă ~0,14 mm pe pixel. Un defect are nevoie de cel puțin ~5 px ca să
fie învățat, adică **~0,7 mm**. De aceea defectele de aici au 2–5 mm. Nu mări numărul de dinți
peste ~30: un dinte trebuie să rămână lat, nu o linie subțire.

**De sus se vede silueta, nu grosimea.** Dinții, găurile, alezajul și pana se văd; grosimea și
teșiturile verticale aproape deloc. De aceea catalogul nu are clase de tip „grosime greșită".

## Eticheta: două artefacte separate

Aceasta este regula care face antrenarea corectă. Inferența vede **doar imaginea**.

| Artefact | Cine îl vede | Ce conține |
| --- | --- | --- |
| snapshot QA | inferența | imaginea, `cycle_id`, `part_id`, camera, rezoluția |
| manifest de etichete | doar antrenarea | seed, **familia**, parametri, clasa de defect, verdictul adevărat |

Manifestul are două capete de etichetă, ambele ieșite din aceeași decizie: **familia** piesei
(ce este) și **clasa de defect** (dacă e conformă). Un model cu un singur cap nu poate
deosebi „piesa asta e din altă familie" de „piesa asta e rebut", iar după mutarea coșurilor
pe raft exact asta trebuie să facă cel de-al doilea braț: să pună fiecare piesă în
compartimentul familiei ei. Schema manifestului a urcat la `1.1.0` odată cu câmpul `family`;
un rând `1.0.0`, de dinainte de familii, se citește în continuare și descrie piesa de
referință.

Separarea este impusă arhitectural, nu prin disciplină (ADR 0013). Contractul cadrului a urcat
la **1.2.0** și a scos câmpurile care purtau răspunsul (`tool_wear_index`,
`joint_friction_index`, `surface_quality`, `is_synthetic_defect`), iar schema s-a închis
(`additionalProperties: false`). Aceleași nume sunt păzite de toți cei care ating cadrul:
geamănul nu are parametru pentru ele, `QaSnapshotWriter` ridică `ValueError`, iar
`parse_snapshot` din Lambda refuză un cadru care le conține. Legătura dintre imagine și etichetă
se face la antrenare, pe `part_id`/`cycle_id`, din artefactele de colectare.

Rezerva pe praguri a ieșit din `qa_vision_inference`. Fără endpoint configurat, Lambda-ul nu
publică niciun verdict: numără cadrele în `QaModelMissing` și lasă piesa rutei de rezervă.
Verdictul din demonstrație vine exclusiv din modelul antrenat, ceea ce face modelul obligatoriu
pentru orice demonstrație care sortează.

## Verdictul se decide înainte de randare

În codul actual verdictul se decide la `finish_part()`, care rulează **după** captură, iar
defectul aleator se trage chiar atunci. Pentru dataset asta e fatal: în momentul fotografierii
eticheta nu există. Fixul este ca alegerea clasei să se facă la `begin_cycle()`, iar acea
alegere să dicteze și geometria randată, și eticheta.

## Randomizarea de randare este separat

Seed-ul de geometrie și seed-ul de randare trebuie să fie **independente**: unul alege forma și
defectul, celălalt unghiul camerei, lumina, poziția și zgomotul. Dacă un singur seed le
controlează pe amândouă, eticheta și condițiile de randare devin corelate accidental, iar
modelul învață unghiul în loc de defect. Invariantul care contează este independența dintre
etichetă și condițiile de randare.

## Ce se poate verifica fără Isaac Sim

`tests/unit/test_part_generator.py` rulează fără simulator și verifică:

- **determinism**: același seed dă exact același mesh, iar reconstrucția din parametrii din
  manifest dă exact aceeași geometrie;
- **topologie**: fiecare muchie apare de exact două ori (mesh închis) și volumul orientat este
  pozitiv, deci normele ies spre exterior;
- **efectul fiecărui defect**: un dinte lipsă scoate exact un sector de vârf, alezajul mărit
  deschide gaura, pana dispărută aplatizează alezajul, cercul rotit mută toate găurile;
- **etichete**: verdictul corespunde clasei, iar fiecare defect chiar schimbă geometria, pe
  fiecare familie;
- **familiile**: fiecare nominal trece validarea și rămâne la distanță de fiecare defect,
  familiile se deosebesc în plan, iar numărul greșit de dinți nu coincide cu nicio familie.

Ce nu se poate verifica aici: cum arată piesa randată. Verificarea asta se face cu
`tests/unit/test_mujoco_scene.py`, care randează o variantă și cere ca PNG-ul să nu fie gol;
imaginea se inspectează apoi cu ochiul.

## Randarea și datasetul de imagini

`simulation/mujoco/render_parts.py` reconstruiește fiecare variantă din manifest (parametrii
compleți, deci geometria este exactă), o așază pe un plan și o fotografiază din camera de sus.
Planul are o textură de beton, iar piesa una de metal periat, ambele generate procedural de
`cncdt_mj/textures.py`; o a treia lumină, laterală, ridică marginea discului, ca un obiect de
metal să nu se piardă în fundal. Unghiurile, lumina și distanța camerei se trag din poziția
globală a piesei și din `--render-seed`, un flux separat de seed-ul de geometrie: altfel eticheta
și condițiile de randare s-ar corela accidental. OBJ-ul repetă vîrfurile pe fiecare triunghi:
dacă un vîrf ar fi împărtășit între fața de sus și flancul unui dinte, normala medie ar înclina
toată fața, iar discul ar ieși vălurit.

```bash
# manifest + imagini, local (venv-ul geamănului are mujoco)
make mj-parts-dataset PARTS_COUNT=220 PARTS_VIEWS=4 PARTS_SIZE=640

# doar cadrele, din manifestul existent
make mj-parts-images

# pe GPU-uri Modal: manifestul într-un container, cadrele în PARTS_SHARDS containere
make mj-modal-parts PARTS_COUNT=220 PARTS_SHARDS=8
```

Ieșirea locală stă în `ml/artifacts/parts/` (ignorat de git), iar cea din bucket sub
`training-data/parts/dt=<zi>/run-<UTC>/`:

```
parts.jsonl            # etichetele: part_id, seed, family, verdict, defect, params
parts-train.jsonl      # split-urile, stratificate pe clasă
parts-val.jsonl
parts-test.jsonl
images.jsonl           # indexul: PNG -> familie, defect, verdict, split, unghi, rezoluție
images-<split>.jsonl   # același index, pe split
images/<familie>/<clasă>/<piesă>-vNN.png
manifest.json          # ce conține datasetul, de unde se citește, revizia git
```

Fiecare rînd din index poartă `part_id`, `view`, `path`, `family`, `defect`, `verdict`, `split`,
`azimuth_deg`, `elevation_deg`, `distance_m`, `width` și `height`. Antrenarea citește indexul;
inferența primește doar imaginea. `publish_parts.py` verifică înainte de urcare că fiecare rînd
are etichetă și că fiecare PNG există, apoi unește indexurile felilor.

## Ce urmează

1. **Antrenarea**: clasificator de imagini pe SageMaker, cu două capete (familie, conformitate),
   pe split-urile din manifest.
2. **Prinderea**: model de grasp pe cadrele de prindere din `training-data/grasp/`; prinderea este
   modelată fizic în geamăn, deci cadrele arată apropierea, piesa între fălci și ridicarea, iar
   fiecare cadru are în `session.json` rezultatul prinderii (contact, deschidere, reușită).
3. **Verificarea**: baseline pe metadate, etichete amestecate, geometrie decuplată de etichetă.
