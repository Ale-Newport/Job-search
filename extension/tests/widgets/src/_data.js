// Shared demo data. Everything is synthetic: no real person, no real employer.
export const COUNTRY_NAMES = ("Afghanistan,Albania,Algeria,Andorra,Angola,Argentina,Armenia,Australia,Austria,Azerbaijan,Bahamas,Bahrain,Bangladesh,Barbados," +
  "Belarus,Belgium,Belize,Benin,Bhutan,Bolivia,Bosnia and Herzegovina,Botswana,Brazil,Brunei,Bulgaria,Burkina Faso,Burundi,Cambodia,Cameroon,Canada," +
  "Cape Verde,Chad,Chile,China,Colombia,Comoros,Costa Rica,Croatia,Cuba,Cyprus,Czechia,Denmark,Djibouti,Dominican Republic,Ecuador,Egypt,El Salvador," +
  "Eritrea,Estonia,Eswatini,Ethiopia,Fiji,Finland,France,Gabon,Gambia,Georgia,Germany,Ghana,Greece,Guatemala,Guinea,Guyana,Haiti,Honduras,Hungary," +
  "Iceland,India,Indonesia,Iran,Iraq,Ireland,Israel,Italy,Jamaica,Japan,Jordan,Kazakhstan,Kenya,Kuwait,Kyrgyzstan,Laos,Latvia,Lebanon,Lesotho,Liberia," +
  "Libya,Liechtenstein,Lithuania,Luxembourg,Madagascar,Malawi,Malaysia,Maldives,Mali,Malta,Mauritania,Mauritius,Mexico,Moldova,Monaco,Mongolia," +
  "Montenegro,Morocco,Mozambique,Myanmar,Namibia,Nepal,Netherlands,New Zealand,Nicaragua,Niger,Nigeria,North Macedonia,Norway,Oman,Pakistan,Panama," +
  "Paraguay,Peru,Philippines,Poland,Portugal,Qatar,Romania,Russia,Rwanda,Saudi Arabia,Senegal,Serbia,Seychelles,Sierra Leone,Singapore,Slovakia," +
  "Slovenia,Somalia,South Africa,South Korea,Spain,Sri Lanka,Sudan,Suriname,Sweden,Switzerland,Syria,Taiwan,Tajikistan,Tanzania,Thailand,Togo," +
  "Trinidad and Tobago,Tunisia,Turkey,Turkmenistan,Uganda,Ukraine,United Arab Emirates,United Kingdom,United States,Uruguay,Uzbekistan,Venezuela," +
  "Vietnam,Yemen,Zambia,Zimbabwe").split(",");

/** 1,000 options, alphabetical, so that "United Kingdom" is far beyond whatever window a virtual list renders first. */
export const BIG_COUNTRIES = (() => {
  const names = new Set(COUNTRY_NAMES);
  const syllables = ["bar", "dor", "fen", "gal", "hol", "kir", "lum", "mar", "nor", "ost", "pel", "quan", "ros", "sul", "tav", "uld", "vor", "wen", "yar", "zel"];
  let i = 0;
  while (names.size < 1000) {
    const a = syllables[i % 20], b = syllables[Math.floor(i / 20) % 20], c = syllables[Math.floor(i / 400) % 20];
    names.add(`${a[0].toUpperCase()}${a.slice(1)}${b}${c}ia`);
    i += 1;
  }
  return [...names].sort((x, y) => x.localeCompare(y));
})();

export const CITIES = ["London, England, United Kingdom", "London, Ontario, Canada", "Madrid, Spain", "Manchester, England, United Kingdom", "Berlin, Germany",
  "Lisbon, Portugal", "Paris, France", "Dublin, Ireland", "Amsterdam, Netherlands", "Edinburgh, Scotland, United Kingdom"];

export const opt = (label, value) => ({ label, value: value ?? label.toLowerCase().replace(/[^a-z0-9]+/g, "-") });
export const delay = (ms) => new Promise((r) => setTimeout(r, ms));
