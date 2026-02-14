from apify_client import ApifyClient
import os
import pandas as pd
from transformers import pipeline
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import re

# --- CONFIGURATION ---
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "Token")
JSON_CREDS = "credentials.json"
SHEET_NAME = "Analyse_Sentiments_Neuro"

client_apify = ApifyClient(APIFY_TOKEN)

# 1. COLLECTE DES DONNÉES (GOOGLE + FACEBOOK)
avis_complets = []

print(" Récupération des avis Google Maps...")
cliniques_google = [
    "Neuroperforma Montréal Centre-ville", "Neuroperforma Montréal Ouest DDO", 
    "Neuroperforma Saint-Hubert Siège social", "Neuroperforma Saint-Hubert TMS", 
    "Neuroperforma Laval", "Neuroperforma Terrebonne", "Neuroperforma Québec", 
    "Neuroperforma Lévis", "Neuroperforma Sherbrooke", "Neuroperforma Gatineau"
]

run_google = client_apify.actor("nwua9Gu5YrADL7ZDj").call(run_input={
    "searchStringsArray": cliniques_google, "maxReviews": 500, "language": "fr"
})

for item in client_apify.dataset(run_google["defaultDatasetId"]).iterate_items():
    reviews = item.get("reviews", [])
    for rev in (reviews if reviews else [item]):
        if rev.get("text") or rev.get("stars"):
            date = rev.get("publishedAtDate", "2026-01-01")
            succursale = item.get("title", "").replace("Neuroperforma ", "")
            texte = rev.get("text", "")
            note = rev.get("stars", 5)
            avis_complets.append([date, succursale, texte, note, "Google Maps"])

print(" Récupération des avis Facebook...")
try:
    run_fb = client_apify.actor("apify/facebook-reviews-scraper").call(run_input={
        "startUrls": [{"url": "https://www.facebook.com/Neuroperforma/reviews"}],
        "maxReviews": 50
    })
    for item in client_apify.dataset(run_fb["defaultDatasetId"]).iterate_items():
        texte_fb = item.get("text") or item.get("reviewText")
        if texte_fb and len(texte_fb) > 20:
            date_fb = item.get("date") or "2026-01-01"
            note_fb = item.get("rating", 5)
            avis_complets.append([date_fb, "Page Facebook", texte_fb, note_fb, "Facebook"])
except Exception as e:
    print(f"Erreur Facebook : {e}")

# --- TRAITEMENT DES DONNÉES ---
df = pd.DataFrame(avis_complets, columns=['date', 'succursale', 'commentaire', 'note', 'source'])
df['date'] = pd.to_datetime(df['date'], errors='coerce', utc=True).dt.tz_localize(None)
df['date'] = df['date'].fillna(pd.Timestamp('2026-01-01'))
df['date_str'] = df['date'].dt.strftime('%Y-%m-%d')

# 2. ANALYSE IA HYBRIDE (BASÉE SUR LA NOTE + NLP)
print(" Analyse NLP en cours (Note > BERT)...")

# Modèles (On garde les modèles pour les thèmes)
theme_task = pipeline("zero-shot-classification", model="facebook/bart-large-mnli")

categories_macro = ["Efficacité du traitement", "Prix et Remboursement", "Service Client", "Professionnalisme", "Délai et Attente"]
sujets_micro = ["remboursement", "assurance", "accueil", "résultat", "prix", "téléphone", "rendez-vous", "écoute", "explication"]

def analyse_hybride(row):
    # A. Sentiment
    note = float(row['note'])
    if note < 3:
        sentiment = "Negatif"
    elif note > 3:
        sentiment = "Positif"
    else:
        sentiment = "Neutre"
    
    texte = str(row['commentaire']).strip()
    
    if len(texte) < 10:
        return sentiment, "General", "N/A"
    
    try:
        # B. Aspect (Catégorie Macro)
        res_t = theme_task(texte[:512], candidate_labels=categories_macro)
        aspect = res_t['labels'][0]
        
        # C. Sujet Précis (Détail Marketing)
        res_sujet = theme_task(texte[:512], candidate_labels=sujets_micro)
        sujet = res_sujet['labels'][0]
        
        return sentiment, aspect, sujet
    except:
        return sentiment, "General", "N/A"

df[['sentiment', 'aspect', 'sujet']] = df.apply(lambda r: pd.Series(analyse_hybride(r)), axis=1)

# 3. MISE À JOUR GOOGLE SHEETS
print(" Envoi vers le Sheet...")
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_CREDS, scope)
client_google = gspread.authorize(creds)
classeur = client_google.open(SHEET_NAME)

feuille_principale = classeur.sheet1
feuille_principale.clear()
en_tetes = [['Date', 'Lieu', 'Commentaire', 'Note', 'Source', 'Sentiment', 'Aspect Marketing', 'Sujet Précis']]
valeurs = df[['date_str', 'succursale', 'commentaire', 'note', 'source', 'sentiment', 'aspect', 'sujet']].values.astype(str).tolist()
feuille_principale.update('A1', en_tetes + valeurs)

# Onglet Détails des plaintes
print(" Mise à jour Details_Plaintes...")
avis_neg = df[df['sentiment'] == "Negatif"]
details = [[row['source'], row['aspect'], row['sujet'], row['commentaire']] for _, row in avis_neg.iterrows()]
try:
    onglet_details = classeur.worksheet("Details_Plaintes")
    onglet_details.clear()
    onglet_details.update('A1', [['Plateforme', 'Aspect Critique', 'Sujet Précis', 'Commentaire negatif']])
    if details:
        onglet_details.update('A2', details)
except:
    pass


print(f" TERMINÉ : Dashboard mis à jour avec {len(df)} analyses !")
