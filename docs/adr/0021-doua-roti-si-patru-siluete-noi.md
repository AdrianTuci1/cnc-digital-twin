# ADR 0021: Doar două familii rămîn roți, restul primesc altă siluetă

Data: 2026-10-18
Stare: acceptată

## Context

ADR 0020 a adăugat placa `plate_84`, dar celelalte cinci familii erau tot roți dințate.
Diferențele dintre ele — diametru, număr de dinți, alezaj, cerc de găuri — se citesc greu la o
rulare întreagă, iar din camera de inspecție silueta rămîne circulară. Cerința este ca rularea să
arate mai multe forme, cu una sau două roți păstrate ca referință.

Generatorul construiește piese plane din inele 2D triangulate cu `mapbox-earcut`. Orice contur
simplu, convex sau nu, intră în același flux; defectele sînt schimbări de parametri, nu operații
pe mesh. Schimbarea formelor nu cere deci un pipeline nou.

## Decizie

Rămîn două roți dințate: `wheel_100` (piesa de referință, Ø100) și `pinion_56` (Ø56, cu pană).
Celelalte trei familii își schimbă numele și geometria:

| Familie veche | Familie nouă | Siluetă | Gabarit |
| --- | --- | --- | --- |
| `flange_72` | `gusset_72` | triunghi echilateral cu colțuri rotunjite | cerc încadrător Ø72, latură ~62 |
| `bushing_68` | `hex_68` | hexagon cu colțuri rotunjite | cerc încadrător Ø68, lățime ~59 |
| `wheel_60` | `link_68` | legătură cu capete semicirculare | 68 × 28 |

Placa `plate_84` rămîne neschimbată. Rezultatul este o suită de cinci siluete — cerc dințat,
dreptunghi, triunghi, hexagon și capsulă — cu șase familii.

- **Contururile noi** se construiesc din arce tangente la laturi: poligoanele regulate primesc
  colțuri rotunjite, iar legătura două arce de 180° unite prin laturi drepte. Triangularea,
  pereții laterali și închiderea meshului sînt aceleași ca la roată.
- **Defectele** rămîn cele care au sens pe fiecare formă: `TOOTH_*` și `KEYWAY_MISSING` lipsesc
  la cele patru piese fără dinți și fără pană. Alezajul, găurile și rotirea cercului de găuri
  rămîn valabile pe toate.
- **Prinderea** se face pe contur pentru piesele sub 80 mm. Roata `wheel_100` rămîne singura
  prinsă pe fața de sus.
- **Suportul din universal** devine un cilindru îngust și înalt (rază 10 mm, înălțime 20 mm).
  Falcile brațului coboară sub punctul de strîngere; cu suportul vechi de rază 35 mm, o piesă
  îngustă ca triunghiul sau legătura se sprijinea pe suport înainte ca falcile să atingă piesa.
- **Ordinea pe raft** rămîne aceeași ca poziții: raftul 1 — `wheel_100`, `gusset_72`; raftul 2 —
  `hex_68`, `link_68`; raftul 3 — `pinion_56`, `plate_84`.

## Consecințe

- Etichetele `family` din manifest și din corpus se schimbă pentru trei familii; un model
  antrenat pe numele vechi trebuie reantrenat sau mapat explicit. Geometric, corpusul vechi nu
  mai corespunde noilor familii.
- Toate cele șase familii au volume între ~13 500 și ~74 000 mm³, deci blocul brut rămîne un
  cilindru de gabarit apropiat, iar scena și raftul nu se schimbă.
- Prinderea fizică și ciclul rămîn verzi pe toate familiile, la două semințe. Suportul mai
  îngust nu schimbă cota de lucru a brațului; schimbă doar înălțimea la care stă piesa în
  universal.
- Testele verifică două lucruri noi: că doar două familii au dinți și că fiecare siluetă nouă are
  gabaritul așteptat, măsurat pe mesh.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Păstrarea numelor vechi cu geometrie nouă | Eticheta ar minți: `flange_72` ar fi un triunghi, iar clasificatorul ar învăța o mapare care nu descrie piesa. |
| Cornier în L | Conturul concav cere rotunjirea colțurilor reflexe și mai multă geometrie; triunghiul dă aceeași distincție vizuală cu mai puțin cod. |
| Încă o roată cu dinți diferiți | Silueta rămîne circulară, exact ce trebuia schimbat. |
| Texturi sau culori diferite pe aceeași formă | Camera de inspecție vede silueta; culoarea nu ajută la separarea familiilor și nu schimbă fizica. |
| Renunțarea la `wheel_100` | Este piesa de referință a testelor și a contractului; două roți păstrează continuitatea. |

## Verificare

- `tests/unit/test_part_generator.py`: mesh închis și orientat pentru fiecare familie și seed,
  volum plauzibil, gabaritul noilor siluete, doar două familii cu dinți, toate clasele de defect
  schimbă geometria.
- `make test` și `make mj-test` — partea pură și scena.
- `make mj-demo` — șase familii × două semințe: 6/6 prinderi pe ciclu, eroare maximă a sculei
  23,4 mm, piesa în compartiment.
- `make mj-record` — sesiuni pentru toate cele șase familii.
- `simulation/mujoco/renders/parts/`: cîte un cadru pentru fiecare siluetă.
