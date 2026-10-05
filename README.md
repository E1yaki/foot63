# Foot animation 63

Résultats, classements et calendriers du football d'animation du Puy-de-Dôme
(U10-U11, U13 Brassage, U13 D2, U13 D1), avec favoris, recherche, alertes, export agenda,
classement de division et zones de montée / descente. Le site est **statique** (HTML + JS, aucun
serveur) ; un script Python récupère les plateaux, les archive en JSON (`data/raw/`) et régénère
les fichiers lus par le site.

```
index.html, assets/        le site (mobile d'abord, mode jour/nuit, installable)
data/rules.json            barème, départage, compositions et zones de montée / descente U13
data/raw/<site_id>.json    HISTORIQUE : un fichier par plateau, jamais effacé, avec ses versions
data/<saison>/<cat>.json   données agrégées lues par le site (générées)
data/catalog.json          liste des catégories + date de mise à jour (généré)
data/changes.json          fil des mises à jour, source des alertes (généré)
scraper/                   scrape.py, check_rules.py, config.json, seeds.txt, tests
.github/workflows/         exécution automatique (GitHub Actions)
```

## Voir le site tout de suite

Ouvre `index.html` : sans dossier `data/` généré, le site passe en **mode démonstration**
(clubs réels, scores inventés ; D1 terminée, D2 à 3 journées sur 4, pour voir les barres claires et foncées).
En local avec les vraies données : `python -m http.server` puis http://localhost:8000.

## Hiérarchie de navigation

Deux familles bien séparées, pour ne jamais mélanger deux choses différentes :
- **Championnat** : Phase (1, 2, 3…) → Division (les catégories disponibles **changent selon la
  phase** : U10-U11/U13 Brassage/D1/D2 en phase 1, d'autres en phase 2…) → Poules.
- **Festival U13** (ou toute autre compétition à part ajoutée plus tard, `"kind": "cup"`) : Tour (1 à 4)
  → Poules directement, sans division.

La toute première rangée (« Compétition ») ne sert qu'à choisir entre ces familles ; elle disparaît s'il
n'y a qu'une seule compétition de configurée.

## Classements, montées et descentes

- Onglet **Division** : toutes les poules d'une division dans un seul classement. On compare d'abord
  les premiers de chaque poule, puis les deuxièmes, etc. (moyenne de points par match, puis différence de
  buts, puis buts marqués). C'est ce qui départage « le meilleur 2e » ou « le meilleur 3e ».
- **Barre claire** à gauche d'une équipe : elle monte (ou descend) d'après le classement actuel.
  **Barre foncée** : c'est mathématiquement acquis, quels que soient les matchs restants. Le calcul
  est prudent (pire cas pour l'équipe, meilleur cas pour les autres) : il vaut mieux une barre claire de
  trop qu'une fausse certitude. Si des équipes manquent dans les données, rien n'est déclaré acquis.
- **Info-bulle** sur le rang d'une équipe (souris) : elle explique pourquoi la place est assurée ou provisoire (points maximum possibles, meilleure et pire place dans la poule). Sur mobile, la fiche de l'équipe (touche son nom) donne la même explication.
- **Tapotement ou survol** d'une équipe : celles qu'elle a déjà rencontrées sont barrées. Sur mobile,
  tapoter son numéro de rang épingle la sélection ; retapoter la même équipe l'enlève.
- Les zones viennent de `data/rules.json`, avec deux portées possibles pour un `tiers` :
  - **`division`** (le cas par défaut, ex. U13) : rangs *toutes poules confondues* de la division.
    `last: 4` = les 4 derniers ; `from_pct`/`to_pct` = un pourcentage de l'effectif (utile quand le
    nombre d'équipes n'est pas fixé à l'avance, ex. « le meilleur quart »).
  - **`scope: "poule"`** (ex. U10-U11) : rang *à l'intérieur de chaque poule*, indépendamment des
    autres — utile quand la règle est « les 2 premiers de chaque poule montent », quelle que soit sa
    taille. `to: "end"` désigne la dernière place de la poule.
  - **`groups`** : une même phase peut mélanger les deux portées, une règle par sous-ensemble de
    poules reconnu par un motif sur leur libellé (ex. poules « Élite » vs poules « Géographique »).
  Une catégorie sans règle s'affiche sans zones. **Tant qu'aucun score n'est publié dans une poule**,
  aucune barre de couleur ne s'affiche (la légende reste visible, pour savoir ce que chaque couleur
  voudra dire une fois les premiers matchs joués).
- **Qualification assurée vers un tour ou une phase sans poule connue** (ex. Festival U13 avant que le
  tour suivant n'existe sur le site) : ajoute `"next_date"` (et `"next_label"`, optionnel) à la règle de
  la phase/du tour concerné dans `rules.json`. Une équipe dont la qualification est assurée ET qui n'a
  plus de match connu affiche alors cette date comme prochain rendez-vous (sans adversaire ni lieu,
  puisqu'inconnus), dans l'onglet Mes équipes et sur sa fiche — pas d'export agenda pour cette entrée,
  ce n'est pas encore un match confirmé.
- `python scraper/check_rules.py` vérifie que montées + descentes + maintiens correspondent aux
  compositions annoncées de chaque division à la phase suivante (uniquement les règles à portée
  division avec un effectif chiffré).

## Mise en route du scraper

```bash
pip install -r scraper/requirements.txt
playwright install chromium                  # le site refuse les requêtes automatiques simples
python scraper/tests/test_scraper.py         # tests sans réseau
```

Toutes les commandes se lancent **depuis la racine du projet** : `python scraper/scrape.py …`.

### Premier lancement, dans cet ordre

1. `python scraper/scrape.py discover` : liste les clubs de chaque catégorie, lit leurs calendriers
   complets, archive tous les plateaux et télécharge les logos. Prévois du temps (plusieurs centaines de
   pages, avec une pause d'une seconde entre chacune).
2. `python scraper/scrape.py glyphs` : apprend les chiffres des scores, **en une seule passe** (voir plus
   bas). Pas besoin de relancer `sync --all` ensuite.
3. `python -m http.server`, puis http://localhost:8000.

### Mise à jour courante

| Commande | Ce qu'elle relit |
|---|---|
| `python scraper/scrape.py sync` | uniquement les plateaux **déjà joués dont les scores sont incomplets** (et les nouveaux plateaux connus). Ignore les plateaux aux scores complets et ceux pas encore joués. |
| `python scraper/scrape.py sync --dry-run` | ne télécharge rien : liste ce qui serait relu |
| `python scraper/scrape.py sync --discover` | lit d'abord les calendriers des clubs pour trouver les **nouveaux plateaux** (à faire une fois par jour), puis comme `sync` |
| `python scraper/scrape.py sync --upcoming` | relit aussi les plateaux à venir (détecte un changement d'horaire ou de lieu) |
| `python scraper/scrape.py sync --all` | relit tout (rarement utile) |

Un plateau joué depuis plus de 14 jours (`rescan_window_days` dans `config.json`) et toujours sans score
(annulé, forfait…) n'est plus relu, sauf avec `--all`. Si `sync` signale des chiffres non reconnus, lance
`glyphs`. `add <url>` archive un plateau précis, `logos` complète les logos manquants.
Si `sync` signale des chiffres non reconnus, relance simplement `glyphs`.

### Accès refusé (HTTP 403) et fenêtres Chromium

Le site refuse les requêtes qui ne viennent pas d'un navigateur. Le scraper essaie `requests`, puis bascule
sur Chromium (Playwright) : un seul onglet réutilisé pour toute la session, sans images ni polices, en mode
« nouveau headless » (aucune fenêtre). Si le site refuse aussi ce mode, mets dans `scraper/config.json`
`"browser_headless": false` : une seule fenêtre s'ouvre, placée hors de l'écran (`"browser_offscreen": false`
pour la voir). `"fetch_mode"` : `auto`, `requests` ou `browser`.

### « Sync API inside the asyncio loop » (tâche planifiée, NAS…)

Certains environnements (task scheduler d'un NAS, service, cron selon le système) peuvent perturber l'API
synchrone de Playwright, qui refuse de démarrer si elle détecte une boucle asyncio active. Ce n'est pas à
régler de ton côté : Chromium tourne toujours dans un **sous-processus** dédié (`python scrape.py _pwworker`,
lancé automatiquement, jamais à la main), qui ne partage rien avec le processus principal — ni thread, ni
état asyncio. C'est la protection la plus forte possible : même un thread séparé peut, selon l'environnement,
hériter de quelque chose d'inattendu ; un sous-processus, jamais.

### Les scores sont des images

Chaque chiffre d'un score est une image (`/scores/<hachage>.png`) dont le nom change d'une image à l'autre
(deux « 0 » n'ont pas le même nom) : le scraper reconnaît le **dessin**. Quand un plateau contient un
chiffre inconnu, ses empreintes sont gardées dans `data/raw/`, et `glyphs` s'en sert :

```bash
python scraper/scrape.py glyphs            # apprend les chiffres inconnus et relit tout, hors ligne
python scraper/scrape.py glyphs --review   # si des plateaux sont « INCOHÉRENTS » : corrige un chiffre mal étiqueté
```

`glyphs` regroupe **toutes** les images inconnues par ressemblance et te demande le chiffre de chaque groupe,
le plus fréquent d'abord (typiquement une dizaine de questions en tout). Puis il relit tous les plateaux
concernés **sans aucune requête** et recoupe chacun avec le mini-classement de sa page (buts pour, buts
contre, matchs joués) : sur la page du 19 septembre, les scores doivent se lire `0-5`, `4-0`, `4-2`. Un
chiffre mal étiqueté est détecté par ce contrôle : le plateau est publié sans score plutôt qu'avec un faux
score, et `glyphs --review` te redemande les chiffres concernés. Commite `scraper/glyphs.json`.

Les plateaux archivés avant ce mécanisme sont relus une seule fois, en ligne, par `glyphs` lui-même.

### Trouver tous les plateaux

La page d'un club (`?fal_id=…&type=fa&clNo=…&clCod=…&checkDate=false`) liste tous les plateaux où il joue,
journées passées comprises. `discover` parcourt les clubs de chaque catégorie (couples `clNo`/`clCod` et noms
mémorisés dans `scraper/teams.json`, à commiter). Les identifiants de plateau sont cherchés partout dans la
page ; s'ils n'y sont pas, le scraper clique sur les plateaux comme un visiteur. Si rien n'est trouvé,
`scraper/recon/calendar_debug.txt` contient ce qu'il faut pour corriger.

Pour la phase 3 : ajoute les nouvelles catégories dans `scraper/config.json` puis `discover --refresh-teams`.

### Logos des clubs

Le logo d'un club est `…/phlogos/BC<code du club>.jpg` ; le code (`clCod`) est dans la liste des clubs, et une
équipe (« F.C. AUBIEROIS 1 ») retrouve son club par son nom. Chaque logo est téléchargé une fois dans
`data/logos/` et partagé par les équipes du club ; une réponse qui n'est pas une vraie image est refusée.
Sans logo, le site affiche les initiales.

### Le Festival U13 et le module Compétitions

Le Festival U13 (« jusqu'à la finale départementale ») est branché, sur une base différente du reste :
les catégories `football-animation-et-loisirs` (U10-U11, U13 D1/D2/Brassage) sont lues en scrapant des
pages HTML ; le Festival vit dans le module **Compétitions** du site (`foot63.fff.fr/competitions?id=…`),
qui s'appuie sur une véritable **API JSON** (`api-dofa.fff.fr`), trouvée grâce à `scrape.py recon-cup`.
C'est en fait plus simple à lire : pas de Chromium nécessaire, pas de chiffres-images à reconnaître (les
scores sont des nombres), et le lieu du terrain est donné directement.

Une catégorie de ce type se déclare dans `scraper/config.json` avec `"kind": "cup"` et le numéro de la
compétition (`cp_no`, visible dans l'URL `?id=…`) au lieu d'un `fal_id` :
```json
{ "id": "u13-festival", "label": "Festival U13", "group": "u13", "kind": "cup", "cp_no": 459205 }
```
`python scrape.py sync` relit alors automatiquement toutes ses poules à chaque passage (des appels JSON
légers, sans Chromium — pas besoin d'attendre `--discover`). Chaque journée d'une poule devient un
« plateau » comme les autres, avec les mêmes favoris, alertes et export agenda. Une particularité : le
Festival ne publie pas toujours l'horaire à l'avance, auquel cas le site affiche « heure à confirmer »
plutôt qu'un horaire inventé.

Si le district ajoute d'autres compétitions de ce module (un autre festival, une coupe), il suffit
d'ajouter une entrée `"kind": "cup"` de plus avec son `cp_no`.

## Sécurité, si tu publies ce dossier tel quel

Le site est entièrement statique (pas de serveur, pas de connexion, pas de formulaire qui envoie des
données) : il n'y a pas de faille classique d'application web à proprement parler. Quelques points à
connaître avant de le mettre en ligne :

- **Aucun secret dans le dossier.** Ni clé, ni mot de passe, ni jeton : `scraper/config.json` ne contient
  que des réglages publics (identifiants de compétition, délais). Rien à retirer avant publication de ce
  côté-là.
- **Les données affichées sont déjà publiques** (résultats, calendriers, clubs) : `data/raw/` n'est qu'un
  historique de ce qui est déjà visible sur foot63.fff.fr, sans aucune donnée nominative (aucun nom de
  joueur n'est jamais collecté).
- **Tout ce qui vient des pages scrapées est échappé** avant d'être affiché (noms d'équipe, lieux…), pour
  éviter qu'un contenu inattendu ne s'exécute comme du code dans le navigateur.
- **HTTPS est nécessaire**, pas juste recommandé : les notifications et le mode hors-ligne (service worker)
  ne fonctionnent que sur une page servie en HTTPS (ou en local via `localhost`). La plupart des
  hébergeurs statiques (GitHub Pages compris) l'activent par défaut.
- **Les sujets ntfy sont publics** (n'importe qui connaissant le nom du sujet peut s'y abonner ou y
  publier) : change le préfixe dans `scraper/config.json` si tu veux limiter les curieux, voir « Alertes »
  plus haut.
- **Si tu copies tout le dossier sur un hébergeur qui sert n'importe quel fichier du répertoire** (FTP,
  hébergement mutualisé…), les gens pourront aussi télécharger `scraper/*.py`, `data/raw/`,
  `scraper/teams.json`, etc. Rien n'y est sensible, mais si tu préfères ne montrer que le site, ne publie
  que `index.html`, `assets/`, `manifest.webmanifest`, `sw.js` et `data/` (sans `data/raw/`) — garde le
  reste (`scraper/`, `.github/`) uniquement dans ton dépôt ou ton poste. Avec GitHub Pages, c'est différent :
  seul ce que tu publies dans les réglages Pages est servi, même si le dépôt contient plus (mais si le
  dépôt est public, tout son contenu reste visible sur GitHub lui-même).
- Le site ne fait ni suivi ni analytics ; le seul stockage côté visiteur est `localStorage` (favoris, thème),
  jamais transmis nulle part.

## Publier

- **GitHub Pages** : pousse ce dossier, active Pages sur `main` (racine). Le workflow
  `.github/workflows/scrape.yml` actualise les données toutes les 30 min le week-end.
  Attention : les serveurs de GitHub ont des adresses de centre de données, que certains sites
  refusent même avec un vrai navigateur. Si le workflow échoue en 403…
- **…lance-le chez toi** : `scraper/run_local.sh` (à mettre dans un `cron`) actualise, commite et pousse
  `data/` depuis ton poste ou un serveur à toi.

## Alertes

- **Page ouverte ou installée** : le site relit `data/changes.json` toutes les 2 minutes et notifie les
  changements des équipes suivies (bouton dans l'onglet Alertes).
- **Téléphone, page fermée** : le scraper publie sur [ntfy](https://ntfy.sh) un sujet par équipe
  (`foot63-<nom-de-l-equipe>`). Ces sujets sont publics (il ne passe que des scores) : change le préfixe
  dans `scraper/config.json` par quelque chose de personnel.

## À valider

- **Parseur (animation)** : testé sur la vraie page d'exemple (U13 D2) fournie par l'utilisateur ; déployé et
  vérifié en conditions réelles (NAS, tâche planifiée).
- **Parseur (Festival U13 / module Compétitions)** : testé sur les vraies réponses de l'API, capturées avant
  le début de la compétition (donc sans aucun score) — le calcul des scores et le format sont couverts par un
  test synthétique séparé, à confirmer une fois les premiers résultats publiés. Pagination de l'API
  (`?page=N`) non gérée au-delà de la première page ; à ajouter si une poule s'avère en compter plusieurs.
- **Règles** : barème 3-1-0 et départage à confirmer avec le règlement. U10-U11 : seule la phase 1 (brassage)
  est réglée ; la suite (Élite/Géographique) attend de connaître le libellé exact des poules de phase 2.
- **Journées** : si le site n'indique pas le numéro de journée, il est déduit de la semaine du plateau.
- **Données** : uniquement clubs, équipes, horaires, lieux et scores, aucun nom de joueur.

