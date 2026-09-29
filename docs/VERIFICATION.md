# Meridian: verificación y límites observados

Fecha de verificación: 29 de septiembre de 2026. Equipo: Apple M2 Max, 64 GB, macOS 15.7.3, arm64. El producto arranca sin ofertas, candidaturas ni hechos profesionales ficticios. Los datos sintéticos se usan exclusivamente en directorios temporales o `.local-data/qa`.

Meridian 0.1.0 está instalada en `/Users/alejandro/Applications/Meridian.app` y se dejó abierta en Overview, con automatización pausada. La copia instalada coincide por SHA-256 con los ejecutables del build. `codesign --verify --deep --strict` y la comprobación del DMG con `hdiutil verify` terminaron correctamente. La base de producción está en revisión `0003_notifications`, con cero hechos, ofertas, candidaturas y mensajes. [Manifest del release y hashes](release-manifest.json).

## Evidencia del flujo principal

| Área | Comprobación realizada |
| --- | --- |
| Aplicación nativa | Bundle Tauri de producción abierto en macOS; interfaz accesible cargada desde `tauri://localhost`, backend empaquetado y base de datos local funcionando. |
| Perfil y documentos | Importación, propuestas sin verificar, edición y bloqueo, hechos históricos, vista previa/diff, PDF aprobado y descarga. Un cambio de evidencia revoca la aprobación derivada. |
| Búsqueda | Consultas públicas reales a Greenhouse/Anthropic, Ashby/OpenAI y Lever/Palantir; normalización y persistencia en una base aislada. Se encontraron 629, 840 y 320 puestos respectivamente en el momento de la prueba. |
| Candidatura | Test de aceptación con Chrome y un ATS local: documento exacto y hash conservados, formulario rellenado, cero envíos antes de aprobar y un único envío después, confirmación independiente y bloqueo de duplicados. |
| Preguntas humanas | Respuestas manuales verificadas tienen provenance. Una respuesta sensible autoriza solamente esa pregunta en esa candidatura; no concede consentimiento global futuro. |
| Email | Mensajes `.eml`, transportes simulados Gmail/Graph/IMAP, clasificación, enlace ambiguo, deduplicación, fechas y transición a prueba técnica/interview/rejection. Confirmaciones tardías no revierten fases posteriores. |
| Recuperación | Envío incierto bloquea reintentos; hechos históricos permanecen; backup cifrado, contraseña errónea rechazada, restauración pausada y migración de copias antiguas antes de sustituir datos. |
| Navegador empaquetado | Desde la app nativa se abrió un sitio local en Chrome. Estado observado: `running=true`, `channel=chrome`, `last_error=null`, perfil separado bajo Application Support/Meridian. |
| Laya instalado | Runtime MLX y modelo instalados fuera del checkout, en Application Support/Meridian. Inferencia real desde el botón de la app: `TYPE_TEXT`, target correcto, 1421,88 ms en esa ejecución. |
| Ciclo de vida | Quit Meridian cerró el backend, Chrome gestionado y Laya gestionado; puertos locales del backend/modelo quedaron libres. |

La prueba de navegador usa una oferta sintética local. No se ha enviado ninguna candidatura real ni ningún mensaje a terceros.

## Resultados reproducibles

Resultado final: **125 pruebas Python y 3 pruebas Rust correctas**, lint limpio, TypeScript y Rust correctos, y los dos flujos E2E de interfaz completos. La suite Python incluye los tests reales de Chrome/ATS local. Hay un aviso de deprecación de Starlette sobre su transporte de pruebas `httpx`, sin fallos de ejecución. Los comandos son:

```sh
make test
make lint
make typecheck
make build
```

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

- Importar y verificar su CV, contacto y autorización laboral. El perfil aproximado del encargo no se convirtió en hechos verificados.
- Registrar/conectar Gmail u Outlook, o configurar IMAP TLS, con consentimiento y credenciales propios. Los flujos están implementados y probados con transportes controlados; no se ha autenticado una cuenta real del usuario.
- Iniciar sesión personalmente en los portales y completar MFA/CAPTCHA. Los widgets o rutas no reconocidos se detienen para intervención.
- Activar búsquedas periódicas, elegir fuentes/perfiles y, si se desea, autorizar dominios y límites para modo Auto. La instalación permanece inicialmente pausada.
- Configurar cualquier proveedor remoto y su presupuesto. Jev y los proveedores de texto no se han probado con una cuenta de pago. El modo extractivo y Laya funcionan sin claves remotas.

## Alcance y límites

- Verificado en Apple Silicon con macOS 15+. El build local tiene firma ad hoc; no está notarizado y no se acredita una distribución Intel.
- Los adaptadores ATS combinan reconocimiento de plataforma y manejo semántico de formularios. No constituyen cobertura garantizada de todos los formularios personalizados. LinkedIn/Indeed utilizan acceso manual.
- La búsqueda consulta fuentes públicas configuradas y páginas de empleo con datos estructurados. No recorre autónomamente resultados de Google ni portales restringidos. Las listas GitHub admiten archivos Markdown, JSON y CSV.
- La extracción de documentos es conservadora y requiere texto seleccionable; no incorpora OCR ni un importador específico para cada formato de exportación de LinkedIn. Portfolio/GitHub pueden añadirse como enlaces/hechos y documentos verificables.
- El matching explica evidencia, vocabulario semántico, filtros y pesos. No representa una probabilidad de contratación. No descarga un modelo de embeddings. Los datos desconocidos se muestran como desconocidos; la autorización laboral nunca se deduce de estudios o nacionalidad.
- Los filtros de industria, contrato, modalidad, antigüedad y patrocinio dependen de datos publicados. La experiencia numérica necesita un hecho verificado explícito de años profesionales totales y un requisito comparable; los proyectos no se convierten en empleo. La formación utiliza un vocabulario limitado de grados y disciplinas.
- La deduplicación fusiona URLs/identidades ATS equivalentes y anuncios con empresa, título, ubicación y descripción idénticos. No fusiona automáticamente anuncios cuya descripción sólo sea semánticamente similar; conviene revisar esos casos antes de preparar candidaturas separadas.
- CVs y cartas se construyen desde hechos seleccionados. La generación remota/local opcional produce borradores revisables; la existencia de un fact ID no demuestra por sí sola cada frase generada.
- El proceso permanece en la barra de menús al cerrar la ventana. La búsqueda y el correo periódicos requieren que siga ejecutándose y que el Mac esté despierto. No se instala un LaunchAgent global.
- Los avisos de revisión, novedades, buenos matches y plazos próximos se deduplican en SQLite. Su presentación depende de las preferencias de notificaciones de macOS. La información también queda visible dentro de la app.

La aceptación completa con una candidatura y correo reales requiere las cuentas del usuario y su aprobación de esa candidatura concreta; esa parte no se presenta como validada.
