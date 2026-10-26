# ADR 0024: Scula se rotește continuu pe traseu, cu comenzi rampate la viteza articulațiilor

- **Status:** acceptat
- **Data:** 2026-10-26
- **Decizat de:** echipa de proiect
- **Pornește de la:** ADR 0019 (prinderea fizică), ADR 0022 (alinierea falcilor la formă),
  [11-geaman-mujoco.md](../11-geaman-mujoco.md)

## Context

Fiecare preluare se termină cu o orientare diferită a sculei: pe bandă falcile coboară drept, iar
în dulap intră înclinat, la 50° în compartimentul sudic și 30° în cel nordic. `arms.follow_line`
ducea vîrful sculei pe o linie, dar impunea orientarea finală încă de la primul punct și scria
comenzile articulațiilor direct, dintr-un pas în următorul.

În viewer, ambele brațe făceau la un moment dat o răsucire bruscă, iar măsurătoarea a arătat
salturi de **128–293°** într-un singur pas de 2 ms. Prinderea fizică ținea piesa, dar solverul
vedea o accelerație pe care niciun servomotor nu o poate produce, iar mișcarea nu se putea filma.
Cerința este ca ambele brațe să se miște continuu, cu aceeași poziție finală.

## Decizie

1. **Orientarea se interpolează pe tot traseul.** `slerp_quat` ia drumul cel mai scurt între
   quaternionii de start și de final (schimbă semnul cînd produsul scalar este negativ, trece la
   amestec liniar doar la unghiuri sub 0,05°), iar `follow_line` cere la fiecare fracțiune
   orientarea interpolată, nu pe cea finală.
2. **Punctul de strîngere urmează linia.** Ținta de poziție se scrie ca punctul de pe traseu minus
   decalajul vîrfului rotit cu orientarea de acum, deci piesa ținută rămîne pe linia cerută în
   timp ce încheietura se rotește.
3. **Unghiurile mari primesc mai multe puncte.** Numărul de puncte este maximul dintre cel cerut de
   lungime și cel cerut de rotație, calculat cu `TOOL_TURN_SPEED_RAD_S = 60°/s`. O răsucire de
   90° nu mai încape într-un singur segment.
4. **Comenzile se rampează.** Diferența de articulații pînă la următorul punct se împarte în pași
   astfel încît `max |Δq| ≤ JOINT_SPEED_RAD_S · timestep`. Solverul pornește din comenzile
   anterioare, ca soluția să rămînă pe aceeași ramură a IK-ului.
5. **Vitezele rămîn cele ale celulei**: `TOOL_SPEED_MPS = 0,15 m/s`, `JOINT_SPEED_RAD_S` și
   `TOOL_TURN_SPEED_RAD_S = 60°/s`. Nu se comprimă timpul ca să ascundă discontinuitatea.

## Consecințe

- Pe trei cicluri înlănțuite, saltul maxim de comandă este **0,11°/pas** la ambele brațe, în loc
  de 128–293°. Testul `test_schimbarea_de_orientare_a_sculei_nu_face_un_salt` verifică și o
  apropiere directă în dulap: eroarea de poziție rămîne sub `checks.MAX_POSE_ERROR_M`, iar saltul
  sub 1°.
- Poziția finală nu se schimbă: prinderile, legăturile `weld` și măsurătorile din compartimente
  rămîn cele din ADR 0019 și 0023.
- Ciclul se lungște acolo unde rotația cere pași în plus; bugetul de demonstrație rămîne
  respectat, iar mișcarea se poate filma la 30 fps fără să sară cadre.
- `follow_line` întoarce în continuare `path_residual_m`, deci o traiectorie care nu poate fi
  atinsă se raportează, nu se ascunde.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Doar `slerp`, fără rampare | Comanda articulațiilor putea sări în continuare la schimbarea punctului, fiindcă soluția IK trece pe altă ramură. |
| Doar rampare, fără `slerp` | Discontinuitatea de orientare rămîne, doar se împarte pe cîțiva pași; încheietura se răsucește tot la primul punct. |
| Filtrarea comenzilor după calcul | Evenimentele de prindere sînt legate de pași; un filtru ar muta contactul și ar ascunde o prindere ratată. |
| Viteză globală mai mică | Nu elimină saltul, doar îl încetinește; ciclul devine mai lung fără cîștig. |
| Trecere prin mai multe puncte intermediare, fără schimbarea orientării | Poziția se netezește, dar încheietura primește aceeași comandă bruscă la primul punct. |
