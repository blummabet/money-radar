#!/usr/bin/env python3
"""
stake_liga_stufe.py — welche Spielklasse eine Fussball-Liga ist
================================================================================
07.09.2026 (Lucas: „ne 50k Wette auf Arsenal sagt 0 / Eine 50k Wette auf ein 2-3. Liga Team /
Ist zumindest jemand der mehr dran glaubt mmn").

## Warum diese Tabelle von Hand kommt und nicht gelernt wird
Die Spielklasse steht in KEINEM Feld des Stake-Feeds. Aus den Daten laesst sie sich auch nicht
ableiten: der naheliegende Ersatz — „wie viele Wetten sieht diese Liga bei uns" — misst die
Marktgroesse, nicht die Klasse. Gemessen am 07.09. landen in der so gebildeten Gruppe „kleine
Liga" die Sueper Lig, die argentinische Primera, MLS, die Serie B Brasiliens und die englische
Championship. Das sind keine Randligen; das sind mittelgrosse Maerkte. Genau die Unterscheidung,
um die Lucas gebeten hat, faellt damit heraus.

Also: eine Tabelle Slug → Ebene. Sie ist WISSEN, keine Messung, und sagt das auch von sich
(`quelle: "Tabelle"`). Was nicht drinsteht, kommt als `None` zurueck — nicht als 1, nicht als
„sonstige". Eine unbekannte Liga darf nicht wie eine gemessene Spitzenliga aussehen; das ist
die Bug-Klasse „fehlende Information rendert als harmloser Default", die in diesem Repo schon
mehrfach zugeschlagen hat.

## Warum der Slug der Schluessel ist und nicht der Name
`ligaSlug` ist stabil, der Anzeigename nicht: derselbe Slug `superliga` erscheint im Ledger
als „Superliga" UND als „Primera LFP". Umgekehrt ist der Slug NICHT sportartenrein — unter
`bundesliga` laufen Fussball und Handball, unter `premier-league-srl` Fussball und Cricket.
Deshalb nimmt `stufe()` die Sportart entgegen und liefert ausserhalb von `soccer` nichts.

## Was gemessen wurde, bevor das hier gebaut wurde
3.512 abgerechnete Fussball-Einzelwetten aus vier Tagen, Einsatz gemessen als Vielfaches des
ueblichen Einsatzes derselben Liga (bzw. derselben Ebene, wo die Liga zu duenn ist):

    Ebene 1   <1.5x  n=2041  ROI  -2,1 %      Ebene 2+3   <1.5x  n=192  ROI  +7,5 %
              1.5-3x n= 596  ROI  -1,9 %                  1.5-3x n= 49  ROI  +5,9 %
              3-6x   n= 271  ROI  -8,0 %                  3-6x   n= 37  ROI +19,2 %
              >6x    n= 210  ROI -13,9 %                  >6x    n= 12  ROI +46,7 %

Beide Reihen sind monoton — und sie laufen in ENTGEGENGESETZTE Richtungen. In der obersten
Spielklasse wird ein grosser Einsatz mit steigender Groesse schlechter, darunter besser. Das
ist Lucas' Vermutung, und es ist zugleich die Begruendung dafuer, dass die bestehende
„Auffaellig"-Ansicht als Einheitsmass nicht funktionieren konnte: sie mischt beide Richtungen.

**Belegt ist davon nichts.** Alle Untergrenzen ausser einer liegen unter null, die
interessanten Zellen haben n=37 und n=12, die Schwellen sind NACH dem Blick auf die Zahlen
gewaehlt, und vier Tage sind vier Tage. Deshalb entstehen aus beiden Richtungen
vorregistrierte Schubladen (`randliga_hoher_einsatz`, `topliga_hoher_einsatz`), und das
Urteil faellt vorwaerts. Bis dahin ist das hier eine Anzeige, keine Empfehlung.
"""
from __future__ import annotations

import re
import statistics
from collections import defaultdict

# ── Die Tabelle ──────────────────────────────────────────────────────────────
# 1 = oberste Spielklasse des Landes, 2 = zweite, 3 = dritte und tiefer / regional / Amateur.
# Die Ebene beschreibt die KLASSE, nicht die Marktgroesse: die englische Championship ist
# Ebene 2, obwohl sie mehr Umsatz sieht als die Eliteserien (Ebene 1). Genau das ist der
# Unterschied, den die Volumen-Sicht nicht abbilden kann.
EBENE = {
    # ── oberste Spielklassen ───────────────────────────────────────────────
    "premier-league": 1, "la-liga": 1, "serie-a": 1, "bundesliga": 1, "ligue-1": 1,
    "primeira-liga": 1, "eredivisie": 1, "super-lig": 1, "brasileiro-serie-a": 1,
    "superliga": 1, "major-league-soccer": 1, "saudi-prof-league": 1, "j-league": 1,
    "primera-division": 1, "primera-division-apertura": 1, "primera-a-apertura": 1,
    "superligaen": 1, "eliteserien": 1, "allsvenskan": 1, "ekstraklasa": 1,
    "first-division-a": 1, "chinese-super-league": 1, "indonesian-super-league": 1,
    "k-league-1": 1, "ligapro-primera-a": 1, "super-league": 1, "super-league-1": 1,
    "premiership": 1, "prvaliga": 1, "nb-i": 1, "arabian-gulf-league": 1, "stars-league": 1,
    "1-liga": 1, "liga-premier-serie-a": 1, "pfl": 1, "thai-league-1": 1,
    "i-liga": 1, "1-hnl": 1, "cymru-premier": 1, "virsliga": 1, "urvalsdeild": 1,
    "premier-soccer-league": 1, "omani-league": 1, "jordan-league": 1, "top-league": 1,
    "premier-division": 1, "prva-liga": 1, "divizia-nationala": 1, "premium-liiga": 1,
    "national-womens-soccer-league": 1, "first-professional-league": 1,
    "liga-nacional-apertura": 1, "cambodian-premier-league": 1,
    "liga-i": 1, "iraqi-league": 1, "vysshaya-liga": 1,
    # 22.09.2026 — stand hier als Ebene 1. Die USL Championship ist von der USSF als
    # DIVISION II sanktioniert und liegt unter der MLS; seit 2017, davor sogar Division III.
    # Das ist kein Mehrdeutigkeits-Fall (der Slug meint genau diese Liga), sondern eine
    # falsche Zeile: die Tabelle behauptete oberste Klasse fuer die zweite. Genau die
    # Richtung, die Lucas interessiert — ein grosser Einsatz dort war als Topliga gestempelt.
    "usl-championship": 2,
    "division-profesional": 1, "liga-portugal": 1,
    # 14.09.2026 (CI-Wachhund): die Canadian Premier League ist die OBERSTE Klasse Kanadas
    # (Pacific FC gegen Cavalry FC, beides Gruendungsklubs). Nicht mit der USL zu verwechseln,
    # in der kanadische Klubs frueher spielten.
    "canadian-premier-league": 1,
    # 14.09.2026 (CI-Wachhund): die A Lyga ist die OBERSTE Klasse Litauens. Beleg aus der
    # Paarung: FK Banga Gargzdai gegen FK Transinvest, beides A-Lyga-Klubs. Der Name sagt es
    # nicht — „A" liest sich wie eine Staffel, ist aber der Ligenname selbst.
    "a-lyga": 1,
    # 13.09.2026 (CI-Wachhund): „1. CFL" — die Prva crnogorska fudbalska liga, oberste Klasse
    # Montenegros. Beleg aus der Paarung: FK Sutjeska Niksic gegen FK Decic Tuzi, beides
    # montenegrinische Erstligisten (Sutjeska ist Rekordmeister). Der Slug sagt es nicht.
    "1-cfl": 1,
    # 13.10.2026 (CI-Wachhund, beim Settle-Fix mitgefunden): drei oberste Klassen kleiner Laender.
    # „1. MFL" = Prva Makedonska Fudbalska Liga (Nordmazedonien) — Beleg: FC Struga Trim Lum
    # gegen KF Bashkimi Kumanovo. „Campionato Sammarinese" = die einzige Liga San Marinos (Tre
    # Fiori, Virtus). „Indian Super League" = oberste Klasse Indiens (Mohun Bagan, Bengaluru FC).
    "1-mfl": 1, "campionato-sammarinese": 1, "indian-super-league": 1,
    # 27.09.2026 (CI-Wachhund): „cymru-championship-south" — Wales' ZWEITE Klasse unter der
    # Cymru Premier (regional geteilt, Nord und Sued). Beleg: „Caerphilly Athletic FC -
    # Pontypridd Town". Die Nord-Staffel gleich mit, sonst faellt sie beim ersten Spiel auf.
    "cymru-championship-south": 2, "cymru-championship-north": 2,
    # 27.09.2026 (CI-Wachhund): „cfl" im FUSSBALL ist die tschechische ČFL (Česká fotbalová
    # liga), DRITTE Klasse unter Chance Liga und FNL. Beleg: „Povltavska FA - FK Motorlet
    # Prague". Derselbe Slug steht im Feed auch fuer die Canadian Football League — die
    # bekommt nichts, weil `stufe()` nur Fussball beantwortet. Nicht zu verwechseln mit
    # „1-cfl" (Montenegro, oberste Klasse) direkt darueber.
    "cfl": 3,
    # 29.09.2026 (Stoerungsmeldung): „erste-liga" — Beleg „FC Widnau - FC Freienbach", die
    # Schweizer 1. Liga (vierte Ebene unter Super League, Challenge League, Promotion League;
    # „3" heisst hier „dritte Klasse und tiefer"). „nb-iii" — Ungarns NB III, dritte Ebene;
    # Beleg „Puskas Akademia FC II - Balatonalmadi SE".
    "erste-liga": 3, "nb-iii": 3,
    # 16.09.2026 (CI-Wachhund): „Premijer Liga" — die Premijer Liga BiH, oberste Klasse
    # Bosnien-Herzegowinas. Beleg aus der Paarung: FK Sloga Doboj gegen FK Borac Banja Luka,
    # beides Erstligisten (Borac ist amtierender Meister). Dieselbe Einstufung wie 1. CFL
    # (Montenegro) und A-Lyga (Litauen): oberste Klasse eines kleinen Verbands bleibt Ebene 1.
    "premijer-liga": 1,
    # 13.09.2026 (CI-Wachhund): die Liga Panameña de Fútbol ist die OBERSTE Klasse Panamas;
    # „Apertura" ist die Halbsaison, kein Rang (wie bei primera-division-apertura oben).
    # Beleg aus der Paarung: Tauro FC — einer der Rekordmeister — gegen CD Universitario.
    "liga-panamena-de-futbol-apertura": 1,
    # 12.09.2026 (CI-Wachhund): „philippines-footb-league" — die Philippines Football League ist
    # die OBERSTE Klasse des Landes. Der abgeschnittene Slug („footb.") sieht nach Amateurstaffel
    # aus; das ist die Abkuerzung des Feeds, nicht der Rang.
    "philippines-footb-league": 1,
    # 08.09.2026 (CI-Wachhund): die finnische Spitze fehlte, waehrend „ykkonen" (2) und
    # „kolmonen" (3) darunter seit dem ersten Tag in der Tabelle stehen. Genau die Luecke,
    # die eine Tabelle ohne Wachhund jahrelang behaelt.
    "veikkausliiga": 1,
    # 09.09.2026 (CI-Wachhund, dritter Treffer des Tages): „mizoram-premier-league". Eine
    # indische STAATSliga — trotz „Premier" im Namen nicht die oberste Klasse des Landes
    # (das ist die ISL/I-League), sondern die Ebene darunter. Genau deshalb steht sie in der
    # TABELLE und nicht in einer Regel: „premier" im Namen sagt hier das Gegenteil dessen,
    # was ein Muster daraus lesen wuerde.
    "mizoram-premier-league": 3,
    # 21.09.2026 (CI-Wachhund, roter Test in der Action): „india-karnataka-super-division".
    # Dieselbe Familie wie Mizoram: eine indische STAATSliga. „Super Division" klingt nach
    # oberster Klasse und ist die Landesmeisterschaft Karnatakas — unter ISL, I-League und
    # I-League 2. Auch sie steht in der TABELLE und nicht in einer Regel: „super" im Namen
    # sagt hier das Gegenteil dessen, was ein Muster daraus lesen wuerde.
    "india-karnataka-super-division": 3,
    # 23.09.2026 (CI-Wachhund, roter Test): „mumbai-super-league". Dritte aus derselben
    # Familie und die kleinste davon: Mizoram und Karnataka sind STAATSmeisterschaften,
    # Mumbai ist eine STADTliga unter der Mumbai Football Association — unter ISL, I-League,
    # I-League 2 und der Maharashtra-Landesliga. Die Ledger-Paarung sagt es deutlicher als
    # der Name: „Iron Born Cfci - Kopana FC". Auch hier verspricht „Super League" im Slug das
    # Gegenteil dessen, was ein Muster daraus lesen wuerde — deshalb TABELLE, keine Regel.
    # Die Skala endet bei 3; eine vierte Ebene fuer „noch kleiner" gibt es (noch) nicht, und
    # eine einzufuehren, waere eine Entscheidung ueber alle 50 Ebene-3-Ligen, nicht ueber diese.
    "mumbai-super-league": 3,
    # 23.09.2026 (CI-Wachhund): „professional-league" — die Nigeria Professional Football
    # League, oberste Klasse Nigerias (Ledger-Paarung „Shooting Stars - Katsina United FC").
    # Wie die South African Premier Division („premier-division": 1) eine nationale Eliteserie,
    # also Ebene 1. Der Slug ist generisch; taucht ein zweiter Wettbewerb darunter auf, meldet
    # ihn `mehrdeutige_kandidaten` — seit heute genau deshalb, weil hier eine ZAHL steht.
    "professional-league": 1,
    # 25.09.2026 (CI-Wachhund): „botola" — Marokkos oberste Klasse. Der Slug laesst das „Pro"
    # weg, deshalb greift keine Regel und der Eintrag muss in die TABELLE. Belegt an der
    # Paarung: „Union Sportif Amal Tiznit - Ittihad Tanger", 24.09.2026 — nach marokkanischen
    # Quellen das ERSTE Spiel der Botola-Pro-Saison 2026/27, nicht ein Zweitliga-Spiel.
    "botola": 1,
    # 10.09.2026 (CI-Wachhund, vierter und fuenfter Slug): „v-league" — die oberste Klasse
    # Vietnams (Ledger-Paarung „The Cong - Viettel FC - Cong An Ha Noi FC"). Der Slug ist
    # NICHT sportartenrein: in Korea und Japan heisst die Volleyball-Liga genauso. Dass hier
    # trotzdem eine „1" stehen darf, haelt allein `stufe()` fest, das ausserhalb von
    # `sport == "soccer"` grundsaetzlich None gibt — dieselbe Vorsichtsmassnahme, die schon
    # „bundesliga" (Fussball und Handball) braucht.
    "v-league": 1,
    # 10.09.2026 (CI-Wachhund): „erovnuli-liga" — Georgiens oberste Klasse. Reiner
    # Tabelleneintrag, der Slug traegt nichts, woraus eine Regel etwas ableiten koennte.
    "erovnuli-liga": 1,
    # ── zweite Spielklassen ────────────────────────────────────────────────
    # 🔴 21.09.2026 (CI-Wachhund, dritter Slug des Tages): „persha-liga". Ukrainisch „persha"
    # heisst ERSTE — und die Persha Liha ist die ZWEITE Spielklasse des Landes, unter der
    # Premjer-Liha. Das Wort im Namen sagt hier das Gegenteil der Klasse, wie schon bei
    # „mizoram-premier-league" und „india-karnataka-super-division". Genau dafuer ist das eine
    # TABELLE und keine Regel: ein Muster, das „persha/first/1" liest, haette sie auf die
    # Ebene der Premjer-Liha gehoben — und die steht in derselben Uebersicht mit +32,3 % ROI.
    "persha-liga": 2,
    "championship": 2, "2nd-bundesliga": 2, "la-liga-2": 2, "serie-b": 2, "ligue-2": 2,
    "j-league-2": 2, "brasileiro-serie-b": 2, "primera-b": 2, "k-league-2": 2,
    "eerste-divisie": 2, "segunda-liga": 2, "liga-de-expansion-mx-apertura": 2,
    "primera-nacional": 2, "ykkonen": 2, "challenge-league": 2, "ligapro-primera-b": 2,
    # 22.09.2026 (CI-Wachhund): Irans zweite Ebene unter der Persian Gulf Pro League. Beleg:
    # „Havadar SC - Besat Kermanshah". Die Ordnungszahl-Regel greift nicht — der Name traegt sie
    # nicht, und geraten wird hier nichts.
    "azadegan-league": 2,
    "thai-league-2": 2, "liga-2": 2, "fnl": 2, "segunda-division": 2, "pervaya-liga": 2,
    "mls-next-pro": 2, "1-lig": 2, "first-division-b": 2, "2nd-division": 2,
    # 10.09.2026 (CI-Wachhund): „superettan" — Schwedens ZWEITE Klasse, obwohl „super" im
    # Namen steht. Genau deshalb Tabelle und keine Regel: „super-lig", „super-league" und
    # „chinese-super-league" sind Ebene 1, „superettan" ist es nicht. Ein Muster auf „super"
    # wuerde vier richtige Eintraege kaputtmachen, um einen zu sparen.
    "superettan": 2,
    # 10.09.2026 (CI-Wachhund): „esiliiga" — Estlands ZWEITE Klasse. Die oberste steht seit
    # jeher als „premium-liiga" (Sponsorname der Meistriliiga) in der Tabelle; ohne diese Zeile
    # waere ausgerechnet die Liga darunter die einzige ohne Ebene.
    "esiliiga": 2,
    # 11.09.2026 (CI-Wachhund): „liga-nacional-de-ascenso" — Panamas ZWEITE Klasse (LNA, die
    # Aufstiegsliga unter der LPF). Tabelleneintrag und keine Regel: „ascenso" heisst in Mexiko
    # die zweite Liga, in anderen Verbaenden steht es im Namen der OBERSTEN. Ein Muster darauf
    # waere in der Haelfte der Faelle falsch.
    "liga-nacional-de-ascenso": 2,
    # 29.09.2026: Costa Ricas Liga de Ascenso, zweite Ebene unter der Primera. Beleg „AD Cariari
    # Pococi - CS Uruguay de Coronado". Die Clausura gleich mit, sonst faellt sie im Januar auf.
    "liga-de-ascenso-apertura": 2, "liga-de-ascenso-clausura": 2,
    # ── dritte Klasse und tiefer, regional, Amateur ────────────────────────
    "league-one": 3, "league-two": 3, "3rd-liga": 3, "tercera-division": 3,
    # 12.09.2026: hier standen frueher `serie-c-group-a/-b/-c` und `tercera-division-group-7`
    # als eigene Zeilen. Die Gruppen kommen jetzt aus der Regel in `_ebene_aus_slug` — in der
    # Tabelle steht nur noch der RUMPF, und zwar genau einmal. Eine Staffel je Zeile war der
    # Grund, warum Gruppe 4 der Tercera (von achtzehn) wieder als Luecke auffiel.
    "serie-c": 3,
    "primera-c": 3, "primera-division-rfef": 3,
    # 22.09.2026 (CI-Wachhund, „liga-primera-premier"): der Name klingt nach Spitze, die Teams
    # sagen etwas anderes — „Reboceros de La Piedad - Irapuato" ist Mexikos Liga Premier, also
    # die dritte Ebene unter Liga MX und Liga de Expansion MX. Genau deshalb wird hier nicht
    # nach Wortbestandteilen geraten: „premier" und „primera" stehen in beiden Richtungen.
    "liga-primera-premier": 3,
    # 22.09.2026 (CI-Wachhund, „shillong-premier-league"): „Premier" heisst hier so wenig wie bei
    # der Liga Primera. Beleg: „Langsning FC - Nangkiew Irat SC" — die Shillong Premier League
    # ist der Staats-Wettbewerb von Meghalaya, also unterhalb der I-League und der ISL.
    "shillong-premier-league": 3,
    "liga-portugal-3": 3, "tweede-divisie": 3, "national": 3, "national-league": 3,
    "national-league-south": 3,  # 07.10.2026 (Wachhund): England 6. Klasse (Braintree - Walton & Hersham)
    "scotland-league-one": 3,   # 06.10.2026 (Wachhund): Scottish League One = 3. Klasse nach Premiership/Championship
    # 30.09.2026 (Stoerungsmeldung): die Southern League Premier Division South ist Englands
    # siebte Ebene (Step 3 im Non-League-System, unter der National League South).
    "southern-football-league-premier-division-south": 3,
    # 01.10.2026 (Stoerungsmeldung): Isthmian League Premier Division — ebenfalls Englands siebte
    # Ebene (Step 3), Schwesterliga der Southern League.
    "isthmian-league-pr-div": 3,
    # 03.10.2026 (Stoerungsmeldung): „National 2" — Frankreichs vierte Klasse (unter Ligue 1,
    # Ligue 2 und National). Eine andere Liga dieses Namens waere ebenfalls Amateurebene.
    "national-2": 3,
    # 13.09.2026 (CI-Wachhund): das Campeonato de Portugal ist die DRITTE portugiesische Klasse
    # (unter Liga Portugal und Liga Portugal 2) — der Name klingt nach Landesmeisterschaft, ist
    # aber die Amateur-/Regionalebene. Beleg: AD Fazendense gegen O Elvas, beides Drittligisten.
    "campeonato-de-portugal": 3,
    "liga-bet-south-a": 3, "shillong-second-divison": 3, "torneo-federal-a": 3,
    "kolmonen": 3, "primera-divisio": 3, "south-australia-state-league-1": 3,
    "nsw-premier-league-2": 3, "japan-football-league": 3, "second-division-b": 3,
    "usl-league-one": 3, "k3-league": 3,
    # 11.09.2026 (CI-Wachhund): „promotion-league" — die DRITTE Schweizer Klasse. Super League
    # (1) und Challenge League (2) stehen schon oben; ohne diese Zeile fehlte ausgerechnet die
    # Liga darunter. „promotion" im Namen meint den Aufstieg, nicht den Rang.
    "promotion-league": 3,
    "northern-territory-premier-league": 3, "npl-western-australia": 3,
    "npl-new-south-wales": 3, "npl-victoria": 3, "npl-queensland": 3,
    "npl-south-australia": 3, "npl-northern-new-south": 3, "npl-capital-football": 3,
    # 11.09.2026 (CI-Wachhund): „NPL 2, Victoria" — die Stufe UNTER `npl-victoria`, das hier
    # schon auf 3 steht. Die 3 ist in diesem Schema der Boden, tiefer geht die Skala nicht;
    # eine 4 zu erfinden waere eine Genauigkeit, die das Modell nicht traegt.
    "npl-2-victoria": 3,
    # 11.09.2026 (CI-Wachhund): „MFL, Division B". Der Name sagt nichts, die PAARUNG schon —
    # gebucht wurde darauf „Samara Kryliya Sovetov - PFK Sochi", zwei russische Erstligisten.
    # Ein Erstliga-Kader in einer „Division B" ist eine Reserve-/Nachwuchsrunde, kein
    # Erstliga-Spiel. Ebene 3, und ausdruecklich NICHT ueber die Reserve-Regel: die liest
    # Namensmuster (`-ii`, „reserve"), und „division-b" ist keines davon. Wer sie dafuer
    # aufbohrt, faengt beim naechsten Lauf jede zweite echte zweite Liga mit.
    "mfl-division-b": 3,
    # 11.09.2026 (CI-Wachhund): „Jordan 1st Division" — die ZWEITE Liga Jordaniens („1st
    # Division" steht dort unter der Premier League). Ein Ligenname mit „1" ist kein Beleg fuer
    # Ebene 1; entschieden hat die Paarung (Sama Al Sarhan - Jerash, beides Zweitligisten).
    "jordan-1st-division": 3,
    # „Division 1" ohne Land — Al-Dhaid gegen Dibba Al Fujairah, also die zweite Liga der VAE.
    # Der Slug ist so generisch, dass er in einer anderen Saison etwas anderes bezeichnen kann;
    # er steht hier trotzdem, weil der Wachhund sonst bei jedem Lauf erneut anschlaegt und die
    # Alternative (ein Rueckfall auf „unbekannt = Ebene 3") die Sperre unterlaufen wuerde.
    "division-1": 3,
    # 13.09.2026 (CI-Wachhund): „Division 2" — Wong Tai Sin gegen Sun Hei SC, also Hongkong.
    # Dort steht die Second Division unter Premier League (1) und First Division (2), ist also
    # die dritte Klasse. Wieder von Hand und nicht als Regel „division-N → Ebene N": in Jordanien
    # ist die „1st Division" die zweite Liga (steht drei Zeilen drueber), die Zahl im Namen sagt
    # ueber den Rang nichts. Entschieden hat auch hier die Paarung.
    "division-2": 3,
    # 12.09.2026 (CI-Wachhund): „Division Nationale" — die ERSTE Liga Luxemburgs (Kaerjeng gegen
    # Victoria Rosport). Oberste Spielklasse eines sehr kleinen Verbands: Ebene 2, nicht 1 — die
    # 1 ist in dieser Tabelle den europaeischen Topligen vorbehalten, nach deren Massstab hier
    # die Einsatzstufen haengen.
    "division-nationale": 2,
    # 12.09.2026 (CI-Wachhund): „First Division" ohne Land — Wexford gegen Finn Harps, also die
    # ZWEITE Liga Irlands (unter der Premier Division). „First" im Namen ist auch hier kein Beleg
    # fuer Ebene 1; entschieden hat die Paarung. Dritter Slug dieser Art nach „division-1" und
    # „jordan-1st-division" — die Quelle benennt zweite Ligen gern „First/1st Division".
    "first-division": 3,
    # 12.09.2026 (CI-Wachhund): „China League" — die ZWEITE Liga Chinas (China League One, unter
    # der Super League). Guandong GZ-Power gegen Dalian Kun City, beides Zweitligisten.
    "china-league": 3,
    # 17.09.2026 (CI-Wachhund): „football-national-league" — die russische FNL, also die ZWEITE
    # Liga (unter der Premier Liga). Beleg aus der Paarung: FC Nizhny Novgorod gegen
    # Leningradets, beides Zweitligisten. „National League" klingt nach oberster Klasse und ist
    # in Russland wie in England die Ebene darunter.
    "football-national-league": 2,
    # 14.09.2026 (CI-Wachhund): Brasileiro Serie C ist die DRITTE brasilianische Klasse
    # (unter Serie A und B). Beleg: Brusque FC gegen AA Internacional Limeira.
    "brasileiro-serie-c": 3,
    # 10.10.2026 (CI-Wachhund): zweite Liga der Staatsmeisterschaft von Paraíba — regional.
    "paraibano-2-divisao": 3,
    # 14.09.2026 (CI-Wachhund): „Calcutta Premier Div." ist eine indische STADT-Liga —
    # wie „mizoram-premier-league" trotz „Premier" weit unter der obersten Klasse (ISL).
    # Beleg aus der Paarung: United SC gegen „Mohun Bagan SG Reserves" — die erste Elf
    # von Mohun Bagan spielt in der ISL, hier laeuft ihre Reserve.
    "calcutta-premier-div-": 3,
    # 15.09.2026 (CI-Wachhund): „calcutta-1st-division". Die Ordnungszahl-Regel liest „1st" NICHT
    # als Ebene 1, und das ist Absicht — sie wuerde eine indische Regionalstaffel neben die
    # Bundesliga stellen. Der Calcutta-Pyramide nach gehoert sie dorthin, wo die Calcutta
    # Premier Division schon steht: Ebene 3. Tabelle statt Regel, weil der Slug das nicht sagt.
    "calcutta-1st-division": 3,
    # 17.09.2026 (CI-Wachhund): „shillong-first-divison-league" (Tippfehler im Feed) — die
    # Stadtliga von Shillong in Meghalaya, eine Amateur-Regionalliga weit unterhalb der
    # I-League. Beleg aus der Paarung: Laitkyrhong SC gegen Mawtawar SC. Dieselbe Klasse wie
    # „calcutta-1st-division" drueber: „First Division" ist der Name der Staffel, nicht ihr Rang.
    "shillong-first-divison-league": 3,
    # 19.09.2026 (CI-Wachhund, fuenf Slugs auf einmal). Alle fuenf haben dasselbe Problem: der
    # Name nennt eine Ordnungszahl oder ein Adjektiv, aber nicht den Rang in seiner Pyramide.
    #
    #   2-hnl                 Kroatien, „Druga HNL" — unter der HNL, also Ebene 2.
    #   china-league-2        China, dritte Ebene (Super League 1 · League One 2 · League Two 3).
    #                         „League 2" heisst hier NICHT zweite Klasse — genau die Falle.
    #   ii-liga               Polen, dritte Ebene (Ekstraklasa 1 · I liga 2 · II liga 3). Auch
    #                         hier zaehlt die roemische Zwei eine Klasse, nicht die Ebene.
    #   kategoria-superiore   Albanien, die hoechste Klasse — „Superiore" ist der Landesname
    #                         fuer Ebene 1, kein Zusatz oberhalb einer anderen Liga.
    #   liga-alef             Israel, dritte Ebene (Premier League 1 · Liga Leumit 2 · Liga
    #                         Alef 3). Passt zu „liga-bet-south-a": 3 weiter oben — Liga Bet ist
    #                         die vierte, steht dort aber schon als 3; die Alef gehoert
    #                         mindestens dorthin.
    "2-hnl": 2,
    "china-league-2": 3,
    "ii-liga": 3,
    "kategoria-superiore": 1,
    "liga-alef": 3,
}

# Wettbewerbe, bei denen „Spielklasse" die falsche Frage ist. Sie bekommen eine eigene Marke
# statt einer Zahl — ein Pokalspiel gegen einen Drittligisten ist kein Drittliga-Spiel.
ART = {
    "uefa-champions-league": "kontinental", "uefa-europa-conference-league": "kontinental",
    "uefa-europa-league": "kontinental", "caf-champions-league": "kontinental",
    "caf-confederations-cup": "kontinental", "copa-libertadores": "kontinental",
    "copa-sudamericana": "kontinental", "leagues-cup": "kontinental",
    "copa-do-brasil": "pokal", "fa-cup": "pokal", "efl-cup": "pokal", "ofb-cup": "pokal",
    # 10.10.2026: hier standen „greece-cup" und „copa-uruguay" HINTER dem Kommentar zur
    # Serie-D-Coppa (07.10.) — mein Einfuegen hatte sie auskommentiert. Eigene Zeile seitdem.
    "coppa-italia": "pokal", "coppa-italia-serie-d": "pokal",  # 07.10.: Nocerina - Turris
    "greece-cup": "pokal", "copa-uruguay": "pokal",
    "copa-paulista": "pokal", "dfb-pokal": "pokal", "copa-del-rey": "pokal",
    # 14.09.2026 (CI-Wachhund): die AFC Champions League ist das asiatische Gegenstueck zu UEFA-
    # und CAF-Champions-League — Vereine aus verschiedenen Verbaenden, also keine Spielklasse.
    # Beleg: FK Neftchi Fargona (Usbekistan) gegen Al Quwa Al Jawiya (Irak).
    "afc-champions-league": "kontinental",
    # 14.09.2026 (CI-Wachhund): die ASEAN Club Championship ist dasselbe eine Stufe kleiner —
    # ein suedostasiatischer Vereinswettbewerb ueber Landesgrenzen (Shan United/Myanmar gegen
    # Ezra FC/Laos), keine Liga. „championship" im Namen heisst hier nicht zweite Klasse.
    "asean-club-championship": "kontinental",
    # 17.09.2026 (CI-Wachhund): die Canadian Championship ist Kanadas POKAL (Voyageurs Cup),
    # ausgespielt zwischen MLS- und CPL-Klubs. Beleg aus der Paarung: Montreal gegen Vancouver
    # Whitecaps, beides MLS-Vereine. „Championship" im Namen heisst auch hier nicht zweite
    # Klasse — dritter Slug dieser Art nach „asean-club-championship" und „usl-championship".
    "canadian-championship": "pokal",
    # 14.09.2026 (Status-Seite, Guard-Batterie): „asian-games". Ein multisportliches
    # Kontinentalereignis — im Ledger stehen aktuell 131 BASKETBALL-Zeilen (China–Philippines),
    # deren Spielklasse `stufe()` ohnehin nicht beantwortet. Gemeldet wurde es trotzdem als
    # Fussball-Luecke, weil das Turnier auch ein Fussballturnier hat und dessen Zeilen durch
    # das rollierende Fenster kommen und gehen. Eine Spielklasse hat es in keiner Sportart:
    # es spielen Nationalauswahlen (im Fussball U23), keine Ligamannschaften.
    "asian-games": "kontinental",
    # 16.09.2026 (CI-Wachhund, „uefa-nations-league"): dieselbe Antwort wie bei den Asian Games,
    # nur in Europa — es spielen Nationalauswahlen (Beleg aus der einen Zeile im Ledger:
    # „Turkiye - France"), und eine Nationalmannschaft hat keine Spielklasse. Aufgetaucht ist
    # der Slug mit dem Laenderspielfenster; dass es nur EINE Wette war, aendert nichts: die
    # Tabelle wird nicht nach Haeufigkeit gepflegt, sondern danach, ob die Frage beantwortbar
    # ist. Eine Namensregel waere hier falsch — „nations" steckt auch in Vereinsturnieren.
    "uefa-nations-league": "kontinental",
    # 22.09.2026 (CI-Wachhund, „africa-cup-of-nations-qualification"): dritter Fall derselben
    # Art. Beleg aus dem Ledger: „Egypt - Angola" — Nationalauswahlen, und eine
    # Nationalmannschaft hat keine Spielklasse. Eine Namensregel auf „-qualification" waere
    # falsch: Vereinswettbewerbe haben ebenfalls Qualifikationsrunden (Champions League), und
    # deren Teilnehmer HABEN eine Spielklasse.
    "africa-cup-of-nations-qualification": "kontinental",
    # 22.09.2026 (CI-Wachhund, „concacaf-nations-league"): vierter Fall derselben Art. Beleg aus
    # dem Ledger: „Bahamas - Saint-Martin" — Nationalauswahlen, keine Spielklasse.
    "concacaf-nations-league": "kontinental",
    # 14.09.2026 (CI-Wachhund): die Professional Development League ist Englands U21-Reserve-
    # wettbewerb. „Swansea City - Huddersfield Town" sieht nach Profifussball aus, es sind die
    # Nachwuchsmannschaften — die Namensregel greift nicht, weil weder U21 noch „youth" im
    # Slug steht. Deshalb Tabelle statt Regel.
    "professional-development-league": "jugend",
    # 14.09.2026 (CI-Wachhund): die NIFL Development League ist Nordirlands Reserve-/Nachwuchs-
    # wettbewerb. Beleg aus der Paarung: „Crusaders FC – Linfield Swifts" — die „Swifts" sind
    # die Reservemannschaft von Linfield. Der Slug sagt „Development", das reicht der
    # bestehenden Jugend-Regel aber nicht (sie sucht u17..u23/youth/academy).
    "nifl-development-league": "jugend",
    # 14.09.2026 (CI-Wachhund): die WK League ist Suedkoreas oberste FRAUEN-Liga (Seoul City WFC
    # gegen Hwacheon KSPO). Die Frauen-Regel sucht „women/femenina/feminin" — die koreanische
    # Abkuerzung „WK" (Women\u2019s K League) enthaelt keines davon.
    "wk-league": "frauen",
}

# Nachwuchs, egal wie der Wettbewerb ihn schreibt: als Kuerzel irgendwo im Slug (u17..u23)
# oder ausgeschrieben. Wortgrenzen, damit „usl-championship" oder „u2" nichts ausloesen.
_JUGEND_RX = re.compile(r"\bu1[789]\b|\bu2[0-3]\b|youth|jugend|junior|academy|primavera")

# Reservemannschaften, in jeder Sprache, in der sie im Ledger auftauchen.
_RESERVE_RX = re.compile(r"reserv|riserv")

# Pokalwettbewerbe, die sich nicht „Cup" nennen. Wortgrenzen, damit „shield" in einem Vereinsnamen
# nicht die ganze Liga zum Pokal macht.
# 10.09.2026 (CI-Wachhund, „dbu-pokalen"): dieselbe Klasse wie „efl-trophy" und „reserva" —
# ein Pokal, den die Regel nicht als Pokal las. `s.endswith("-pokal")` fing die deutsche Form,
# aber im Skandinavischen haengt der bestimmte Artikel HINTEN an: „pokalen" ist „der Pokal".
# Gegenprobe an allen Fussball-Slugs des Ledgers: die breitere Regel beantwortet genau den
# einen offenen Slug und stuft keinen bereits eingestuften um.
# 15.09.2026 (CI-Wachhund, „turkiye-kupasi"): dieselbe Klasse zum dritten Mal. „kupa" stand
# schon da, aber im Tuerkischen haengt die Besitzform HINTEN an — „kupasi" ist „der Pokal von".
# Genau wie beim skandinavischen „pokalen" am 10.09. Also die Endung zulassen, nicht den Slug
# eintragen: der naechste Landespokal heisst wieder anders, meint aber dasselbe.
_POKAL_RX = re.compile(
    r"(?:^|-)(?:trophy|shield|coupe|taca|kupa(?:s[iı])?|kubok|beker|cupa|cupen"
    r"|pokal(?:en|et)?"
    # 27.09.2026 (CI-Wachhund, „supercopa-internacional"): Estudiantes - Rosario Central, ein
    # Supercup — ein einzelnes Titelspiel zwischen Meister und Pokalsieger, keine Spielklasse.
    # Als Wort in die Pokal-Regel statt als Tabellenzeile: Supercopa/Supercoppa/Supercup gibt
    # es in fast jedem Verband, und der naechste kommt sonst wieder von Hand.
    r"|super-?(?:copa|coppa|cup|kupa|pokal))(?:-|$)")

# Auszeichnungen und Langzeitwetten ohne Spielklasse. Wortgrenzen, damit „winner" in einem
# Marktnamen nicht ganze Ligen zu Auszeichnungen macht.
_AUSZEICHNUNG_RX = re.compile(
    r"(?:^|-)(?:ballon-dor|golden-boy|golden-boot|golden-ball|puskas|"
    r"player-of-the-year|team-of-the-year|top-scorer|award|awards)(?:-|$)")

# Mustererkennung fuer alles, was neu dazukommt. Sie ersetzt die Tabelle nicht, sie faengt
# nur die Faelle ab, bei denen der Slug die Antwort selbst mitbringt.
# 🔴 21.09.2026 (CI-Wachhund, „int-friendly-games"). Der zweite offene Slug des Tages, und
# anders als „india-karnataka-super-division" ist dieser KEINE Spielklasse, der nur die Zeile
# fehlt: ein Freundschaftsspiel hat keine Ebene. Jede Zahl waere hier falsch — „1", weil
# Nationalmannschaften spielen, oder „3", weil es um nichts geht. Deshalb eine eigene Marke,
# wie bei Reserve, Jugend und Pokal: die Tabelle sagt damit „angesehen und kein Rang", statt
# einen zu erfinden. `randliga()` bleibt fuer diese Slugs False — ein Testspiel ist keine
# Randliga, es ist gar keine Liga.
# Als MUSTER und nicht als Zeile: Testspiele tauchen unter vielen Namen auf (club-friendlies,
# international-friendlies, friendlies-clubs). Gegenprobe am Ledger: die Regel beantwortet
# genau diesen einen offenen Slug und stuft keinen bereits eingestuften um.
_FREUNDSCHAFT_RX = re.compile(r"friendl|freundschaft|testspiel|amistoso|amichevol")

# ── Mehrdeutige Schluessel ───────────────────────────────────────────────────
# 🔴 22.09.2026 (Lucas schickt einen Fremd-Radar-Post: „#Uzbekistan #Pro_League, PFC Terdu –
# FK Gazalkent … Check den Stake Radar, ob wir diese Liga auch nicht haben").
#
# Wir hatten sie: sechs Fussballwetten auf genau dieses Spiel stehen im Ledger, darunter die
# aus dem fremden Post ($2.000 x 1,75 auf FK Gazalkent -1,5). Was wir NICHT hatten, war die
# richtige Spielklasse. Die uzbekische Pro League ist die ZWEITE Liga, unter der Super League.
# In der Tabelle stand `"pro-league": 1` — also oberste Klasse. Genau die Unterscheidung, um
# die Lucas am 07.09. gebeten hat („ne 50k Wette auf Arsenal sagt 0, eine 50k Wette auf ein
# 2-3. Liga Team ist zumindest jemand der mehr dran glaubt"), stand hier auf dem Kopf.
#
# Der Kopf dieser Datei kannte die Fehlerklasse schon halb: „der Slug ist NICHT sportartenrein"
# (`bundesliga` = Fussball und Handball), deshalb nimmt `stufe()` die Sportart entgegen. Die
# zweite Achse ist dieselbe Sache und war offen: der Slug ist auch nicht LAENDERREIN. Gemessen
# am Ledger (20.000 Wetten, 166 Fussball-Slugs):
#
#   pro-league    Fussball: PFC Terdu – FK Gazalkent (Uzbekistan, Ebene 2)
#                 Volleyball: Lions-Pro – Legion-Pro, Sokol-Pro – Berkut
#   1st-division  Daenemark: AB Gladsaxe – Esbjerg, HB Koege – Hobro    → Ebene 2
#                 Norwegen:  FK Haugesund – Stroemmen                    → Ebene 2
#                 Zypern:    Pafos – AEL Limassol, Aris – Karmiotissa    → Ebene 1
#                 Irak:      Al Hedod – AL Shatra
#                 Ein Schluessel, zwei Spielklassen. Jede Zahl ist fuer die Haelfte falsch.
#
# Gegenprobe, damit das hier nicht zum Rundumschlag wird: die uebrigen laenderuebergreifenden
# Slugs im Ledger tragen ueberall DIESELBE Klasse und bleiben deshalb unberuehrt — `bundesliga`
# (Deutschland + Oesterreich), `ligue-1` (Frankreich + Tunesien), `superliga` (Argentinien +
# Serbien), `premier-league` (England, Russland, Aegypten, Armenien, Kasachstan, Bhutan,
# St. Kitts), `championship` (England + Nordirland, beide Ebene 2), `primera-division`
# (Venezuela, Uruguay, Peru). Mehrdeutig ist nicht „mehrere Laender", sondern
# „mehrere KLASSEN unter einem Schluessel".
#
# Warum keine Zahl geraten wird: der Feed hat den richtigen Schluessel bereits. Die GraphQL-
# Abfrage in `stake_highroller_fetch.py` holt `tournament { id name slug }` und wirft die `id`
# weg. Mit ihr ist die Liga eindeutig — bis sie mitgeschrieben und genug gesammelt ist, ist
# „angesehen, und der Schluessel reicht nicht" die einzige ehrliche Auskunft. Deshalb eine
# eigene Marke wie bei Reserve, Jugend und Pokal, KEINE None: None heisst „noch nie
# angesehen" und laesst den CI-Wachhund anschlagen, der genau dafuer da ist.
MEHRDEUTIG = {
    "pro-league": "Uzbekistan Pro League (Ebene 2) und Volleyball unter demselben Slug",
    "1st-division": "Daenemark/Norwegen Ebene 2, Zypern Ebene 1 — derselbe Slug",
}

# 🔴 22.09.2026 abends: der Wachhund `mehrdeutige_kandidaten` hat am selben Tag zugeschlagen,
# an dem er gebaut wurde — mit `efl-trophy` und zwei Turnier-IDs. Nachgesehen:
#
#     4b60bce4…  13 Wetten  Barnsley–Leeds, Leicester–Fulham U21, Rochdale–Liverpool U21
#     7807b604…   3 Wetten  Bradford–Newcastle U21, Cheltenham–Exeter, Wigan–Blackpool
#
# Beide heissen „EFL Trophy", beide Fussball, beide dieselbe Sorte Paarung (League One/Two plus
# U21-Nachwuchs). Das sind die Nord- und die Sued-Sektion EINES Wettbewerbs, keine zwei
# Spielklassen. Der Wachhund zaehlte IDs und meinte KLASSEN — Fehlerklasse: *ein Waechter, der
# ein Symptom zaehlt statt die Wirkung*.
#
# Die Regel dahinter steht in dieser Datei schon einmal, bei `_GRUPPE`: „eine Gruppe IST ihre
# Liga". Regionalstaffeln teilen eine Spielklasse per Definition. Automatisch von aussen
# unterscheidbar sind die beiden Faelle nicht — Daenemarks und Zyperns „1st Division" tragen
# ebenfalls denselben Namen und dieselbe Sportart. Deshalb eine gepruefte Ausnahmeliste, wie
# bei jedem anderen Wissen in dieser Datei: was hier steht, ist nachgesehen worden, und der
# Waechter meldet weiterhin ALLES, was nicht draufsteht.
EIN_WETTBEWERB = {
    "efl-trophy": "Nord- und Sued-Sektion desselben Pokals (22.09.2026 an den Paarungen geprueft)",
    # 24.09.2026: zwei IDs, und der Beweis steht in den Paarungen selbst — „America de Cali -
    # Aguilas Doradas Rionegro" laeuft unter BEIDEN (7x unter 25afe81c…, 3x unter bc8d0d08…).
    # Dasselbe Spiel kann nicht in zwei Spielklassen liegen. Alle Vereine dahinter sind
    # kolumbianische Erstligisten (Santa Fe, Deportivo Cali, Medellin, Bucaramanga, Tolima,
    # Atletico Nacional) — eine Liga, im Feed auf zwei Turnier-IDs verteilt.
    "primera-a-apertura": "Dasselbe Spiel laeuft unter beiden Turnier-IDs (24.09.2026 an den "
                          "Paarungen geprueft) — eine Liga, zwei IDs im Feed",
    # 06.10.2026 (Wachhund): „San Francisco FC - Umecit" unter 6b79bf59…, „San Francisco - UMECIT"
    # unter e98fc544… — dasselbe Spiel, zwei Schreibweisen; alle Vereine Panamas oberste Liga.
    "liga-panamena-de-futbol-apertura": "Pre-Match-/Live-ID derselben Liga (06.10.2026 an den "
                                        "Paarungen geprueft: San Francisco - Umecit unter beiden)",
    # 06.10.2026: beide IDs englische National League (Eastleigh, Southend, Kidderminster, Sutton,
    # Barrow, Wealdstone, Hartlepool, Gateshead) — ein Wettbewerb, 5. Klasse Englands.
    "national-league": "Beide IDs englische National League (06.10.2026 an den Paarungen geprueft)",
    # 07.10.2026 (Wachhund): Nanjing City - Shaanxi Union unter fc23edd2…, Foshan Nanshi - Guangxi
    # Hengchen unter 6fe69359… — alle vier Vereine China League One. UNSICHER, nur ueber die Vereine
    # geprueft, kein gemeinsames Spiel; taucht ein League-Two-Verein auf, gehoert der Slug nach
    # TURNIER_EBENE.
    "china-league": "Beide IDs China League One (07.10.2026 an den Vereinen geprueft, kein "
                    "gemeinsames Spiel)",
}

# 🔴 27.09.2026 (CI-Wachhund, zehn Slugs auf einmal). Nachgesehen an den Paarungen: bei SIEBEN
# davon laufen DIESELBEN Spiele unter beiden IDs („Girona - Albacete" unter e9a30c04… und
# 6911916f…) — der Feed vergibt je Wettbewerb eine ID fuer Pre-Match und eine fuer Live. Das
# ist kein zweites Turnier, und der Beweis ist derselbe wie am 24.09. bei primera-a-apertura:
# dasselbe Spiel kann nicht in zwei Spielklassen liegen. `mehrdeutige_kandidaten` fasst IDs
# mit gemeinsamem Spiel deshalb jetzt selbst zusammen, statt jede Doppel-ID von Hand in
# EIN_WETTBEWERB nachzutragen.
#
# Echt verschieden waren drei — hier je Turnier-ID von Hand eingestuft, mit Beleg:
#   premier-league    England (Arsenal - Leeds) und BHUTAN (Ugyen Academy - Drukpa, Thimphu
#                     FC - Tensung): beide oberste Klasse, das Urteil stimmt fuer beide.
#   primera-division  Uruguay (Penarol - Boston River) und PERU (AD Tarma - Cienciano): beide
#                     oberste Klasse.
#   primera-b         Chile (Cobreloa - Santiago Wanderers) und Kolumbien (Real Santander -
#                     Orsomarso) sind die ZWEITE Klasse — ARGENTINIEN (Arsenal de Sarandi -
#                     Liniers, Excursionistas - Real Pilar) ist die Primera B Metropolitana,
#                     die DRITTE unter Liga Profesional und Primera Nacional. Dort war die „2"
#                     falsch.
# Jede weitere ID unter einem dieser Slugs wird vom Wachhund gemeldet, bis sie hier steht —
# auch wenn sie per gemeinsamem Spiel zu einem eingestuften Turnier gehoert (dann ist es die
# Live-/Pre-Match-Schwester und braucht nur dieselbe Zeile).
TURNIER_EBENE = {
    # 07.10.2026 (Wachhund): Frankreich (AS Monaco - Toulouse) und ALGERIEN (CS Constantine - US
    # Biskra) — beide oberste Klasse ihres Landes.
    "ligue-1": {
        "4ec37b6b-a7f8-456a-b18b-e127f06d52b7": ("Frankreich", 1),
        "418bdc1c-59bc-4e65-9c37-b049bcbb88f0": ("Algerien (CS Constantine - US Biskra)", 1),
    },
    "premier-league": {
        "c94f5db7-4fd2-4407-a4fc-9a4e807f0ea1": ("England", 1),
        "261292f1-ed72-4765-93c3-abee6a5ebee0": ("Bhutan", 1),
        # 06.10.2026 (Wachhund): beide oberste Klasse.
        "47043c57-9221-4bce-86bb-ed83826a230e": ("Ukraine (Veres Rivne - FK Kudrivka)", 1),
        "5dbb8017-40fc-496e-9b05-352444d232b1": ("Bangladesch (Fortis FC - City Club)", 1),
        # 10.10.2026 (Wachhund), eingestuft ueber die Vereine — beide oberste Klasse.
        "7b267538-cdcd-4328-9d42-c3f566d0116a": ("Libanon (Nejmeh, Al Ansar, Safa SC)", 1),
        "f560ca90-38a3-4f92-9eb3-49097ad163dd": ("Israel (Ironi Tiberias - Hapoel Be'er Sheva)", 1),
    },
    "primera-division": {
        "118c993d-1294-4f73-9afb-c97213bcfab0": ("Uruguay", 1),
        "d7cebc4a-a25f-4e7c-8511-3ed7f37eaeb8": ("Peru", 1),
        # 01.10.2026 (CI-Wachhund): „AFF Guatemala - Deportivo Fraijanes" — in Guatemala heisst
        # die OBERSTE Liga Liga Nacional; die Primera Division ist die ZWEITE. Ueber den Slug
        # allein haette sie als Ebene 1 gegolten.
        "16c027b0-893a-40de-b829-63d5ea863a64": ("Guatemala, Primera Division (2. Liga)", 2),
        # 06.10.2026 (Wachhund): „Universidad de Concepcion - Huachipato" — Chiles oberste Liga.
        "4b9f9ae2-1d49-4296-9d87-bc4e84dc0d31": ("Chile (U. de Concepcion - Huachipato)", 1),
        # 10.10.2026 (Wachhund): „Unan Managua - Real Esteli FC" — Nicaraguas oberste Liga.
        "42bb04f7-0ad1-48fb-a031-4d8eaee6f0ee": ("Nicaragua (UNAN Managua - Real Esteli)", 1),
    },
    # 06.10.2026 (Wachhund): Venezuela (Urena SC, Barinas, Deportivo Lara, Real Frontera) ist die
    # ZWEITE Klasse. Paraguay (b072c9f7…, „Sportivo Carapegua - Deportivo Capiata"): eingestuft
    # ueber die VEREINE, nicht ueber den Namen — beide spielen in der Division Intermedia, der
    # zweiten Klasse unter der Division Profesional. Kommt unter dieser ID ein Verein aus der
    # Primera B (3.), ist die Zahl falsch — dann gehoert die ID nach MEHRDEUTIG.
    "segunda-division": {
        "7e76a474-ef1d-4b36-9ff8-7e0b34ac9054": ("Venezuela, Segunda Division", 2),
        "b072c9f7-4707-4bb4-be98-0ac3db79b1a0": ("Paraguay, Division Intermedia (ueber die Vereine)", 2),
        # 10.10.2026 (Wachhund): Bentin Tacna, Carlos Mannucci, ADA Jaen — Perus Liga 2.
        "496047b8-b9ae-4ae1-ad95-d8640c25b022": ("Peru, Liga 2 (Bentin Tacna - Mannucci)", 2),
    },
    # 29.09.2026: drei Laender unter einem Slug — alle oberste Klasse, das Urteil stimmt fuer alle.
    "primera-division-apertura": {
        "73fd62cb-be5d-4a0a-8a6f-41bad1da141c": ("Mexiko, Liga MX (Live)", 1),
        "278999cd-a15b-4d9c-8d94-86da3b8770a4": ("Mexiko, Liga MX (Pre-Match)", 1),
        "fb070494-604d-46bb-b14c-479d82849e63": ("Paraguay (Libertad - Olimpia)", 1),
        "0e9a4e87-f59b-4413-8f14-bf3906b36159": ("El Salvador (Alianza - CD Inca)", 1),
    },
    "primera-b": {
        "180b07b7-4b3a-45c4-a1a6-3e56b1f68d78": ("Argentinien, Primera B Metropolitana", 3),
        "ec7f8cc8-357f-46c9-9350-182662cb8487": ("Chile", 2),
        "f5da1103-437b-4567-a484-e41513e26725": ("Kolumbien", 2),
        # 10.10.2026 (Wachhund): zweite kolumbianische ID — Tigres FC, Real Cartagena,
        # Atletico FC Cali, Barranquilla FC sind alle Primera-B-Vereine (vermutlich die
        # Finalrunde als eigenes Turnier). Ueber die Vereine eingestuft.
        "451881c2-8c79-44e9-91fe-af1921b3dcc1": ("Kolumbien (Tigres FC, Real Cartagena)", 2),
    },
}

# 🟡 14.10.2026 (Stoerungsmeldung „11 Liga-Schluessel bezeichnen mehrere Turniere"): 26 offene
# Turnier-IDs unter 11 Slugs, jede an ihren Paarungen nachgesehen. Ergebnis vorweg: KEINE traegt
# eine andere Klasse als ihr Slug — die Laufzeit-Einstufung (`stufe()` faellt fuer unbekannte IDs
# auf den Slug zurueck) war in allen Faellen richtig. Eingetragen wird trotzdem jede ID: erst dann
# meldet der Wachhund nur noch WIRKLICH neue, und eine abweichende Klasse (wie Guatemalas
# „Primera Division" = 2. Liga am 01.10.) faellt sofort auf statt in einer Liste von 26 unterzugehen.
# Wo die Einstufung nur ueber Vereine laeuft und unsicher ist, steht es dabei.
TURNIER_EBENE["premier-league"].update({
    "f2458771-8cd7-4321-95d4-c8c0213a2420": ("England, zweite ID (Arsenal - Leeds, Liverpool - Man City)", 1),
    "4d9ff4d8-b060-496a-8c44-28490952674e": ("Russland (CSKA - Rodina, Fakel - Lokomotiv)", 1),
    "5142bb72-422a-4e1f-a0d9-6741798761e4": ("Kasachstan (Kairat - Zhetysu, Ulytau - Okzhetpes)", 1),
    "5cba677c-fbd4-4667-b5cc-c94e4177f29c": ("Singapur (Hougang - Balestier, Lion City Sailors)", 1),
    "9187248c-2c09-4e43-91a9-95535b68d407": ("Singapur, zweite ID (Lion City Sailors - Young Lions)", 1),
    "652c995d-3fbe-4e85-bae0-20fe321f50d0": ("Israel, zweite ID (Hapoel Jerusalem - Maccabi Haifa)", 1),
    "720d2e1f-39e3-4f63-985a-d9b1b097b341": ("Hongkong (Rangers - Lee Man, Southern - Eastern)", 1),
    "134f0a60-a7d1-40b1-b006-b89583cf27fb": ("Bahrain (Manama Club - Al-Ahli Manama)", 1),
    "9369d46f-d399-4eee-b18e-6e8463cd9306": ("St. Kitts und Nevis (Cayon Rockets - St. Pauls)", 1),
    "e65fda02-bd78-475d-b1ef-9203f22043be": ("Aegypten (Pyramids - Ceramica Cleopatra)", 1),
    "f00175c1-2b09-4300-98e1-9454be395358": ("Armenien (Pyunik - BKMA, Ararat-Armenia - Alashkert)", 1),
})
TURNIER_EBENE["ligue-1"]["0e41fdea-749a-445f-ab8c-aee7fccd3410"] = ("Frankreich, zweite ID (PSG - Le Mans)", 1)
TURNIER_EBENE["primera-division"].update({
    "08169963-97d9-4273-85e0-9e0e7f8c3b47": ("Chile, zweite ID (U. de Chile - Nublense)", 1),
    "6dfb1fe4-0297-4160-a4e9-6a5375d67f60": ("Venezuela, Liga FUTVE (Tachira - UCV, Portuguesa - La Guaira)", 1),
})
TURNIER_EBENE["primera-division-apertura"].update({
    "bb16eb31-5e92-4c0f-8c33-ab2dd5de9ac5": ("El Salvador, zweite ID (CD Inca - Platense)", 1),
    # Ueber die Vereine: Saprissa, Cartagines, Puntarenas, Perez Zeledon, AD San Carlos sind
    # Costa Ricas oberste Liga. Escorpiones Belen und Inter San Carlos sind NICHT sicher erste
    # Klasse — tauchen dort weitere Zweitligisten auf, gehoert die ID nach MEHRDEUTIG.
    "e8debc1b-037b-45dd-b81b-2442587bf61f": ("Costa Rica (Saprissa, Cartagines, Puntarenas) — ueber die Vereine", 1),
})
# Unsicher, nur ueber die Vereine: Orion FC spielt in Costa Ricas Liga de Ascenso (2. Klasse).
TURNIER_EBENE["segunda-division"]["13692592-cf35-478b-bf52-2ecf9450e3d9"] = (
    "Costa Rica, Liga de Ascenso (Orion FC - Independiente) — ueber die Vereine", 2)
TURNIER_EBENE.update({
    "championship": {
        "3b662c1a-f26b-4463-9fa0-9ff957b706ee": ("England (West Ham - QPR, Southampton - Portsmouth)", 2),
        "e53bf7a3-35e5-4549-9cae-d48af0964180": ("England, zweite ID (Wolves - Bolton)", 2),
        "cccf9635-e69f-4d1a-a0e3-27d5c332a579": ("Schottland (Queen's Park - Inverness CT)", 2),
    },
    "bundesliga": {
        "1add4a75-3be9-4f08-b8f8-b33d903faf74": ("Deutschland (Augsburg - Bayern, Dortmund - Bremen)", 1),
        "62699282-5557-4952-875c-f9f48b5a8827": ("Oesterreich (LASK - Rapid, GAK - Salzburg)", 1),
    },
    "superliga": {
        "75075ee8-c5fe-49b4-a87a-8da3ab4e028b": ("Argentinien, Liga Profesional (Instituto - Boca)", 1),
        "a8e8f99d-40ee-4b18-80f7-bd7658c906cd": ("Argentinien, zweite ID (River - Estudiantes RC)", 1),
        "e0a854b2-d8a5-4990-8a85-81d7565dd403": ("Serbien (Partizan - Novi Pazar, Zemun - Zeleznicar)", 1),
    },
    "superligaen": {
        "90ae85a9-3f06-47e9-bb15-8fbf8380cba0": ("Daenemark (Silkeborg - Horsens, Randers - Viborg)", 1),
        "d725499a-e368-4852-8fc7-512e69c87561": ("Daenemark, zweite ID (Midtjylland - Kopenhagen)", 1),
    },
    "premiership": {
        "b3e8923b-3afa-48b1-b39b-bb4a92d76e3f": ("Schottland (Motherwell - Celtic)", 1),
        "c8e1d9f6-82a4-4588-967b-897fa2f82bd1": ("Schottland, zweite ID (Rangers - Kilmarnock)", 1),
        "425e9e11-1e7e-440f-afde-338219a4c5aa": ("Nordirland, NIFL Premiership (Linfield - Bangor)", 1),
    },
})

_MUSTER = (
    ("srl", lambda s: s.endswith("-srl") or "-srl-" in s),          # Simulated Reality League
    ("freundschaft", lambda s: bool(_FREUNDSCHAFT_RX.search(s))),
    # 07.09.2026 — am Tag nach dem Bau tauchte „Primera Division Reserve, Clausura" auf und
    # stand als einzige Liga ohne Ebene da. Eine Reserveliga ist keine Spielklasse: es sind
    # zweite Mannschaften eines Vereins, die Aufstellung ist naeher an einer Jugendliga als
    # an der Liga, deren Namen sie traegt. Deshalb eine eigene Marke statt einer Zahl — und
    # als MUSTER, damit die naechste Reserveliga nicht wieder von Hand nachgetragen werden
    # muss.
    # 08.09.2026 (CI-Wachhund, „campeonato-de-reserva-de-primera-division-c"): die Regel las
    # nur die ENGLISCHE Schreibweise. Reserveligen heissen in Suedamerika „reserva", in
    # Italien „riserve" — dieselbe Sache, anderes Wort. Ein Muster, das nur eine Sprache
    # kennt, faellt bei jedem neuen Land wieder aus. Gegenprobe an den 171 Fussball-Slugs des
    # Ledgers: die breitere Regel beantwortet genau den einen offenen Slug und stuft keinen
    # bereits eingestuften um.
    ("reserve", lambda s: bool(_RESERVE_RX.search(s)) or s.endswith("-ii")),
    # 08.09.2026 (CI-Wachhund, „uefa-youth-league"): die Regel las nur die ERSTEN DREI
    # ZEICHEN, fing also „u19-…" und „u23-…", aber kein Wettbewerb, der seine Jugend im Namen
    # ausschreibt. Dieselbe Unterscheidung, die heute frueh `elf_marker` in betfair_consensus
    # bekommen hat: Nachwuchs ist eine andere Mannschaft, nicht eine Spielklasse.
    #
    # Die UEFA Youth League koennte man auch „kontinental" nennen — das waere nicht falsch,
    # aber die schwaechere Auskunft: fuer den Einsatz eines Highrollers verhaelt sich ein
    # U19-Spiel wie Nachwuchs und nicht wie ein Champions-League-Abend.
    #
    # Gegenprobe an den 170 Fussball-Slugs des Ledgers: die breitere Regel stuft KEINEN
    # bereits eingestuften Slug um; sie beantwortet genau einen, der vorher None war.
    ("jugend", lambda s: bool(_JUGEND_RX.search(s))),
    ("frauen", lambda s: ("women" in s or "femenina" in s or "feminin" in s
                          or "damallsvenskan" in s or "frauen" in s)),
    # 08.09.2026 (CI-Wachhund, „efl-trophy"): dieselbe Klasse wie „reserva" zwei Stunden vorher.
    # Die Regel kannte drei Woerter fuer „Pokal" — englisch, spanisch, deutsch. Ein Wettbewerb,
    # der sich Trophy, Shield, Coupe oder Taca nennt, ist derselbe Wettbewerbstyp und fiel durch.
    # Gegenprobe an den Fussball-Slugs des Ledgers: beantwortet genau den einen offenen Slug und
    # stuft keinen bereits eingestuften um.
    # 15.09.2026 (CI-Wachhund, „cup"): der Slug heisst NUR „cup" — die Quelle liefert fuer den
    # tschechischen Pokal (FK Trinec – Mlada Boleslav) nichts als das Wort. `endswith("-cup")`
    # verlangte einen Bindestrich davor und griff deshalb nicht. Die Wettbewerbs-ART ist damit
    # trotzdem bekannt: es ist ein Pokal. Die Spielklasse ist es nicht — und genau dafuer gibt
    # es `art()`: eine Kategorie fuer Wettbewerbe, bei denen die Spielklasse nichts sagt. Eine
    # Zahl waere hier erfunden, „pokal" ist die Wahrheit.
    ("pokal", lambda s: (s == "cup" or s.endswith("-cup") or s.startswith("copa-")
                         or s.endswith("-pokal") or bool(_POKAL_RX.search(s)))),
    # 09.09.2026 (CI-Wachhund, „ballon-dor"): und diesmal eine ANDERE Klasse als die drei
    # Treffer davor. „reserva", „efl-trophy" und „mizoram-premier-league" waren Wettbewerbe,
    # deren Ebene nur fehlte. Der Ballon d'Or ist gar kein Wettbewerb: „Ballon dor 2026 ·
    # Winner · Harry Kane" ist eine AUSZEICHNUNG mit einem Sieger, kein Spiel mit einer
    # Spielklasse. Eine Zahl dafuer waere erfunden — es gibt keine Liga, in der Harry Kane
    # den Ballon d'Or gewinnt.
    #
    # Deshalb eine eigene Marke und keine Ebene: die Zeile faellt damit nicht mehr aus der
    # Ansicht (das war der Grund fuer den Waechter), behauptet aber auch keine Spielklasse.
    ("auszeichnung", lambda s: bool(_AUSZEICHNUNG_RX.search(s))),
)

SPORT = "soccer"


def art(slug: str):
    """Wettbewerbsart, wo die Spielklasse nichts sagt — sonst None."""
    s = (slug or "").lower()
    if not s:
        return None
    # Mehrdeutig schlaegt alles: ein Schluessel, der zwei Spielklassen bezeichnet, darf keine
    # tragen — auch dann nicht, wenn er zufaellig in ART oder in einem Muster steht.
    if s in MEHRDEUTIG:
        return "mehrdeutig"
    if s in ART:
        return ART[s]
    for name, passt in _MUSTER:
        if passt(s):
            return name
    return None


# 07.09.2026 — der zweite Nachtrag in zwei Tagen („2nd-division-league", „super-league-2"),
# und beide Male stand die Antwort im Slug. Die Tabelle bleibt die Wahrheit fuer alles, was
# man WISSEN muss; diese Regeln lesen nur, was der Slug selbst sagt. Nicht geraten wird
# weiterhin alles andere: was hier nicht greift, bleibt None und faellt auf.
_ORDNUNGSZAHL = re.compile(r"^([2-9])(?:st|nd|rd|th)?-(?:division|liga|league|lig)\b")
# 15.09.2026 (CI-Wachhund, „second-prof-league"): dieselbe Aussage, nur ausgeschrieben. Die
# Ziffern-Regel las „2nd-division", aber nicht „second-…". Bewusst OHNE „first": die Ziffern-
# Regel beginnt aus demselben Grund bei 2 — eine Regionalstaffel, die sich „1st Division"
# nennt, waere sonst mit einem Wort auf die Ebene der Bundesliga gehoben (siehe
# „calcutta-1st-division" in der Tabelle). Was man WISSEN muss, bleibt Tabelle.
_ORDNUNGSWORT = re.compile(r"^(second|third|fourth|fifth|sixth|seventh|eighth|ninth)-")
_ORDNUNGSWORT_ZAHL = {"second": 2, "third": 3, "fourth": 4, "fifth": 5,
                      "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9}
_ANHANG = re.compile(r"^(.*)-([23])$")
# 12.09.2026 (CI-Wachhund, „tercera-division-group-4"). Zum ZWEITEN Mal eine Gruppe derselben
# Liga: `tercera-division-group-7` steht seit Tagen von Hand in der Tabelle, Gruppe 4 stand
# wieder ohne Ebene da — und die spanische Tercera hat achtzehn Gruppen. Eine Tabellenzeile je
# Gruppe ist die Instanz; die Klasse ist „eine Gruppe IST ihre Liga". Regionalstaffeln teilen
# eine Spielklasse per Definition, das muss niemand wissen, das sagt der Slug selbst.
_GRUPPE = re.compile(r"^(.*)-group-(?:\d{1,2}|[a-z])$")


def _ebene_aus_slug(s: str):
    """Ebene, wenn der Slug sie selbst nennt — sonst None.

    Zwei Faelle, beide nur mit Beleg im Namen:
      · „2nd-division-league", „3rd-liga"     → die Ordnungszahl steht vorne.
      · „super-league-2", „thai-league-2"     → der Anhang zaehlt die Klasse HOCH, und der
        Rumpf muss dafuer als oberste Klasse bekannt sein. Ohne diese Bedingung wuerde
        „serie-a-2" oder eine Gruppennummer stillschweigend zur zweiten Liga.
    """
    m = _ORDNUNGSZAHL.match(s)
    if m:
        return min(int(m.group(1)), 3)
    m = _ORDNUNGSWORT.match(s)
    if m:
        return min(_ORDNUNGSWORT_ZAHL[m.group(1)], 3)
    m = _ANHANG.match(s)
    if m and EBENE.get(m.group(1)) == 1:
        return int(m.group(2))
    # „…-group-4", „…-group-b" → die Ebene des Rumpfs, wenn der bekannt ist. Nur mit Rumpf in
    # der Tabelle: eine unbekannte Liga wird durch eine Gruppennummer nicht bekannter.
    m = _GRUPPE.match(s)
    if m and EBENE.get(m.group(1)):
        return EBENE[m.group(1)]
    return None


def stufe(slug: str, sport: str = SPORT, liga_id=None):
    """'1' | '2' | '3' | 'kontinental' | 'pokal' | 'frauen' | 'jugend' | 'srl' — oder None.

    None heisst „nicht in der Tabelle" und muss auch so angezeigt werden. Der Slug ist NICHT
    sportartenrein (`bundesliga` = Fussball und Handball), deshalb die Sportart als Bedingung.
    `liga_id` (Turnier-ID aus dem Feed) schlaegt den Slug, wo TURNIER_EBENE sie kennt.
    """
    if (sport or "") != SPORT:
        return None
    _t = TURNIER_EBENE.get((slug or "").lower()) or {}
    if liga_id is not None and str(liga_id) in _t:
        return str(_t[str(liga_id)][1])
    a = art(slug)
    if a:
        return a
    s = (slug or "").lower()
    v = EBENE.get(s) or _ebene_aus_slug(s)
    return str(v) if v else None


def mehrdeutige_kandidaten(zeilen, sport: str = SPORT) -> dict:
    """Slugs, die eine Ebene tragen und im Feed MEHRERE Turniere bezeichnen.

    Der Wachhund fuer die Klasse, die am 22.09.2026 aufgefallen ist. Von Hand gefunden wurde
    sie nur, weil Lucas einen Fremd-Post geschickt hat — das ist keine Methode. Mit der
    `ligaId` aus dem Feed ist sie zaehlbar: ein Schluessel, hinter dem zwei Turnier-IDs
    stehen, kann keine Spielklasse tragen.

    ⚠️ Mehrere IDs heissen nicht automatisch mehrere Spielklassen: ein Wettbewerb mit Nord-
    und Sued-Sektion hat zwei. Geprueft und fuer gleich befundene Faelle stehen in
    `EIN_WETTBEWERB` und werden nicht mehr gemeldet — alles andere schon.

    Zeilen ohne `ligaId` zaehlen NICHT als „ein Turnier" — sie zaehlen gar nicht. Der Feed
    schreibt die ID erst seit dem 22.09. mit, und das rollierende Fenster traegt sie erst
    nach und nach; fehlende Information ist hier kein Freispruch. `nMitId` sagt, wie weit die
    Abdeckung ist, damit ein leeres Ergebnis nicht als Unbedenklichkeit gelesen wird.
    """
    je = {}
    spiele = {}                  # (slug, spiel) -> {ids}
    mit_id = 0
    for z in zeilen or []:
        if not isinstance(z, dict):
            continue
        if (z.get("sport") or "") != sport:
            continue
        sl, lid = z.get("ligaSlug"), z.get("ligaId")
        if not sl or lid in (None, ""):
            continue
        # Kombis zaehlen nicht als eigenes Turnier (ihre Liga ist die des ersten Beins), sind
        # aber ein gueltiger BEWEIS fuer „dasselbe Spiel": Liga, ID, Paarung und Anpfiff kommen
        # alle aus demselben Bein.
        if not z.get("kombi"):
            mit_id += 1
            je.setdefault(sl, set()).add(str(lid))
        # Paarung + Anpfiff, NICHT eventId: der Feed fuehrt Pre-Match und Live auch als zwei
        # Spiel-Objekte („Girona - Albacete" 25.09. 18:30 unter fc9de54d… und 506e8bc2…).
        if z.get("event") and z.get("anpfiff"):
            spiele.setdefault((sl, (str(z["event"]), str(z["anpfiff"]))), set()).add(str(lid))
    # 🔴 23.09.2026: der Wachhund meldete `fa-cup` mit zwei Turnier-IDs — englischer und
    # thailaendischer FA Cup. Kein Fund: `fa-cup` traegt die ART „pokal", und die ist fuer
    # beide dieselbe. Fehlerklasse: *ein Waechter, der zaehlt, was verschieden ist, statt zu
    # pruefen, ob das Urteil verschieden waere.* Deshalb zaehlen nur Slugs mit Ebene 1/2/3.
    #
    # 🔴 27.09.2026: dieselbe Klasse eine Stufe weiter. Zwei IDs, unter denen DASSELBE Spiel
    # laeuft, sind ein Turnier (Pre-Match- und Live-ID). Sie werden hier zusammengefasst —
    # gemeldet wird nur, was danach noch mehr als ein Turnier ist.
    def _gruppen(sl, ids):
        eltern = {i: i for i in ids}
        def wurzel(i):
            while eltern[i] != i:
                eltern[i] = eltern[eltern[i]]
                i = eltern[i]
            return i
        for (s2, _sp), gl in spiele.items():
            if s2 != sl:
                continue
            gl = sorted(i for i in gl if i in eltern)
            for b in gl[1:]:
                eltern[wurzel(b)] = wurzel(gl[0])
        out = {}
        for i in ids:
            out.setdefault(wurzel(i), set()).add(i)
        return list(out.values())

    treffer = {}
    for sl, ids in je.items():
        if len(ids) < 2 or sl in MEHRDEUTIG or sl in EIN_WETTBEWERB:
            continue
        if not str(stufe(sl, sport) or "").isdigit():
            continue
        bekannt = TURNIER_EBENE.get(sl)
        if bekannt:
            # Von Hand eingestuft: jede ID, die dort nicht steht, ist offen — auch die
            # Live-/Pre-Match-Schwester, denn `stufe()` liest je ID.
            offen = sorted(i for i in ids if i not in bekannt)
            if offen:
                treffer[sl] = offen
            continue
        if len(_gruppen(sl, ids)) > 1:
            treffer[sl] = sorted(ids)
    return {"nMitId": mit_id, "kandidaten": treffer}


def randliga(slug: str, sport: str = SPORT, liga_id=None) -> bool:
    """Ebene 2 oder tiefer — das, was Lucas „2.-3. Liga" nennt.

    14.10.2026: mit Turnier-ID, sonst liest die Funktion am Slug vorbei, wo TURNIER_EBENE die
    Klasse je ID kennt (Guatemalas „Primera Division" = 2. Liga, Argentiniens Primera B = 3.)."""
    return stufe(slug, sport, liga_id) in ("2", "3")


# ── Referenzeinsatz ──────────────────────────────────────────────────────────
# Was ein normaler Einsatz ist, kommt aus stake_league_norm.json (wachsender Stand, keine
# Ledger-Momentaufnahme — die Begruendung steht im Kopf von stake_league_norm.py). Ligen
# unter dessen MIN_N haben dort KEINE Norm. Fuer genau die Ligen ist die Frage aber am
# interessantesten, deshalb der Rueckfall auf den Median der EBENE — nicht auf den globalen
# Median, denn der wird von Tennis und E-Sport getragen und hat mit Fussball nichts zu tun.
# Welcher der beiden Wege benutzt wurde, steht in jeder Zeile (`refBasis`).
EBENE_MIN_N = 15


def ebene_median(wetten: list) -> dict:
    """Median des Einsatzes je Ebene, aus den Fussball-Einzelwetten des Ledgers."""
    je = defaultdict(list)
    for w in wetten or []:
        if w.get("kombi") or not w.get("einsatzUsd"):
            continue
        st = stufe(w.get("ligaSlug"), w.get("sport"), w.get("ligaId"))
        if st:
            je[st].append(float(w["einsatzUsd"]))
    return {k: round(statistics.median(v), 2) for k, v in je.items() if len(v) >= EBENE_MIN_N}


def referenz(w: dict, norm: dict, ebmed: dict):
    """-> (referenzEinsatz, basis) oder (None, 'unbekannt').

    `norm` ist der Ligen-Block aus stake_league_norm.json, dort nach ANZEIGENAME verschluesselt.
    """
    st = stufe(w.get("ligaSlug"), w.get("sport"), w.get("ligaId"))
    if not st:
        return None, "unbekannt"
    n = (norm or {}).get(w.get("liga")) or {}
    if n.get("basis") == "gelernt" and n.get("median"):
        return float(n["median"]), "liga"
    m = (ebmed or {}).get(st)
    if m:
        return float(m), "ebene"
    return None, "unbekannt"


def faktor(w: dict, norm: dict, ebmed: dict):
    r, basis = referenz(w, norm, ebmed)
    e = w.get("einsatzUsd")
    if not r or not e:
        return None, basis
    return round(float(e) / r, 2), basis


# ── Was daraus fuer die Anzeige wird ─────────────────────────────────────────
# Die Schwellen stehen hier EINMAL. Wenn das Frontend sie noch einmal setzt, gibt es sie
# zweimal, und irgendwann sagt die eine Flaeche etwas anderes als die andere — die Klasse
# „ein Frontend baut Produzenten-Logik nach" hat dieses Repo schon mehrfach getroffen.
KAND_AB = 3.0          # ab diesem Vielfachen gilt ein Einsatz auf Ebene 2/3 als bemerkenswert
TOP_AB = 6.0           # das Gegenstueck auf Ebene 1 (dort ist die Reihe negativ)
STUFEN = [(1.5, "<1.5x"), (3.0, "1.5-3x"), (6.0, "3-6x"), (float("inf"), ">6x")]

# 07.09.2026 — feinere Baender fuer die Phase-Ansicht (live vs vor Anpfiff). Die grobe
# STUFEN-Liste bleibt, wie sie ist: sie traegt die Spielklasse-Tabelle, und eine Aenderung
# dort waere eine stille Aenderung an einer laufenden Messung. Der Grund fuer den Schnitt
# bei 15x: der Ausreisser-Schwanz verhaelt sich messbar anders als „3-6x", und in „>6x"
# verschwand er zwischen den ruhigen Faellen.
STUFEN_FEIN = [(1.5, "<1.5x"), (3.0, "1.5-3x"), (6.0, "3-6x"), (15.0, "6-15x"),
               (float("inf"), ">15x")]


def _bucket(f, stufen=None):
    for grenze, name in (stufen or STUFEN):
        if f < grenze:
            return name
    return (stufen or STUFEN)[-1][1]


def bucket(f, fein: bool = False):
    """Oeffentlich, damit niemand die Schwellen anderswo nachbaut."""
    return _bucket(f, STUFEN_FEIN if fein else STUFEN)


def kandidaten(wetten: list, norm: dict, ab: float = KAND_AB, max_n: int = 60) -> list:
    """Grosse Einsaetze auf Ebene 2/3 — die Zeilen, um die Lucas gebeten hat.

    Bewusst OHNE Ausgangsfilter: die Liste zeigt, was gesetzt wurde, nicht was aufging.
    Ob sie traegt, entscheidet die vorregistrierte Schublade, nicht diese Anzeige.

    07.09.2026 (Backlog: „Sortierung im Spielklasse-Reiter: aktuell nur nach Faktor. Nach
    Betrag waere die zweite sinnvolle Achse — wie ungewoehnlich vs. wie viel Geld").

    Der Deckel macht daraus mehr als eine Sortierfrage: eine Liste, die nach Faktor
    ABGESCHNITTEN ist, laesst sich nicht ehrlich nach Betrag sortieren — die groesste Summe
    des Tages kann bei Faktor 3,1 liegen und waere dann nie in der Auswahl. Deshalb ist die
    Auswahl jetzt die VEREINIGUNG der beiden Bestenlisten, und jede Zeile sagt in `warumDrin`,
    ueber welche sie hereingekommen ist. Sortiert wird danach in der Flaeche; die Auswahl
    haengt nicht mehr an der Sortierung.
    """
    ebmed = ebene_median(wetten)
    out = []
    for w in wetten or []:
        if w.get("kombi") or not w.get("einsatzUsd"):
            continue
        st = stufe(w.get("ligaSlug"), w.get("sport"), w.get("ligaId"))
        if st not in ("2", "3"):
            continue
        f, basis = faktor(w, norm, ebmed)
        if f is None or f < ab:
            continue
        a = w.get("abrechnung") or {}
        out.append({
            "id": w.get("id"), "ts": w.get("ts"),
            "liga": w.get("liga"), "ligaSlug": w.get("ligaSlug"), "ebene": st,
            "event": w.get("event"), "eventId": w.get("eventId"),
            "markt": w.get("markt"), "auswahl": w.get("auswahl"),
            "einsatzUsd": round(float(w["einsatzUsd"]), 2), "quote": w.get("quote"),
            "phase": w.get("phase"), "faktor": f, "refBasis": basis,
            "pnlUsd": a.get("pnlUsd"),
            "ausgang": ([b.get("status") for b in (a.get("beine") or [])] or [None])[0],
        })
    # Zwei Bestenlisten, je halber Deckel, dann vereinigt: so ist keine der beiden Achsen
    # von der anderen abhaengig. Ueberschneidung ist der Normalfall und kein Fehler — die
    # Zeile steht dann mit beiden Gruenden da.
    haelfte = max(1, max_n // 2)
    nach_faktor = sorted(out, key=lambda x: -x["faktor"])[:haelfte]
    nach_betrag = sorted(out, key=lambda x: -x["einsatzUsd"])[:haelfte]
    drin = {}
    for grund, liste in (("faktor", nach_faktor), ("betrag", nach_betrag)):
        for x in liste:
            z = drin.setdefault(id(x), x)
            z.setdefault("warumDrin", []).append(grund)
    aus = sorted(drin.values(), key=lambda x: -x["faktor"])
    return aus[:max_n]


def kreuz(wetten: list, norm: dict) -> dict:
    """Ebene × Einsatzgroesse, gemessen an den ABGERECHNETEN Einzelwetten.

    Zwei Zahlen je Zelle, und sie meinen Verschiedenes:
      · roi   — geldgewichtet: was der Fluss dort tatsaechlich verdient/verloren hat.
      · flach — jede Wette gleich schwer, dazu die einseitige 95%-Untergrenze. Nur die
                entscheidet; ein Punktschaetzer ist kein Beleg.
    """
    ebmed = ebene_median(wetten)
    zellen = defaultdict(lambda: {"n": 0, "einsatz": 0.0, "pnl": 0.0, "flach": [],
                                  "spiele": set()})
    for w in wetten or []:
        if w.get("kombi") or not w.get("einsatzUsd"):
            continue
        st = stufe(w.get("ligaSlug"), w.get("sport"), w.get("ligaId"))
        if not st:
            continue
        pnl = (w.get("abrechnung") or {}).get("pnlUsd")
        if pnl is None:
            continue
        f, _ = faktor(w, norm, ebmed)
        if f is None:
            continue
        z = zellen[(st, _bucket(f))]
        z["n"] += 1
        z["einsatz"] += float(w["einsatzUsd"])
        z["pnl"] += float(pnl)
        z["spiele"].add(w.get("eventId"))
        q = w.get("quote")
        if q and q > 1:
            z["flach"].append((q - 1) if pnl > 0 else -1.0)
    out = {}
    for (st, b), z in zellen.items():
        u = _untergrenze(z["flach"])
        o = _obergrenze(z["flach"])
        out.setdefault(st, {})[b] = {
            "n": z["n"], "spiele": len(z["spiele"]),
            "roi": round(z["pnl"] / z["einsatz"], 4) if z["einsatz"] else None,
            "flach": round(sum(z["flach"]) / len(z["flach"]), 4) if z["flach"] else None,
            "flachUg": round(u, 4) if u is not None else None,
            "flachOg": round(o, 4) if o is not None else None,
            "belegt": bool(u is not None and u > 0),
            # 07.09.2026 — eine Zelle kann auf zwei Arten etwas sagen, und die eine Grenze
            # taugt nur fuer eine davon. Folgen belegt die UNTERgrenze ueber null. Dagegen
            # halten belegt die OBERgrenze unter null; mit der Untergrenze allein waere die
            # Ebene-1-Reihe auf ewig „kein Urteil", obwohl sie genau die Aussage traegt,
            # um die es hier geht.
            "belegtGegen": bool(o is not None and o < 0),
        }
    return out


def _untergrenze(werte: list):
    """Einseitige 95%-Untergrenze des Mittelwerts (z=1,645). Unter n=UG_MIN_N: None.

    Der harte Boden ist derselbe wie ueberall im Projekt. Ohne ihn faellt eine Schublade mit
    n=2 und ROI +81% als „belegt" durch — das ist am 06.09. in der Freigabe passiert.
    """
    UG_MIN_N = 30
    n = len(werte or [])
    if n < UG_MIN_N:
        return None
    m = sum(werte) / n
    sd = (sum((v - m) ** 2 for v in werte) / (n - 1)) ** 0.5
    return m - 1.645 * sd / (n ** 0.5)


def _obergrenze(werte: list):
    """Das Gegenstueck: einseitige 95%-OBERgrenze. Unter demselben n-Boden: None."""
    UG_MIN_N = 30
    n = len(werte or [])
    if n < UG_MIN_N:
        return None
    m = sum(werte) / n
    sd = (sum((v - m) ** 2 for v in werte) / (n - 1)) ** 0.5
    return m + 1.645 * sd / (n ** 0.5)


def kreuz_phase(wetten: list, norm: dict, phase) -> dict:
    """Phase x Einsatzgroesse — dieselbe Rechnung wie `kreuz`, andere Zeilenachse.

    07.09.2026 (Lucas, Backlog: „Achse umstellen auf live x Einsatzgroesse statt auffaellig
    ja/nein"). Der Grund fuer die Umstellung ist gemessen: „auffaellig ja/nein" trennt
    nichts — die Trennung liegt zwischen live und vor Anpfiff und im aeussersten Band.

    `phase` wird hereingereicht (stake_analyse._phase), damit die Nachrechnung fuer alte
    Zeilen an EINER Stelle steht und nicht hier ein zweites Mal.
    """
    ebmed = ebene_median(wetten)
    zellen = defaultdict(lambda: {"n": 0, "einsatz": 0.0, "pnl": 0.0, "flach": [],
                                  "spiele": set()})
    for w in wetten or []:
        if w.get("kombi") or not w.get("einsatzUsd"):
            continue
        pnl = (w.get("abrechnung") or {}).get("pnlUsd")
        if pnl is None:
            continue
        f, _ = faktor(w, norm, ebmed)
        if f is None:
            continue
        b = _bucket(f, STUFEN_FEIN)
        ph = phase(w)
        for zeile in ({ph, "alle"} if ph in ("vor", "live") else {"alle"}):
            z = zellen[(zeile, b)]
            z["n"] += 1
            z["einsatz"] += float(w["einsatzUsd"])
            z["pnl"] += float(pnl)
            z["spiele"].add(w.get("eventId"))
            q = w.get("quote")
            if q and q > 1:
                z["flach"].append((q - 1) if pnl > 0 else -1.0)
    out = {}
    for (zeile, b), z in zellen.items():
        u = _untergrenze(z["flach"])
        o = _obergrenze(z["flach"])
        out.setdefault(zeile, {})[b] = {
            "n": z["n"], "spiele": len(z["spiele"]),
            "roi": round(z["pnl"] / z["einsatz"], 4) if z["einsatz"] else None,
            "flach": round(sum(z["flach"]) / len(z["flach"]), 4) if z["flach"] else None,
            "flachUg": round(u, 4) if u is not None else None,
            "flachOg": round(o, 4) if o is not None else None,
            "belegt": bool(u is not None and u > 0),
            "belegtGegen": bool(o is not None and o < 0),
        }
    return out


def block(wetten: list, norm: dict) -> dict:
    """Der komplette `randliga`-Block fuer stake_auswertung.json."""
    ebmed = ebene_median(wetten)
    je_ebene = defaultdict(int)
    for w in wetten or []:
        st = stufe(w.get("ligaSlug"), w.get("sport"), w.get("ligaId"))
        if st:
            je_ebene[st] += 1
    ohne = sorted({w.get("ligaSlug") for w in (wetten or [])
                   if (w.get("sport") == SPORT and w.get("ligaSlug")
                       and stufe(w.get("ligaSlug"), w.get("sport"), w.get("ligaId")) is None)})
    return {
        "abFaktor": KAND_AB,
        "topAbFaktor": TOP_AB,
        "ebeneMedian": ebmed,
        "jeEbene": dict(sorted(je_ebene.items())),
        "ohneEbene": ohne[:40],
        "nOhneEbene": len(ohne),
        # 22.09.2026 — der zweite Wachhund, neben „ohne Ebene". Der erste findet Ligen, die
        # FEHLEN; dieser findet Ligen, die falsch DRINSTEHEN, weil ihr Schluessel zwei
        # Turniere bezeichnet. `nMitId` gehoert dazu: solange der Feed die Turnier-ID erst
        # sammelt, ist eine leere Liste kein Freispruch, sondern eine leere Messung.
        "mehrdeutig": mehrdeutige_kandidaten(wetten),
        "kandidaten": kandidaten(wetten, norm),
        "kandidatenAuswahl": ("Vereinigung der besten 30 nach Faktor und der besten 30 nach "
                              "Betrag — sonst haenge die Betrags-Sortierung an einer Liste, "
                              "die nach Faktor abgeschnitten wurde."),
        "kreuz": kreuz(wetten, norm),
        "warum": ("Die Spielklasse steht in keinem Feld des Feeds und laesst sich aus dem "
                  "Volumen nicht ableiten — nach Volumen gelten Sueper Lig, MLS und die "
                  "Championship als 'kleine Liga'. Sie kommt deshalb aus einer Tabelle "
                  "(stake_liga_stufe.py). Gemessen am 07.09. laufen die Reihen "
                  "gegenlaeufig: auf Ebene 1 wird der Fluss mit steigendem Einsatz "
                  "schlechter, auf Ebene 2/3 besser. Belegt ist das nicht — die "
                  "interessanten Zellen haben n=37 und n=12, und die Schwellen wurden nach "
                  "dem Blick auf die Zahlen gesetzt. Das Urteil faellt vorwaerts, in "
                  "randliga_hoher_einsatz und topliga_hoher_einsatz."),
    }
