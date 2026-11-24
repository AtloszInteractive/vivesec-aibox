# 1. Bevezetés és alapfogalmak

[← Tartalom](index.md)

## Mire való a Matrix AI?

A Matrix AI a szervezet dokumentumaiban keres és azokból válaszol. Kérdezhet természetes
nyelven, például: „Kik Kovács Péter kapcsolatai, és milyen dokumentumok említik?”. A válasz
minden állítása mellett hivatkozás áll arra a dokumentumrészletre, amelyből származik, így a
válasz ellenőrizhető.

- **Helyben fut.** A dokumentumok, a kérdések és a válaszok nem hagyják el a gépet; a felület
  alján ezt a **Végpontok közötti titkosítás · helyben feldolgozva** felirat is jelzi.
- **Csak abból válaszol, amit Ön láthat.** Minden kérdésnél a rendszer a szerveren dönti el,
  mely dokumentumokhoz fér hozzá. Ami Önnek nem látható, abból a válasz sem idézhet.
- **A forrás a döntő.** A modell tévedhet. Fontos döntés előtt mindig nyissa meg a hivatkozott
  forrást (lásd [4. fejezet](04-valaszok.md)).

## Alapfogalmak

| Fogalom | Jelentése |
| --- | --- |
| **Szoba** | A dokumentumok egy elkülönített gyűjteménye (például *Nyilvántartás*, *Kommunikáció*). Csak azokban a szobákban keres a rendszer, amelyeket a rendszergazda Önhöz rendelt. |
| **Cél (ügyszám)** | A lekérdezések célja. Kérdezés előtt meg kell adni; minden kérdéssel együtt naplózódik. |
| **Beszélgetés** | Egymásra épülő kérdések és válaszok sora. A korábbi beszélgetések visszanyithatók. |
| **Forrás, hivatkozás** | A válaszban a `[#12]` alakú jel egy dokumentumrészletre mutat; a részlet szövege szó szerint az eredeti dokumentumból származik. |
| **Kapcsolatelemzés** | A dokumentumokból kinyert személyek, szervezetek, helyek és azonosítók kapcsolati ábrája (lásd [6. fejezet](06-kapcsolatok.md)). |

## Szerepkörök

Hogy mit lát és mit tehet a felületen, azt a szerepköre dönti el. A szerepköröket a
rendszergazda osztja ki (lásd [7. fejezet](07-adminisztracio.md)).

| Szerepkör | Mire jogosít |
| --- | --- |
| `analyst` (elemző) | Kérdezés a hozzárendelt szobákban, kapcsolatelemzés, saját lekérdezési napló. |
| `leader` (vezető) | Mint az elemző, és ezen felül láthatja mindenki lekérdezési naplóját; a beállítástól függően strukturált adatot is betölthet. |
| `admin` (rendszergazda) | Az **Adminisztráció** oldal: felhasználók, szobák és jogosultságok, frissítések, eseménynapló. Alapértelmezésben a dokumentumtárba is ő tölthet fel. |

## A két hozzáférési mód

- **Önálló mód (böngészőből).** A Matrix AI saját bejelentkezési oldalt mutat; a belépéshez
  felhasználónév, jelszó és a telefonos hitelesítő alkalmazás kódja kell
  ([2. fejezet](02-belepes.md)).
- **ViVeSecBox-ba ágyazva.** A Matrix AI a ViVeSecBox felületén belül nyílik meg; külön
  belépés nincs, a ViVeSecBox-felhasználóhoz az első megnyitáskor automatikusan létrejön egy
  Matrix-felhasználó elemző szerepkörrel és a telepítéskor megadott alapértelmezett szobákkal.
  További szobákat és jogokat a rendszergazda ad. Beágyazott módban a **Dokumentumok** ablak
  nem érhető el; a dokumentumokat a ViVeSecBox kezeli.
