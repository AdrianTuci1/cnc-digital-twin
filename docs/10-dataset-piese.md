# Datasetul de piese: generator, etichete, separare

Piesa de referință a celulei este o **roată dințată plană**. Acest document explică de unde
vin variantele, cum se nasc etichetele și unde se trage linia între antrenare și inferență.

## Ce produce generatorul

`simulation/extensions/cncdt.cnc_cell/cncdt/cnc_cell/parts/gear.py` construiește piesa
direct, din inele 2D, fără operații booleene: conturul dințat, alezajul (cu sau fără pană de
cheie) și fiecare gaură sunt câte un inel propriu, fețele se triangulează cu `mapbox-earcut`
(acceptă inele interioare), iar pereții se ridică între cele două fețe.

Fiecare **defect este o schimbare de parametri**, nu o operație pe geometrie. De aici vine
proprietatea care contează: geometria randată și eticheta provin din același obiect, deci nu
se pot dezsincroniza.

Piesa nominală: Ø100 × 15 mm, 18 dinți, alezaj Ø40 cu pană 12 × 5, 5 găuri Ø9 pe un cerc Ø70.

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

## Catalogul de defecte

| Clasă | Ce se schimbă |
| --- | --- |
| `PASS` | nimic, doar abaterea în toleranță |
| `TOOTH_MISSING` | un dinte dispare din contur |
| `TOOTH_CHIPPED` | vârful unui dinte cade pe o pantă |
| `TOOTH_COUNT_WRONG` | 17 sau 19 dinți în loc de 18 |
| `BORE_OVERSIZE` / `BORE_UNDERSIZE` | alezajul central cu ±2 mm |
| `BORE_MISSING` | alezajul dispare |
| `KEYWAY_MISSING` | pana de cheie dispare |
| `HOLE_MISSING` | una din cele 5 găuri dispare |
| `HOLE_OFF_CIRCLE` | o gaură iese 2,5 mm de pe cercul de buloane |
| `BOLT_PATTERN_ROTATED` | tot cercul de găuri e rotit cu 15° |

## Trei reguli de design care nu se văd din cod

**Numărul de dinți nu este multiplu de 5.** Cu 20 de dinți și 5 găuri, o rotire de 72°
suprapune piesa peste ea însăși, iar dezalinierea dintre cercul de găuri și dinți devine
invizibilă. Cu 18 dinți (pas 20°) și 5 găuri (pas 72°), cel mai mic multiplu comun este 360°,
deci ansamblul are simetrie de ordin 1 și orice rotire relativă se vede. Aceeași regulă
interzice 10, 15 și 20 de dinți.

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
| manifest de etichete | doar antrenarea | seed, parametri, clasa de defect, verdictul adevărat |

Separarea trebuie impusă arhitectural, nu prin disciplină: alt prefix sau alt bucket, cu IAM
care nu ajunge la Lambda-ul de inferență. Câmpurile de degradare (`tool_wear_index`,
`surface_quality`) și `is_synthetic_defect` ies din snapshot. Altfel modelul învață metadatele
care conțin răspunsul, iar acuratețea mare nu înseamnă nimic.

Rezerva pe praguri din `qa_vision_inference` trebuie scoasă de pe calea de viziune: altfel, în
`dev`, verdictul vine din metadate și pare că modelul funcționează.

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
- **etichete**: verdictul corespunde clasei, iar numărul de dinți nu este niciodată multiplu de 5.

Ce nu se poate verifica aici: cum arată piesa randată. Asta cere Isaac Sim.

## Ce urmează

1. **Randarea**: bucla Replicator care încarcă o variantă, o fotografiază din camera de sus și
   scrie imaginea plus snapshot-ul, cu seed de randare separat.
2. **Antrenarea**: clasificator de imagini pe SageMaker, pe split-urile din manifest.
3. **Verificarea**: baseline pe metadate, etichete amestecate, geometrie decuplată de etichetă.
