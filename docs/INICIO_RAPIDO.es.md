# Meridian: ofertas diarias y seguimiento

Abre **Meridian.app** en Aplicaciones. **Today** muestra una selección diaria de ofertas según tus preferencias; **Refresh jobs** consulta las fuentes en ese momento. La búsqueda programada funciona mientras la app está abierta y se pone al día después del reposo. Cerrar la ventana conserva la app en la barra de menús; **Quit Meridian** la detiene.

1. Consulta **Today**: puesto, empresa, ubicación, fuente y enlace original. **Save** guarda para después; **Pass** aparta una oferta y permite restaurarla desde **Tracker → Ignored**.
2. Pulsa **Apply with review** para elegir un CV aprobado y abrir el navegador. **Review section** muestra los valores y adjuntos propuestos antes de escribir. **Approve & fill section** autoriza esa sección; **Approve & continue** permite pasar a la página siguiente. **Review & approve submission** solicita una aprobación final independiente. Los datos desconocidos se resuelven en **Answers → Add verified answer** y después **Resume agent**. Inicio de sesión, CAPTCHA y controles no compatibles requieren intervención en el navegador.
3. También puedes usar **Open application** y presentar la candidatura tú mismo. Después, **Mark applied → Save as applied** registra la fecha y tus notas. Abrir el enlace por sí solo no equivale a haber aplicado.
4. En **Tracker** tienes empresa, puesto, ubicación, fuente, fase, fecha de envío, plazos, último correo y notas. Filtra **To apply**, **Saved for later** o **Applied**. Puedes cambiar la fase y abrir **Timeline & notes**.
5. **Email** muestra la cuenta y su última sincronización. Los correos de confirmación, pruebas, entrevistas y decisiones actualizan candidaturas cuando la relación es clara. Los ambiguos requieren revisión. Una confirmación con empresa y título completos e inequívocos puede registrar la candidatura automáticamente.
6. **Sources** permite editar tus 27 roles, países/ciudades y fuentes. GitHub y Trackr se consultan directamente; las alertas compatibles de Indeed y LinkedIn se importan desde el buzón. LinkedIn requiere recibir sus alertas para que esa fuente tenga ofertas. La app mantiene visibles los errores de cada fuente.
7. **Profile** conserva los hechos verificados de tu CV. **Documents** permite consultar o preparar documentación. El navegador y el envío asistido quedan como opciones en **Advanced automation**, fuera del flujo diario.

Los datos permanecen en `~/Library/Application Support/Meridian/`. No se envían candidaturas automáticamente con esta configuración. La información de empresa y los requisitos se muestran con su fuente; los campos que la fuente no publica quedan como desconocidos. Una oferta puede cerrar después de importarse: comprueba su disponibilidad al abrir el enlace.

Gmail ya completó la primera lectura. Si macOS solicita permiso del llavero tras una actualización, hay que responder personalmente al aviso; **Email** muestra esa espera sin bloquear la búsqueda de ofertas.

Las recomendaciones de **Today** y **Opportunities** están limitadas a ubicaciones europeas identificables, incluido Reino Unido, con Londres primero. Una ubicación vacía, «Remote» sin región o «EMEA» no basta para recomendar una oferta. **Tracker** conserva el historial completo. El modo asistido de una candidatura concreta funciona sin activar los envíos automáticos de otras candidaturas.

## Asistente en tu navegador (Chrome y Safari)

El asistente de Meridian es una extensión que te ayuda a rellenar una candidatura en tu propio Chrome o Safari, campo a campo. Pulsas su icono en una oferta, haces clic en un campo y ves qué te sugiere para ese campo y por qué. Con **Tab** insertas la sugerencia y pasas a la siguiente pregunta. Las respuestas largas («¿Por qué quieres trabajar con nosotros?») se preparan en segundo plano como **borrador**, a partir de tu perfil verificado y de una investigación sobre la empresa y el puesto, para que estén listas cuando llegues.

**Lo que nunca hace.** No pulsa Siguiente, Enviar ni Aplicar: enviar la candidatura es siempre un clic tuyo. No toca contraseñas, códigos de un solo uso, datos de tarjeta o de banco, números de documento, CAPTCHA ni pasos de verificación en dos factores. No marca casillas de consentimiento o legales y no escribe una firma sin que tú pulses Enter. Solo sube tu propio CV **aprobado**. No sobrescribe lo que ya has escrito en un campo (te ofrece **Reemplazar**). Y nunca da por enviada una candidatura sin que tú lo confirmes. Tu perfil y tus respuestas se quedan en este Mac; la extensión solo habla con Meridian a través de `127.0.0.1`.

### Instalar en Chrome

1. Abre **Meridian.app** y entra en **Settings → Browser assistant**. Pulsa **Install in Chrome**: Meridian prepara la carpeta de la extensión, copia su ruta, la muestra en Finder y abre la página de extensiones de Chrome. La carpeta es `~/Library/Application Support/Meridian/extensions/chrome` y no cambia al actualizar Meridian.
2. En Chrome activa el **Modo de desarrollador** (arriba a la derecha de la página de extensiones). Chrome lo exige para cualquier extensión que no venga de la Chrome Web Store.
3. Pulsa **Cargar descomprimida**, pulsa ⌘⇧G, pega la ruta (⌘V), pulsa Retorno y elige Abrir.
4. Fija el icono de Meridian (menú con forma de pieza de puzle) y pulsa **Connect** (ver «Conectar el navegador»).

Si no tienes Google Chrome, Meridian prepara igualmente la carpeta y copia la ruta: abre tú la página `chrome://extensions` de tu navegador basado en Chromium. Si Meridian muestra en rojo **extensión no reconocida**, no es el asistente que viene con la app: deniégala salvo que la hayas cargado tú.

### Instalar en Safari

La extensión de Safari viene dentro de Meridian.app, pero Safari no permite que nadie la active por ti:

1. Abre Meridian desde la carpeta **Aplicaciones** al menos una vez (macOS solo la registra desde esa copia; deja una sola copia).
2. En Safari: **Safari → Ajustes → Extensiones** y marca **Meridian Assistant**. Si no aparece, cierra Safari (⌘Q) y vuelve a abrirlo. Esta compilación no está notarizada, así que puede hacer falta **Desarrollo → Permitir extensiones sin firmar** (si no ves el menú Desarrollo: Ajustes → Avanzado → Mostrar funciones para desarrolladores web). Safari desactiva esa opción cada vez que se cierra.
3. Con Meridian Assistant seleccionada, elige **Editar sitios web…** y ponla en **Permitir**, mejor **Permitir siempre en todos los sitios web**. Añade el icono a la barra (Visualización → Personalizar barra de herramientas…) y pulsa **Connect**.

Meridian no puede ver los ajustes de Safari, así que no sabe si la extensión está marcada; los pasos se dan por hechos cuando la extensión llega a Meridian. Safari no permite atajos de teclado para extensiones: usa el icono. Safari es lo menos probado de las dos opciones (ver «Límites»).

### Conectar el navegador (una sola vez)

1. Pulsa el icono de Meridian y luego **Connect**. La extensión muestra un código de 6 dígitos.
2. Meridian abre su propia ventana pequeña pidiendo ese código: escríbelo y pulsa **Connect** (o **Deny**). También puedes pulsar **Approve connection** en la solicitud que aparece arriba en Settings → Browser assistant, después de comprobar que el código es el mismo. Vale lo que hagas primero; con tres códigos erróneos la solicitud se deniega. La solicitud dura 120 segundos: si caduca, pulsa Connect otra vez.
3. **Escribe el código solo si la ventana la ha abierto Meridian.** Si la extensión enseña un código pero Meridian no muestra ninguna ventana ni solicitud, pulsa Cancelar: puede que algo distinto de Meridian esté respondiendo en este Mac.

Para desconectar, pulsa **Disconnect** junto al navegador en Settings → Browser assistant; no se borra nada de tu perfil.

### Uso diario

Abre el formulario de una candidatura y pulsa el icono de Meridian (en Chrome, el atajo por defecto es ⌘⇧Y). En el icono verás **ON** mientras el asistente está activo en esa pestaña y **!** si necesita tu atención. En las plataformas de empleo conocidas (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, etc.) aparece además un pequeño aviso «Meridian está listo» que también inicia el asistente. Si pulsas el icono con el panel visible, el asistente se desactiva en esa página. El asistente solo actúa donde tú lo inicias o en los sitios que le pidas que inicie solo.

Haz clic en un campo y aparece la **sugerencia** junto a él, con el valor, una etiqueta de confianza, el motivo y su procedencia:

| Etiqueta | Qué significa | ¿Tab la inserta? |
|---|---|---|
| **Verificado** (verde) | Sale de tu perfil verificado o de una preferencia guardada | Sí |
| **Revísalo** (ámbar) | Deducido de tu perfil o probable, sin certeza. Léelo antes de aceptarlo | Sí |
| **Borrador con IA: revísalo** (azul) | Redactado por tu modelo de IA con tu perfil y la investigación de la empresa. Revísalo y edítalo | Sí, cuando lleva 0,5 s en pantalla |
| **Escribiendo un borrador** | Aún se está redactando | No: Tab avanza y estará listo cuando vuelvas |
| **Respuesta protegida** | Autorización de trabajo, patrocinio de visado, datos demográficos, ajustes razonables, protección de datos, una firma | Solo con **Enter** (o un clic), salvo que lo permitas en los ajustes; una firma siempre necesita Enter |
| **La decides tú** / **Lo dejo en blanco** (gris) | Consentimientos, cuestiones legales y preguntas personales opcionales | Nunca |
| **Te toca a ti** (gris) | Meridian no tiene una respuesta segura | Nunca |

**Teclas** (en Mac, Alt es la tecla Option):

| Tecla | Qué hace |
|---|---|
| **Tab** | Inserta la sugerencia (si procede) y pasa al campo siguiente, cuya sugerencia ya está visible. Si no hay nada que insertar, o el campo ya tiene tu texto, solo avanza. Si el navegador tiene mejor derecho a esa tecla (un enlace, un botón, un selector de fecha o una ventana emergente de la página), se la deja |
| **Mayús+Tab** | Vuelve al campo anterior sin cambiar nada |
| **Enter** | Inserta una respuesta protegida cuando su sugerencia lo indica. Nunca envía el formulario |
| **Esc** | Oculta la sugerencia de ese campo |
| **Option+R** | Reescribe un borrador |
| **Option+Z** | Deshace la última inserción (el botón Deshacer dura 8 segundos) |
| **Option+Mayús+Z** | Deshace todo lo que insertó el último «Rellenar los verificados» |
| **Option+Mayús+M** | Lleva el teclado al panel de Meridian; púlsala de nuevo para volver al campo |
| **Control+Option+1 a 7** | Ejecuta los botones numerados de una sugerencia (Reemplazar, Más corto, Más largo, Otro enfoque, Ver más, fuentes). Cada botón muestra su tecla; estas combinaciones nunca escriben nada en el formulario |

En el panel, desactiva **Usar Tab para insertar y avanzar** si quieres recuperar el Tab normal del navegador un rato.

**Rellenar los verificados.** El botón del panel rellena de una vez los campos verificados que Tab puede insertar y que no son personales ni legales. Antes de escribir nada te enseña la lista de campos y valores; solo se rellena al pulsar **Rellenar ahora**, y **Deshacer todo** lo revierte.

### Investigación de la empresa y borradores

Al abrir una candidatura, Meridian investiga en segundo plano la empresa y el puesto (una vez por página, sin usar ningún modelo de IA): lee la oferta y, si logra verificar que es la web oficial de la empresa, algunas de sus páginas públicas (quiénes somos, misión, valores…). Nunca lee LinkedIn, Indeed ni Glassdoor. El panel muestra qué fuentes usó. Los borradores se redactan solo para preguntas largas (y para un campo corto si pulsas **Redactar con IA**), nunca para preguntas sensibles, legales ni de salario, de una en una y con el modelo de IA que elegiste en Meridian. Cada borrador aparece marcado para que lo revises. Para proteger tu presupuesto, Meridian redacta por su cuenta un máximo de 8 borradores por página y hora, y una candidatura gasta como mucho el **límite por candidatura** de Settings (1 USD por defecto); los borradores que pides tú no cuentan para ese máximo.

Si Meridian se equivoca de empresa, pulsa **¿No es esta empresa?** en la franja de investigación y escribe la correcta, o usa **Añadir la web de la empresa**. Si la investigación llegó tarde, el borrador te lo dice y te ofrece **Reescribir con la nueva investigación**; nunca se reescribe solo, porque cuesta dinero.

### IA local: opciones y forma de escribir un dato

Algunas preguntas no se resuelven con un solo dato de tu perfil: un desplegable para elegir, o un campo que dice cómo quiere la respuesta (**«Fecha de graduación (mes y año en palabras)»**, **«Teléfono (con prefijo, sin espacios)»**, **«(p. ej. 1 de noviembre de 2026)»**). Para eso Meridian puede preguntar a dos modelos que funcionan **en tu propio Mac** (gratis, sin enviar nada fuera; solo si están instalados y el ajuste *Dejar que la IA local complete lo que el perfil no resuelve* está activado):

- **Laya** y tu **modelo de texto local (Ollama)** eligen entre las opciones que el formulario *ofrece*, mirando los pocos datos de tu perfil que vienen al caso. No pueden elegir nada que no se ofrezca, nunca eligen un «No», «Ninguno», «Nunca» u «Otro» a partir de un dato que falta, y nunca tocan una pregunta protegida, legal o de consentimiento. Si los dos eligen lo mismo y un dato tuyo dice una palabra de esa opción (y de ninguna otra), sale en **ámbar** («Revísalo») y Tab la inserta. Si tus datos nombran varias opciones («híbrido o remoto»), o ninguno dice nada pero los dos modelos se apoyan en uno, sale en ámbar como sugerencia y **solo Enter o un clic** la toma. Si discrepan, no se sugiere nada: el campo es tuyo, como antes.
- El **modelo de texto local** escribe un dato que ya tienes **como el campo lo pide**: la respuesta del motor sigue siendo la misma y el modelo solo puede escribirla de otra forma (la misma fecha con menos partes, nunca con más; las mismas cifras; las mismas palabras). Si lo que escribe no es el mismo dato, o incumple lo que dice el campo (solo cifras, sin espacios, la forma del ejemplo), se descarta y el campo queda como estaba: un ejemplo de la etiqueta nunca se copia como tu respuesta. Las fechas con símbolos (DD-MM-AAAA, MM/AAAA, dd.mm.aa) se escriben exactamente así sin ningún modelo.

Mientras un modelo trabaja, el campo lo dice («Consultando tu perfil con IA local en este Mac…») y se actualiza al terminar; las primeras respuestas de la página nunca esperan. La primera vez tras abrir Meridian puede tardar unos segundos más, porque hay que cargar el modelo.

### Guardar tus correcciones y registrar la candidatura

Cuando cambias o escribes una respuesta, el asistente pregunta **¿Guardar esta respuesta en Meridian?** con las opciones **Solo esta candidatura**, **Reutilizar en futuras candidaturas** y **No**. Las respuestas largas, de una empresa concreta, protegidas, legales o de salario solo se guardan para esa candidatura. Contraseñas, códigos, tarjetas, cuentas bancarias y números de documento no se ofrecen nunca. En Settings → Browser assistant puedes elegir preguntar siempre, guardar automáticamente o no guardar. Ese es el único momento en que lo que escribes viaja a Meridian, y se queda en este Mac.

Navegar no crea nada en Meridian. **Guardar en el Tracker** (en el menú del panel) añade la oferta como candidatura en preparación, sin decir que la enviaste. Si pulsas tú el botón final de envío y la página siguiente muestra una confirmación («gracias por tu candidatura», «hemos recibido tu solicitud»…), el asistente pregunta **¿Registrar esta candidatura en Meridian?**; solo se registra si aceptas, y también puedes usar **Ya la envié: registrarla** en cualquier momento. Si ya la habías enviado, un aviso lo dice («Ya enviaste tu candidatura a esta oferta») y no inserta nada hasta que pulses **Continuar de todos modos**.

### Si algo no funciona

| Qué ves | Qué hacer |
|---|---|
| «Meridian no está abierto» | Ábrelo (el panel tiene un botón **Abrir Meridian**); la extensión reintenta sola |
| La extensión enseña un código y Meridian no muestra ventana | Pulsa Cancelar; no escribas el código en ningún sitio |
| «Más de un programa de este Mac dice ser Meridian» | No se envía nada a ninguno. Cierra la copia sobrante (por ejemplo un backend de desarrollo) y reintenta |
| «Meridian desconectó este navegador» | Pulsa **Conectar de nuevo** |
| «Este formulario está en un marco de otro sitio» | Permite la extensión en todos los sitios web (página de opciones; en Safari, «Permitir siempre en todos los sitios web») |
| Las respuestas largas dicen que hace falta un modelo | Configúralo y pruébalo en Settings → AI & providers |
| Un campo de CV dice «CV sin aprobar» | Importa y aprueba tu CV en Documents |
| «Recarga la página» | La extensión se actualizó; recarga la página (guarda antes lo que hayas escrito) |

### Qué sale de tu Mac

Tus datos no salen. Lo único que puede salir, y siempre por Meridian y nunca por la extensión: (1) Meridian lee páginas públicas del empleador (la oferta, la web oficial verificada de la empresa) con peticiones de solo lectura que no llevan tus datos; (2) si la oferta no enlaza la web de la empresa y la opción **Search the web for the company's official site** está activada, Meridian busca en un buscador público solo el **nombre de la empresa**; (3) para redactar un borrador, el texto de la pregunta, un extracto de la oferta, páginas verificadas de la empresa y los datos verificados necesarios se envían al modelo de IA que elegiste (si es un modelo local, no sale nada). No hay telemetría ni servidor del proyecto.

### Límites que conviene conocer

- Solo entiende **inglés y español**. En un formulario en otro idioma sugiere menos, no más.
- **Safari no se ha probado en Safari real** con pruebas automáticas; se ha probado el mismo código en WebKit de Playwright. Chrome está mejor comprobado.
- El primer emparejamiento no se puede autenticar por naturaleza (aún no hay secreto compartido): la comprobación real es que **Meridian abra su ventana** pidiendo el código.
- Los componentes de formulario de algunas plataformas (Workday, SmartRecruiters, Workable, iCIMS, Teamtailor, Lever) se probaron con réplicas hechas a partir de su marcado público, no con empresas reales.
- Si una pregunta tiene palabras de más que el motor no sabe interpretar, la deja en «Te toca a ti» en lugar de adivinar.
- Para empleos en España y en la UE, la autorización de trabajo, el patrocinio y el salario esperado usan campos propios del **Application profile** (España y UE/EEE); hasta que los rellenes, esas preguntas quedan para ti.

Guía completa, ajustes, desarrollo y pruebas: [docs/EXTENSION.md](EXTENSION.md).
