
HEy das ist meine funktionierende externe/ausgelagerte(soll auch so bleiben) daytrading website. diese soll verbessert werden

mit den 10 besten crypto coins die werden dauerhafdt analysiert chart wird analysiert das wirst du alles finden auf dem github account man hat auch eine gute ansicht 
von dem charts von den coins und es gibt verschiedene startegien und immer wenn 4 bedingunen zu treffen die auf long hindeuten wird long 10er hebel gekauft und wenn 4 für short zu treffen dann short hebel 10er, er sichert sich mit stop loss ab
die website macht echte trades über crypto börse bitunix, das sind die live trades und dann gibt es auch simulations trades paper trades
also das gleiche soll genommen werden was schon gebaut und noch verbessert werden



wichtige verbesserung:
-was richtig gut wär ist die verschiedenen strategien vergleichen zu können also quasi mit allen trades die diese strategie genutzt wurde wie gut ws da lief und so das man es leicht gegenüberstellen kann und schnell sieht das ist die beste strategie
-das hier wenn du das richtig machst wär auch ultra big: Strategie-Backtester der automatisch beide Strategien auf historischen 1-Min-Daten vergleicht? So könntest du sehen, welche Strategie bei welchem Coin besser performt und deine Auswahl datenbasiert
 optimieren. das wär cool damit man nicht ewig auf die paper trades warten muss sondern  quasi die vergangenheit nutzt um die strategien zu testen das sollte ja auch schneller gehen 
-also wenn position gut im gewinn dann hebel erhöhen und marsche reduzieren so viel wie man rausnehmen kann und dann stoploss ins gewinn setzten, das als eine möglich keit die man aktivieren kann bei jeder strategie und und anpassen kann bei wie viel gewinn, ...


Kleine verbesserungen:
eigene strategien hinzufügen das geht schon allerdings noch nicht gut man kann noch nicht so viele strategien baue, da wenig variation an indikatoren und so
außerdem ist dean sich unsicher ob die paper trades richtig sind also er ist da im plus und ist sich nicht sichert wie true das ist deswegen, bei dem paper trades müssen auch gebühren mit berechnet werden um wirklich zu gucken ob es bei live trades auch funktioniert
simple fehler gibt es noch beim eingeben von zahlen fenster wo man was eingibt schließt sich wenn man bei makieren außerhalb des popups ist wo man die geldanzahl und hebel höhe eingeben kann  und  dann beim makieren über dem chart ist dann mit der maus dann schließt sich das fenster
und bei manchen strategien ist wenn man es von paper auf live umstellt das nicht direkt bestätig und man muss das 2mal machen,
kannst du nochmal überprüfen und nach logik fehlern suchen, 

hier der public github account: dort sind die datein der funktionierenden website nutze die
https://github.com/dean06greif-ai/krypto-Alert/tree/bitunix-fix




hat nicht priorität aber wär auch cool: ki analyse verbessern:
-für offene und geschlossene trades die möglichkeit ki zu fragen nach bewertung kritik und positiv und verbesserunbgs ideen
-und ki analyse für performance einer strategie je coin, also wie die strategie für den coin performt und was die möglich probleme sind an der strategie



ich möchte das gleiche für mich dafür muss ich mir noch ein crypto konto anlegen und dann alle namen bzw verbindungen zu deans konto auf mich schalten





---------------------------------------------------------------------------------------------------------------





backtesterverbesserungen: 
-einzelne strategien sind gerade immer beim testen in der basic einstellung, man wuss diese bearbeiten können um zu testen was es ändern würde für jede strategie einzeln (take profit stoploss)
-der backtester ist nur für 1min historie aber man muss mehr einstellungens möglichekeiten von den time frames haben also auch 2min 3min 5min 10min 15min 30min 1h 2h 4h 6h 8h 12h 24h 3d 1week 1monat, das gilt dann für jede strategie man muss in den strategie einstellungen sagen können für welchen time frame diese angewandt werden soll(das mit dem time frime muss noch in die einstellungen von der strategie eingebaut werden also die möglichkeit)
-die möglichkeit die daten zu sehen die der backtester ausgewertet hat um diese auch überprüfen zu können vielleicht als button zum download von csv datei wo man alles sieht woraus sich das ergebnis berechnet hat, das könnte man dann ja auch einer anderen ki geben zum überprüfen
-denke weitere strategien dir und suche nach der besten strategie mit der höchsten winrate: 

maybe zeitraum angeben statt immer nur fest die letzten 3tage 10tage, ... also sagen 24.07.2026 - 30.07.2026


------------------------------



kleine extras:
- der plus button für strategie hinzufügen soll nur dafür genutzt werden um eine neue strategie hinzuzufügen, also

Zeitfenster
- es gibt zeitfenster wo man festlegen kann zu welcher tageszeit getradet werden darf, das gilt für alle strategien gerade, es soll aber möglich sein für jede strategie ein individuelles zeitfenster zu haben/ einzustellen
-das mit den zeit fenstern muss man natürlich auch im backtester machen können um noch mehr test möglichkeiten zu haben, auch ebend dann für jede strategie



Penis!13


---------------------
mehr Tage für mehr Statistik
-parameter optimieren und neue regel erstellen soll auch 60 und 90 tage 180 und 360 Tage gehen als zeitraum auch für backtester bzw auch wo wo es sinn macht und ich gerade nich drann denke
Parameter-Optimierung soll zusätzlich auf:
60 Tage90 Tage180 Tage360 Tagelaufen können.
Gleiches gilt für:Strategie-ErstellungRegel-ErstellungBacktesterGenerell sollten überall dort, wo Zeiträume verwendet werden, auch längere Zeiträume zur Verfügung stehen.


abbruch button
-optimizer und backtestert hängen sich manchmal auf deswegen wär abbruch button cool und auch vielleicht überlegen was man dagegen tun kann, und wenn man raus geht aus dem popup für optimierungen und wieder reingeht sieht man nicht ob gerade eine läuft und wie weit diese ist, das muss man immer sehen
Zusätzlich sollte die aktuelle Optimierung immer sichtbar sein:
Fortschritt in % aktuelle Kombination
verbleibende Zeit Wenn man das Fenster schließt und später wieder öffnet, sollte weiterhin sichtbar sein:
ob eine Optimierung läuftwie weit sie fortgeschritten ist


leichtere backtests von optimierten strats
-wenn man durch parameter optimieren was gutes gefunden hat kann man das auf paper und live trades übertragen es muss aber auch möglich sein auf backtester zu übertragen
Aktuell können nur neue Strategien erstellt werden.Zusätzlich sollte es möglich sein:bestehende Strategien auszuwählendiese weiterzuentwickelnneue Regeln zu vorhandenen Strategien hinzuzufügenvorhandene Regeln zu verbessern bzw auch testen ob eine regel tauschen auuch einen vorteil bringen kann
-backtest problem hab nen ultra strongen backtesz machen wollen aber bei 90tagen und allen coins und 1min hat er leider abgebrochen da müsste man nochmal gucken das es von mir aus länger lädt sber abbrechen ist shit
-nach möglichkeit suchen die backtests schneller zu machen ohne an genauikeit zu verlieren natürlich, es ist sehr wichtig das die backtests 100% akkurat sind um richtig forschen zu können und sich drauf verlassen zu können,

- aktuell Performance / Infrastruktur
Aktuell läuft das Backend auf einer kostenlosen Render-Instanz mit:512 MB RAM 0.1 CPU
Bei großen Backtests (z. B. 90 Tage, alle Coins, 1-Minuten-Timeframe) kommt das System möglicherweise an CPU- oder RAM-Grenzen.
Bitte prüfen:ob die aktuelle Infrastruktur der Flaschenhals ist ob Backtester und Optimierer effizienter gemacht werden könnenob die Abbrüche durch Ressourcenlimits verursacht werden
gucken ob man code algortihmen optimieren kann 
ziel ist das die backtest so schnell wie möglich sind 100% akkurat und auch wenn es viele berechnungen sind sie nicht selbst abbrechen sondern dann halt bissel länger dauern



bereits bekannte strategie entwickler:
-gerade ebend kann man nur neue strateguien erstellen lassen aber ich fände es auch cool wenn man die möglich keit hat eine gegebene noch weiterzuentwickeln

bessere schnellere algorithmen
-Bayes’sche Optimierung probieren dass soll ein guter algorithmus sein um schneller bei so blackbox funktion zu einem guten ergebnis zu kommen baue das vielleicht als weitere möglichkeit ein also parameter optimieren baysche methode, kann man natürlich auch nehmen um neue strat zu finden, 


-beim backtest festgestellt das manche trades auch ausgeführt werden obwohl nur 3/5 regeln zugetroffen haben

Zeitfenster
- es gibt zeitfenster wo man festlegen kann zu welcher tageszeit getradet werden darf, das gilt für alle strategien gerade, es soll aber möglich sein für jede strategie ein individuelles zeitfenster zu haben/ einzustellen
-das mit den zeit fenstern muss man natürlich auch im backtester machen können um noch mehr test möglichkeiten zu haben, auch ebend dann für jede strategie
Aktuell gelten Zeitfenster global für alle Strategien.
Es sollte möglich sein, für jede Strategie eigene Zeitfenster festzulegen.
Beispiel:
Plain Text1Strategie A209:00 - 12:003 4Strategie B515:00 - 22:006 7Strategie C824 StundenWeitere Zeilen anzeigen
Das sollte auch vollständig im Backtester testbar sein.

breakeven verbessern
Break-Even verbessern / konfigurierbar machen
Aktuell erfolgt Break-Even hauptsächlich über TP1.
Zusätzliche Optionen wären sinnvoll:
Break-Even bei TP1
Break-Even bei frei wählbarem CRV (z. B. 1R, 2R, 3R)
Break-Even bei festem Gewinn-Prozentsatz
Smart Break-Even (z. B. unter Swing-Low / über Swing-High)
Break-Even komplett deaktivieren
Dadurch könnten deutlich mehr Varianten getestet werden.





-bug gefunden
min rel volumen wenn man es auf 0/ 1.5/ 2.5/ 10/ 100/ 1000 setzt ändert es null an den trades
Relatives Volumen prüfen
Beim Testen ist aufgefallen:
Min. Rel. Volumen = 1.5
Min. Rel. Volumen = 2.2
Min. Rel. Volumen = 1000
liefern identische Ergebnisse
Bitte prüfen, ob der Filter:
korrekt verwendet wird
korrekt aus dem UI übernommen wird

vielleicht haben andere indikatoren auch noch fehler? rsi funktiniert aber den hab ich getestet

Gewinnsicherung funktioniert irgendwie noch nicht richtig bzw sieht man es nicht richtig was es macht und ob es einen unterschied macht





-------------------------------------------------


-Volumen-Bug prüfen/ backtester für großen zeitraum prüfen/ mehr tage/ abbruch button und rausklicken und reinklicjen sehen ob noch was läfut/ keine eigenstendigen abbruchs backtest/ strategie entwickler/ bayopt/ gucken wegen 3/5 regeln nur met woher kommt das/ zeit fenster frü trades finden
Alle Strategien auf 90 Tage testen
Beste 3 auswählen
Bayes-Optimierung
Walk-Forward-Test
Zeitfenster testen



