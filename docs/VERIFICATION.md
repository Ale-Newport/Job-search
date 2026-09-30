# Verificación de la actualización — 30 de septiembre de 2026

- **Apply with review** abre el navegador desde una oferta y exige permiso antes de rellenar cada sección, adjuntar archivos, avanzar y enviar. El consentimiento se conserva como requisito de esa candidatura; no activa reglas de envío para otras ofertas.
- **202 pruebas Python y 3 Rust correctas**, lint y compilación nativa correctos. Los formularios locales prueban texto, selectores, radio, casillas, CV, navegación, rechazo de permisos repetidos/caducados y ausencia de envíos antes de la aprobación final. La restauración de backups antiguos incluye la migración del requisito de consentimiento.
- Interfaz comprobada con datos ficticios: botón desde Today, datos exactos antes de aprobar, pausa posterior para Contact details y consentimiento independiente de Continue. En la versión instalada se verificó el botón, el selector del CV aprobado (versión 2) y **Europe only · London first**. No se abrió ni envió una candidatura real.
- Producción: **229 recomendaciones europeas**, 50 visibles, Londres primero. Today y Opportunities excluyen ubicaciones no europeas o sin evidencia suficiente; Tracker conserva el historial. Se mantienen los 27 roles y los hechos del CV.
- Instalación y DMG verificados, hashes de los ejecutables instalados iguales a los de la compilación. Backup previo en `.local-data/section-release-backup/`.
- Gmail conserva la cuenta conectada y una sincronización del 30 de septiembre. El nuevo ejecutable espera la aceptación del permiso protegido del Llavero para su próxima lectura; esto no bloquea el flujo de candidaturas asistidas.

## Verificación de la versión de seguimiento diario — 29 de septiembre de 2026

La pantalla principal es **Today** y **Tracker** reúne oportunidades y candidaturas. Se verificó en una copia aislada el alta manual, la marca de envío sin documentos, la persistencia, la incorporación de un correo de prueba y el cambio a prueba técnica con plazo. La configuración de producción conserva los hechos del CV y las preferencias; pausa los envíos, activa una consulta por día y añade GitHub, Trackr y fuentes de alertas.

- Backend: 184 pruebas Python y 3 Rust superadas. Incluyen registro idempotente, correos ambiguos, asociación posterior, fechas, tablas Markdown/HTML y alertas sin parámetros personales.
- Fuentes públicas: dos repositorios de GitHub y Trackr importados con respuestas reales, además de los ocho ATS existentes. El primer ensayo incorporó 1.371 ofertas nuevas; los enlaces se obtienen de las filas originales.
- Indeed: 254 apariciones de ofertas en las alertas existentes se redujeron a 141 ofertas únicas, ya incorporadas también en producción. Los boletines dejan de crear tareas falsas de candidatura.
- Aplicación instalada: 1.636 ofertas almacenadas, 704 recomendaciones que superan los filtros actuales y hasta 50 mostradas en Today. Tracker presenta empresas, puestos, estado, fechas, último correo, plazo y acciones. Se conservan 50 hechos verificados del CV y los 27 roles; cero candidaturas reales registradas o enviadas.
- Interfaz: tres flujos independientes superados (`ui_smoke.py`, `workflow_e2e.py`, `onboarding_e2e.py`), además del recorrido manual de seguimiento. Las 19 pruebas dirigidas finales de API/seguimiento, lint y typecheck pasaron. El paquete instalado y el DMG se verificaron después de la compilación final.
- Gmail: primera sincronización real confirmada a las 16:34:50 UTC del 29 de septiembre, con 45 mensajes importados. La cuenta sigue conectada. Tras instalar el ejecutable actualizado, macOS vuelve a solicitar permiso del Llavero para la siguiente sincronización; la interfaz muestra esa espera y sigue respondiendo. La aceptación de ese diálogo protegido corresponde al usuario.
- No se envió ninguna candidatura real. Las modificaciones de fases durante QA se hicieron exclusivamente en una base separada.
- Limitaciones explícitas: LinkedIn está preparado para alertas recibidas pero todavía no hay alertas verificadas de esa fuente. La búsqueda funciona mientras Meridian está ejecutándose y no despierta el Mac. La geografía sigue sin limitarse hasta que el usuario indique su preferencia. Las descripciones, fases y requisitos no publicados se mantienen desconocidos.

El manifiesto de release registra los recuentos finales de producción, hashes del paquete instalado y la validación del DMG. Los registros que siguen son históricos y corresponden a versiones anteriores.

## Historial de verificaciones anteriores

# Meridian: verificación y límites observados

Fecha de verificación: 29 de septiembre de 2026. Equipo: Apple M2 Max, 64 GB, macOS 15.7.3, arm64. El producto arranca sin ofertas, candidaturas ni hechos profesionales ficticios. Los datos sintéticos se usan exclusivamente en directorios temporales o `.local-data/qa`.

Meridian 0.1.0 está instalada en `/Users/alejandro/Applications/Meridian.app`. La configuración personal posterior al primer release conserva el CV original y sus versiones, 50 hechos contrastados con ese documento, un perfil con 27 roles y 124 ofertas recuperadas de ocho fuentes públicas. Las 52 propuestas del detector anterior se conservan rechazadas con historial. No hay candidaturas enviadas. Gmail ha completado la autorización OAuth real y la verificación de la cuenta esperada; la primera sincronización se comprueba por separado. La autenticación del navegador sigue pendiente. Los detalles personales y las auditorías del CV permanecen en almacenamiento local excluido de Git. [Manifest del release y hashes](release-manifest.json).

## Evidencia del flujo principal

| Área | Comprobación realizada |
| --- | --- |
| Aplicación nativa | Bundle Tauri de producción abierto en macOS; interfaz accesible cargada desde `tauri://localhost`, backend empaquetado y base de datos local funcionando. |
| Perfil y documentos | Importación, propuestas sin verificar, edición y bloqueo, hechos históricos, vista previa/diff, PDF aprobado y descarga. Un cambio de evidencia revoca la aprobación derivada. |
| Búsqueda | Consultas públicas reales a Greenhouse/Anthropic, Ashby/OpenAI y Lever/Palantir; normalización y persistencia en una base aislada. Se encontraron 629, 840 y 320 puestos respectivamente en el momento de la prueba. |
| Candidatura | Test de aceptación con Chrome y un ATS local: documento exacto y hash conservados, formulario rellenado, cero envíos antes de aprobar y un único envío después, confirmación independiente y bloqueo de duplicados. |
| Preguntas humanas | Respuestas manuales verificadas tienen provenance. Una respuesta sensible autoriza solamente esa pregunta en esa candidatura; no concede consentimiento global futuro. |
| Email | Mensajes `.eml`, transportes simulados Gmail/Graph/IMAP, clasificación, enlace ambiguo, deduplicación, fechas y transición a prueba técnica/interview/rejection. Confirmaciones tardías no revierten fases posteriores. |
| Recuperación OAuth | Se detectó un client secret ausente y se guardó desde el JSON local del mismo cliente Desktop, exclusivamente en el Llavero. Google confirmó intercambio de tokens y cuenta con HTTP 200. Los rechazos conocidos ahora tienen instrucciones concretas, se guardan sin incluir respuestas privadas del proveedor y aparecen en la app al volver del navegador. Estado OAuth de un solo uso y denegaciones con state inválido comprobados. |
| Estado Gmail y Llavero | Muestreo del proceso real confirmó que `SecItemCopyMatching` retenía el hilo principal. El acceso al Llavero se ejecuta ahora fuera del bucle de la API. Una prueba con credenciales bloqueadas verifica que health, cuenta, progreso y onboarding siguen respondiendo y que no se inicia una segunda sincronización. La app instalada muestra Gmail conectado y la espera del Llavero por separado; no se ha completado todavía la primera sincronización real. |
| Recuperación | Envío incierto bloquea reintentos; hechos históricos permanecen; backup cifrado, contraseña errónea rechazada, restauración pausada y migración de copias antiguas antes de sustituir datos. |
| Navegador empaquetado | Desde la app nativa se abrió un sitio local en Chrome. Estado observado: `running=true`, `channel=chrome`, `last_error=null`, perfil separado bajo Application Support/Meridian. |
| Laya instalado | Runtime MLX y modelo instalados fuera del checkout, en Application Support/Meridian. Inferencia real desde el botón de la app: `TYPE_TEXT`, target correcto, 1421,88 ms en esa ejecución. |
| Ciclo de vida | Quit Meridian cerró el backend, Chrome gestionado y Laya gestionado; puertos locales del backend/modelo quedaron libres. |
| Importador corregido | DOCX conserva orden, tablas, listas, fechas, métricas y destinos de hipervínculos. Reimportar crea una versión inmutable nueva, preserva hechos verificados/bloqueados y sólo sustituye propuestas sin revisar. Reintentar no duplica versiones. CV real contrastado visualmente y mediante cobertura de párrafos; original intacto. |
| Onboarding | Ocultar la guía no completa pasos. Guardar una cuenta no equivale a conectarla. La cuenta Gmail se contrasta con `/users/me/profile`; cambios de cuenta/configuración invalidan evidencia. IMAP guarda y conecta antes de mostrar éxito. |
| Texto local | Ollama oficial 0.34.4 y Qwen 2.5 7B Q4_K_M, nube desactivada, localhost, contexto 8192. Pruebas reales de JSON y borrador con referencias a los hechos. El borrador final conserva métricas del CV y sigue requiriendo revisión. |
| Identidad visual | Símbolo y logotipo vectoriales propios, variantes clara/oscura/monocroma, icono ICNS y plantilla de barra de menús. Compilación y lint de frontend, comprobación/formato de Rust y revisión visual en la app instalada correctos. Firma, hashes de la copia instalada y checksum del DMG final verificados. [Archivos y guía de marca](brand/README.md). |

La prueba de navegador usa una oferta sintética local. No se ha enviado ninguna candidatura real ni ningún mensaje a terceros.

## Resultados reproducibles

Resultado de la suite completa: **159 pruebas Python y 3 pruebas Rust correctas**. Después de reforzar la redacción se ejecutaron **25 pruebas dirigidas**, incluyendo dos casos nuevos sobre cartas y motivaciones sin evidencia. Lint limpio, TypeScript y Rust correctos, y tres flujos E2E de interfaz aprobados, con una repetición específica de reimportación tras añadir esa acción. La suite Python incluye los tests reales de Chrome/ATS local. Hay un aviso de deprecación de Starlette sobre su transporte de pruebas `httpx`, sin fallos de ejecución. Los comandos son:

```sh
make test
make lint
make typecheck
make build
```

Tras corregir la conexión Gmail, **41 pruebas dirigidas** de correo, API y onboarding correctas, incluyendo fallos de OAuth, respuestas malformadas, redacción de datos privados y denegaciones. Ruff, ESLint y TypeScript correctos. Backend e interfaz recompilados e instalados; firma, hashes y checksum del nuevo DMG verificados.

Después de separar la espera del Llavero: **43 pruebas dirigidas correctas**, más `frontend/tests/email_connection_e2e.py` con todas las respuestas API interceptadas. Comprueba que una autorización externa se refleja sin recargar, que se muestra el progreso del Llavero, que un error no genera un aviso de éxito y que la sincronización satisfactoria actualiza la cuenta. Ruff, ESLint y TypeScript correctos. Estado Connected y espera de permiso observados en la app nativa instalada con la cuenta real.

Para las pruebas UI, arrancar en dos terminales un backend **aislado** y Vite antes de ejecutar `make test-ui`:

```sh
MERIDIAN_TEST=1 MERIDIAN_DATA_DIR="$PWD/.local-data/qa" make backend
make dev-web
make test-ui
```

Estas pruebas realizan CRUD, importan un CV sintético, preparan una candidatura y añaden evidencia de email. No deben ejecutarse contra datos personales de producción. Cubren las trece vistas, temas, Command-K, filtros, reglas, documentos, respuestas y analytics.

Capturas de QA: [dashboard](screenshots/dashboard.png) y [analytics con datos sintéticos](screenshots/analytics-cohorts.png).

Evidencia adicional: [discovery real](discovery-smoke.json), [benchmark Laya](local-laya-benchmark.json), [instalación Laya](installed-laya-runtime.json), [contratos y límites del navegador](BROWSER_AUTOMATION.md), [dependencias y licencias](DEPENDENCIES.md).

## Qué requiere configuración del usuario

- Confirmar preferencias de ubicación, modalidad, disponibilidad y autorización laboral/patrocinio. Estos datos no se deducen del CV. La revisión de los 50 hechos acredita fidelidad de extracción respecto al documento proporcionado, no una certificación independiente de empleadores.
- Gmail ya ha autenticado y verificado una cuenta real. El acceso a sus credenciales depende del Llavero y la primera sincronización debe completarse antes de dar por terminado ese paso. Outlook e IMAP se han probado con transportes controlados; requieren sus propias cuentas y credenciales si se desean usar.
- Iniciar sesión personalmente en los portales y completar MFA/CAPTCHA. Los widgets o rutas no reconocidos se detienen para intervención.
- El modo Review está configurado, con búsquedas cada seis horas mientras la app está abierta; no hay reglas de envío automático ni dominios autorizados para Auto. Los límites son cinco candidaturas al día, dos por empresa y 120 segundos de intervalo.
- Configurar cualquier proveedor remoto y su presupuesto si se desea utilizarlo. Jev y los proveedores de texto remotos no se han probado con una cuenta de pago. Laya y Qwen funcionan localmente sin claves remotas.

## Alcance y límites

- Verificado en Apple Silicon con macOS 15+. El build local usa una identidad Apple Development estable; no está notarizado y no se acredita una distribución Intel.
- Los adaptadores ATS combinan reconocimiento de plataforma y manejo semántico de formularios. No constituyen cobertura garantizada de todos los formularios personalizados. LinkedIn/Indeed utilizan acceso manual.
- La búsqueda consulta fuentes públicas configuradas y páginas de empleo con datos estructurados. No recorre autónomamente resultados de Google ni portales restringidos. Las listas GitHub admiten archivos Markdown, JSON y CSV.
- La extracción de documentos es conservadora y requiere texto seleccionable; no incorpora OCR ni un importador específico para cada formato de exportación de LinkedIn. Portfolio/GitHub pueden añadirse como enlaces/hechos y documentos verificables.
- El matching explica evidencia, vocabulario semántico, filtros y pesos. No representa una probabilidad de contratación. No descarga un modelo de embeddings. Los datos desconocidos se muestran como desconocidos; la autorización laboral nunca se deduce de estudios o nacionalidad.
- Los filtros de industria, contrato, modalidad, antigüedad y patrocinio dependen de datos publicados. La experiencia numérica necesita un hecho verificado explícito de años profesionales totales y un requisito comparable; los proyectos no se convierten en empleo. La formación utiliza un vocabulario limitado de grados y disciplinas.
- La deduplicación fusiona URLs/identidades ATS equivalentes y anuncios con empresa, título, ubicación y descripción idénticos. No fusiona automáticamente anuncios cuya descripción sólo sea semánticamente similar; conviene revisar esos casos antes de preparar candidaturas separadas.
- CVs y cartas se construyen desde hechos seleccionados. Las pruebas detectaron requisitos laborales y motivaciones inventados en borradores iniciales. Se excluyó la descripción de la oferta del contexto de escritura, se añadió una plantilla neutral para cartas y se rechazan expresiones comunes de motivación sin respaldo. La existencia de un fact ID y estos filtros no demuestran por sí solos cada frase generada: los borradores siguen sujetos a revisión.
- El proceso permanece en la barra de menús al cerrar la ventana. La búsqueda y el correo periódicos requieren que siga ejecutándose y que el Mac esté despierto. No se instala un LaunchAgent global.
- Los avisos de revisión, novedades, buenos matches y plazos próximos se deduplican en SQLite. Su presentación depende de las preferencias de notificaciones de macOS. La información también queda visible dentro de la app.

La aceptación completa con una candidatura real requiere las cuentas del usuario y su aprobación de esa candidatura concreta; no se ha enviado ninguna como parte de esta validación.


## Corrección de Apply with review — 30 septiembre 2026

La versión instalada rellena automáticamente con permiso inicial por candidatura y exige confirmación separada para el envío. Sigue disponible el consentimiento por sección. Se ha probado desde el botón real de Today con **Junior Machine Learning Engineer — Trainline**: nombre, email, LinkedIn, CV original aprobado y Londres/Reino Unido quedaron rellenados por Meridian. No se introdujeron campos manualmente en ese navegador ni se pulsó Submit Application.

La candidatura real permanece pendiente de cuatro respuestas que no están verificadas: preaviso, salario esperado, patrocinio actual/futuro y asistencia híbrida mínima del 60 %. Las preguntas opcionales desconocidas, diversidad, ajustes y consentimiento para futuras ofertas quedaron sin responder. No se afirma que la candidatura esté completa ni enviada.

Se corrigieron la espera de hidratación del formulario, los controles Sí/No, el archivo oculto asociado a su botón visible, los desplegables con búsqueda, sus etiquetas frente a IDs internos, la selección inequívoca de Londres y la recuperación acotada de observaciones invalidadas por cambios asíncronos. La prueba local reproduce esos controles, ciudades homónimas y carga tardía, y comprueba cero envíos antes de la aprobación final.

Verificación: **208 pruebas Python y 3 Rust correctas**, más **3 regresiones dirigidas** tras el último ajuste de etiquetas. Ruff, ESLint, TypeScript y Rust correctos. Ambas copias instaladas y el DMG coinciden con la compilación; firma estricta e integridad del DMG verificadas. Código: `8e4079af39f67e68b447da2e8f055eea982e5fad`.

La identidad de firma del backend permanece idéntica entre binarios diferentes. Gmail renovó el token y sincronizó después de actualizar. Se mantienen las credenciales en Keychain y una caché exclusivamente en memoria. Las autorizaciones persistentes de macOS siguen siendo por elemento del llavero; ver [firma y permisos](KEYCHAIN_SIGNING.md). Se cerraron únicamente tres avisos técnicos resueltos de las pruebas anteriores, con la referencia a la ejecución que los resolvió. Los cuatro datos pendientes y la revisión final siguen abiertos.
