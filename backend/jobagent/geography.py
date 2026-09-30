"""Conservative location evidence for European recommendations, including the UK."""

import re
import unicodedata

COUNTRIES = """albania|andorra|austria|belarus|belgium|bosnia|bulgaria|croatia|cyprus|czechia|czech republic|denmark|estonia|finland|france|germany|greece|hungary|iceland|ireland|italy|kosovo|latvia|liechtenstein|lithuania|luxembourg|malta|moldova|monaco|montenegro|netherlands|north macedonia|norway|poland|portugal|romania|san marino|serbia|slovakia|slovenia|spain|sweden|switzerland|ukraine|united kingdom|uk|u.k.|great britain|england|scotland|wales|northern ireland|vatican|españa|deutschland|italia|nederland|suisse|osterreich|europe|european union|eu|eea""".split(
    "|"
)
CITIES = """london|londres|bristol|manchester|cambridge|oxford|birmingham|leeds|edinburgh|glasgow|belfast|cardiff|nottingham|sheffield|southampton|leicester|reading|newcastle upon tyne|exeter|bath|york|dublin|cork|galway|belfast|paris|lyon|grenoble|toulouse|bordeaux|nantes|lille|berlin|munich|munchen|hamburg|frankfurt|stuttgart|cologne|koln|dusseldorf|dresden|karlsruhe|aachen|amsterdam|eindhoven|veldhoven|rotterdam|utrecht|delft|the hague|madrid|barcelona|valencia|malaga|bilbao|sevilla|lisbon|lisboa|porto|zurich|geneva|lausanne|basel|bern|vienna|wien|graz|linz|brussels|bruxelles|leuven|ghent|antwerp|stockholm|gothenburg|goteborg|malmo|copenhagen|aarhus|oslo|trondheim|helsinki|espoo|tampere|warsaw|warszawa|krakow|wroclaw|gdansk|poznan|prague|praha|brno|budapest|bucharest|cluj|sofia|tallinn|riga|vilnius|kaunas|athens|thessaloniki|rome|roma|milan|milano|turin|torino|bologna|naples|napoli|bratislava|ljubljana|zagreb|belgrade|beograd|sarajevo|skopje|tirana|reykjavik|kyiv|kiev|lviv|chisinau|nicosia|limassol|valletta""".split(
    "|"
)
OUTSIDE = """canada|canadian|ontario|toronto|vancouver|united states|usa|u.s.|california|texas|massachusetts|new york|boston|san francisco|australia|new zealand|india|singapore|china|hong kong|japan|south korea|taiwan|malaysia|philippines|indonesia|vietnam|thailand|brazil|mexico|argentina|chile|colombia|peru|south africa|nigeria|kenya|egypt|dubai|united arab emirates|saudi arabia|israel|pakistan|bangladesh""".split(
    "|"
)


def norm(value):
    return unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()


def has(text, terms):
    return any(re.search(r"(?<!\w)" + re.escape(norm(term)) + r"(?!\w)", text) for term in terms)


def location_evidence(location):
    """Unknown/worldwide/EMEA alone is insufficient; never infer location from a company."""
    european, london = False, False
    for part in re.split(r"[;\n]|\s+\|\s+", norm(location)):
        if has(part, OUTSIDE) or re.search(
            r"\b(?:london|cambridge|paris|birmingham|bristol|manchester|york)\s*,?\s*(?:on|ca|oh|ky|ma|tx|al|ct|nh|pa|me|tn)\b",
            part,
        ):
            continue
        known = has(part, COUNTRIES) or has(part, CITIES)
        european |= known
        london |= known and has(part, ["london", "londres"]) and not has(part, ["londonderry", "new london"])
    return {"europe": european, "london": london}


def recommendable(job, settings):
    return settings.get("suggested_region") != "europe" or location_evidence(job.get("location"))["europe"]
