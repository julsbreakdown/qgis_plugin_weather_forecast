FRENCH = {
    "Weather Forecast needs extra Python packages: {packages}. Install them with the qpip plugin "
    'or with pip, see <a href="{url}">the installation guide</a>.': (
        "Weather Forecast a besoin de paquets Python supplémentaires : {packages}. Installez-les avec "
        "l'extension qpip ou avec pip, voir <a href=\"{url}\">le guide d'installation</a>."
    ),
    "&Weather Forecast": "&Prévisions météo",
    "Weather forecast": "Prévisions météo",
    "{variable}: calculated from {components}": "{variable} : calculée à partir de {components}",
    "{variable}: raw, read as stored": "{variable} : brute, lue telle quelle",
    "now": "maintenant",
    "Single map: hours after the latest model run. 'now' picks the forecast nearest the current time. "
    "Greyed out with Time range.": "Carte unique : heures après le dernier run du modèle. "
    "« maintenant » prend la prévision la plus proche de l'heure actuelle. "
    "Grisé avec Plage horaire.",
    " day": " jour",
    " days": " jours",
    "Single time: one map. Time range: every forecast step from now to now + duration, as one "
    "layer for the Temporal Controller.": (
        "Heure unique : une seule carte. Plage horaire : chaque échéance de maintenant à maintenant + "
        "durée, en une couche pour le contrôleur temporel."
    ),
    "all steps": "toutes les échéances",
    "Time range only: keep one step every N hours (GFS is hourly to 120 h, then 3-hourly). "
    "Greyed out with Single time.": "Plage horaire seulement : garde une échéance toutes les N heures "
    "(GFS est horaire jusqu'à 120 h, puis tri-horaire). Grisé avec Heure unique.",
    "Whole world": "Monde entier",
    "Fetch": "Récupérer",
    "Reload catalog": "Recharger le catalogue",
    "Style override file (TOML); empty: the library defaults": (
        "Fichier de styles personnalisés (TOML) ; vide : styles par défaut de la bibliothèque"
    ),
    "Reload styles and restyle the forecast layers": (
        "Recharger les styles et les réappliquer aux couches de prévision"
    ),
    "Model": "Modèle",
    "Variable": "Variable",
    "Map time": "Heure de la carte",
    "Time": "Temps",
    "Animated wind": "Vent animé",
    "Wind only: the flow line dashes move downwind. The layer is redrawn continuously, which "
    "may be slow on large extents or long time ranges.": (
        "Vent seulement : les tirets des lignes de flux avancent dans le sens du vent. La couche est "
        "redessinée en continu, ce qui peut être lent sur une grande emprise ou une longue plage horaire."
    ),
    "Animated wind flow lines (moving dashes, redrawn continuously: may be slow)": (
        "Lignes de flux du vent animées (tirets mobiles, redessinées en continu : peut être lent)"
    ),
    "Single time": "Heure unique",
    "Time range": "Plage horaire",
    "Duration": "Durée",
    "Step interval": "Pas de temps",
    "Extent": "Emprise",
    "Styles": "Styles",
    "Styles not applied: {error}": "Styles non appliqués : {error}",
    "Styles reloaded from {path}: {count} layer(s) restyled": (
        "Styles rechargés depuis {path} : {count} couche(s) restylée(s)"
    ),
    "the defaults": "les valeurs par défaut",
    "Loading catalog...": "Chargement du catalogue...",
    "Load weather forecast catalog": "Chargement du catalogue des prévisions météo",
    "cancelled": "annulé",
    "Cannot load the catalog: {error}": "Impossible de charger le catalogue : {error}",
    "{n} models": "{n} modèles",
    "No forecast model found": "Aucun modèle de prévision trouvé",
    "Styles file unusable, fix it or clear the Styles field: {error}": (
        "Fichier de styles inutilisable, corrigez-le ou videz le champ Styles : {error}"
    ),
    "Fetching {variable}...": "Récupération de {variable}...",
    "Fetch of {variable} cancelled": "Récupération de {variable} annulée",
    "unknown error": "erreur inconnue",
    "Fetch of {variable} failed: {error}": "Échec de la récupération de {variable} : {error}",
    "Fetched {variable}": "{variable} récupérée",
    "Model id": "Identifiant du modèle",
    "Map time: hours after the latest run (empty: forecast nearest now; ignored with a time range)": (
        "Heure de la carte : heures après le dernier run "
        "(vide : prévision la plus proche de maintenant ; ignorée avec une plage horaire)"
    ),
    "Time range: days ahead (0: single time, one map; else every step from now to now + days)": (
        "Plage horaire : jours à venir (0 : heure unique, une seule carte ; "
        "sinon chaque échéance de maintenant à maintenant + jours)"
    ),
    "Step interval in hours, time range only (empty: all steps)": (
        "Pas de temps en heures, plage horaire seulement (vide : toutes les échéances)"
    ),
    "Fetching {variable} from {model}, bbox {bbox}": (
        "Récupération de {variable} depuis {model}, emprise {bbox}"
    ),
    "whole world": "monde entier",
    "Run {init:%Y-%m-%d %H:%M} UTC, {n} step(s) valid {first:%Y-%m-%d %H:%M} to {last:%Y-%m-%d %H:%M} UTC": (
        "Run {init:%Y-%m-%d %H:%M} UTC, {n} échéance(s) valides "
        "du {first:%Y-%m-%d %H:%M} au {last:%Y-%m-%d %H:%M} UTC"
    ),
    "Fetch forecast": "Récupérer une prévision",
    "Fetches one forecast map from dynamical.org into a styled GeoTIFF. No extent: whole world. "
    "Time range (days > 0): one GeoTIFF per step and one VRT stacking them, loaded as a single "
    "layer that shows each step over its time range in the Temporal Controller. "
    "A wind variable (e.g. wind_10m) gives the speed raster, with a direction band, and a "
    "GeoPackage of animated flow lines next to it, both loaded in one group.": (
        "Récupère une carte de prévision depuis dynamical.org dans un GeoTIFF stylé. "
        "Sans emprise : monde entier. Plage horaire (jours > 0) : un GeoTIFF par échéance et un VRT qui les "
        "empile, chargés en une seule couche qui affiche chaque échéance sur sa plage temporelle dans le "
        "contrôleur temporel. Une variable de vent (par ex. wind_10m) donne le raster de vitesse, avec une "
        "bande de direction, et à côté un GeoPackage de lignes de flux animées, chargés tous deux dans un "
        "même groupe."
    ),
    "Forecast": "Prévision",
    "Wind flow lines": "Lignes de flux du vent",
    "Forecast steps": "Échéances de la prévision",
    "Export forecast to PMTiles": "Exporter une prévision en PMTiles",
    "Fetches one forecast map and writes coloured Web Mercator PNG tiles into a PMTiles archive "
    "(days > 0: one archive per step plus index.json in the folder, one shared value range), "
    "over an extent, polygons, or both. With 'Clip to polygons', pixels outside are transparent. "
    "The legend (min, max, colours) is in the archive metadata under 'wxforecast'. "
    "A wind variable (e.g. wind_10m) is coloured by speed with arrows drawn where the wind blows to.": (
        "Récupère une carte de prévision et écrit des tuiles PNG colorées en Web Mercator dans une archive "
        "PMTiles (jours > 0 : une archive par échéance plus index.json dans le dossier, une plage de valeurs "
        "commune), sur une emprise, des polygones, ou les deux. Avec « Découper selon les polygones », "
        "les pixels à l'extérieur sont transparents. La légende (min, max, couleurs) est dans les "
        "métadonnées de l'archive sous « wxforecast ». Une variable de vent (par ex. wind_10m) est colorée "
        "selon la vitesse, avec des flèches dessinées dans le sens où souffle le vent."
    ),
    "Polygons": "Polygones",
    "Min zoom": "Zoom min",
    "Max zoom (empty: from the model grid)": "Zoom max (vide : d'après la grille du modèle)",
    "Clip to polygons": "Découper selon les polygones",
    "PMTiles (days = 0)": "PMTiles (jours = 0)",
    "PMTiles folder, one per step (days > 0)": "Dossier PMTiles, un fichier par échéance (jours > 0)",
    "Give an extent, polygons, or both.": "Indiquez une emprise, des polygones, ou les deux.",
    "{n} area(s)": "{n} zone(s)",
    "A time range (days > 0) writes one PMTiles per step: set the PMTiles folder, not the file.": (
        "Une plage horaire (jours > 0) écrit un PMTiles par échéance : "
        "indiquez le dossier PMTiles, pas le fichier."
    ),
    "One map (days = 0) writes one PMTiles: set the PMTiles file, not the folder.": (
        "Une seule carte (jours = 0) écrit un seul PMTiles : indiquez le fichier PMTiles, pas le dossier."
    ),
    "About {count} tiles": "Environ {count} tuiles",
    "About {count} tiles per step; the limit covers all steps, checked after the fetch": (
        "Environ {count} tuiles par échéance ; la limite porte sur toutes les échéances, "
        "vérifiée après la récupération"
    ),
    "Wrote {path}": "Écrit : {path}",
}


def translate(text: str, locale: str) -> str:
    return FRENCH.get(text, text) if locale.startswith("fr") else text


def tr(text: str) -> str:
    from qgis.core import QgsApplication

    return translate(text, QgsApplication.locale())
