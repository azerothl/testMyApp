# SPEC — TestMyApp

> **Nom de code** : TestMyApp · **Version de spec** : 1.0.2 · **Date** : 19 novembre 2026
> **Slogan** : *Un laboratoire de test où des personas en pixel art testent vos applications à votre place — et vous rendent un rapport complet.*

---

## Table des matières

1. [Vision et objectifs](#1-vision-et-objectifs)
2. [Périmètre](#2-périmètre)
3. [Terminologie](#3-terminologie)
4. [Personas](#4-personas)
5. [Modèle émotionnel](#5-modèle-émotionnel)
6. [Cibles et drivers de test](#6-cibles-et-drivers-de-test)
7. [Moteur d'orchestration](#7-moteur-dorchestration)
8. [Mesures et scoring](#8-mesures-et-scoring)
9. [Rapport de test](#9-rapport-de-test)
10. [Interface TUI](#10-interface-tui)
11. [Interface graphique (GUI pixel art)](#11-interface-graphique-gui-pixel-art)
12. [Architecture logicielle](#12-architecture-logicielle)
13. [Formats de données](#13-formats-de-données)
14. [CLI et commandes](#14-cli-et-commandes)
15. [Exigences non fonctionnelles](#15-exigences-non-fonctionnelles)
16. [Sécurité, éthique et conformité](#16-sécurité-éthique-et-conformité)
17. [Outils et assets (chaîne de production)](#17-outils-et-assets-chaîne-de-production)
18. [Risques et mitigations](#18-risques-et-mitigations)
19. [Roadmap](#19-roadmap)
20. [Critères d'acceptation](#20-critères-dacceptation)
21. [Glossaire](#21-glossaire)
- [Annexe A — Exemples de fichiers](#annexe-a--exemples-de-fichiers)
- [Annexe B — Catalogue des émotions](#annexe-b--catalogue-des-émotions)
- [Annexe C — Catalogue d'événements du bus](#annexe-c--catalogue-dévénements-du-bus)

---

## 1. Vision et objectifs

### 1.1 Problème

Les tests automatisés classiques (unitaires, E2E) vérifient qu'une application **fonctionne**, mais pas qu'elle est **agréable, compréhensible et performante pour de vrais profils d'utilisateurs**. Les tests utilisateurs réels sont coûteux, lents et difficiles à répéter. Les rapports de tests automatisés sont pauvres : ils disent « échec à l'étape 3 », pas « les novices abandonnent le tunnel d'achat parce que le bouton de paiement est introuvable et la page met 4 s à charger ».

### 1.2 Vision

TestMyApp est un **laboratoire de test par personas simulés**. L'utilisateur définit (ou génère) des personas — profils d'utilisateurs fictifs avec leur personnalité, leur niveau technique, leurs contraintes (matériel, réseau, accessibilité) et leurs objectifs. Ces personas *exécutent* des scénarios sur une application cible — **webapp via un vrai navigateur piloté, ou client lourd via l'automatisation d'UI** — en se comportant comme leur profil l'indique : un novice hésite, fait des fautes de frappe, cherche de l'aide ; un expert va vite et directement au but.

Pendant la campagne, les personas sont **incarnés par des personnages pixel art visibles dans un espace (le « Test Floor »)** : ils marchent vers leurs stations de test, travaillent, et des **émotions apparaissent au-dessus de leur tête** selon leur satisfaction en temps réel. À la fin, TestMyApp produit un **rapport détaillé** : points positifs, points négatifs, performances mesurées, accessibilité, et recommandations priorisées.

### 1.3 Objectifs quantifiés

| # | Objectif | Mesure de succès |
|---|----------|------------------|
| O1 | Tester des webapps dans de vrais navigateurs | Parcours multi-étapes avec saisies, clics, navigation ; captures et traces |
| O2 | Tester des clients lourds Windows | Lancement, interactions UIA, détection de crashes et de freezes |
| O3 | Personas différenciés | Le même scénario produit des résultats mesurablement différents selon le persona (temps, erreurs, abandons) |
| O4 | Retour visuel vivant | TUI et GUI temps réel ; émotions affichées < 500 ms après l'événement déclencheur |
| O5 | Rapport actionnable | Rapport complet généré < 30 s après la fin de campagne ; chaque constat lié à une preuve (capture, métrique, log) |
| O6 | Reproductibilité | Une campagne relancée avec la même graine produit la même séquence de décisions |
| O7 | Zéro dépendance obligatoire à un service externe | Fonctionne 100 % en local ; l'IA (optionnelle) améliore mais ne conditionne rien |

### 1.4 Ce que TestMyApp n'est pas

- **Pas un outil de test de charge** : pas de requêtes massives, pas de flood. Les personas simulent des humains, un à un, à vitesse humaine (configurable).
- **Pas un scanner de sécurité / pentest** : aucune recherche de vulnérabilités.
- **Pas un remplacement des tests unitaires/integration** : c'est la couche « UX simulée » au-dessus.
- **Pas un outil pour tester des applications sans autorisation** : voir §16.

---

## 2. Périmètre

### 2.1 Inclus (v1.0)

| Domaine | Détail |
|---|---|
| Cibles web | Webapps locales ou distantes, servies en HTTP(S), pilotées via Playwright (Chromium, Firefox, WebKit) |
| Cibles desktop | Applications Windows (Win32, WinForms, WPF, Qt partiel, Electron via CDP) via UI Automation ; repli par reconnaissance d'image (template matching) |
| Personas | Bibliothèque embarquée (8 archétypes), création assistée, génération contrainte, import/export TOML |
| Scénarios | Scriptés (pas à pas) + exploratoire pondéré + exploratoire guidé par LLM (optionnel, local via Ollama ou API compatible OpenAI) |
| Mesures | Fonctionnel, performance (web + desktop), UX simulée, accessibilité (WCAG 2.2 AA), stabilité |
| Rapport | Markdown, HTML autonome, JSON ; comparaison de campagnes |
| Interfaces | TUI (Textual) + GUI locale web (canvas pixel art) + mode headless (CI) |

### 2.2 Exclus (v1.0, candidats v2)

- Mobile natif (Appium) — v2.
- Desktop macOS (AX API) et Linux (AT-SPI) — v2.
- Multi-écrans / multi-instances concurrentes d'un même client lourd — v2.
- Serveur central / mode SaaS multi-utilisateur — hors périmètre.
- Son spatialisé, sauvegarde vidéo du Test Floor — v1.1 au plus tôt.

---

## 3. Terminologie

| Terme | Définition |
|---|---|
| **Persona** | Profil d'utilisateur fictif complet : identité, capacités, matériel, comportement, objectifs. Représenté par un sprite pixel art. |
| **Cible** | L'application sous test : une webapp (URL + navigateur + conditions) ou un client lourd (chemin d'exécutable + fenêtre). |
| **Scénario** | Séquence d'étapes décrivant un parcours utilisateur (ex. « commander un produit »). |
| **Étape** | Unité d'un scénario : intention + actions + attentes (assertions). |
| **Action** | Interaction élémentaire (clic, saisie, navigation…) exécutée par le driver, modulée par le persona (délais, erreurs). |
| **Observation** | Donnée collectée pendant l'exécution (métrique, log console, capture, violation a11y…). |
| **Verdict** | Résultat d'une attente : `pass`, `fail`, `warn`, `skip`. |
| **Session** | Exécution d'un scénario par un persona sur une cible. Unité de base du rapport. |
| **Campagne** | Ensemble de sessions (matrice personas × scénarios × cibles) lancée d'un coup. |
| **Humeur** | Valeur de valence glissante d'un persona (−5 à +5), alimentée par les événements. |
| **Émotion** | Représentation affichée de l'humeur / d'un événement ponctuel (sprite au-dessus de la tête). |
| **Test Floor** | Espace 2D du GUI où les personas se déplacent entre stations de test. |
| **Station** | Emplacement du Test Floor où un persona travaille (un écran = une cible/session). |
| **Rapport** | Document généré en fin de campagne : constats, métriques, recommandations. |
| **Bus d'événements** | Flux typé d'événements émis par le moteur, consommé par TUI, GUI, reporter et enregistreur. |

---

## 4. Personas

### 4.1 Modèle de persona (schéma complet)

Un persona est décrit dans un fichier TOML (voir Annexe A). Champs :

**Identité**
- `id` (slug unique), `name`, `pronouns`, `age`, `occupation`, `bio` (2–3 phrases)
- `sprite.base` (corps de base parmi la bibliothèque), `sprite.palette` (4–6 couleurs nommées), `sprite.accessories` (liste : lunettes, casque, canne, tasse…)
- `locale`, `timezone`

**Capacités et contraintes d'accessibilité**
- `a11y.vision` : `normal` | `low` | `blind` | `deuteranopia` | `protanopia` | `tritanopia`
- `a11y.motor` : `normal` | `tremor` (clics imprécis) | `keyboard_only` (pas de souris)
- `a11y.reading` : vitesse de lecture (mots/min), dyslexie (booléen — influence hésitations)
- `a11y.screen_reader` : booléen (le persona « vérifie » les labels ARIA au lieu du rendu visuel)

**Matériel et environnement**
- `device.type` : `desktop` | `laptop` | `tablet` | `phone` (viewport + touch)
- `device.screen` : largeur × hauteur, DPR
- `device.input` : `mouse` | `trackpad` | `touch` | `keyboard`
- `network.profile` : `fiber` | `dsl` | `4g` | `3g` | `slow-3g` | `offline-flaky` (throttling Playwright)
- `cpu.throttle` : multiplicateur de ralentissement CPU (1 = none, 4 = machine modeste)
- `browser` : moteur préféré + version majeure

**Personnalité et comportement** (échelles 0–1)
- `patience` — tolérance aux attentes (influence abandon vs attente)
- `curiosity` — probabilité d'explorer en dehors du parcours
- `tech_savviness` — 0 (grand débutant) à 1 (expert)
- `error_proneness` — probabilité de faute de frappe / misclick par action
- `persistence` — nombre de réessais avant abandon (dérivé : `1 + round(persistence × 4)`)
- `help_seeking` — tendance à chercher un libellé d'aide / un placeholder / un message d'erreur
- `speed.factor` — multiplicateur des délais de base (0.6 expert pressé → 2.0 personne âgée)
- `behavior.on_error` : `retry` | `explore` | `ask_help` | `abandon` (pondérations)

**Objectifs**
- `goals.primary[]`, `goals.secondary[]` — phrases en langage naturel utilisées par l'exploratoire et le mode IA
- `knowledge[]` — concepts que le persona connaît déjà (ex. « panier », « 2FA »)
- `misconceptions[]` — croyances erronées (l'IA exploratoire peut les jouer : chercher « Commander » dans la barre de menu alors que c'est un bouton)

**Émotions**
- `emotion.bias` : décalage de base de l'humeur (−1 « de mauvaise humeur » à +1 « enthousiaste »)
- `emotion.volatility` : amplificateur des variations (0.5 zen ↔ 1.5 hypersensible)

### 4.2 Bibliothèque embarquée : 8 archétypes

| Persona | Âge | Profil clé | Ce qu'il révèle |
|---|---|---|---|
| **Nadine, retraitée curieuse** | 68 | tech_savviness 0.1, speed 2.0, low vision, souris | Lisibilité, contrastes, vocabulaire, tailles de cibles |
| **Éric, développeur** | 34 | tech_savviness 1.0, speed 0.7, clavier | Raccourcis, densité d'information, vue « expert pressé » |
| **Paula, cadre pressée** | 41 | patience 0.2, persistence 1, fiber | Friction du parcours critique, nombre d'étapes, vitesse perçue |
| **Marc, méfiant** | 52 | curiosity 0.2, help_seeking 1 | Clarté des prix/CGV/confiance, messages de confirmation |
| **Dany, daltonien** (deutéranopie) | 29 | vision deuteranopia | Code couleur exclusif, contrastes, états des boutons |
| **Karim, clavier seul** | 26 | keyboard_only, screen reader partiel | Focus visible, ordre de tabulation, pièges clavier |
| **Mona, mobile seule** | 23 | phone, touch, 4G | Viewport, cibles tactiles ≥ 44 px, poids des pages |
| **Alice, admin métier** | 47 | usage intensif, données volumineuses | Tables denses, filtres, performance avec beaucoup de données |

Chaque archétype est fourni en TOML, éditable et clonable. Une commande `testmyapp persona generate` crée des variants (âge, métier, humeur) autour d'un archétype.

### 4.3 Création et gestion des personas

- **Assistant TUI/GUI** : formulaire guidé, aperçu du sprite en direct (corps × palette × accessoires), bouton « tester en 30 s » (lance un mini-scénario de smoke test).
- **Génération contrainte** : `testmyapp persona generate --archetype novice --count 5 --seed 42` — variations aléatoires reproductibles des paramètres comportementaux.
- **Import/export** : TOML (format canonique) ; export PNG de la fiche (sprite + paramètres clés) pour documentation.
- **Validation** : JSON Schema versionné ; avertissements sur combinaisons incohérentes (ex. `blind` + `device.input mouse`).

### 4.4 Modes de comportement (comment un persona « décide »)

Un scénario peut mixer trois modes, par étape :

**M1 — Scripté** (par défaut). Les étapes sont définies ; le persona module l'exécution :
- délais inter-actions = base(étape) × `speed.factor` × bruit lognormal (graine),
- fautes de frappe simulées si `error_proneness` (frapper « demlle » puis corriger),
- misclicks (clic à 2–6 px, puis re-ciblage),
- hésitations avant les actions si `tech_savviness` bas (mouvement de souris « exploratoire » vers les libellés),
- lecture : pause proportionnelle à la densité de texte de la page ÷ vitesse de lecture.

**M2 — Exploratoire pondéré** (sans script). But en langage naturel (`goals.primary`) ; boucle percevoir → choisir → agir :
- perception : DOM accessible (rôle, libellé, visibilité, distance visuelle),
- choix : scoring d'affinité entre éléments perçus et le but (similarité de libellé, connaissances/méconnaissances du persona, saillance visuelle — position, taille, couleur), pondéré par `curiosity` (détours) et `help_seeking` (préférer les éléments d'aide après une erreur),
- arrêt : but atteint (assertion du but satisfaite), abandon (patience épuisée), ou budget d'étapes consommé.

**M3 — Agent IA (optionnel, désactivé par défaut).** Le même budget percevoir/choisir est confié à un LLM local (Ollama) ou distant (API compatible OpenAI), prompté avec la fiche du persona (personnalité, connaissances, méconnaissances) et un instantané compact de la page (accessibility tree tronqué). Le LLM propose l'action suivante + une justification courte (affichée en bulle). Repli automatique sur M2 si l'IA est indisponible ou hors budget. Coût et latence tracés dans le rapport.

### 4.5 Simulation d'erreurs humaines (détail)

| Simulation | Paramètre | Effet mesurable |
|---|---|---|
| Faute de frappe | `error_proneness` | Saisie erronée + correction → temps de complétion ↑ |
| Misclick | `error_proneness` × inverse `tech_savviness` | Clic à côté → action ratée, parfois élément voisin activé |
| Mauvais onglet / menu | `tech_savviness` bas | Détour de navigation → profondeur de parcours ↑ |
| Abandon | `patience`, `persistence` | Session marquée `abandoned` — signal UX fort |
| Recours à l'aide | `help_seeking` | Interaction avec tooltips/FAQ → les parcours où l'aide est introuvable échouent |
| Double-clic impulsive | `curiosity` haut + `patience` bas | Peut révéler absence de protection double-soumission |

---

## 5. Modèle émotionnel

### 5.1 Principe

L'humeur `mood ∈ [−5, +5]` d'un persona évolue par événements. Chaque verdict/observation contribue une **valence** `v` (pondérée par `emotion.volatility` et `emotion.bias`). L'humeur est une moyenne mobile exponentielle :

```
mood ← clamp(mood + α × (v_total − mood), −5, +5)   avec α = 0.3 (ajustable)
```

En parallèle, certains événements déclenchent une **émotion ponctuelle** (affichée immédiatement) indépendante de l'humeur.

### 5.2 Valence des événements

| Événement | Valence v |
|---|---|
| Étape réussie rapide (< seuil persona) | +2 |
| Étape réussie normale | +1 |
| Étape réussie lente (> 2× seuil) | −1 |
| Avertissement (`warn`) | −1 |
| Échec d'étape (`fail`) | −2 |
| Erreur console / requête en erreur | −1 (plafonné à −2 par page) |
| Crash ou freeze de la cible | −4 |
| Violation d'accessibilité touchant le persona | −2 (plafonné à −4 par page) |
| Découverte hors parcours réussie (curiosité récompensée) | +2 |
| Objectif final atteint | +3 |
| Abandon | −5, émotion `colère` ou `abandonné` |
| Attente > 3 s (spinner) | −1 par tranche, émotion `impatient` |

### 5.3 Émotions affichées

Le mapping humeur → émotion de fond (badge persistant sous le nom) :

| Humeur | Émotion de fond |
|---|---|
| ≥ +3 | `radieux` |
| +1 … +3 | `content` |
| −1 … +1 | `neutre` |
| −3 … −1 | `agacé` |
| < −3 | `furieux` |

Émotions ponctuelles (bulle, priorité sur le badge) : `célébration`, `confus`, `surpris`, `fatigué`, `impatient`, `eureka` (découverte), `dort` (inactif > 30 s), `abandonné`. Catalogue complet des sprites et animations : Annexe B.

### 5.4 Exigences d'affichage

- Latence événement → émotion à l'écran : **< 500 ms**.
- Bulle : apparition « pop » (2 frames), tenue 1,5–3 s selon intensité, fondu.
- Anti-spam : cooldown de 2 s ; les événements répétés fusionnent (badge « ×3 » sur la bulle).
- L'émotion est **toujours doublée d'un libellé texte** (tooltip, TUI, accessibilité GUI) — jamais la couleur seule.
- Inactivité > 30 s → `dort` ; reprise → `sursaut`.

---

## 6. Cibles et drivers de test

### 6.1 Architecture des drivers

Un driver est un plugin implémentant le protocole : `launch(target, persona) → Session`, `perform(action) → ActionResult`, `observe() → Observations[]`, `snapshot() → PageState`, `screenshot()`, `stop()`. Les drivers n'implémentent **aucune** logique de décision (c'est le rôle du moteur) ni de scoring (rôle du reporter).

### 6.2 Driver Web (Playwright)

**Lancement**
- Navigateurs : Chromium (défaut), Firefox, WebKit. `--browser` par cible.
- Contexte par persona : viewport/DPR/touch selon `device`, locale, timezone, permissions (géolocalisation, notifications), `color_scheme` (light/dark/forced-colors pour simuler les modes contraste élevé).
- Throttling : réseau (`network.profile` → latency/débit/perte de paquets) + CPU (`cpu.throttle`).
- Authentification : storage state (cookies/session) par persona, ou formulaire de login joué comme étape.

**Actions supportées** : `navigate`, `click`, `dblclick`, `hover`, `type` (avec fautes), `press` (clavier), `select`, `check`, `upload`, `download`, `scroll`, `drag`, `wait_for` (selector/état), `screenshot`, `dialog` (accept/dismiss).

**Observations collectées (continu, via CDP)**
- Console : messages, erreurs, exceptions non capturées.
- Réseau : chaque requête (URL, méthode, statut, taille, timings DNS/TTFB/total), échecs, 4xx/5xx, requêtes bloquantes.
- Performance : **Core Web Vitals** — LCP, CLS, INP (+ FCP, TTFB) — par navigation ; métriques de ressources ; long tasks.
- Page : titre, URL, nombre de nœuds DOM, images sans dimensions (CLS), formulaires sans labels.
- Accessibilité : snapshot axe-core (rules `wcag2a/2aa/21aa/22aa`) par page visitée + focus order (tab simulation) pour les personas `keyboard_only`.
- Enregistrements : screenshot par étape (avant/après), trace Playwright (activation par campagne), vidéo (option).

**Sélecteurs — stratégie de résilience** : ordre de résolution `role+name` → `label` → `text` → `test-id` → `css` ; chaque étape scriptée peut indiquer sa préférence. Un sélecteur introuvable déclenche le comportement `on_error` du persona (recherche visuelle, aide) avant de déclarer `fail` — c'est précisément ce que le rapport doit capturer (« le bouton existe mais n'est pas trouvé par un novice »).

### 6.3 Driver Desktop (Windows d'abord)

**Automatisation** (prioritaire) : UI Automation via COM (`FlaUI`/`pywinauto`). Capture de la fenêtre principale, arbre UIA (types de contrôle, noms, patterns), actions `invoke`, `set_value`, `select`, `focus`, `hotkey`, `menu`.

**Repli image** (apps canvas/jeux/rendu custom) : template matching OpenCV sur captures d'écran ; templates déclarés dans la cible (`targets/*.toml` : image + zone + tolérance) ; actions = clic aux coordonnées du match, saisie via événements système.

**Electron** : détection automatique (process + flag `--remote-debugging-port`), pilotage hybride CDP + UIA.

**Observations desktop**
- Temps de démarrage à fenêtre responsive (mesuré, seuil configurable).
- Temps de réponse par action (injection → état UI changé).
- Frozen UI : fenêtre « ne répond pas » (polling de l'état du thread UI), compteur et durée.
- Crash : sortie de processus inattendue, code de sortie, dump Windows Error Reporting si présent.
- Ressources : CPU %, mémoire (working set privé), I/O — échantillonnées à 1 Hz via `psutil` sur le processus cible **et ses enfants**.
- Écran : screenshot par étape.

**Multi-plateforme** : v1 Windows uniquement ; interfaces du driver conçues pour ajout macOS (AX) / Linux (AT-SPI) en v2 sans changement du moteur.

### 6.4 Accessibilité (transverse)

- Web : axe-core injecté (règles WCAG 2.2 AA) + vérifications propriétaires : focus visible (persona clavier), cibles tactiles ≥ 44 px (persona tactile), contrastes calculés sur styles calculés (persona malvoyant/daltonien — simulation des couleurs via filtres de daltonisme sur les captures pour le rapport).
- Desktop : arbre UIA — nom accessible manquant, rôle incorrect, raccourcis non documentés, focus désordonné.
- Alignement méthodologique : WCAG-EM (échantillonnage de pages/flux), sévérité `critical/serious/moderate/minor`, base de preuve `vérifié / signalé / humain-requis` — même grille que le skill `accessibility-audit` installé dans l'environnement (§17).

---

## 7. Moteur d'orchestration

### 7.1 Hiérarchie d'exécution

```
Campagne (campaign.toml, --seed)
 ├─ Session 1 = persona × scénario × cible
 │   ├─ Étape 1 … N (scriptées et/ou exploratoires)
 │   │   ├─ Action(s) … (modulées par le persona)
 │   │   ├─ Observations (continues)
 │   │   └─ Verdict(s)
 │   └─ Résultat de session (réussite/échec/abandon + humeur finale)
 └─ Session M (exécution parallèle configurable)
```

- **Async** : chaque session est une tâche asyncio ; parallélisme = `campaign.max_workers` (défaut 3 web, 1 desktop par machine) ; isolation stricte (un contexte navigateur / une instance d'app par session).
- **Vitesse** : `--speed realtime` (délais humains complets) | `fast` (×0.3) | `turbo` (délais minimaux, toujours ordonnés) — le mode turbo réduit la valeur des métriques UX mais accélère la régression fonctionnelle ; le rapport l'indique.
- **Reproductibilité** : toutes les décisions aléatoires passent par un PRNG par session, dérivé de la graine de campagne (taskgetter : `seed(campaign, persona, scénario, index)`).
- **Reprise** : une campagne interrompue peut être relancée avec `--resume runs/<id>` (sessions terminées non rejouées, artifacts conservés).

### 7.2 Bus d'événements

Le moteur émet des événements typés horodatés (schéma Annexe C) sur un bus in-process (pub/sub) avec les garanties :

- **Ordre** : ordre total par session ; les consommateurs voient les événements d'une session dans l'ordre d'émission.
- **Persistance** : un consommateur « recorder » écrit `events.jsonl` (replay et audit) ; les métriques brutes vont dans `metrics.jsonl`.
- **Consommateurs** : TUI, GUI, recorder, live-reporter (rapport préliminaire), hooks plugins.

```
 moteur ──► bus ──┬── TUI (rendu terminal)
                  ├── GUI (WebSocket → canvas Test Floor)
                  ├── recorder (events.jsonl, metrics.jsonl, run.db)
                  └── plugins/hooks
```

### 7.3 Cycle de vie d'une étape

1. `step.started` — intention annoncée (bulle du persona : « Je cherche comment payer »).
2. Résolution de la cible de l'action (sélecteur / élément UIA / template) — inclut la « recherche visuelle » simulée du persona si échec.
3. Exécution des actions (délais, erreurs simulées).
4. Évaluation des attentes → verdict(s) `step.passed|failed|warned|skipped`.
5. Collecte des observations de fin d'étape (screenshot, métriques).
6. Mise à jour humeur/émotion → événements `persona.mood`, `persona.emotion`.

---

## 8. Mesures et scoring

### 8.1 Axes et métriques

**A. Fonctionnel** — par étape/scénario/session : taux de réussite, échecs, abandons, détours (profondeur de navigation vs baseline), corrections.

**B. Performance web** (moyennes + p75/p95 par page) : LCP, INP, CLS, FCP, TTFB ; poids transféré, nombre de requêtes ; latence API (requêtes XHR) ; long tasks > 50 ms.

**C. Performance desktop** : démarrage à responsive ; latence d'action p95 ; freezes (nombre, durée totale) ; CPU/mémoire p95 ; fuite mémoire (pente RSS sur session longue).

**D. UX simulée** : temps de complétion vs `baseline × speed.factor` ; hésitations ; recours à l'aide ; abandons par persona ; humeur finale moyenne ; « friction score » = actions superflues / actions minimales.

**E. Accessibilité** : violations par critère WCAG / sévérité / page ; taux de contraste minimal ; cibles tactiles non conformes ; ordre de focus problématique.

**F. Stabilité** : erreurs console/JS ; crashes ; requêtes échouées ; données perdues (soumission suivie d'erreur).

### 8.2 Seuils

Chaque métrique a un seuil configurable (`campaign.thresholds`, défauts fournis : LCP ≤ 2,5 s, CLS ≤ 0,1, INP ≤ 200 ms, démarrage desktop ≤ 3 s, action p95 ≤ 400 ms, contraste ≥ 4,5:1, cible tactile ≥ 44 px…). Un dépassement → `warn` (constat) ou `fail` (constat bloquant) selon la gravité configurée.

### 8.3 Score global

Score composite **0–100**, pondérations par défaut configurables :

```
global = 0.30·fonctionnel + 0.20·perf + 0.15·a11y + 0.20·UX + 0.15·stabilité
```

- Chaque axe est normalisé 0–100 (réussite pondérée par sévérité pour a11y ; ratios aux seuils, plafonnés, pour la perf).
- Sous-scores par persona, par scénario, par cible — visualisés en heatmap (TUI/GUI/rapport).
- Le score est un **résumé**, jamais la seule lecture : le rapport impose la lecture des constats qui le composent.

---

## 9. Rapport de test

### 9.1 Structure du rapport

Généré en fin de campagne (`report/`), toutes sections obligatoires :

1. **Synthèse exécutive** — score global, verdict d'un coup d'œil (badge), durée, personas × scénarios, 3 constats majeurs, top 5 recommandations.
2. **Points positifs** — liste des constats favorables, chacun : titre, preuve (métrique, étape, capture), personas concernés. Ex. : « Tunnel d'achat complété par 8/8 personas, dont Nadine (novice) ».
3. **Points négatifs** — idem, classés par sévérité ; chaque point cite ses preuves cliquables (capture, ligne de log, métrique).
4. **Détail par persona** — fiche (sprite, profil), scénarios joués, humeur moyenne/min/max, timeline d'émotions, difficultés rencontrées, citations de « bulles » marquantes.
5. **Détail par scénario** — taux de réussite, temps (min/moy/max par persona), points de friction (étapes les plus lentes/échouées), captures avant/après des échecs.
6. **Performances** — tableaux + graphiques (barres, percentiles, séries temporelles) web et/ou desktop ; comparaison aux seuils.
7. **Accessibilité** — violations groupées par critère WCAG 2.2 avec sévérité et preuves ; captures avec simulation daltonisme quand pertinent ; liste « humain-requis ».
8. **Stabilité** — erreurs, crashes, freezes.
9. **Recommandations** — voir 9.2.
10. **Annexes** — journal complet (events.jsonl résumé), traces Playwright, références des artifacts, graine et version de config (reproductibilité).

### 9.2 Recommandations

- **Générées par règles** : catalogue de règles motifs → recommandation (ex. `LCP > seuil sur page produit` → « Optimiser le chargement de la page produit : précharger l'image principale, différer les scripts tiers » ; `échec étape X par personas tech_savviness < 0.3` → « Simplifier l'étape X : le vocabulaire ou la hiérarchie visuelle perd les novices », avec preuves).
- **Priorisation** : `impact` (personas touchés × fréquence × sévérité) × `effort` (estimation S/M/L par règle) → P0 (bloquant) … P3 (polish). Matrice impact/effort dans le rapport.
- **Option IA** : reformulation/priorisation affinée par LLM (même option que M3), marquée « assistée par IA » ; les preuves restent obligatoirement liées aux données brutes.
- **Traçabilité** : chaque recommandation référence les constats (ids) qui la motivent ; chaque constat référence les événements bruts.

### 9.3 Formats et comparaison

| Format | Usage | Détails |
|---|---|---|
| `report.md` | lecture/versionner | images liées au dossier `report/assets/` |
| `report.html` | partage | **autonome** (CSS + charts inline, images base64) — ouvrable hors ligne |
| `report.json` | machine | schéma versionné, intégral (constats + métriques + recommandations) |
| `report.pdf` | partage et impression | export à la demande depuis le rapport HTML, captures intégrées et liens internes |
| `compare` | régression | `testmyapp compare runs/A runs/B` — diff des scores, métriques, violations, nouvelles recommandations |

---

## 10. Interface TUI

### 10.1 Base technologique

**Textual** (Python). Application unique `testmyapp run --tui`, thème sombre par défaut (pixel/labo), 100 % pilotable au clavier, souris supportée.

### 10.2 Écrans

1. **Dashboard** — état de la campagne : sessions en cours (arbre), scores live, humeur des personas (pastilles + emoji : 😀 🙂 😐 😕 😠), flux d'événements filtrable, sparklines de métriques clés.
2. **Vue Test Floor (TUI)** — rendu du laboratoire en caractères/ANSI : grille, stations `[WEB-1] [APP-2]`, personas représentés par leur sprite converti en demi-blocs ANSI 2×1 avec palette du persona, déplacements animés, émotions en emoji au-dessus (bulle `╭─╮`).
3. **Fiche persona** — paramètres, objectifs, humeur (jauge), progression de session, mini-log, dernier screenshot (rendu en half-blocks si terminal truecolor).
4. **Vue session** — étapes avec verdicts, timings, erreurs console, captures.
5. **Rapport** — aperçu du rapport en cours/fini, navigation par sections.
6. **Aide / raccourcis** — modale `?`.

### 10.3 Interactions clés

`lancer/pause/reprendre/arrêter` une campagne ; `filtres` (par persona, sévérité, type d'événement) ; `suivre un persona` (focus caméra) ; `export` du rapport ; `quitter proprement` (finalise les artifacts). Raccourcis affichés en pied d'écran. Densité d'information réglable (compact/normal/verbeux).

### 10.4 Exigences

- Fraîcheur d'affichage : 4 Hz min pour les listes, 10 Hz pour le floor ; aucun artefact sous redimensionnement ; dégradation propre si terminal < 100×30 (mode liste seule).
- Parité fonctionnelle totale avec la GUI (mêmes commandes du bus, mêmes données) — la seule différence est le rendu.

---

## 11. Interface graphique (GUI pixel art)

### 11.1 Choix d'implémentation

**GUI = application web locale** : le cœur démarre un serveur HTTP local (FastAPI + WebSocket) et ouvre la fenêtre via le navigateur par défaut **ou** une fenêtre dédiée (PyWebView, optionnel) — zéro installation pour l'utilisateur, rendu canvas performant, animations riches, style maîtrisé. URL : `http://127.0.0.1:7417`.

> Alternative documentée mais écartée en v1 : GUI Pygame native (plus « jeu », mais ergonomie générale — panneaux, listes, formulaires — beaucoup plus coûteuse à bien faire ; le web local obtient le même rendu pixel art sur `<canvas>` avec `image-rendering: pixelated`).

### 11.2 Le Test Floor (espace de vie)

Vue **top-down légèrement 3/4** (perspective RPG 16-bit) d'un laboratoire :

- **Sol** : tuiles pixel art (parquet + câbles + tapis), damier discret pour lire le mouvement ; décor : plantes, distributeur, tableau des scores, canapé, porte d'entrée.
- **Stations de test** : bureaux avec écran ; un écran = une session active ; l'écran affiche en miniature l'état de la cible (screenshot downscalé pixelisé rafraîchi 1 Hz) ; une station « web » a un logo navigateur, une station « desktop » un logo fenêtre.
- **Bureau rapport** : zone où les personas se rendent en fin de session pour « déposer » leurs constats (feedback visuel de l'accumulation du rapport : la pile de papier grandit).
- **Personas** : sprites **32×32** (rendus ×2 ou ×3, `image-rendering: pixelated`), palette propre + accessoires ; ombre portée ; direction 4 ou 8 facings ; animations : `idle` (2–4 frames, respiration/clignement), `walk` (4–6 frames), `work` (frappe au clavier, 4 frames), `celebrate` (saut + bras levés, 6 frames), `frustrated` (épaules tombées + tête basculée, 4 frames), `sleep` (4 frames, « zzz »), `enter/exit` (porte).
- **Déplacement** : grille de tuiles + pathfinding A* simple ; à l'affectation d'une session le persona marche de son point d'attente à sa station ; à la fin → bureau rapport → porte → disparaît. Vitesses et trajectoires légèrement désynchronisées pour la vie de la scène.
- **Cycle** : porte → canapé/attente → marche → travail → (fin) → rapport → sortie. Des personas en attente font des micro-activités (sieste, téléphone, café au distributeur).

### 11.3 Émotions et bulles

- **Bulle d'émotion** : sprite 16×16 dans une bulle blanche à bord noir, 1,5–3 s, pop-in scale 0.6→1 avec léger overshoot, fondu de sortie ; position au-dessus de la tête, décalage si plusieurs personas proches (anti-collision de bulles).
- **Badge d'humeur** : petite icône persistante sous le nom (fond de l'humeur).
- **Bulle de dialogue** : action/intention en texte court au-dessus (« Je cherche le bouton Payer… », « Encore un spinner ! »), police pixel (Web Open Font, sous-ensemble latin), tenue 4 s max, file de 1 (la nouvelle remplace l'ancienne).
- **Effets** : confettis pixel (succès d'étape), petites étoiles (découverte), fumée/éclair rouge (échec, crash), « zzz » (sommeil) — effets code style pixelfx (§17), honorant `prefers-reduced-motion` (remplacés par flash discret + badge).
- **Double accès** : chaque émotion/action est aussi annoncée dans le panneau d'activité et via `aria-live` (polite) — l'émotion n'est jamais portée par la seule image.

### 11.4 Panneaux et contrôles (ergonomie)

Layout trois zones (redimensionnables, mémorisés) :

```
┌───────────────────────────────┬────────────────────┐
│  TEST FLOOR (canvas plein)    │  PANNEAU DROIT     │
│  personas + stations + bulles │  ├ Persona sel.    │
│                               │  │ fiche/humeur    │
│  mini-cam : suivre un persona │  │ progression     │
│  zoom molette, drag pan       │  │ mini-log bulles │
├───────────────────────────────┤  ├ Métriques live  │
│ TIMELINE ─ événements filtr.  │  │ sparklines      │
│ contrôles : ▶ ⏸ ⏹ ⏩ seed     │  └ Score live      │
└───────────────────────────────┴────────────────────┘
```

- **Clic sur persona** → panneau fiche + option « suivre » (caméra centrée, l'écran de la station s'agrandit).
- **Clic sur station** → vue session (étapes, verdicts, captures).
- **Contrôles campagne** : lancer, pause, stop, vitesse (realtime/fast/turbo), graine affichée, export rapport, ouvrir TUI parallèle.
- **Fin de campagne** : confettis généraux si score ≥ 70, sinon le tableau des scores reste affiché avec bouton « Voir le rapport » (ouvre `report.html`).

### 11.5 Performances et vivacité

- Objectif : **60 FPS** avec 24 personas animés sur machine moyenne (30 FPS plancher) ; frame skip et réduction d'effets automatiques sous charge.
- Canvas unique, sprites en atlas (une image par planche), pas de re-render statique (cache du décor).
- Sons optionnels chiptune (sous-ensemble libre, off par défaut).
- Toute animation a un état « réduite » (media query + réglage utilisateur).

---

## 12. Architecture logicielle

### 12.1 Stack (décision)

| Composant | Choix | Justification |
|---|---|---|
| Langage | **Python 3.11+** | Playwright first-class, pywinauto/FlaUI (desktop Windows), psutil, écosystème testing ; prototypage rapide |
| Async | asyncio | Parallélisme de sessions, bus d'événements |
| Driver web | **Playwright (Python)** | Multi-navigateurs, CDP, traces, throttling, axe-core |
| Driver desktop | **FlaUI/pywinauto (UIA)** + OpenCV (repli image) | Couverture Win32/WinForms/WPF, repli universel |
| TUI | **Textual** | Framework TUI riche, widgets, animable, maintenu |
| GUI | **FastAPI + WebSocket (`uvicorn[standard]`) + canvas** (front vanilla, pas de build lourd) | Locale, fluide, zéro install ; style pixel maîtrisé |
| Rapport | Jinja2 (MD/HTML) + charts en SVG inline | HTML autonome, pas de dépendance JS externe |
| Données run | SQLite (run.db) + JSONL (events/metrics) + fichiers assets | Auditables, diffables, légers |
| Config | TOML partout | Lisible, commentable, versionnable |
| IA optionnelle | Ollama local / API compatible OpenAI | Off par défaut, repli M2 automatique |
| Packaging | `pyproject.toml`, pipx/uv ; exécutables PyInstaller en v1.0 | Installation simple |

### 12.2 Modules

```
testmyapp/
├── core/        # campaign, engine (orchestration), events/bus, rng, scoring
├── personas/    # modèle, bibliothèque, génération, validation
├── drivers/     # base.py (protocole), web/ (playwright), desktop/ (uia, image), a11y/
├── behavior/    # M1 scripté, M2 exploratoire, M3 llm, erreurs humaines
├── metrics/     # collecteurs, seuils, agrégation
├── report/      # constats, règles de recommandations, rendus md/html/json
├── tui/         # app Textual, écrans, widgets
├── gui/         # serveur FastAPI, static/, ws, protocole d'états floor
├── assets/      # sprites/ (personas, émotions, décor), fonts/, fx/
├── cli.py       # argparse/typer, commandes §14
└── config.py    # chargement/validation testmyapp.toml
```

**Règles de dépendance** : `tui` et `gui` ne dépendent que du bus et d'un service de requêtes d'état — jamais du moteur directement. `drivers` ne connaissent ni personas (ils reçoivent des actions déjà modulées) ni scoring. `core` ne connaît pas les frontends. Cela garantit la parité TUI/GUI et le mode headless.

### 12.3 Protocole GUI (WebSocket)

Messages JSON typés : `floor.state` (snapshot complet à la connexion), `floor.delta` (positions/animations deltas, 10 Hz), `persona.emotion`, `persona.speech`, `session.update`, `campaign.stats`, `campaign.control` (client→serveur). Le front garde l'état et interpole les positions entre deltas (mouvement fluide à 60 FPS alors que le bus envoie 10 Hz).

---

## 13. Formats de données

Tous les schémas sont versionnés (`schema_version`) et publiés dans `schemas/` (JSON Schema pour les fichiers convertis en interne).

- **`personas/<id>.toml`** — fiche persona (§4.1, exemple Annexe A).
- **`targets/<id>.toml`** — cible : `type = web|desktop`, url/exécutable, navigateur/args, conditions réseau/CPU par défaut, identifiants (référence env, jamais en clair), templates image (desktop), fenêtre (titre/classe).
- **`scenarios/<id>.toml`** — métadonnées, préconditions, `[[steps]]` : `intent` (bulle), mode (M1/M2/M3), actions, attentes, `baseline_seconds`, captures.
- **`campaigns/<id>.toml`** — matrices personas × scénarios × cibles, `max_workers`, vitesse, seuils, pondérations du score, options (trace, vidéo, IA).
- **`runs/<horodatage>/`** — `campaign.json` (config effective figée), `events.jsonl`, `metrics.jsonl`, `run.db` (index + agrégats), `screenshots/`, `traces/`, `report/` (md/html/json + assets).
- **`assets/sprites/manifest.toml`** — atlas : quelles planches, frames, points d'ancrage, palettes, mapping émotions.

---

## 14. CLI et commandes

```
testmyapp init                          # scaffold: config, personas d'exemple, scénario démo
testmyapp target check <id>             # ping/launch la cible, vérifie les prérequis
testmyapp persona list|show|new|generate|clone|validate
testmyapp run [-c campaigns/x.toml] [--mode tui|gui|headless] [--seed N]
             [--speed realtime|fast|turbo] [--persona …] [--scenario …] [--resume runs/<id>]
testmyapp report runs/<id> [--format md|html|json|pdf] [--open]
testmyapp replay runs/<id> [--mode tui|gui] [--speed …]   # rejoue les événements enregistrés
testmyapp compare runs/A runs/B [--format html]
testmyapp gui [--port 7417]             # GUI seule (connectée à un moteur détaché)
testmyapp selftest                      # vérifie l'environnement (navigateurs, accès UIA, etc.)
```

`run --mode gui` démarre le serveur GUI, ouvre le navigateur et lance la campagne au premier client. `--mode headless` produit le rapport sans interface. Code de sortie 0 si la campagne réussit, 1 en cas d’erreur d’exécution, d’interruption ou d’absence de résultat fonctionnel, 2 si une étape critique échoue, si une session abandonne ou si le score est sous le seuil. Une étape est critique par défaut et peut être déclarée informative via `critical = false`.

---

## 15. Exigences non fonctionnelles

| Catégorie | Exigence |
|---|---|
| Capacité | Jusqu'à 100 sessions par campagne ; GUI fluide avec 24 personas animés simultanément |
| Performance moteur | Overhead moteur < 5 % du temps de session (les délais simulés dominent) |
| Portabilité | Windows 10/11 (desktop driver complet), macOS/Linux (web driver + TUI/GUI) |
| Offline | 100 % fonctionnel sans réseau (cibles locales) ; IA optionnelle uniquement |
| Robustesse | Crash d'une session n'arrête pas la campagne ; driver défaillant → sessions marquées `error`, reste continue |
| Observabilité | Logs structurés (niveaux), events.jsonl intégral, graine et versions dans chaque run |
| i18n | FR (défaut) et EN : UI, TUI, GUI, rapports ; bulles des personas dans la locale du persona |
| Accessibilité produit | TUI compatible lecteurs d'écran (sortie textuelle), GUI contraste AA, tout pilotable clavier, animations réductibles |
| Temps de démarrage GUI | < 3 s du lancement à l'écran floor |
| Tests du projet | Couverture unitaire cœur/behavior/report ; tests E2E de TestMyApp par… TestMyApp (dogfooding, campagne démo sur une webapp d'exemple embarquée) |

---

## 16. Sécurité, éthique et conformité

- **Autorisation** : TestMyApp est conçu pour tester des applications que l'utilisateur **possède ou est autorisé à tester** (ses propres apps, environnements de recette). Mention affichée à la première exécution et dans le rapport.
- **Données fictives** : les personas ne saisissent jamais de données personnelles réelles ; générateur de faux noms/emails/adresses embarqué ; interdiction de stocker des secrets dans les TOML (références de variables d'environnement uniquement, avertissement de validation sinon).
- **Pas d'exfiltration** : tous les artifacts restent dans `runs/` ; aucun télémétrie ; l'IA optionnelle n'envoie que des snapshots accessibles purgés des valeurs de saisie (et est off par défaut).
- **Vitesse humaine par défaut** : pas de comportement assimilable à du flood même en turbo (délais minimaux conservés, un seul contexte par session).
- **Rapport honnête** : distinction systématique `vérifié / signalé / humain-requis` ; le score n'est jamais présenté sans ses constats ; mode turbo étiqueté (métriques UX dégradées).

---

## 17. Outils et assets (chaîne de production)

Skills installés dans l'environnement agent (catalogue `agent-extension/README.md`) et leur usage dans ce projet :

| Skill | Usage dans TestMyApp |
|---|---|
| **sprite-gen** | Génération des planches de sprites (corps personas × palettes × accessoires, émotions 16×16, décor) ; exports atlas + manifest |
| **pixelfx** | Effets pixel art en code (confettis, étoiles, éclair d'échec, zzz) réutilisés dans le canvas GUI |
| **animate** (Emil Kowalski) | Décisions d'animation du GUI : quoi animer, courbes, durées, interruptions, `prefers-reduced-motion` |
| **webapp-testing** (Anthropic) | Méthodologie Playwright du driver web + tests du produit lui-même |
| **accessibility-\*** (AccessLint) | Grille WCAG 2.2 / WCAG-EM du module a11y et du rapport |
| *déjà présents* : `impeccable`, `frontend-design`, `emil-design-eng`, OpenSpec | Design GUI, revues d'interface, tenue de la spec dans le dépôt |

Dépendances binaires documentées pour le développement : Node 18+ (pixelfx), FFmpeg (vidéo optionnelle), Chrome/Chromium (Playwright, accesslint), Python 3.12+.

---

## 18. Risques et mitigations

| Risque | Impact | Mitigation |
|---|---|---|
| Flakiness des sélecteurs web | Faux négatifs | Stratégie de résolution en cascade + screenshot systématique + retry au niveau étape (tracé comme « hésitation », pas comme échec immédiat) |
| Anti-bot / captcha sur cibles distantes | Sessions bloquées | Cibles recommandées locales/recette ; détection captcha → `skip` documenté, pas de contournement |
| Apps desktop non automatisables (UIA vide) | Couverture réduite | Repli template matching ; diagnostic `target check` dit quel mode s'applique |
| Dérive des métriques en turbo | Rapports trompeurs | Étiquetage turbo dans le rapport ; realtime recommandé pour mesures UX |
| Coût/latence IA (M3) | Campagnes lentes | Off par défaut, budget par session, repli M2, coût tracé |
| Vivacité GUI coûteuse en CPU | Machines modestes pénalisées | Frame skip, réduction d'effets, cache décor, plancher 30 FPS |
| Faux-positifs a11y automatiques | Bruit dans le rapport | Base de preuve `◐ signalé` distincte de `● vérifié` ; section « humain-requis » |

---

## 19. Roadmap

| Version | Contenu | Critère de sortie |
|---|---|---|
| **v0.1** | Moteur + driver web (M1/M2) + personas embarqués + TUI dashboard + rapport MD/JSON | Campagne démo sur webapp d'exemple, rapport complet, reproductible par graine |
| **v0.2** | GUI Test Floor v1 (sprites, déplacements, émotions, bulles) + HTML autonome | Floor 60 FPS / 24 personas ; émotion < 500 ms ; parité de contrôle avec TUI |
| **v0.3** | Driver desktop (UIA + image) + module a11y complet + scoring composite | Cible démo Windows testée (démarrage, actions, crash) ; rapport a11y WCAG-EM |
| **v0.4** | M3 IA optionnel (Ollama/API) + compare + replay | Compare A/B ; replay fidèle ; IA tracée et bornée |
| **v1.0** | Durcissement, i18n FR/EN, packaging (pipx + exécutables), selftest, docs utilisateur | 100 sessions sans crash ; tests du produit par lui-même ; docs complètes |

---

## 20. Critères d'acceptation

**Généraux** — toute version :
- `testmyapp init && testmyapp run --mode headless` aboutit à un rapport en < 5 min sur la démo, machine propre.
- Même graine ⇒ mêmes décisions (test de non-régression du comportement).
- Aucun événement émotionnel sans libellé textuel associé.

**v0.2 (GUI, cœur du demande visuelle)** :
- Un persona se déplace de la porte à une station, travaille, affiche ≥ 5 émotions distinctes sur un scénario mixte, et se rend au bureau rapport en fin de session.
- Clic persona → fiche complète ; clic station → vue session avec captures.
- `prefers-reduced-motion` remplaçant les effets par des badges statiques, vérifié.
- 60 FPS soutenus avec 24 personas (test intégré de performance du canvas).

**Rapport (v0.1+)** :
- Chaque point positif/négatif cite ≥ 1 preuve accessible depuis le rapport HTML.
- Les recommandations sont priorisées et tracées vers leurs constats.
- `report.json` valide contre son schéma versionné.

---

## 21. Glossaire

→ §3 (Terminologie) constitue le glossaire de référence. Termes additionnels :

- **CWV** : Core Web Vitals (LCP, CLS, INP).
- **UIA** : UI Automation, API d'accessibilité/automatisation Windows.
- **WCAG-EM** : méthodologie d'évaluation de conformité WCAG (échantillonnage de pages).
- **Storage state** : état de session Playwright (cookies/localStorage) réutilisable.
- **EMA** : moyenne mobile exponentielle (utilisée pour l'humeur).

---

## Annexe A — Exemples de fichiers

**`personas/nadine.toml`** (extrait) :

```toml
schema_version = 1
id = "nadine"
name = "Nadine"
pronouns = "elle"
age = 68
occupation = "Retraitée, bénévole bibliothèque"
bio = "Découvre les services en ligne depuis la médiathèque. Lit attentivement, écrit lentement."

[sprite]
base = "body_a"
palette = { skin = "#f2c9a0", hair = "#d9d9d9", top = "#b0526d", bottom = "#4a4a5a" }
accessories = ["glasses"]

[a11y]
vision = "low"          # zoom 125 %, taille de police agrandie
motor = "normal"
reading_wpm = 140
screen_reader = false

[device]
type = "desktop"
screen = { w = 1920, h = 1080, dpr = 1 }
input = "mouse"

network.profile = "dsl"
cpu.throttle = 2
browser = "chromium"

[behavior]
patience = 0.8
curiosity = 0.6
tech_savviness = 0.1
error_proneness = 0.30
persistence = 0.9        # → 5 réessais
help_seeking = 0.8
speed.factor = 2.0
on_error = { retry = 0.5, explore = 0.2, ask_help = 0.3, abandon = 0.0 }

[emotion]
bias = 0.1
volatility = 0.9

[[goals.primary]]
text = "Commander un livre pour ma petite-fille"
```

**`scenarios/commander-produit.toml`** (extrait) :

```toml
schema_version = 1
id = "commander-produit"
name = "Commander un produit"
baseline_seconds = 180

[[steps]]
intent = "Trouver un produit précis"
mode = "scripted"
actions = [
  { type = "navigate", url = "{target.base_url}/" },
  { type = "type", selector_pref = "search", text = "{goal.product}" },
  { type = "press", key = "Enter" },
]
expects = [
  { kind = "visible", selector = "[role=listitem] a", timeout_ms = 8000 },
]
screenshot = "after"

[[steps]]
intent = "Payer la commande"
mode = "scripted"
actions = [
  { type = "click", selector_pref = "role:button[name='Payer']" },
]
expects = [
  { kind = "url_matches", pattern = "confirmation" },
  { kind = "metric_below", metric = "step.duration_ms", max = 20000 },
]
```

**`campaigns/recette.toml`** (extrait) :

```toml
schema_version = 1
id = "recette-hebdo"
target = "boutique-recette"
matrix.personas = ["nadine", "eric", "paula", "dany", "karim", "mona"]
matrix.scenarios = ["commander-produit", "creer-compte", "consulter-aide"]
max_workers = 3
speed = "realtime"
seed = 20261119

[thresholds]
lcp_ms = 2500
cls = 0.1
inp_ms = 200
desktop_startup_ms = 3000
contrast_min = 4.5
touch_target_px = 44

[weights]  # score global
fonctionnel = 0.30
perf = 0.20
a11y = 0.15
ux = 0.20
stabilite = 0.15
```

## Annexe B — Catalogue des émotions

| Id | Déclencheur | Sprite (16×16) | Animation bulle |
|---|---|---|---|
| `joyeux` | humeur ≥ +3 | sourire yeux plissés | pop + scintillement |
| `content` | +1…+3 | sourire simple | pop |
| `neutre` | −1…+1 | point • | fondu |
| `intrigué` | recherche > 5 s | sourcil levé | pop, léger tilt |
| `confus` | élément introuvable / texte dense | point d'interrogation, goutte | pop + tremblement |
| `impatient` | spinner > 3 s | tapping de pied | pop répété (cooldown) |
| `agace` | −3…−1 | sourcils froncés | pop sec |
| `furieux` | < −3 | rouge, cri | pop + éclair |
| `eureka` | découverte hors parcours | ampoule | pop + étoiles |
| `celebre` | objectif final atteint | confettis + bras levés | pop + confettis (pixelfx) |
| `fatigue` | session > 20 min | paupières | fondu lent |
| `dort` | inactivité > 30 s | zzz | boucle |
| `abandonne` | abandon session | drapeau blanc | fondu + tête basse |

Chaque émotion existe en 2 frames min (statique + clignement) ; libellés FR/EN fournis (`assets/sprites/emotions.json`).

## Annexe C — Catalogue d'événements du bus

```
campaign.started      { campaign_id, seed, config_hash, sessions_total }
campaign.completed    { score, duration_ms, report_path }
session.started       { session_id, persona, scenario, target }
session.completed     { outcome: pass|fail|abandoned|error, mood_final, duration_ms }
step.started          { session_id, step_id, intent }
step.passed|failed|warned|skipped  { session_id, step_id, duration_ms, evidence[] }
action.performed      { session_id, action, duration_ms, simulated_errors }
persona.mood          { session_id, mood, emotion_badge }
persona.emotion       { session_id, emotion, cause_event, label }
persona.speech        { session_id, text, ttl_ms }
target.metric         { session_id, metric, value, thresholds }
target.console_error  { session_id, level, text, source }
target.network        { session_id, request, status, timings }
target.crash          { session_id, exit_code, dump_path }
a11y.violation        { session_id, rule, severity, selector, evidence }
report.ready          { formats[] }
```

---

*Fin de la spécification — TestMyApp v1.0.2 (19/11/2026). Toute modification passe par une révision de version en tête de document.*
