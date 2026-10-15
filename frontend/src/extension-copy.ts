import type { ExtensionBridge, ExtensionOverallState } from "./types";

/**
 * Product copy for the browser assistant, English and Spanish.
 *
 * The desktop UI stays English (it has no i18n layer). Spanish is rendered in the panel's
 * "Guía rápida en español" block, which shows the Spanish equivalent of whatever the English panel
 * says right now. `es` is typed against `en`, so a missing or extra key fails the typecheck.
 * Register: informal "tú", as in docs/INICIO_RAPIDO.es.md.
 */
export interface StateVars {
  browser: string; // "Chrome" | "Safari" | "Chrome and Safari"
  port?: number | null;
  appVersion?: string;
  extVersion?: string;
  holder?: string | null; // process holding the first port, when known
  ports?: number[]; // the ports Meridian tries
  bridgeState?: ExtensionBridge["state"];
}
/** [47615, …, 47624] -> "47615 to 47624" (`word` is "to" / "a"); one port stays as it is, a gap lists the ports. */
export function portRange(ports: number[] | undefined, word = "to"): string {
  const sorted = [...(ports ?? [])].sort((a, b) => a - b);
  if (!sorted.length) return "";
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  if (sorted.length === 1) return String(first);
  return last - first === sorted.length - 1 ? `${first} ${word} ${last}` : sorted.join(", ");
}
export interface StateCopy {
  headline: string;
  body: string;
  tag: string;
}
const en = {
  states: {
    disabled: (): StateCopy => ({
      headline: "The browser assistant is turned off",
      body: "The extension cannot reach your profile while this is off. Nothing is shared with any website.",
      tag: "Turned off",
    }),
    bridge_error: (v: StateVars): StateCopy => ({
      headline: "Meridian cannot open its browser connection",
      body:
        v.bridgeState === "port_in_use"
          ? `Meridian could not find a free port for its browser connection: every port from ${portRange(v.ports, "to")} is taken${v.holder ? ` (${v.holder} is using ${v.port ?? "the first one"})` : ""}. The extension cannot reach Meridian until one is free. Quit the app that is using them, then try again.`
          : "Meridian's browser connection stopped working, so the extension cannot reach Meridian. Try again. If it keeps failing, restart Meridian.",
      tag: "Needs attention",
    }),
    pairing: (v: StateVars): StateCopy => ({
      headline: `${v.browser} wants to connect`,
      body: "Check that the code below matches the one in the extension, then approve. Only approve if you just clicked Connect.",
      tag: "Waiting for you",
    }),
    outdated: (v: StateVars): StateCopy => ({
      headline: `Update the assistant in ${v.browser}`,
      body: `Meridian ${v.appVersion ?? ""} is newer than the extension ${v.extVersion ?? ""}. Reload the extension to update it. Some suggestions may be missing until you do.`,
      tag: "Update needed",
    }),
    ready: (v: StateVars): StateCopy => ({
      headline: `Assistant ready in ${v.browser}`,
      body: "Open a job application, click the Meridian icon, then click any field to see a suggestion. Press Tab to fill it and move to the next field.",
      tag: "Connected",
    }),
    detected: (v: StateVars): StateCopy => ({
      headline: "Almost there: connect the extension",
      body: `Meridian found the assistant in ${v.browser}. Click its icon in the toolbar and choose Connect. The extension shows a 6-digit code: type it into the Meridian window that opens, or approve the request here.`,
      tag: "Not connected",
    }),
    not_installed: (): StateCopy => ({
      headline: "Fill applications in your own browser",
      body: "Add the Meridian assistant to Chrome or Safari. On any job application page it suggests an answer for each field. Press Tab to accept it and move on. It never submits anything.",
      tag: "Not set up",
    }),
  } satisfies Record<ExtensionOverallState, (v: StateVars) => StateCopy>,
  pairing: {
    eyebrow: (browser: string) => `PAIRING REQUEST · ${browser.toUpperCase()}`,
    title: "Approve this connection?",
    compare: "The extension is showing a 6-digit code. Check that it is the same as the one here.",
    dialog:
      "Meridian also opens a small window that asks for the code. Type the extension's code there to connect, or approve here once the two codes match. Whichever you do first counts, and three wrong codes deny the request.",
    version: (version: string) => `Extension ${version}.`,
    expired: "This request expired. Click Connect in the extension to start again.",
    expiresIn: (clock: string) => `Expires in ${clock}`,
    unrecognised:
      "Unrecognised extension. This is not the Meridian Assistant that comes with this app. Deny it unless you installed it yourself.",
    announce: (browser: string, spoken: string) =>
      `${browser} wants to connect to Meridian. Pairing code ${spoken}.`,
  },
  approve: "Approve connection",
  deny: "Deny",
  disconnectTitle: (browser: string) => `Disconnect ${browser}?`,
  disconnectBody:
    "The extension will stop receiving suggestions until you connect it again. Nothing is deleted from your profile.",
  disconnect: "Disconnect",
  refreshFailed: "Could not refresh the assistant status. Retrying…",
  waiting: "Waiting for the extension to appear…",
  foundTitle: (browser: string) => `Found the extension in ${browser}. One step left.`,
  found: (browser: string) =>
    browser === "Safari"
      ? "Click the Meridian icon in Safari's toolbar (View > Customize Toolbar… adds it) and choose Connect. Then type the code it shows into the Meridian window that opens, or approve the request here."
      : `Click the Meridian icon in ${browser}'s toolbar (open the puzzle-piece menu if you don't see it) and choose Connect. Then type the code it shows into the Meridian window that opens, or approve the request here.`,
  bridge: {
    retry: "Try again",
    start: "Start it again",
    restored: "The browser connection is back.",
    still: "The browser connection is still blocked. Quit the other app and try again.",
  },
  help: {
    title: "If connecting does not work",
    items: [
      [
        "No Meridian window asks for the code",
        "Meridian opens it itself, and the request also appears at the top of this page. If the extension shows a code but Meridian shows no request, do not type the code anywhere: press Cancel in the extension, because something else on this Mac may be answering.",
      ],
      [
        "The extension says more than one program answers as Meridian",
        "Usually two copies of Meridian are open, for example the installed app and a development build. Quit the extra one, then press Retry in the extension. For your safety the extension sends nothing until only one answers.",
      ],
      [
        "The extension says Meridian isn't open, or lost its connection",
        "Open Meridian and the extension reconnects by itself. If you disconnected the browser here, press Connect in the extension again.",
      ],
      [
        "Edge, Brave and other Chromium browsers",
        "They can load the same folder, but Meridian lists them as Chrome and Open with assistant always opens Google Chrome.",
      ],
    ] as Array<[string, string]>,
    port: (port: number, range: string) =>
      `Meridian listens on this Mac only, on port ${port}. The extension looks for it on ports ${range}.`,
    portOutside: (port: number, range: string) =>
      `Meridian is listening on port ${port}, which the extension does not look at (it tries ports ${range}). The extension cannot find Meridian until it listens on one of those ports.`,
  },
  privacy: {
    title: "What the assistant will and won't do",
    items: [
      "It never submits an application, clicks Next, ticks consent boxes, solves CAPTCHAs or touches password fields. Those stay yours.",
      "It attaches only your own approved CV, never any other file: the one you chose for that application, else the one Meridian wrote for that job, else your default CV (Settings > Automation > Default CV), else your newest approved CV. A cover letter goes in only if Meridian wrote it for that job and you approved it.",
      "Suggestions come from your verified profile. Drafts are labelled and need your review.",
      "Your profile and answers stay on this Mac. The extension itself talks only to Meridian on this Mac (127.0.0.1), and what you type goes to Meridian only when you choose to save it.",
      "Meridian itself, not the extension, reaches out to prepare answers: it reads public pages on the company's own website and application system. When it cannot find the company's website it may also search the web for it, sending only the company name to a public search engine. You control that under Settings > Browser assistant > Preparing answers.",
      "The one part of your information that can leave this Mac is the text Meridian sends to the writing model you chose (Settings > AI & providers): the question, the job description and the verified facts needed to answer. A model that runs on this Mac sends nothing out.",
    ],
  },
  howTo: {
    title: "How it works",
    intro: "You press Next and Submit yourself. The assistant only helps with the fields.",
    keys: [
      ["Click a field", "Meridian shows its suggestion and where it comes from."],
      ["Tab", "Inserts the suggestion and moves to the next question."],
      ["Shift + Tab", "Goes back one field without changing anything."],
      ["Esc", "Hides the suggestion for this field."],
      ["Enter", "Inserts a sensitive answer, such as work authorisation. Tab does not, unless you allow it below."],
      ["⌥ Z", "Undoes the last insertion."],
      ["⌥ R", "Writes a long answer again."],
      ["⌃ ⌥ 1 to 7", "Runs the numbered buttons on a suggestion: Replace, Shorter, Longer, Different angle, Show more and the sources. Each button prints its key. These keys never type anything into the form. (Alt + Shift + 1 to 7 on other systems.)"],
      ["⌥ ⇧ M", "Moves keyboard focus to the Meridian panel, when the page allows it. Press it again to go back to the field you were on."],
      ["⌥ ⇧ Z", "Takes back everything the last “Fill verified” inserted, while the Undo all button is showing."],
      ["⌘ ⇧ Y", "Starts or stops the assistant on the page you are on. Chrome only, and only the default: another extension may use it, and you can change it at chrome://extensions/shortcuts. Safari has no such shortcut: click the Meridian icon."],
    ],
    more: "Everything else is on the extension's options page (Meridian icon, then Settings): Help lists the keys and colours.",
  },
  settings: {
    groups: {
      assistant: "Assistant",
      form: "While you fill a form",
      prepare: "Preparing answers",
      browser: "In your browser",
    },
    enabled: ["Turn on the browser assistant", "When off, the extension cannot reach Meridian."],
    autoAdvance: ["Tab fills and moves to the next field", "Turn off to fill with Tab but move on yourself."],
    skipFilled: ["Skip fields that already have a value", "Tab passes over questions you have already answered."],
    tabProtected: [
      "Let Tab insert sensitive answers",
      "Answers about work authorisation, demographics or legal matters normally need Enter or a click. Allow Tab only if you want them inserted as quickly as the rest.",
    ],
    tabProtectedConfirm: {
      title: "Let Tab insert sensitive answers?",
      body: "Holding Tab through a form could then insert answers about work authorisation, sponsorship or demographics without a second keypress. Meridian only inserts answers you confirmed, and you can switch this off at any time.",
      button: "Allow Tab",
    },
    saveCorrections: [
      "When you correct a suggestion",
      "Meridian can remember your version for next time. Sensitive answers are always confirmed first.",
    ],
    saveOptions: { ask: "Ask me each time", always: "Save automatically", never: "Never save" },
    research: [
      "Research the company and role in the background",
      "Uses public pages to prepare personalised answers before you reach the question.",
    ],
    webLookup: [
      "Search the web for the company's official site",
      "Only when the job page does not link to it. Sends just the company name to a public search engine.",
    ],
    localAssist: [
      "Let local AI finish what the profile alone cannot",
      "Laya and your local writing model (Ollama) pick among the options a form offers and write a value the way the field asks (a date as dd-mm-yyyy, a number without spaces). They run on this Mac, cost nothing and never invent a value: what they pick is shown in amber with the facts it came from.",
    ],
    drafts: ["Prepare long answers in advance", "Uses your answer model, counted in your monthly AI limit."],
    draftCap: [
      "Spending cap per application",
      "Stops preparing drafts for one application once it has cost this much. Leave empty to use the default of 1 USD per application.",
    ],
    hint: [
      "Show a discreet hint on known application sites",
      "A small Meridian pill appears on common job systems such as Greenhouse and Lever. It never fills anything on its own.",
    ],
    language: ["Language on websites", "Applies to the assistant's buttons and tips, not to your answers."],
    languageOptions: { auto: "Automatic", en: "English", es: "Español" },
    sites: [
      "Start automatically on these sites",
      "Starts the assistant as soon as a page from one of these sites loads, without the toolbar click. The browser must also let the extension work on each site: the extension's options page lists these sites, marks the ones that still need your permission, and asks for it.",
    ],
  },
  chrome: {
    title: "Google Chrome",
    install: "Install in Chrome",
    intro:
      "One click prepares the extension folder, copies its path, shows the folder in Finder and opens Chrome's extensions page. Three steps are left, all in Chrome:",
    steps: [
      "Turn on Developer mode, top right of the extensions page. Chrome requires it for any extension that does not come from the Chrome Web Store.",
      "Click Load unpacked, press ⌘⇧G, paste the folder path (⌘V), press Return and choose Open.",
      "Click the Meridian icon (pin it from the puzzle-piece menu to keep it in view) and choose Connect. The extension shows a 6-digit code: type it into the Meridian window that opens, or approve the request here.",
    ],
    installed: "The folder path is copied and Chrome's extensions page is open. Three steps are left in Chrome.",
    noChrome:
      "Google Chrome was not found in Applications. The button still prepares the folder, copies its path and shows it in Finder: open your Chromium browser's extensions page (chrome://extensions) yourself.",
    installedNoChrome:
      "The folder path is copied and the folder is shown in Finder. Open your browser's extensions page (chrome://extensions) yourself, then follow the three steps.",
    updateButton: "Update in Chrome",
    updateIntro: "One click refreshes the extension folder and opens Chrome's extensions page. Then:",
    updated: "The extension folder is refreshed and Chrome's extensions page is open.",
    updatedNoChrome:
      "The extension folder is refreshed. Open your browser's extensions page (chrome://extensions) yourself and reload Meridian Assistant.",
    update: [
      "Click the reload arrow on Meridian Assistant.",
      "Open a job application and click the Meridian icon to finish.",
    ],
    again: { copy: "Copy path again", reveal: "Show in Finder", open: "Open extensions page" },
    missing: "This build of Meridian does not include the Chrome extension.",
  },
  safari: {
    title: "Safari",
    steps: [
      "Open Safari, choose Safari > Settings > Extensions and tick Meridian Assistant.",
      "Allow it on websites: with Meridian Assistant selected, choose Edit Websites… and set it to Allow, ideally Always Allow on Every Website.",
      "Click the Meridian icon in Safari's toolbar (View > Customize Toolbar… adds it) and choose Connect. Then type the code it shows into the Meridian window, or approve the request here.",
    ],
    update: [
      "Open the updated Meridian from your Applications folder, then quit Safari (⌘Q) and open it again so it loads the new version.",
      "In Safari > Settings > Extensions, check that Meridian Assistant is still ticked and still allowed on your websites.",
    ],
    unseen:
      "Meridian cannot see Safari's settings, so it cannot tell whether the extension is ticked or which websites it may use. These steps change to done once the extension reaches Meridian.",
    applications: "macOS only finds the Safari extension in the copy of Meridian that sits in your Applications folder.",
    notListed: "Not listed in Safari's Extensions? Quit Safari (⌘Q) and open it again.",
    unsigned:
      "Still missing? This build is not notarised, so Safari may need Develop > Allow Unsigned Extensions (no Develop menu? turn on Safari > Settings > Advanced > Show features for web developers). Safari switches that option off every time it quits, so you may need it again after a restart.",
    missing: "This build of Meridian does not include the Safari extension.",
  },
};
type Dict = typeof en;
const es: Dict = {
  states: {
    disabled: () => ({
      headline: "El asistente de navegador está desactivado",
      body: "La extensión no puede acceder a tu perfil mientras esté desactivado. No se comparte nada con ninguna web.",
      tag: "Desactivado",
    }),
    bridge_error: (v) => ({
      headline: "Meridian no puede abrir su conexión con el navegador",
      body:
        v.bridgeState === "port_in_use"
          ? `Meridian no ha encontrado ningún puerto libre para su conexión con el navegador: todos los puertos de ${portRange(v.ports, "a")} están ocupados${v.holder ? ` (${v.holder} está usando el ${v.port ?? "primero"})` : ""}. La extensión no puede comunicarse con Meridian hasta que quede uno libre. Cierra la aplicación que los usa y vuelve a intentarlo.`
          : "La conexión de Meridian con el navegador ha dejado de funcionar, así que la extensión no puede comunicarse con Meridian. Vuelve a intentarlo. Si sigue fallando, reinicia Meridian.",
      tag: "Requiere atención",
    }),
    pairing: (v) => ({
      headline: `${v.browser} quiere conectarse`,
      body: "Comprueba que el código de abajo coincide con el de la extensión y apruébalo. Aprueba solo si acabas de pulsar Conectar.",
      tag: "Esperando tu respuesta",
    }),
    outdated: (v) => ({
      headline: `Actualiza el asistente en ${v.browser}`,
      body: `Meridian ${v.appVersion ?? ""} es más reciente que la extensión ${v.extVersion ?? ""}. Recarga la extensión para actualizarla. Hasta entonces pueden faltar algunas sugerencias.`,
      tag: "Actualización necesaria",
    }),
    ready: (v) => ({
      headline: `Asistente listo en ${v.browser}`,
      body: "Abre una candidatura, haz clic en el icono de Meridian y selecciona cualquier campo para ver una sugerencia. Pulsa Tab para rellenarlo y pasar al campo siguiente.",
      tag: "Conectado",
    }),
    detected: (v) => ({
      headline: "Casi listo: conecta la extensión",
      body: `Meridian ha detectado el asistente en ${v.browser}. Haz clic en su icono de la barra de herramientas y elige Conectar. La extensión muestra un código de 6 dígitos: escríbelo en la ventana de Meridian que se abre, o aprueba la solicitud aquí.`,
      tag: "Sin conectar",
    }),
    not_installed: () => ({
      headline: "Rellena candidaturas en tu propio navegador",
      body: "Añade el asistente de Meridian a Chrome o Safari. En cualquier página de candidatura te propone una respuesta para cada campo. Pulsa Tab para aceptarla y pasar al siguiente. Nunca envía nada.",
      tag: "Sin configurar",
    }),
  },
  pairing: {
    eyebrow: (browser) => `SOLICITUD DE CONEXIÓN · ${browser.toUpperCase()}`,
    title: "¿Aprobar esta conexión?",
    compare: "La extensión muestra un código de 6 dígitos. Comprueba que es el mismo que el de aquí.",
    dialog:
      "Meridian también abre una ventana pequeña que pide el código. Escribe ahí el código de la extensión para conectarla, o aprueba aquí cuando los dos códigos coincidan. Vale lo que hagas primero, y tres códigos erróneos rechazan la solicitud.",
    version: (version) => `Extensión ${version}.`,
    expired: "La solicitud ha caducado. Pulsa Conectar en la extensión para empezar de nuevo.",
    expiresIn: (clock) => `Caduca en ${clock}`,
    unrecognised:
      "Extensión no reconocida. No es el asistente de Meridian que viene con esta aplicación. Recházala a menos que la hayas instalado tú.",
    announce: (browser, spoken) => `${browser} quiere conectarse a Meridian. Código de conexión ${spoken}.`,
  },
  approve: "Aprobar conexión",
  deny: "Rechazar",
  disconnectTitle: (browser) => `¿Desconectar ${browser}?`,
  disconnectBody:
    "La extensión dejará de recibir sugerencias hasta que la vuelvas a conectar. No se borra nada de tu perfil.",
  disconnect: "Desconectar",
  refreshFailed: "No se pudo actualizar el estado del asistente. Reintentando…",
  waiting: "Esperando a que aparezca la extensión…",
  foundTitle: (browser) => `Extensión encontrada en ${browser}. Solo falta un paso.`,
  found: (browser) =>
    browser === "Safari"
      ? "Haz clic en el icono de Meridian en la barra de herramientas de Safari (Visualización > Personalizar barra de herramientas… lo añade) y elige Conectar. Después escribe el código que muestra en la ventana de Meridian que se abre, o aprueba la solicitud aquí."
      : `Haz clic en el icono de Meridian en la barra de herramientas de ${browser} (abre el menú del puzle si no lo ves) y elige Conectar. Después escribe el código que muestra en la ventana de Meridian que se abre, o aprueba la solicitud aquí.`,
  bridge: {
    retry: "Reintentar",
    start: "Iniciarla de nuevo",
    restored: "La conexión con el navegador ha vuelto.",
    still: "La conexión con el navegador sigue bloqueada. Cierra la otra aplicación y vuelve a intentarlo.",
  },
  help: {
    title: "Si no se conecta",
    items: [
      [
        "Ninguna ventana de Meridian pide el código",
        "Meridian la abre por sí mismo y la solicitud también aparece arriba en esta página. Si la extensión muestra un código pero Meridian no muestra ninguna solicitud, no escribas el código en ningún sitio: pulsa Cancelar en la extensión, porque puede que otro programa de este Mac esté respondiendo.",
      ],
      [
        "La extensión dice que más de un programa responde como Meridian",
        "Lo habitual es que haya dos copias de Meridian abiertas, por ejemplo la aplicación instalada y una versión de desarrollo. Cierra la que sobre y pulsa Reintentar en la extensión. Por tu seguridad, la extensión no envía nada hasta que responda uno solo.",
      ],
      [
        "La extensión dice que Meridian no está abierto o que perdió la conexión",
        "Abre Meridian y la extensión se reconectará sola. Si desconectaste el navegador desde aquí, pulsa Conectar de nuevo en la extensión.",
      ],
      [
        "Edge, Brave y otros navegadores basados en Chromium",
        "Pueden cargar la misma carpeta, pero Meridian los muestra como Chrome y Abrir con el asistente siempre abre Google Chrome.",
      ],
    ],
    port: (port, range) =>
      `Meridian escucha solo en este Mac, en el puerto ${port}. La extensión lo busca en los puertos ${range}.`,
    portOutside: (port, range) =>
      `Meridian escucha en el puerto ${port}, que la extensión no mira (prueba los puertos ${range}). La extensión no puede encontrar a Meridian hasta que escuche en uno de ellos.`,
  },
  privacy: {
    title: "Lo que el asistente hará y no hará",
    items: [
      "Nunca envía una candidatura, ni pulsa Siguiente, ni marca casillas de consentimiento, ni resuelve CAPTCHA, ni toca campos de contraseña. Eso lo haces tú.",
      "Solo adjunta tu propio CV aprobado, ningún otro archivo: el que elegiste para esa candidatura y, si no hay, el que Meridian redactó para ese puesto; si tampoco, tu CV predeterminado (Settings > Automation > Default CV) y, si no, tu CV aprobado más reciente. Una carta de presentación solo entra si Meridian la redactó para ese puesto y tú la aprobaste.",
      "Las sugerencias salen de tu perfil verificado. Los borradores van marcados y necesitan tu revisión.",
      "Tu perfil y tus respuestas se quedan en este Mac. La extensión en sí solo se comunica con Meridian en este Mac (127.0.0.1), y lo que escribes llega a Meridian solo cuando decides guardarlo.",
      "Es Meridian mismo, no la extensión, quien sale a internet para preparar respuestas: lee páginas públicas del sitio web de la propia empresa y de su sistema de candidaturas. Cuando no encuentra el sitio de la empresa, también puede buscarlo en la web, enviando solo el nombre de la empresa a un buscador público. Lo controlas en Settings > Browser assistant > Preparar respuestas.",
      "Lo único de tu información que puede salir de este Mac es el texto que Meridian envía al modelo de redacción que elegiste (Settings > AI & providers): la pregunta, la descripción del puesto y los datos verificados necesarios para responder. Un modelo que se ejecuta en este Mac no envía nada fuera.",
    ],
  },
  howTo: {
    title: "Cómo se usa",
    intro: "Siguiente y Enviar los pulsas tú. El asistente solo ayuda con los campos.",
    keys: [
      ["Haz clic en un campo", "Meridian muestra su sugerencia y de dónde viene."],
      ["Tab", "Inserta la sugerencia y pasa a la siguiente pregunta."],
      ["Mayús + Tab", "Vuelve al campo anterior sin cambiar nada."],
      ["Esc", "Oculta la sugerencia de este campo."],
      ["Intro", "Inserta una respuesta sensible, como la autorización de trabajo. Tab no lo hace, salvo que lo permitas en los ajustes."],
      ["⌥ Z", "Deshace la última inserción."],
      ["⌥ R", "Vuelve a redactar una respuesta larga."],
      ["⌃ ⌥ 1 a 7", "Ejecuta los botones numerados de una sugerencia: Reemplazar, Más corto, Más largo, Otro enfoque, Ver más y las fuentes. Cada botón muestra su tecla. Estas teclas nunca escriben nada en el formulario. (Alt + Mayús + 1 a 7 en otros sistemas.)"],
      ["⌥ ⇧ M", "Lleva el foco del teclado al panel de Meridian, cuando la página lo permite. Púlsalo otra vez para volver al campo en el que estabas."],
      ["⌥ ⇧ Z", "Deshace todo lo que insertó el último «Rellenar los verificados», mientras se muestra el botón Deshacer todo."],
      ["⌘ ⇧ Y", "Inicia o detiene el asistente en la página en la que estás. Solo en Chrome, y solo el atajo predeterminado: puede que lo use otra extensión y puedes cambiarlo en chrome://extensions/shortcuts. Safari no tiene ese atajo: pulsa el icono de Meridian."],
    ],
    more: "Lo demás está en la página de opciones de la extensión (icono de Meridian y después Ajustes): Ayuda enumera las teclas y los colores.",
  },
  settings: {
    groups: {
      assistant: "Asistente",
      form: "Mientras rellenas un formulario",
      prepare: "Preparar respuestas",
      browser: "En tu navegador",
    },
    enabled: ["Activar el asistente de navegador", "Si está desactivado, la extensión no puede comunicarse con Meridian."],
    autoAdvance: ["Tab rellena y pasa al campo siguiente", "Desactívalo para rellenar con Tab pero avanzar tú mismo."],
    skipFilled: ["Saltar los campos que ya tienen valor", "Tab pasa de largo las preguntas que ya has respondido."],
    tabProtected: [
      "Permitir que Tab inserte respuestas sensibles",
      "Las respuestas sobre autorización de trabajo, datos demográficos o asuntos legales suelen requerir Intro o un clic. Permite Tab solo si quieres insertarlas igual de rápido que el resto.",
    ],
    tabProtectedConfirm: {
      title: "¿Permitir que Tab inserte respuestas sensibles?",
      body: "Si mantienes Tab pulsado en un formulario, se podrían insertar respuestas sobre autorización de trabajo, patrocinio o datos demográficos sin una segunda pulsación. Meridian solo inserta respuestas que has confirmado y puedes desactivarlo cuando quieras.",
      button: "Permitir Tab",
    },
    saveCorrections: [
      "Cuando corriges una sugerencia",
      "Meridian puede recordar tu versión para la próxima vez. Las respuestas sensibles siempre se confirman antes.",
    ],
    saveOptions: { ask: "Preguntarme cada vez", always: "Guardar automáticamente", never: "No guardar nunca" },
    research: [
      "Investigar la empresa y el puesto en segundo plano",
      "Usa páginas públicas para preparar respuestas personalizadas antes de que llegues a la pregunta.",
    ],
    webLookup: [
      "Buscar en la web el sitio oficial de la empresa",
      "Solo cuando la oferta no lo enlaza. Envía únicamente el nombre de la empresa a un buscador público.",
    ],
    localAssist: [
      "Dejar que la IA local complete lo que el perfil no resuelve",
      "Laya y tu modelo de texto local (Ollama) eligen entre las opciones que ofrece un formulario y escriben un valor como pide el campo (una fecha como dd-mm-aaaa, un número sin espacios). Funcionan en este Mac, no cuestan nada y nunca inventan un dato: lo que eligen se muestra en ámbar con los hechos de los que sale.",
    ],
    drafts: ["Preparar respuestas largas con antelación", "Usa tu modelo de respuestas y cuenta en tu límite mensual de IA."],
    draftCap: [
      "Límite de gasto por candidatura",
      "Deja de preparar borradores de una candidatura cuando haya costado esta cantidad. Si lo dejas vacío se usa el valor por defecto de 1 USD por candidatura.",
    ],
    hint: [
      "Mostrar una pista discreta en sitios de candidaturas conocidos",
      "Aparece una pequeña píldora de Meridian en sistemas habituales como Greenhouse o Lever. Nunca rellena nada por sí sola.",
    ],
    language: ["Idioma en las webs", "Se aplica a los botones y consejos del asistente, no a tus respuestas."],
    languageOptions: { auto: "Automático", en: "English", es: "Español" },
    sites: [
      "Iniciar automáticamente en estos sitios",
      "Inicia el asistente en cuanto carga una página de alguno de estos sitios, sin hacer clic en la barra de herramientas. El navegador también debe permitir que la extensión actúe en cada sitio: la página de opciones de la extensión enumera estos sitios, marca los que aún necesitan tu permiso y lo pide.",
    ],
  },
  chrome: {
    title: "Google Chrome",
    install: "Instalar en Chrome",
    intro:
      "Un solo clic prepara la carpeta de la extensión, copia su ruta, muestra la carpeta en el Finder y abre la página de extensiones de Chrome. Quedan tres pasos, todos en Chrome:",
    steps: [
      "Activa el modo de desarrollador, arriba a la derecha de la página de extensiones. Chrome lo exige para cualquier extensión que no venga de la Chrome Web Store.",
      "Pulsa Cargar descomprimida, escribe ⌘⇧G, pega la ruta de la carpeta (⌘V), pulsa Intro y elige Abrir.",
      "Haz clic en el icono de Meridian (fíjalo desde el menú del puzle para tenerlo a la vista) y elige Conectar. La extensión muestra un código de 6 dígitos: escríbelo en la ventana de Meridian que se abre, o aprueba la solicitud aquí.",
    ],
    installed: "La ruta de la carpeta está copiada y la página de extensiones de Chrome está abierta. Quedan tres pasos en Chrome.",
    noChrome:
      "No se ha encontrado Google Chrome en Aplicaciones. El botón sigue preparando la carpeta, copiando su ruta y mostrándola en el Finder: abre tú mismo la página de extensiones de tu navegador Chromium (chrome://extensions).",
    installedNoChrome:
      "La ruta de la carpeta está copiada y la carpeta se muestra en el Finder. Abre tú mismo la página de extensiones de tu navegador (chrome://extensions) y sigue los tres pasos.",
    updateButton: "Actualizar en Chrome",
    updateIntro: "Un solo clic actualiza la carpeta de la extensión y abre la página de extensiones de Chrome. Después:",
    updated: "La carpeta de la extensión está actualizada y la página de extensiones de Chrome está abierta.",
    updatedNoChrome:
      "La carpeta de la extensión está actualizada. Abre tú mismo la página de extensiones de tu navegador (chrome://extensions) y recarga Meridian Assistant.",
    update: [
      "Pulsa la flecha de recarga en Meridian Assistant.",
      "Abre una candidatura y haz clic en el icono de Meridian para terminar.",
    ],
    again: { copy: "Copiar la ruta otra vez", reveal: "Mostrar en el Finder", open: "Abrir la página de extensiones" },
    missing: "Esta versión de Meridian no incluye la extensión de Chrome.",
  },
  safari: {
    title: "Safari",
    steps: [
      "Abre Safari, elige Safari > Ajustes > Extensiones y marca Meridian Assistant.",
      "Permítelo en los sitios web: con Meridian Assistant seleccionado, elige Editar sitios web… y ponlo en Permitir, a ser posible Permitir siempre en todos los sitios web.",
      "Haz clic en el icono de Meridian en la barra de herramientas de Safari (Visualización > Personalizar barra de herramientas… lo añade) y elige Conectar. Después escribe el código que muestra en la ventana de Meridian, o aprueba la solicitud aquí.",
    ],
    update: [
      "Abre la versión actualizada de Meridian desde la carpeta Aplicaciones, cierra Safari (⌘Q) y ábrelo de nuevo para que cargue la versión nueva.",
      "En Safari > Ajustes > Extensiones, comprueba que Meridian Assistant sigue marcado y permitido en tus sitios web.",
    ],
    unseen:
      "Meridian no puede ver los ajustes de Safari, así que no sabe si la extensión está marcada ni en qué sitios web puede actuar. Estos pasos se dan por hechos cuando la extensión se comunica con Meridian.",
    applications: "macOS solo encuentra la extensión de Safari en la copia de Meridian que está en tu carpeta Aplicaciones.",
    notListed: "¿No aparece en las extensiones de Safari? Cierra Safari (⌘Q) y ábrelo de nuevo.",
    unsigned:
      "¿Sigue sin aparecer? Esta versión no está notarizada, así que Safari puede necesitar Desarrollo > Permitir extensiones sin firmar (¿no ves el menú Desarrollo? Activa Safari > Ajustes > Avanzado > Mostrar funciones para desarrolladores web). Safari desactiva esa opción cada vez que se cierra, así que puede hacer falta de nuevo tras reiniciar.",
    missing: "Esta versión de Meridian no incluye la extensión de Safari.",
  },
};
/** Headings of the Spanish quick guide (the English panel needs no equivalent). */
export const GUIDE_ES = {
  summary: "Guía rápida en español",
  now: "Estado actual",
  settings: "Opciones del asistente",
};
/** Ports the extension tries, in order (backend/jobagent/extension/__init__.py BRIDGE_PORTS). */
export const EXTENSION_PORTS = { first: 47615, last: 47624 };
export type Lang = "en" | "es";
export const COPY: Record<Lang, Dict> = { en, es };
