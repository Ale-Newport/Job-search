"""Where a job is: countries and locations as employer pages state them (schema.org, ATS APIs, page text).

Pure functions, no network and no model. The browser assistant needs ONE thing from a posting's place that the answer
engine can trust: the country the work is done in (work authorisation, sponsorship and salary answers are scoped by
it). Everything here is deliberately conservative: a country is named only when the page states it (an ISO code, a
country name or a place the engine's own ``target_country`` knows); anything ambiguous (a posting open in several
countries, a remote role with no stated country) yields "" so the engine answers "needs you" instead of guessing.

Country names are the English names the engine uses (``intelligence.engine.country_name``), so the value can be put in
``job["country"]`` and compared with the candidate's own immigration facts.
"""

from __future__ import annotations

import re
import unicodedata

# ISO 3166-1 alpha-2, alpha-3 and the English short name used on application forms (one country per line).
_COUNTRIES = """
AF AFG Afghanistan
AX ALA Åland Islands
AL ALB Albania
DZ DZA Algeria
AS ASM American Samoa
AD AND Andorra
AO AGO Angola
AI AIA Anguilla
AQ ATA Antarctica
AG ATG Antigua and Barbuda
AR ARG Argentina
AM ARM Armenia
AW ABW Aruba
AU AUS Australia
AT AUT Austria
AZ AZE Azerbaijan
BS BHS Bahamas
BH BHR Bahrain
BD BGD Bangladesh
BB BRB Barbados
BY BLR Belarus
BE BEL Belgium
BZ BLZ Belize
BJ BEN Benin
BM BMU Bermuda
BT BTN Bhutan
BO BOL Bolivia
BQ BES Bonaire, Sint Eustatius and Saba
BA BIH Bosnia and Herzegovina
BW BWA Botswana
BV BVT Bouvet Island
BR BRA Brazil
IO IOT British Indian Ocean Territory
BN BRN Brunei
BG BGR Bulgaria
BF BFA Burkina Faso
BI BDI Burundi
CV CPV Cape Verde
KH KHM Cambodia
CM CMR Cameroon
CA CAN Canada
KY CYM Cayman Islands
CF CAF Central African Republic
TD TCD Chad
CL CHL Chile
CN CHN China
CX CXR Christmas Island
CC CCK Cocos Islands
CO COL Colombia
KM COM Comoros
CG COG Congo
CD COD Democratic Republic of the Congo
CK COK Cook Islands
CR CRI Costa Rica
CI CIV Cote d'Ivoire
HR HRV Croatia
CU CUB Cuba
CW CUW Curacao
CY CYP Cyprus
CZ CZE Czech Republic
DK DNK Denmark
DJ DJI Djibouti
DM DMA Dominica
DO DOM Dominican Republic
EC ECU Ecuador
EG EGY Egypt
SV SLV El Salvador
GQ GNQ Equatorial Guinea
ER ERI Eritrea
EE EST Estonia
SZ SWZ Eswatini
ET ETH Ethiopia
FK FLK Falkland Islands
FO FRO Faroe Islands
FJ FJI Fiji
FI FIN Finland
FR FRA France
GF GUF French Guiana
PF PYF French Polynesia
TF ATF French Southern Territories
GA GAB Gabon
GM GMB Gambia
GE GEO Georgia
DE DEU Germany
GH GHA Ghana
GI GIB Gibraltar
GR GRC Greece
GL GRL Greenland
GD GRD Grenada
GP GLP Guadeloupe
GU GUM Guam
GT GTM Guatemala
GG GGY Guernsey
GN GIN Guinea
GW GNB Guinea-Bissau
GY GUY Guyana
HT HTI Haiti
HM HMD Heard Island and McDonald Islands
VA VAT Vatican City
HN HND Honduras
HK HKG Hong Kong
HU HUN Hungary
IS ISL Iceland
IN IND India
ID IDN Indonesia
IR IRN Iran
IQ IRQ Iraq
IE IRL Ireland
IM IMN Isle of Man
IL ISR Israel
IT ITA Italy
JM JAM Jamaica
JP JPN Japan
JE JEY Jersey
JO JOR Jordan
KZ KAZ Kazakhstan
KE KEN Kenya
KI KIR Kiribati
KP PRK North Korea
KR KOR South Korea
KW KWT Kuwait
KG KGZ Kyrgyzstan
LA LAO Laos
LV LVA Latvia
LB LBN Lebanon
LS LSO Lesotho
LR LBR Liberia
LY LBY Libya
LI LIE Liechtenstein
LT LTU Lithuania
LU LUX Luxembourg
MO MAC Macao
MG MDG Madagascar
MW MWI Malawi
MY MYS Malaysia
MV MDV Maldives
ML MLI Mali
MT MLT Malta
MH MHL Marshall Islands
MQ MTQ Martinique
MR MRT Mauritania
MU MUS Mauritius
YT MYT Mayotte
MX MEX Mexico
FM FSM Micronesia
MD MDA Moldova
MC MCO Monaco
MN MNG Mongolia
ME MNE Montenegro
MS MSR Montserrat
MA MAR Morocco
MZ MOZ Mozambique
MM MMR Myanmar
NA NAM Namibia
NR NRU Nauru
NP NPL Nepal
NL NLD Netherlands
NC NCL New Caledonia
NZ NZL New Zealand
NI NIC Nicaragua
NE NER Niger
NG NGA Nigeria
NU NIU Niue
NF NFK Norfolk Island
MK MKD North Macedonia
MP MNP Northern Mariana Islands
NO NOR Norway
OM OMN Oman
PK PAK Pakistan
PW PLW Palau
PS PSE Palestine
PA PAN Panama
PG PNG Papua New Guinea
PY PRY Paraguay
PE PER Peru
PH PHL Philippines
PN PCN Pitcairn Islands
PL POL Poland
PT PRT Portugal
PR PRI Puerto Rico
QA QAT Qatar
RE REU Reunion
RO ROU Romania
RU RUS Russia
RW RWA Rwanda
BL BLM Saint Barthelemy
SH SHN Saint Helena
KN KNA Saint Kitts and Nevis
LC LCA Saint Lucia
MF MAF Saint Martin
PM SPM Saint Pierre and Miquelon
VC VCT Saint Vincent and the Grenadines
WS WSM Samoa
SM SMR San Marino
ST STP Sao Tome and Principe
SA SAU Saudi Arabia
SN SEN Senegal
RS SRB Serbia
SC SYC Seychelles
SL SLE Sierra Leone
SG SGP Singapore
SX SXM Sint Maarten
SK SVK Slovakia
SI SVN Slovenia
SB SLB Solomon Islands
SO SOM Somalia
ZA ZAF South Africa
GS SGS South Georgia and the South Sandwich Islands
SS SSD South Sudan
ES ESP Spain
LK LKA Sri Lanka
SD SDN Sudan
SR SUR Suriname
SJ SJM Svalbard and Jan Mayen
SE SWE Sweden
CH CHE Switzerland
SY SYR Syria
TW TWN Taiwan
TJ TJK Tajikistan
TZ TZA Tanzania
TH THA Thailand
TL TLS Timor-Leste
TG TGO Togo
TK TKL Tokelau
TO TON Tonga
TT TTO Trinidad and Tobago
TN TUN Tunisia
TR TUR Turkey
TM TKM Turkmenistan
TC TCA Turks and Caicos Islands
TV TUV Tuvalu
UG UGA Uganda
UA UKR Ukraine
AE ARE United Arab Emirates
GB GBR United Kingdom
US USA United States
UM UMI United States Minor Outlying Islands
UY URY Uruguay
UZ UZB Uzbekistan
VU VUT Vanuatu
VE VEN Venezuela
VN VNM Vietnam
VG VGB British Virgin Islands
VI VIR US Virgin Islands
WF WLF Wallis and Futuna
EH ESH Western Sahara
YE YEM Yemen
ZM ZMB Zambia
ZW ZWE Zimbabwe
XK XKX Kosovo
"""

# Names a posting uses for the same country (folded: lower case, ASCII, words separated by one space).
_ALIASES = {
    "uk": "GB", "u k": "GB", "great britain": "GB", "britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "northern ireland": "GB", "united kingdom of great britain and northern ireland": "GB", "reino unido": "GB",
    "usa": "US", "u s": "US", "u s a": "US", "united states of america": "US", "estados unidos": "US",
    "uae": "AE", "emirates": "AE", "the netherlands": "NL", "holland": "NL", "paises bajos": "NL", "czechia": "CZ",
    "turkiye": "TR", "russian federation": "RU", "republic of korea": "KR", "viet nam": "VN", "ivory coast": "CI",
    "cabo verde": "CV", "macau": "MO", "espana": "ES", "deutschland": "DE", "alemania": "DE", "francia": "FR",
    "italia": "IT", "brasil": "BR", "peoples republic of china": "CN", "pr china": "CN",
    "burma": "MM", "swaziland": "SZ", "east timor": "TL", "republic of ireland": "IE", "eire": "IE",
    "hong kong sar": "HK", "macao sar": "MO", "palestinian territories": "PS", "state of palestine": "PS",
    "the united states": "US", "the united kingdom": "GB",
}
# Never a country in running text: a US state (or a town) is far more likely ('Atlanta, Georgia').
_NOT_A_COUNTRY_IN_TEXT = {"georgia"}
# ISO codes that are ALSO a US state, Canadian province or US territory abbreviation: 'San Francisco, CA' is not Canada.
AMBIGUOUS_CODES = {
    "AL", "AR", "AS", "AZ", "CA", "CO", "DE", "GA", "GU", "ID", "IL", "IN", "LA", "MA", "MD", "ME", "MN", "MO", "MP",
    "MS", "MT", "NC", "NE", "NL", "NU", "PA", "PE", "PR", "SC", "SD", "SK", "TN", "VA", "VI", "YT",
}


def fold(value) -> str:
    """Lower case, ASCII, words separated by one space ('Côte d'Ivoire' -> 'cote d ivoire')."""
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _load() -> tuple[dict, dict, dict]:
    by2: dict[str, str] = {}
    by3: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for line in _COUNTRIES.strip().splitlines():
        two, three, name = line.split(" ", 2)
        by2[two], by3[three] = name, name
        by_name[fold(name)] = name
    for alias, code in _ALIASES.items():
        by_name[alias] = by2[code]
    return by2, by3, by_name


ALPHA2, ALPHA3, BY_NAME = _load()


def country_of(value) -> str:
    """One country name from a schema.org ``addressCountry`` (a name, an ISO alpha-2/alpha-3 code or ``{"name": ...}``)
    or a plain country name. '' when the value does not name exactly one country."""
    if isinstance(value, dict):
        value = value.get("name") or value.get("alternateName") or value.get("@id") or value.get("identifier") or ""
    if isinstance(value, list):
        names = {country_of(v) for v in value[:6]} - {""}
        return names.pop() if len(names) == 1 else ""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or len(text) > 80:
        return ""
    if "://" in text:
        text = text.rstrip("/").rsplit("/", 1)[-1]
    if len(text) == 2 and text.isalpha():
        return "United Kingdom" if text.upper() == "UK" else ALPHA2.get(text.upper(), "")
    if len(text) == 3 and text.isalpha() and text.isupper():
        return ALPHA3.get(text, "")
    return BY_NAME.get(fold(text), "")


def _segments(text: str) -> list[str]:
    """Separate places of one string: 'London, UK; Paris, FR' -> ['London, UK', 'Paris, FR']."""
    return [s.strip() for s in re.split(r"[;\n|]|\s/\s", str(text or "")) if s.strip()]


def _parts(segment: str) -> list[str]:
    cleaned = re.sub(r"[()\[\]{}]", ",", segment)
    return [p.strip() for p in re.split(r"[,\u2013\u2014/]|\s-\s|\s\u00b7\s", cleaned) if p.strip()]


def _part_country(part: str, *, last: bool, codes: bool) -> str:
    if len(part) == 2 and part.isalpha():
        if part in {"UK", "US"}:  # never a state or a word in capitals
            return "United Kingdom" if part == "UK" else "United States"
        # Any other bare code is a country only in capitals, as the last part ('Berlin, DE'), and never when it is also
        # a US state or Canadian province ('San Francisco, CA').
        return country_of(part) if codes and last and part.isupper() and part not in AMBIGUOUS_CODES else ""
    if len(part) == 3 and part.isalpha() and part.isupper():
        return ALPHA3.get(part, "") if codes else ""
    name = fold(part)
    return "" if name in _NOT_A_COUNTRY_IN_TEXT else BY_NAME.get(name, "")


def _known_place(part: str) -> str:
    """The country of a place the answer engine itself knows (London, Paris, Berlin ...), else ''."""
    from ..intelligence.engine import target_country
    return target_country({"location": part}) or ""


def countries_in_text(text: str, *, codes: bool = True, known_places: bool = False) -> list[str]:
    """Countries a location string names, in order, without repeats: 'London, UK' -> ['United Kingdom'].

    Whole comma/dash separated parts only, so words never read as countries ('IT Support', 'New Mexico'); several
    places ('London; Paris') are read one by one. With ``known_places`` a place that names no country is looked up in
    the answer engine's own place list, so the result agrees with ``engine.target_country``."""
    found: list[str] = []
    for segment in _segments(text):
        parts = _parts(segment)
        named = [_part_country(part, last=position == len(parts) - 1, codes=codes) for position, part in enumerate(parts)]
        if any(named) and len(parts) > 1 and parts[-1] in AMBIGUOUS_CODES:
            continue  # a town that shares a country's name followed by a state ('Lebanon, PA'): not that country
        names = [n for n in named if n]
        if not names and known_places:
            names = [n for n in (_known_place(part) for part in parts) if n]
        found.extend(n for n in dict.fromkeys(names) if n not in found)
    return found


REMOTE = re.compile(r"\b(?:remote|remoto|telecommute|teletrabajo|home ?office|work from home)\b", re.I)
NO_PLACE = {"location": "", "country": "", "countries": [], "remote": False}


def _name(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("addressLocality") or "")
    if isinstance(value, list):
        return ", ".join(x for x in (_name(v) for v in value[:3]) if x)
    return str(value or "")


def _as_list(value) -> list:
    return value if isinstance(value, list) else ([] if value in (None, "") else [value])


def _entry(value) -> dict:
    """One schema.org ``Place`` / ``PostalAddress`` / plain string -> {text, countries}."""
    if isinstance(value, str):
        text = re.sub(r"\s+", " ", value).strip()
        return {"text": text, "countries": _countries_of_text(text)}
    if not isinstance(value, dict):
        return {"text": "", "countries": []}
    address = value.get("address")
    if isinstance(address, str):
        inner = _entry(address)
        return {"text": inner["text"] or _name(value.get("name")), "countries": inner["countries"]}
    address = address if isinstance(address, dict) else value
    stated = address.get("addressCountry")
    parts = [_name(address.get("addressLocality")), _name(address.get("addressRegion")), _name(stated)]
    text = ", ".join(p.strip() for p in parts if p and p.strip()) or _name(value.get("name"))
    if _name(stated).strip():
        # The page names the country: it is that one or none (an unreadable value is never replaced by a guess).
        named = country_of(stated)
        return {"text": text, "countries": [named] if named else []}
    return {"text": text, "countries": _countries_of_text(text)}


def _countries_of_text(text: str) -> list[str]:
    return countries_in_text(text, known_places=True)


def jsonld_place(node: dict) -> dict:
    """The place of a schema.org ``JobPosting`` node: ``{location, country, countries, remote}``.

    ``jobLocation`` (a Place, a PostalAddress, a string or a list of them) names where the work is; ``addressCountry``
    may be a name, an ISO code or a Country object. ``jobLocationType: TELECOMMUTE`` makes the role remote, and then
    the countries applicants must live in (``applicantLocationRequirements``) say where the work is done. Several
    countries leave ``country`` empty: the engine must not pick one for the candidate."""
    kinds = {str(k).upper() for k in _as_list(node.get("jobLocationType"))}
    entries = [e for e in (_entry(v) for v in _as_list(node.get("jobLocation"))[:6]) if e["text"] or e["countries"]]
    texts = [e["text"] for e in entries if e["text"]][:3]
    placed = list(dict.fromkeys(c for e in entries for c in e["countries"]))
    restrictions = _as_list(node.get("applicantLocationRequirements"))[:8]
    required = list(dict.fromkeys(c for c in (country_of(v) for v in restrictions) if c))
    telecommute = "TELECOMMUTE" in kinds
    location = "; ".join(texts)
    remote = telecommute or bool(REMOTE.search(location))
    # A remote job is worked from where the applicant lives: when the posting restricts that ('Europe', a state, three
    # countries) and no single country can be read from it, the office's country says nothing about the applicant's.
    countries = required if telecommute and restrictions else placed
    if telecommute and not REMOTE.search(location):
        detail = location or ", ".join(required)
        location = f"Remote ({detail})" if detail else "Remote"
    return {"location": location[:200], "country": countries[0] if len(countries) == 1 else "",
            "countries": countries[:6], "remote": remote}


def text_place(text: str) -> dict:
    """The place of a plain location string (an ATS API field, a stored job row, a label on the page)."""
    value = re.sub(r"\s+", " ", str(text or "")).strip()[:200]
    if not value:
        return dict(NO_PLACE)
    named = _countries_of_text(value)
    return {"location": value, "country": named[0] if len(named) == 1 else "", "countries": named[:6],
            "remote": bool(REMOTE.search(value))}


LABELLED = re.compile(
    r"(?im)^[ \t>*\u2022-]*(?:job |work |office |primary |role )?(?:location|locations|based in|ubicaci[o\u00f3]n|"
    r"lugar de trabajo)[ \t]*[:\-\u2013\u2014][ \t]*([^\n]{2,100})$")


def labelled_location(text: str) -> str:
    """'Location: London, UK' from the visible description: a line that is only the label and the place."""
    match = LABELLED.search(str(text or ""))
    return match.group(1).strip(" .") if match else ""


def title_place(*titles: str) -> dict:
    """A place a page title states WITH a country ('Backend Engineer (London, UK)', 'Data Analyst | Madrid, Spain').
    Company names and departments never qualify: only a part that names exactly one country."""
    for title in titles:
        candidates = [m.group(1) for m in re.finditer(r"\(([^()]{3,80})\)", str(title or ""))]
        candidates += re.split(r"\s+[|\u2013\u2014-]\s+", str(title or ""))[1:]
        for part in candidates:
            named = countries_in_text(part, codes=False)
            if len(named) == 1:
                return {"location": re.sub(r"\s+", " ", part).strip()[:200], "country": named[0], "countries": named,
                        "remote": bool(REMOTE.search(part))}
    return dict(NO_PLACE)
