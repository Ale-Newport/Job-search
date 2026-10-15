// REAL Select2 4 on jQuery 3 (iCIMS, Taleo, many older ATS pages): the native <select> is clipped to 1px and replaced by a
// span role=combobox whose results are <li role=option> in a container appended to <body>, chosen on mouseup.
import $ from "jquery";
import initSelect2 from "select2/dist/js/select2.full.js";
import "select2/dist/css/select2.min.css";

window.jQuery = window.$ = $;
initSelect2(window, $);

const COUNTRIES = [["gb", "United Kingdom"], ["es", "Spain"], ["de", "Germany"], ["fr", "France"], ["us", "United States"]];
const options = (list) => list.map(([v, t]) => `<option value="${v}">${t}</option>`).join("");
const group = (id, label, control) => `<div class="form-group field"><label for="${id}" class="label">${label}</label>${control}</div>`;

document.getElementById("root").innerHTML = `<form onsubmit="return false"><h1>Select2 demo application</h1>
  ${group("nick", "Preferred name", '<input id="nick" name="nick" type="text">')}
  ${group("country_s2", "Country", `<select id="country_s2" name="country" class="form-control"><option></option>${options(COUNTRIES)}</select>`)}
  ${group("skills_s2", "Skills", `<select id="skills_s2" name="skills" multiple class="form-control">${options([["py", "Python"], ["ts", "TypeScript"], ["go", "Go"]])}</select>`)}
  ${group("level_s2", "Seniority (search box disabled)", `<select id="level_s2" name="level" class="form-control"><option></option>${options([["jr", "Junior"], ["sr", "Senior"], ["st", "Staff"]])}</select>`)}
  ${group("city_s2", "Where are you based? (remote search)", '<select id="city_s2" name="city" class="form-control"></select>')}
  <button type="submit">Submit application</button></form>`;

$("#country_s2").select2({ placeholder: "Select a country", width: "100%" });
$("#skills_s2").select2({ placeholder: "Pick skills", width: "100%" });
$("#level_s2").select2({ placeholder: "Select level", minimumResultsForSearch: Infinity, width: "100%" });
const CITIES = ["London, UK", "London, Ontario, Canada", "Madrid, Spain", "Manchester, UK"];
$("#city_s2").select2({
  placeholder: "Type a city", width: "100%", minimumInputLength: 2,
  ajax: { transport: (params, success) => { setTimeout(() => { const q = (params.data.term || "").toLowerCase(); success({ results: CITIES.filter((c) => c.toLowerCase().includes(q)).map((c) => ({ id: c, text: c })) }); }, 250); return { abort() {} }; },
    processResults: (data) => data },
});
window.__state = () => ({ country: $("#country_s2").val(), skills: $("#skills_s2").val(), level: $("#level_s2").val(), city: $("#city_s2").val() });
