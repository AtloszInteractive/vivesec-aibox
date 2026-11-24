# 3. Kérdezés és beszélgetések

[← Tartalom](index.md)

## A főképernyő

![A főképernyő belépés után](img/fo-01-kezdokepernyo.png)

| Hely | Elem | Mire való |
| --- | --- | --- |
| Fejléc | **Kapcsolatok** | A kapcsolatelemzés megnyitása ([6. fejezet](06-kapcsolatok.md)). |
| Fejléc | **Dokumentumok** | A szobák dokumentumainak böngészése ([5. fejezet](05-dokumentumok.md)). Csak önálló módban. |
| Fejléc | **AI Box csatlakoztatva** | A kapcsolat állapota. Ha a felirat mást mutat, a válaszadás nem elérhető. |
| Fejléc | az Ön neve | Fiókmenü: jelszócsere, kijelentkezés ([2. fejezet](02-belepes.md)). |
| Fejléc | **⋮** | Nyelv és téma. |
| Eszközsor | **Cél megadása** | A lekérdezések céljának (ügyszámának) megadása. |
| Eszközsor | **Aktuális beszélgetés** | A korábbi beszélgetések listája. |
| Eszközsor | **Új beszélgetés** | Üres beszélgetés indítása. |
| Középen | **Kezdj egy tipikus kérdéssel** | Kész kérdéssablonok. |
| Alul | **Írd ide az üzenetet…** | A kérdés beírása. |

## 1. lépés: a cél megadása

Kérdezés előtt meg kell adni, milyen ügyben kérdez. Amíg ez hiányzik, a beviteli mező felett a
**Kérdés előtt adja meg az ügyszámot.** figyelmeztetés látszik, és a kérdés nem küldhető el.

1. Kattintson a **Cél megadása** gombra.
2. Az **Ügyszám** mezőbe írja be az ügyszámot a kért formában (a mezőben szürkén látszik egy
   példa, például `2026/1113/2310`). Hibás formánál a mező alatt **Az ügyszám formája nem
   megfelelő.** üzenet jelenik meg.
3. Kattintson a **Mentés** gombra.

![A lekérdezések céljának megadása](img/fo-02-cel-megadasa.png)

A megadott ügyszám ezután az eszközsorban látszik; rákattintva módosítható. Az ügyszám minden
kérdéssel együtt naplózódik, és a böngészőlap bezárásáig érvényes. A telepítéstől függően
ügyszám helyett vagy mellett jogalap is kérhető.

## 2. lépés: a kérdés

Írja be a kérdést a képernyő alján lévő mezőbe, és nyomja meg az **Enter** billentyűt, vagy
kattintson a küldés (papírrepülő) ikonra.

Jó kérdés:

- **konkrét**: nevet, azonosítót, időszakot tartalmaz, például „Kik Kovács Péter
  (NY-284486) kapcsolatai, és milyen dokumentumok említik?”;
- **egyértelmű**: azonos nevű személyeknél adja meg az azonosítót (például a nyilvántartási
  számot) is;
- **egy dologra kérdez**: összetett kérdés helyett kérdezzen többször, a beszélgetés
  megőrzi az előzményt.

A **Kezdj egy tipikus kérdéssel** sablonjai (például **Egy személy kapcsolatai**, **Egy
személy idővonala**, **Közös telefonszám**) beírják a kérdés elejét a mezőbe; a nevet vagy az
időszakot Önnek kell kiegészítenie, mielőtt elküldi.

A válasz általában néhány másodperc és egy-két perc között érkezik, attól függően, hány
dokumentumot kell átnéznie a rendszernek. Közben a felület mutatja, hogy a válasz készül.

## Beszélgetések

- A rendszer minden beszélgetést megjegyez, és az első kérdés alapján címet ad neki.
- Az eszközsor beszélgetésgombja (belépéskor **Aktuális beszélgetés**, később a beszélgetés
  címe) lenyitja a korábbi beszélgetéseket; mindegyik mellett látszik, hány körből áll.
  Kattintással visszanyitható.
- Az **Új beszélgetés** üres lappal indít. Új témánál mindig kezdjen új beszélgetést: így a
  korábbi kérdések nem befolyásolják a választ.
- Üres beszélgetésben a **Folytatás:** sor a legutóbbi beszélgetést kínálja fel.

![A korábbi beszélgetések listája](img/fo-05-beszelgetesek.png)

A beszélgetések felhasználónként, és a keresési körrel (a kiválasztott szobákkal) együtt
tárolódnak: más felhasználó nem látja őket, és egy szűkebb körű beszélgetésbe nem kerülhet át
tágabb körből származó előzmény. Ha egynél több szobához fér hozzá, és megváltoztatja, hogy
melyikben keres, a rendszer ezért új beszélgetést nyit.

## Felolvasás és diktálás

A beviteli mező hangszóró ikonja (**Utolsó válasz felolvasása**) felolvassa a legutóbbi
választ; felolvasás közben ugyanez a gomb állítja le. Ha a telepítés támogatja, a mezőben
mikrofon ikon is megjelenik: ezzel a kérdés diktálható, és a diktált kérdés ugyanúgy
dolgozódik fel, mint a begépelt.

## Műveleti asszisztens

Az eszközsor jobb oldalán a **Műveleti asszisztens** az aktív AI-ügynököt mutatja
(**Napi műveletek és forgatókönyvek**). Elemző üzemmódban – ilyen a kapcsolatelemzésre
beállított telepítés – a kérdéssablonok jelennek meg; általános üzemmódban a beviteli mezőbe
írt `/` jel egy parancslistát nyit (például összefoglaló, jelentés, feljegyzés, prezentáció
készítése).
