# ADR 0020: A șasea familie este o placă dreptunghiulară

Data: 2026-10-18
Stare: acceptată

## Context

Toate cele cinci familii din generator erau roți dințate plane. Diferențele dintre ele se
citeau în plan — diametru, număr de dinți, alezaj, cerc de găuri —, dar la o rulare a celulei
silueta rămînea circulară. Al șaselea compartiment al dulapului era gol, iar ADR 0017 lăsase
deschisă completarea lui.

Cerința este ca o rulare să arate și altceva decît o roată, fără să schimbe procesul, scula sau
contractul de date.

## Decizie

A șasea familie este `plate_84`, o placă de montaj de 84 × 56 × 12 mm, cu colțuri rotunjite
R8, alezaj central Ø18 și patru găuri Ø6 pe un cerc R20.

- **Conturul** se construiește din patru arce de colț și laturi drepte, în același mod în care
  roata se construiește din arce de vîrf și de rădăcină. Triangularea, pereții și închiderea
  meshului rămîn aceleași.
- **Gabaritul** are diametrul cercului încadrator de 101 mm, dar se prinde pe latura de 56 mm,
  deci încape între fălcile de 80 mm ale lui Panda. Blocul brut rămîne un cilindru de Ø105,
  ca să acopere placa.
- **Defectele** sînt cele de alezaj și de găuri: `BORE_*`, `HOLE_*`, `BOLT_PATTERN_ROTATED`.
  `TOOTH_*` nu are sens pe o placă și este scos din `PartFamily.defect_classes()`;
  `KEYWAY_MISSING` lipsește, fiindcă placa nu are pană.
- **Locul** este al șaselea compartiment, raftul 3 nord, care se umple acum ca celelalte.

## Alternative respinse

- **Un suport în L ori o consolă**: ar cere fie operații booleene, fie un al doilea nivel de
  mesh și prindere. Silueta s-ar distinge mai tare, dar costul de geometrie și de verificare
  nu se justifică pentru o singură familie.
- **Un al doilea tip de roată**: ar fi păstrat cerința de simplitate, dar nu ar fi rezolvat
  problema vizuală.
- **Un al doilea dulap ori un raft suplimentar**: ar schimba planul celulei și atingerea
  brațului 2; compartimentul gol exista deja.

## Consecințe

- Generatorul are șase familii, iar dulapul are toate compartimentele ocupate. Cifrele de
  atingere rămîn cele din ADR 0018; placa stă pe raftul 3 nord, la 0,679 m de baza brațului 2.
- `make mj-demo` și `make mj-record` rulează șase familii, cu 19–20 de opriri pe ciclu și 6/6
  prinderi. Sesiunile sînt cu ~1/6 mai multe, iar timpul pe familie rămîne ~70 s simulați.
- Corpusul de clasificare capătă o clasă de formă suplimentară; eticheta `family` o separă
  de roți, iar `defect` rămîne în catalogul existent.
- Testele verifică faptul că toate familiile au siluete distincte, că placa trece validarea
  și că fiecare defect al ei schimbă geometria.

## Verificare

- `tests/unit/test_part_generator.py`: mesh închis, orientat, volum plauzibil, toate clasele
  plăcii schimbă geometria.
- `make mj-demo` și `make mj-record`: ciclul complet pentru `plate_84`, cu piesa în raftul 3
  nord și 6/6 prinderi.
- `simulation/mujoco/renders/parts/plate_84.png`: cadrul de catalog al plăcii.

## Notă (ADR 0021)

Placa rămîne a șasea familie. ADR 0021 schimbă restul compoziției, astfel încît doar două
familii mai sînt roți dințate, iar afirmația de aici că placa este singura piesă fără siluetă
circulară nu mai descrie starea actuală.
