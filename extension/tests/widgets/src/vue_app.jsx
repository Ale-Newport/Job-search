// REAL Vue 3 (runtime compiler, in-DOM template): v-model on text / email (.trim) / number (.number) / .lazy inputs, textarea, select,
// multiple select, radio group, single checkbox, checkbox group bound to an array, and a custom component using v-model.
import { createApp, ref, reactive, toRaw } from "vue";

const FancyInput = {
  props: ["modelValue", "label", "id"],
  emits: ["update:modelValue"],
  template: `<div class="field"><label class="label" :for="id">{{ label }}</label><input :id="id" type="text" :value="modelValue" @input="$emit('update:modelValue', $event.target.value)"></div>`,
};

const app = createApp({
  components: { FancyInput },
  setup() {
    const s = reactive({ first: "", email: "", years: null, lazy: "", bio: "", country: "", skills: [], auth: "", alerts: false, langs: [], fancy: "" });
    window.__state = () => JSON.parse(JSON.stringify(toRaw(s)));
    return { s, ref };
  },
  template: `
  <form @submit.prevent>
    <h1>Vue 3 demo application</h1>
    <div class="field"><label class="label" for="v_first">First name</label><input id="v_first" name="first" v-model="s.first"></div>
    <div class="field"><label class="label" for="v_email">Email (v-model.trim)</label><input id="v_email" name="email" type="email" v-model.trim="s.email"></div>
    <div class="field"><label class="label" for="v_years">Years of experience (v-model.number)</label><input id="v_years" name="years" type="number" v-model.number="s.years"></div>
    <div class="field"><label class="label" for="v_lazy">Reference code (v-model.lazy: updates on change)</label><input id="v_lazy" name="lazy" v-model.lazy="s.lazy"></div>
    <div class="field"><label class="label" for="v_bio">Short bio</label><textarea id="v_bio" name="bio" v-model="s.bio"></textarea></div>
    <div class="field"><label class="label" for="v_country">Country</label>
      <select id="v_country" name="country" v-model="s.country"><option value="">Select</option><option value="gb">United Kingdom</option><option value="es">Spain</option><option value="de">Germany</option></select></div>
    <div class="field"><label class="label" for="v_skills">Skills (multiple select)</label>
      <select id="v_skills" name="skills" multiple v-model="s.skills"><option value="py">Python</option><option value="ts">TypeScript</option><option value="go">Go</option></select></div>
    <fieldset class="field"><legend class="label">Are you authorised to work in the UK?</legend>
      <label><input type="radio" name="auth" value="yes" v-model="s.auth"> Yes</label>
      <label><input type="radio" name="auth" value="no" v-model="s.auth"> No</label></fieldset>
    <div class="field"><label><input type="checkbox" name="alerts" v-model="s.alerts"> Send me job alerts</label></div>
    <fieldset class="field"><legend class="label">Languages</legend>
      <label><input type="checkbox" name="langs" value="en" v-model="s.langs"> English</label>
      <label><input type="checkbox" name="langs" value="es" v-model="s.langs"> Spanish</label>
      <label><input type="checkbox" name="langs" value="fr" v-model="s.langs"> French</label></fieldset>
    <fancy-input id="v_fancy" label="Nickname (custom component with v-model)" v-model="s.fancy"></fancy-input>
    <button type="submit">Submit application</button>
  </form>`,
});
app.mount("#root");
