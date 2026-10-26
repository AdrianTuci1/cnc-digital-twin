# ADR 0025: Brațul 1 își încarcă următorul bloc cît brațul 2 sortează

- **Status:** acceptat
- **Data:** 2026-10-26
- **Decizat de:** echipa de proiect
- **Pornește de la:** ADR 0012 (geamănul MuJoCo), ADR 0019 (prinderea fizică),
  ADR 0024 (mișcare continuă), [11-geaman-mujoco.md](../11-geaman-mujoco.md)

## Context

Ciclul era strict serial: brațul 1 încarcă blocul, așteaptă fereastra de așchiere, descarcă piesa
pe banda 2, iar apoi **aștepta** cît brațul 2 o ridica, o ducea la dulap și o așeza în
compartiment. Brațul 1 stătea aproximativ 25 s degeaba, deși următorul bloc era deja pe banda 1,
la linia de preluare, adus acolo de pasul de transport. Cerința este ca brațul 1 să își continue
treaba în paralel, iar celula să meargă în flux continuu.

Constrîngerea este motorul: există un singur `MjData`, iar `mujoco.mj_step` nu se poate apela din
două fire pe aceeași stare. `follow_line` este, de asemenea, o funcție blocantă: pașii ei sînt
chiar pașii simulării. O a doua sarcină care ar mișca brațul 1 ar intra în conflict cu pașii
brațului 2.

## Decizie

1. **Încărcarea următoare se înregistrează pe o copie a stării.** Cînd verdictul a fost decis și
   ruta este spre dulap, `_plan_preload` copiază `MjData` cu `mujoco.mj_copyData`, rulează pe copie
   aceiași pași ca la începutul ciclului (`Cycle.preload`, adică banda 1 → brațul 1 → universal) și
   salvează comenzile. Copia are aceeași fizică, aceleași constrîngeri și aceleași poziții, deci
   încărcarea se desfășoară exact ca în scena reală.
2. **Înregistrarea ține comenzile și evenimentele.** Pentru fiecare pas se salvează cîte un vector
   de comenzi pentru cele opt actuatoare ale brațului 1, iar legarea și desfacerea (`attach`,
   `release`) plus clema blocului (`clamp`, `unclamp`) se rețin cu indexul pasului la care s-au
   întîmplat. `model.eq_data` este comun copiei și scenei, deci se pune la loc după probă.
3. **Redarea se face în pasul brațului 2.** `_PreloadPlayback` scrie comenzile brațului 1 înainte
   de fiecare `mj_step` pe care îl fac mișcările brațului 2 și aplică evenimentele la indexul
   înregistrat. Un singur fir pașește motorul, deci nu există curse.
4. **Pornirea are loc imediat după verdict**, înainte ca brațul 2 să apuce piesa: de acolo îi
   rămîn aproximativ 25 s de mers pînă la raft, destul pentru o încărcare întreagă. Dacă brațul 2
   ajunge primul, `_finish_preload` duce înregistrarea pînă la capăt.
5. **Ciclul următor începe la așchiere.** `preloaded=True` sare peste banda 1 și peste încărcare,
   face doar așezarea scurtă și intră direct în `CUTTING`. `_swap_material` pune piesa mașinată în
   universal, ca de obicei.
6. **La rebut nu se pregătește nimic.** Piesa rebut pleacă în ladă, iar ciclul următor trebuie să
   readucă `part_01` în poziția nominală; un bloc lăsat în universal ar fi mutat la resetare. Fără
   `next_blank` sau fără verdict de raft, ciclul rămîne serial și viewer-ul resetează celula.

## Consecințe

- Cu `settle=300` și 12 s de așchiere, primul ciclu durează **115,9 s**, iar ciclurile
  înlănțuite **93,5 s** și **91,5 s**: paralelizarea economisește 22–24 s pe ciclu. Toate cele trei
  cicluri se încheie `OK`.
- La sfîrșitul fiecărui ciclu, blocul următor stă în universal, iar piesa de dinainte în
  compartimentul familiei (măsurat la (2,16–2,19, 1,02–1,03, 0,857) m la raftul 1 sudic).
- Jurnalul arată explicit `Arm 1 starts loading the next blank while arm 2 sorts`, iar
  `CycleResult` primește cîmpul `preloaded`. Viewer-ul sare peste `reset_cell` cînd ciclul următor
  este pregătit.
- Costul este o copie de `MjData` și o încărcare planificată pe ciclu, în afara ceasului de
  simulare. Copia nu se păstrează după înregistrare.
- Dacă planificarea eșuează, ciclul se oprește cu mesajul „brațul 1 nu a putut pregăti următorul
  bloc cît brațul 2 sorta”, nu continuă cu o încărcare pe jumătate.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Două fire, fiecare cu brațul lui | Un singur `MjData`; `mj_step` din două fire ar citi și scrie aceleași buffere, iar rezultatul ar depinde de ordinea de schedulare. |
| Coroutine pentru fiecare acțiune de braț | Rescrie toate fazele ciclului și toate apelurile blocante; mult mai mult cod decît copia și redarea, cu aceeași limită de un singur fir. |
| IK online pentru brațul 1 în timpul mișcării brațului 2 | Ar cere intercalarea a două `follow_line`-uri în același pas; copia dă deja traiectoria exactă, iar redarea este deterministă. |
| Pornirea pregătirii după ce brațul 2 ajunge la raft | Se suprapun doar cîteva secunde, iar cîștigul scade la aproape nimic. |
| Pregătire și la rebut | `part_01` iese din celulă, iar resetarea ar muta blocul deja încărcat; ciclul ar începe cu o stare inconsistentă. |
| Păstrarea ciclului serial | Brațul 1 stă 25 s pe ciclu, iar fluxul nu mai este continuu. |
