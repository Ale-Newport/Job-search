"""Which place does a question name?

A work-authorisation, sponsorship or salary answer is true FOR A JURISDICTION: the candidate's right to work in the UK says
nothing about Australia. The engine used to look for six country names after the words "work in"; any other country (or the
same country spelled 'U.S.' or 'the States') fell through to the country of the VACANCY, and the candidate's UK right was
reported as a right to work wherever the question asked.

This module reads, from the text of a question and without any model, every country, region and place it names:

* a country: the ISO gazetteer of ``extension/places.py`` plus the names and spellings a form uses in English and Spanish
  ('U.S.', 'the States', 'EE.UU.', 'Países Bajos'), with a US state, a Canadian province or a city mapped to its country;
* a region that is no single country ('the EU', 'Europe', 'EMEA', 'Latinoamérica', 'worldwide');
* a place the gazetteer does not know ('work in Kyiv'): a capitalised name after a work preposition. It is never guessed
  at: the question is then not answered unless the place is the one the vacancy itself is in;
* an ambiguous name ('Georgia': a country or a US state).

Pure functions over strings, linear in the length of the text (which is capped), so a hostile label cannot hold the
event loop. The caller decides what to do with the result: ``engine.target_country`` and the legal reading of
``intelligence/legal.py``.
"""

from __future__ import annotations

import re

from ..extension import places

MAX_TEXT = 1500  # nothing a person asks about a place needs more; the rest is dropped before any pattern runs

# Names a form uses that the gazetteer does not hold. Folded: lower case, ASCII, one space between words.
EXTRA = {
    # English
    "the us": "United States", "u s": "United States", "united states of america": "United States",
    "the united states of america": "United States", "the states": "United States", "ee uu": "United States",
    "eeuu": "United States", "n ireland": "United Kingdom", "the uk": "United Kingdom", "u k": "United Kingdom",
    "the republic of ireland": "Ireland", "the czech republic": "Czech Republic", "south korea": "South Korea",
    "korea": "South Korea", "the philippines": "Philippines", "the bahamas": "Bahamas", "the gambia": "Gambia",
    "uae": "United Arab Emirates", "the uae": "United Arab Emirates", "united arab emirates": "United Arab Emirates",
    # Spanish
    "reino unido": "United Kingdom", "gran bretana": "United Kingdom", "inglaterra": "United Kingdom",
    "escocia": "United Kingdom", "gales": "United Kingdom", "irlanda del norte": "United Kingdom",
    "estados unidos": "United States", "estados unidos de america": "United States", "espana": "Spain",
    "francia": "France", "alemania": "Germany", "italia": "Italy", "irlanda": "Ireland", "paises bajos": "Netherlands",
    "holanda": "Netherlands", "belgica": "Belgium", "suiza": "Switzerland", "polonia": "Poland", "suecia": "Sweden",
    "noruega": "Norway", "dinamarca": "Denmark", "finlandia": "Finland", "grecia": "Greece", "rusia": "Russia",
    "ucrania": "Ukraine", "turquia": "Turkey", "marruecos": "Morocco", "egipto": "Egypt", "sudafrica": "South Africa",
    "kenia": "Kenya", "canada": "Canada", "brasil": "Brazil", "peru": "Peru", "japon": "Japan",
    "corea del sur": "South Korea", "tailandia": "Thailand", "filipinas": "Philippines", "singapur": "Singapore",
    "nueva zelanda": "New Zealand", "emiratos arabes unidos": "United Arab Emirates", "emiratos arabes": "United Arab Emirates",
    "arabia saudita": "Saudi Arabia", "arabia saudi": "Saudi Arabia", "hungria": "Hungary", "republica checa": "Czech Republic",
    "rumania": "Romania", "croacia": "Croatia", "eslovaquia": "Slovakia", "eslovenia": "Slovenia", "luxemburgo": "Luxembourg",
    "chipre": "Cyprus", "islandia": "Iceland", "letonia": "Latvia", "lituania": "Lithuania", "bulgaria": "Bulgaria",
    "estonia": "Estonia", "malta": "Malta", "portugal": "Portugal", "austria": "Austria", "mexico": "Mexico",
    "argentina": "Argentina", "chile": "Chile", "colombia": "Colombia", "uruguay": "Uruguay", "venezuela": "Venezuela",
    "ecuador": "Ecuador", "bolivia": "Bolivia", "paraguay": "Paraguay", "cuba": "Cuba", "costa rica": "Costa Rica",
    "panama": "Panama", "republica dominicana": "Dominican Republic", "china": "China", "india": "India", "israel": "Israel",
    "indonesia": "Indonesia", "pakistan": "Pakistan", "argelia": "Algeria", "tunez": "Tunisia", "nigeria": "Nigeria",
    "ghana": "Ghana", "etiopia": "Ethiopia", "senegal": "Senegal", "vietnam": "Vietnam", "malasia": "Malaysia",
    "taiwan": "Taiwan", "hong kong": "Hong Kong", "australia": "Australia", "serbia": "Serbia", "bielorrusia": "Belarus",
}
# A name that is a country AND something else: it is never read as the country (the caller fails closed instead).
AMBIGUOUS = {"georgia"}
# The member states of the European Union, as the gazetteer spells them.
EU_MEMBERS = {"Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic", "Denmark", "Estonia", "Finland", "France",
              "Germany", "Greece", "Hungary", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta", "Netherlands",
              "Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Spain", "Sweden"}
REGIONS = {
    "European Union": ("european union", "union europea", "eu member states", "eu member state", "estados miembros de la ue",
                       "estados miembros de la union europea"),
    "European Economic Area": ("european economic area", "espacio economico europeo"),
    "Europe": ("europe", "europa", "continental europe"),
    "Schengen Area": ("schengen", "espacio schengen", "schengen area", "schengen zone"),
    "Eurozone": ("eurozone", "euro zone", "eurozona", "zona euro"),
    "EMEA": ("emea",), "APAC": ("apac", "asia pacific", "asia pacifico"),
    "Latin America": ("latin america", "latinoamerica", "america latina", "latam", "iberoamerica"),
    "North America": ("north america", "norteamerica", "america del norte"),
    "South America": ("south america", "sudamerica", "suramerica", "america del sur"),
    "Asia": ("asia",), "Africa": ("africa",), "Middle East": ("middle east", "oriente medio", "medio oriente"),
    "Oceania": ("oceania",), "Caribbean": ("caribbean", "caribe"), "Scandinavia": ("scandinavia", "escandinavia"),
    "Nordics": ("the nordics", "nordic countries", "paises nordicos", "nordics"), "Benelux": ("benelux",),
    "DACH": ("dach",), "Commonwealth": ("commonwealth",), "Gulf": ("gcc", "gulf states", "gulf region", "paises del golfo"),
    "ASEAN": ("asean",),
    "Worldwide": ("worldwide", "globally", "anywhere in the world", "any country", "all countries", "every country",
                  "en cualquier pais", "en todo el mundo", "a nivel mundial", "internationally"),
}
# Written in capitals only: 'EU' and 'UE' are letters of other languages' words in lower case.
CAPITAL_REGIONS = {"EU": "European Union", "E.U.": "European Union", "UE": "European Union", "EEA": "European Economic Area",
                   "EEE": "European Economic Area"}

# Places inside a country (folded). Only the unambiguous ones: 'Cambridge' is also a US city, 'Reading' is a word.
PLACES = {
    "United Kingdom": ("london", "londres", "edinburgh", "edimburgo", "glasgow", "belfast", "cardiff", "manchester",
                       "leeds", "bristol", "liverpool", "newcastle", "sheffield", "nottingham", "southampton", "brighton",
                       "milton keynes", "northern ireland", "greater london", "city of london", "canary wharf"),
    "United States": ("new york", "new york city", "nyc", "los angeles", "san francisco", "bay area", "silicon valley",
                      "chicago", "boston", "seattle", "austin", "denver", "atlanta", "dallas", "houston", "miami",
                      "washington dc", "washington d c", "san jose", "san diego", "portland", "philadelphia", "palo alto",
                      "mountain view", "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
                      "delaware", "florida", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
                      "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
                      "montana", "nebraska", "nevada", "new hampshire", "new jersey", "new mexico", "north carolina",
                      "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
                      "south dakota", "tennessee", "texas", "utah", "vermont", "virginia", "west virginia", "wisconsin",
                      "wyoming", "washington state", "district of columbia"),
    "Spain": ("madrid", "barcelona", "valencia", "sevilla", "seville", "malaga", "bilbao", "zaragoza", "palma de mallorca",
              "alicante", "granada", "cataluna", "catalonia", "andalucia", "andalusia", "pais vasco", "basque country",
              "galicia", "islas canarias", "canary islands", "islas baleares", "balearic islands", "comunidad de madrid"),
    "France": ("paris", "lyon", "marseille", "toulouse", "bordeaux", "lille", "ile de france"),
    "Germany": ("berlin", "munich", "munchen", "hamburg", "frankfurt", "cologne", "koln", "stuttgart", "dusseldorf",
                "bavaria", "baviera"),
    "Canada": ("toronto", "vancouver", "montreal", "ottawa", "calgary", "ontario", "quebec", "british columbia", "alberta",
               "manitoba", "saskatchewan", "nova scotia", "new brunswick", "newfoundland"),
    "Ireland": ("dublin", "galway"), "Netherlands": ("amsterdam", "rotterdam", "the hague", "utrecht", "eindhoven"),
    "Switzerland": ("zurich", "geneva", "ginebra", "basel"), "Sweden": ("stockholm", "estocolmo"),
    "Denmark": ("copenhagen", "copenhague"), "Norway": ("oslo",), "Finland": ("helsinki",), "Belgium": ("brussels", "bruselas"),
    "Portugal": ("lisbon", "lisboa"), "Italy": ("milan", "milano", "rome", "roma"), "Poland": ("warsaw", "varsovia", "krakow"),
    "Czech Republic": ("prague", "praga"), "Hungary": ("budapest",), "Romania": ("bucharest", "bucarest"),
    "Greece": ("athens", "atenas"), "Israel": ("tel aviv",), "United Arab Emirates": ("dubai", "abu dhabi"),
    "Australia": ("sydney", "melbourne", "brisbane", "perth", "new south wales", "queensland"),
    "New Zealand": ("auckland", "wellington"), "Japan": ("tokyo", "tokio"), "South Korea": ("seoul",),
    "China": ("shanghai", "beijing", "shenzhen"), "India": ("bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune"),
    "Brazil": ("sao paulo", "rio de janeiro"), "Argentina": ("buenos aires",), "Mexico": ("mexico city", "ciudad de mexico"),
    "Colombia": ("bogota",), "South Africa": ("cape town", "johannesburg"), "Kenya": ("nairobi",), "Nigeria": ("lagos",),
}

# Words that open a capitalised phrase which is not a place: a language, a month, a work-form word.
NOT_PLACES = set(places.fold(w) for w in (
    "english spanish french german portuguese italian dutch arabic mandarin chinese japanese korean russian hindi bengali "
    "polish turkish ukrainian catalan galician basque january february march april may june july august september october "
    "november december monday tuesday wednesday thursday friday saturday sunday yes no the this our your any all one "
    "person office hybrid remote site store team company group department division unit position role job internship "
    "placement programme program project industry sector field technology engineering software data finance banking "
    "healthcare retail consulting education research sales marketing operations support management").split())


def _build():
    names: dict[str, str] = {}
    for folded, country in places.BY_NAME.items():
        names[folded] = country
    names.update({places.fold(k): v for k, v in EXTRA.items()})
    for country, items in PLACES.items():
        for item in items:
            names.setdefault(places.fold(item), country)
    for ambiguous in AMBIGUOUS:
        names.pop(ambiguous, None)
    regions = {places.fold(alias): region for region, aliases in REGIONS.items() for alias in aliases}
    for key in list(regions):
        names.pop(key, None)  # 'Africa' is a region, never a country; 'the nordics' too
    return names, regions


NAMES, REGION_NAMES = _build()
# One alternation per table, the longest name first: 'new mexico' is read whole, so 'mexico' is not found inside it.
_ALL = {**{k: ("country", v) for k, v in NAMES.items()}, **{k: ("region", v) for k, v in REGION_NAMES.items()}}
_PATTERN = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(k) for k in sorted(_ALL, key=len, reverse=True)) + r")(?![a-z0-9])")
_CAPITALS = re.compile(r"(?<![A-Za-z.])(?P<tok>EU|E\.U\.|UE|EEA|EEE)(?![A-Za-z])")
# 'US' is a country only after a preposition or as an adjective ('US citizen'): 'work for us' is the pronoun.
_US_RAW = (
    re.compile(r"\b(?:in|within|across|throughout|inside|into|to|from|of|outside(?: of)?|for|con|en|de) "
               r"(?:(?:the|a|an|el|la|un|una) )?(?P<tok>US|U\.S\.A?\.?|USA|EE\.? ?UU\.?)(?![A-Za-z])"),
    re.compile(r"\b(?P<tok>US)[- ](?:based|citizen\w*|national|resident|work|employment|visa|permanent|passport|immigration|"
               r"persons?|authori[sz]\w*|government|federal|tax|universit\w+|college|degree|school|institution|qualification|"
               r"company|firm|office|branch|subsidiary|employer)\b"),
    re.compile(r"(?i)\b(?:in|within|across|throughout|into|to|from) the (?P<tok>us|states|u\.s\.)\b"),
    re.compile(r"(?i)\b(?:in|within|across|throughout|into|to) (?P<tok>america)\b"),
)
_EXAMPLES = re.compile(r"\((?:e\.? ?g\.?|i\.? ?e\.?|such as|for example|for instance|like|including|incl\.?|ej\.?|p\. ?ej\.?|"
                       r"por ejemplo|como|tipo)[^)]*\)", re.I)
# A capitalised name that follows a work word and a preposition: 'authorised to work in Kyiv'.
_PLACE_AFTER_WORK = re.compile(
    r"\b(?:work(?:ing)?|employ(?:ed|ment)|authori[sz]ed|authori[sz]ation|eligible|eligibility|permitted|permission|allowed|"
    r"entitled|able|right|reside|resident|residing|based|located|sponsorship|sponsor|visa|trabajar|trabajo|residir|"
    r"residente|autorizad[oa]|autorizacion|elegible|derecho)\b[^.?!;:()]{0,60}?\b(?:in|within|across|throughout|inside|"
    r"en|dentro de)\s+(?:the |el |la |los |las )?((?:[A-Z][\w’'.-]*)(?:\s+(?:of |de |del |de la |and |y )?[A-Z][\w’'.-]*){0,3})")
_ORDINARY_CAPITAL = re.compile(r"^(?:I|It|If|In|On|At|We|You|Your|Our|This|That|These|Those|A|An|The|And|Or)$")


def clean(text) -> str:
    """The text a question's places are read from: one line, bounded, without 'e.g. ...' examples, and with the spellings
    that only capitals tell apart ('US' after a preposition, 'EU', 'UE') written out ('United States', 'European Union')."""
    value = re.sub(r"\s+", " ", str(text or "")[: MAX_TEXT * 4]).strip()[:MAX_TEXT]
    value = _EXAMPLES.sub(" ", value)

    def written(pattern, names):
        def replace(match):
            token = match.group("tok")
            name = names(token)
            return match.group(0)[: match.start("tok") - match.start()] + name + match.group(0)[match.end("tok") - match.start():]
        return replace

    for pattern in _US_RAW:
        value = pattern.sub(written(pattern, lambda token: "United States"), value)
    return _CAPITALS.sub(written(_CAPITALS, lambda token: CAPITAL_REGIONS[token]), value)


def canonical(value) -> str:
    """The one spelling of a jurisdiction: a country as the gazetteer spells it, a region as REGIONS does, else the text
    itself in title case (a stored record's own 'country' compares with what a question names)."""
    if str(value).strip() in CAPITAL_REGIONS:
        return CAPITAL_REGIONS[str(value).strip()]
    folded = places.fold(value)
    if not folded:
        return ""
    folded = folded[4:] if folded.startswith("the ") and folded[4:] in _ALL else folded
    hit = _ALL.get(folded)
    if hit:
        return hit[1]
    return str(value).strip().title()


def is_region(value) -> bool:
    return canonical(value) in REGIONS


def named_places(text) -> dict:
    """Every jurisdiction a question names, in order of appearance, without repeats:

    ``{"countries": [...], "regions": [...], "places": [...], "ambiguous": [...], "connective": "or" | "and" | ""}``
    ``places`` are capitalised names after a work preposition that no table knows (never a guess); ``ambiguous`` are names
    that are a country and something else ('Georgia'). ``connective`` says how several of them combine."""
    raw = clean(text)
    folded = places.fold(raw)
    found = [(match.start(), match.end(), *_ALL[match.group(1)]) for match in _PATTERN.finditer(folded)]
    countries: list[str] = []
    regions: list[str] = []
    for _, _, kind, value in found:
        target = countries if kind == "country" else regions
        if value not in target:
            target.append(value)
    ambiguous = [name for name in sorted(AMBIGUOUS) if re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", folded)]
    unknown: list[str] = []
    for match in _PLACE_AFTER_WORK.finditer(raw):
        candidate = re.sub(r"[.,;:]+$", "", match.group(1)).strip()
        first = candidate.split()[0] if candidate else ""
        name = places.fold(candidate)
        if (not candidate or _ORDINARY_CAPITAL.match(first) or name in _ALL or name in NOT_PLACES
                or places.fold(first) in NOT_PLACES or any(part in _ALL for part in name.split())):
            continue
        if candidate not in unknown:
            unknown.append(candidate)
    join = ""
    if len(found) >= 2:
        between = folded[found[0][0]: found[-1][1]]
        has_or = bool(re.search(r"(?<![a-z])(?:or|o|u|either|ya sea)(?![a-z])", between))
        has_and = bool(re.search(r"(?<![a-z])(?:and|y|e|both|ambos|all)(?![a-z])", between))
        join = "or" if has_or and not has_and else "and" if has_and and not has_or else ""
    return {"countries": countries, "regions": regions, "places": unknown, "ambiguous": ambiguous, "connective": join}


# The place a question refers to by POSITION and not by name: the vacancy's own ('the country where this job is located', 'this
# location', 'the job's location'). Folded text, like ``names``: the answer is then the vacancy's country, never a named one.
PLACE_REFERENCE = re.compile(
    r"(?<![a-z0-9])(?:"
    r"the country (?:where|in which|in witch) (?:this|the|our) (?:job|role|position|vacancy|post|opening|office)(?: is)? "
    r"(?:located|based|situated|advertised)|"
    r"the country (?:for|to|in) which you are (?:applying|interviewing)|the country you are (?:applying|interviewing) (?:to|for|in)|"
    r"(?:this|the|our) (?:job|role|position|vacancy|post|company|office) s (?:location|country|jurisdiction)|"
    r"the (?:job|role|position|vacancy) (?:location|country)|the (?:location|country) of (?:this|the) (?:job|role|position|vacancy)|"
    r"this (?:country|location|jurisdiction)|where (?:this|the) (?:job|role|position) is (?:located|based)|"
    r"el pais (?:donde|en el que|en el cual) (?:se (?:encuentra|ubica|situa)|esta) (?:este|el|la|esta) (?:puesto|trabajo|empleo|oferta|vacante)|"
    r"el pais (?:del|de la|de esta) (?:puesto|oferta|vacante)|(?:este|esta) (?:pais|ubicacion)|"
    r"la ubicacion (?:del|de este|de esta) (?:puesto|trabajo|oferta)"
    r")(?![a-z0-9])")
MASK = "zzplace"


def mask(text) -> str:
    """The text of a question with every place it names written as ``zzplace``: folded (lower case, ASCII, one space between
    words). A known country, region or city, a capitalised name after a work preposition that no table knows ('work in
    Kyiv') and a reference to the vacancy's own place ('the country where this job is located') are all the same token: a
    grammar over a question reads WHERE as a slot, and ``named_places`` / ``engine.jurisdiction_of`` say which place it is."""
    raw = clean(text)
    spans = []
    for match in _PLACE_AFTER_WORK.finditer(raw):
        candidate = re.sub(r"[.,;:]+$", "", match.group(1)).strip()
        first = candidate.split()[0] if candidate else ""
        name = places.fold(candidate)
        if (not candidate or _ORDINARY_CAPITAL.match(first) or name in _ALL or name in NOT_PLACES
                or places.fold(first) in NOT_PLACES or any(part in _ALL for part in name.split())):
            continue
        spans.append((match.start(1), match.start(1) + len(candidate)))
    for begin, end in reversed(spans):
        raw = raw[:begin] + " " + MASK + " " + raw[end:]
    return mask_folded(places.fold(raw))


def mask_folded(folded: str) -> str:
    """``mask`` for a text that is folded already (the known names and the references to the vacancy's place only)."""
    folded = _PATTERN.sub(" " + MASK + " ", folded)
    folded = PLACE_REFERENCE.sub(" " + MASK + " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def country_names(country) -> set[str]:
    """Every name of a COUNTRY itself (folded: 'united kingdom', 'uk', 'the uk', 'great britain'), none of a city or a region of it:
    'Are you based in the UK?' is true of a person who lives in the UK, 'in London' only of one who lives in London."""
    wanted = canonical(country)
    found = {name for name, value in {**places.BY_NAME, **{places.fold(k): v for k, v in EXTRA.items()}}.items() if value == wanted}
    return {name[4:] if name.startswith("the ") else name for name in found} - PARTS_OF_A_COUNTRY


# the nations of the UK name a PART of it: 'Are you based in Scotland?' is not 'Are you based in the UK?' the other way round
PARTS_OF_A_COUNTRY = {"england", "scotland", "wales", "northern ireland", "n ireland", "inglaterra", "escocia", "gales", "irlanda del norte"}


def place_of_job(job: dict, candidate: str) -> bool:
    """Is this capitalised name the place the vacancy itself is in? (then it is no other jurisdiction)"""
    wanted = places.fold(candidate)
    for key in ("location", "company", "title"):
        if wanted and re.search(r"(?<![a-z0-9])" + re.escape(wanted) + r"(?![a-z0-9])", places.fold(job.get(key) or "")):
            return True
    return False
