# ICORESearch - Autonomous Academic Conference Dashboard

**ICORESearch** est un tableau de bord web interactif couplé à un pipeline de données autonome conçu pour automatiser la veille académique. 

L'outil filtre les conférences de la base CORE (2026) par rangs et thématiques de recherche, recherche sur le Web pour extraire automatiquement les dates de soumission, gère le roulement des éditions annuelles, et surveille les extensions de deadlines (Deadline Extensions).

Le système repose sur un script ETL Python (`update_db.py`) exécuté quotidiennement via **GitHub Actions** et qui interroge l'API **Gemini (Google Search Grounding)** pour lire et synthétiser les pages web des conférences.

![Dashboard Preview](https://via.placeholder.com/800x400?text=ICORESearch+Dashboard)

---

## 🚀 Fonctionnalités Clés

### 1. Tableau de bord Web Interactif (Frontend)
* **Filtrage Multicritères** : Recherche dynamique par Acronyme, Rangs (ex: `A`, `A*`), et sélection multiple de Thématiques (via un menu déroulant checkbox).
* **Tri Dynamique** : Tri en un clic par dates de soumission, rangs ou acronymes.
* **Alertes visuelles** : Les dates passées sont grisées, les dates imminentes (moins de 30 jours) sont mises en évidence, et les dates étendues ("Deadline Extended") sont signalées.
* **Correction Manuelle** : Bouton d'édition (icône stylo) sur chaque ligne redirigeant vers le code source GitHub pour forcer une modification via Pull Request.

### 2. Le Moteur de Recherche (Pipeline ETL `update_db.py`)
* **Architecture en Cascade (Phase 1 & 2)** :
  * *Phase 1 (Site Officiel strict)* : Le LLM navigue uniquement sur le site officiel de l'édition cible.
  * *Phase 2 (Fallback Aggregators)* : Si introuvable, le LLM cherche les informations sur WikiCFP ou Research.com.
* **Moteur de Vérification (Surveillance d'Extensions)** :
  * Si une conférence est validée, le pipeline met en place un *Cooldown Dynamique*. Plus la deadline approche, plus le script va inspecter le site souvent (jusqu'à tous les 2 jours) pour détecter si les organisateurs repoussent la date limite.
* **Gestion du Cycle de Vie (Rollover & Hibernation)** :
  * Une fois la date de notification passée, le script passe automatiquement à l'édition de l'année suivante (`Target Year + 1`) et met la conférence en hibernation pendant 150 jours afin de préserver le quota d'API et laisser le temps aux organisateurs de créer le nouveau site.
* **Gestion Stricte des Quotas** :
  * Limitation à des lots (*batches*) de 100 conférences par jour.
  * Kill-switch intégré pour s'arrêter proprement avant de dépasser le quota quotidien gratuit de l'API Gemini.
  * Sauvegarde atomique et progressive (anti-panne/timeout).

---

## 🛠️ Architecture du Système

```mermaid
flowchart TD
    subgraph GitHub Actions [Serveur CI/CD Quotidien (2h00 AM)]
        Cron([Cron Job]) --> Python[update_db.py]
        Python --> Rollover[Yearly Rollover & Hibernation]
        Rollover --> Triage[Sélection du Batch (100 conférences max)]
        
        Triage --> |Nouvelles & Non Trouvées| Phase1[Cascade LLM Search]
        Phase1 --> |Succès| Extract[Extraction JSON Dates]
        Phase1 --> |Echec| Phase2[Recherche WikiCFP]
        
        Triage --> |Conférences Terminées| Verify[Moteur de Vérification (Extensions)]
        Verify --> Extract
        
        Extract --> DB[(conferences_db.json)]
        DB --> Export[conferences_filtrees.csv]
    end
    
    subgraph Frontend [Tableau de Bord Web - Hébergement statique]
        Export -.-> HTTP[index.html / script.js]
        HTTP --> UI[Interface Utilisateur (Tableau interactif)]
    end
```

---

## 📦 Installation Globale (Pour le Développement Local)

### Prérequis
* Python 3.11+
* Une clé API Gemini configurée (`GEMINI_API_KEY`).
* Un serveur HTTP simple (ex: `python3 -m http.server`) pour héberger l'interface web.

### 1. Cloner et Installer les dépendances Python
```bash
git clone https://github.com/JohanPy/ICORESearch.git
cd ICORESearch
pip install -r requirements.txt
```

### 2. Démarrer le Dashboard Web
Lancez un serveur local à la racine du projet :
```bash
python3 -m http.server 8000
```
Ouvrez votre navigateur sur `http://localhost:8000`.

### 3. Exécuter le Pipeline de Mise à Jour manuellement
Le script s'appuie sur le fichier `config.yaml` ou la variable d'environnement `GEMINI_API_KEY`.
```bash
export GEMINI_API_KEY="votre_cle_api"
python3 update_db.py
```

---

## 💻 Structure du Projet et Fichiers Clés

* **Frontend** :
  * `index.html` : L'interface utilisateur.
  * `styles.css` : Règles de style (Design moderne, Glassmorphism, animations).
  * `script.js` : Logique de chargement PapaParse du CSV, gestion du tri, filtrage, et rendu du tableau.
* **Backend (Data ETL)** :
  * `update_db.py` : Le script autonome responsable de l'enrichissement via Gemini.
  * `conferences_db.json` : La source de vérité absolue contenant l'état de chaque conférence (`DONE`, `INCOMPLETE`, `PENDING`), les dates, et le suivi de l'hibernation.
  * `conferences_filtrees.csv` : L'export tabulaire lu par l'interface Web.
  * `CORE_all26.csv` : Le dataset brut initial.
* **DevOps** :
  * `.github/workflows/daily_update.yml` : Workflow automatisé d'exécution nocturne.

---

## 🔧 Override Manuel (Intervention Humaine)

Pour corriger une conférence récalcitrante ou forcer le script à l'ignorer, modifiez le fichier `conferences_db.json` directement sur GitHub.

Modifiez l'entrée de l'acronyme concerné et passez la variable `"manual_override": true`. 
Le script `update_db.py` ignorera totalement et définitivement cette conférence lors des prochaines mises à jour automatiques.
