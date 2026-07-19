# DOCUMENT DE SPÉCIFICATION 
**Contexte :**
Je veux un script Python complet (ETL) pour filtrer les conférences académiques depuis le fichier `CORE_all26.csv`, trouver leurs sites web pour l'année en cours/suivante (via Google Custom Search API), extraire le texte de ces sites, et utiliser l'API Gemini pour parser les dates clés en JSON.

Voici la liste des tâches (TODO) que tu dois implémenter étape par étape. Utilise `pandas` pour la manipulation de données, `requests` et `BeautifulSoup` pour le scraping, et les SDK officiels de Google (Custom Search et `google-generativeai`).

### TODO LIST (Architecture du script)

* [ ] **Étape 1 : Initialisation & Configuration**
* Charger les variables d'environnement (`GOOGLE_SEARCH_API_KEY`, `GOOGLE_SEARCH_CX`, `GEMINI_API_KEY`).
* Définir l'année cible (ex: `TARGET_YEAR = 2026` et `2027`).


* [ ] **Étape 2 : Filtrage de la base CORE**
* Lire `CORE_all26.csv` avec Pandas.
* Filtrer les lignes selon les critères : Rang (colonne Rank = 'A' ou 'A*') et Thème (colonne FoR ou champs de recherche pertinents).
* Extraire l'Acronyme et le Nom complet de la conférence.


* [ ] **Étape 3 : Module de Recherche (Google Custom Search API avec Fallbacks)**
* *Logique de cascade :* Pour chaque conférence, faire la Requête Principale.
* Extraire le premier lien pertinent.
* Si la Requête Principale ne donne rien d'exploitable, lancer la Requête Fallback 1 (WikiCFP).
* Si toujours rien, lancer la Requête Fallback 2 (Research.com).


* [ ] **Étape 4 : Module d'Extraction Web (Scraping)**
* Faire une requête GET HTTP sur l'URL trouvée (avec un header `User-Agent` classique).
* Nettoyer le HTML avec BeautifulSoup : supprimer les balises `<script>`, `<style>`, `<nav>`, `<footer>` pour ne garder que le contenu textuel brut utile.
* *Optionnel mais recommandé :* Si le texte récupéré est très court et contient des liens vers "Important Dates" ou "Call for Papers", suivre ce lien et concaténer le texte.


* [ ] **Étape 5 : Module d'Extraction IA (API Gemini)**
* Envoyer le texte nettoyé à l'API Gemini avec le System Prompt fourni ci-dessous.
* Forcer le mode de sortie en JSON (`response_schema` ou instructions strictes dans le prompt).


* [ ] **Étape 6 : Formatage et Export**
* Compiler les résultats (Nom, Rang, Thème, URL de la source, Date de soumission, Date de notification).
* Gérer les erreurs et ajouter des `time.sleep()` pour éviter le rate-limiting.
* Exporter le DataFrame final en `conferences_filtrees.csv`.



---

### LES REQUÊTES GOOGLE À UTILISER

L'agent devra utiliser ces templates de recherche (remplacer les variables par les données de Pandas) :

**1. Requête Principale (Recherche du site officiel de l'édition cible) :**

> `"{Acronym} {TARGET_YEAR}" "{Conference Name}" ("Call for papers" OR "Important dates")`

**2. Requête Fallback 1 (Si échec, on cherche sur WikiCFP) :**

> `site:wikicfp.com "{Acronym} {TARGET_YEAR}" "{Conference Name}"`

**3. Requête Fallback 2 (Si échec, on cherche sur Research.com / Guide2Research) :**

> `site:research.com "{Acronym} {TARGET_YEAR}" "conferences"`

---

### LE PROMPT POUR L'IA (À envoyer à l'API Gemini)

L'agent devra utiliser ce prompt exact (comme *System Prompt* ou instruction principale) en concaténant le texte scrappé à la fin.

**Prompt :**

```text
Tu es un assistant spécialisé dans l'extraction de métadonnées académiques. Je vais te fournir le texte brut extrait d'une page web de conférence (ou d'un annuaire académique).

Ta mission est d'extraire les dates de soumission et de notification pour l'édition {TARGET_YEAR} de la conférence {Acronym}.

RÈGLES STRICTES :
1. Tu dois chercher uniquement les dates concernant l'année {TARGET_YEAR}. Si le texte parle d'une édition passée (ex: l'année précédente), tu dois considérer que l'information n'y est pas.
2. Fais la distinction entre l'Abstract (résumé) et le Full Paper (papier complet). Si une seule date de soumission est donnée sans précision, considère-la comme la date du Full Paper.
3. NE DEVINE PAS. Si la date est inscrite comme "TBD", "TBA", ou si tu n'es pas absolument sûr à 100% que la date correspond à la soumission du papier ou à la notification, retourne `null`.
4. Formate les dates au standard ISO (YYYY-MM-DD).

Tu dois répondre UNIQUEMENT avec un objet JSON valide, sans aucun texte avant ou après, respectant strictement cette structure :

{
  "conference_year_found": booléen (true si le texte parle bien de l'année cible, false sinon),
  "abstract_submission_date": "YYYY-MM-DD" ou null,
  "paper_submission_date": "YYYY-MM-DD" ou null,
  "notification_date": "YYYY-MM-DD" ou null,
  "timezone": "Chaîne de caractère du fuseau horaire si précisé (ex: 'AoE')" ou null,
  "confidence_score": entier de 1 à 10 (mets < 5 si tu as un doute, 10 si c'est explicite)
}

Voici le texte extrait de la page web :
[INSÉRER LE TEXTE SCRAPPÉ ICI]


