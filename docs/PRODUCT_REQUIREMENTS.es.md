Quiero que diseñes, implementes, pruebes y dejes completamente funcional una aplicación de escritorio LOCAL para macOS cuyo objetivo sea automatizar de extremo a extremo mi búsqueda de empleo.

NO quiero una web pública, SaaS ni una aplicación que dependa de un servidor remoto propio. Debe ser una aplicación personal instalada en mi Mac, local-first, que pueda dejar ejecutándose y que se encargue de:

1. descubrir trabajos;
2. analizarlos;
3. determinar cuáles encajan conmigo;
4. preparar una aplicación personalizada;
5. rellenar formularios;
6. aplicar automáticamente cuando esté permitido/configurado;
7. almacenar absolutamente toda la información de cada aplicación;
8. monitorizar el email que uso para aplicar;
9. detectar automáticamente cambios de estado;
10. avisarme cuando necesite intervenir;
11. aprender de mis respuestas manuales para futuras aplicaciones;
12. proporcionarme una visión completa de mi pipeline de empleo.

Quiero un producto terminado, no un proof of concept.

# 1. PRINCIPIO GENERAL

La aplicación debe actuar como un "personal job search operating system".

Debe conocer de forma estructurada toda mi información profesional:

- información personal relevante para aplicaciones;
- CV;
- educación;
- experiencia laboral;
- internships;
- proyectos;
- skills;
- tecnologías;
- lenguajes;
- portfolio;
- GitHub;
- LinkedIn;
- publicaciones si existen;
- disponibilidad;
- localizaciones;
- permisos de trabajo;
- preferencias laborales;
- expectativas salariales;
- respuestas utilizadas anteriormente;
- versiones de CV;
- cover letters;
- preguntas frecuentes de formularios;
- cualquier información adicional que yo añada.

Esta información constituirá una única fuente de verdad llamada:

CANDIDATE KNOWLEDGE BASE

Nunca inventes experiencia, métricas, tecnologías, estudios, fechas, permisos de trabajo, logros o cualquier otro dato.

Cada dato del Knowledge Base tendrá:

- value
- category
- source
- verification status
- last updated
- locked/unlocked
- notes

Los campos marcados como `verified` o `locked` pueden utilizarse automáticamente.

Si una aplicación pregunta algo que no se conoce con suficiente seguridad:

NO INVENTAR.

Crear:

`Human Input Required`

y mostrarme la pregunta.

Cuando yo responda, almacenar la respuesta en el Knowledge Base para poder reutilizarla posteriormente.

# 2. INFORMACIÓN INICIAL SOBRE EL CANDIDATO

La aplicación está siendo creada específicamente para mí.

Mi perfil general es aproximadamente:

- Computer Science graduate de King's College London.
- BSc Computer Science.
- Actualmente MSc Artificial Intelligence and Data Engineering en UCL.
- Interés profesional principal: Software Engineering, Artificial Intelligence, Machine Learning, Data/AI Engineering y roles relacionados.
- Experiencia como Graduate Teaching Assistant en King's College London.
- Experiencia profesional/internship de desarrollo de software.
- Co-founder y developer de una aplicación educativa basada en IA.
- Experiencia construyendo múltiples proyectos de software y AI.
- Experiencia con Python, Java, JavaScript/TypeScript, React, Vue, AI/ML, SQL, AWS y tecnologías relacionadas.

NO asumas que esta lista contiene todos mis datos.

La aplicación debe disponer de un onboarding inicial en el que pueda importar:

- CV PDF
- CV DOCX
- LinkedIn export si dispongo de él
- portfolio
- GitHub
- documentos de proyectos
- documentos adicionales

Extrae los datos y proponlos para importación, pero déjame verificar/corregir la información antes de marcarla como `verified`.

Quiero poder editar toda esta información después desde la interfaz.

# 3. ARQUITECTURA GENERAL

Quiero una arquitectura local y mantenible.

Preferencia:

Desktop:
- Tauri 2

Frontend:
- React
- TypeScript
- Vite
- Tailwind
- componentes accesibles y modernos

Backend/orchestrator:
- Python 3.12+
- FastAPI local
- asyncio
- Pydantic

Persistence:
- SQLite
- WAL
- FTS5
- migrations con Alembic

Secrets:
- macOS Keychain

Browser automation:
- Playwright/CDP
- Chrome/Chromium persistent profiles
- browser-harness si resulta conveniente

AI browser decision engine:
- Laya local como backend preferente;
- Jev como backend alternativo configurable.

Text generation:
permitir al usuario elegir:
- local LLM mediante Ollama;
- modelos compatibles OpenAI API;
- Anthropic;
- Gemini;
- cualquier endpoint OpenAI-compatible.

La aplicación debe poder funcionar con la máxima cantidad posible de funciones sin APIs de pago.

No uses Electron salvo que exista una razón técnica extremadamente fuerte.

# 4. JEV / LAYA BROWSER AGENT

Estudia en profundidad como referencia:

https://github.com/browser-use/jev-ultrafast

Y también investiga el proyecto público:

ChenneyZhuang/laya-browser-agent

No copies ciegamente ninguno de ellos.

Extrae las mejores ideas de ambas arquitecturas y crea un browser automation engine específico para job applications.

El enfoque principal debe ser structured DOM / accessibility tree, NO computer vision constante.

Por cada página genera una tabla indexada equivalente a:

[1] textbox First name
[2] textbox Last name
[3] textbox Email
[4] combobox Country
[5] button Upload resume
[6] checkbox Privacy Policy
[7] button Submit application

El motor debe trabajar utilizando operaciones limitadas y validadas:

- CLICK
- TYPE_TEXT
- SELECT
- CHECK
- UNCHECK
- UPLOAD
- SCROLL_UP
- SCROLL_DOWN
- OPEN_TAB
- CLOSE_TAB
- WAIT
- BACK
- DONE
- BLOCKED
- HUMAN_REQUIRED

El AI decision model selecciona:

operation
target index
confidence

pero NO genera directamente:

- selectors;
- JavaScript;
- shell commands;
- coordinates;
- arbitrary executable code.

Cada target debe referenciar un elemento que haya sido realmente observado.

Antes de realizar una acción:

1. comprobar que sigue existiendo;
2. comprobar que sigue visible;
3. comprobar que está habilitado;
4. comprobar que no está cubierto;
5. comprobar que pertenece al documento correcto;
6. comprobar que la página no ha cambiado significativamente.

Implementa stale-state protection.

Si la confianza es demasiado baja, detener el flujo o utilizar un fallback.

# 5. ESTRATEGIA HÍBRIDA DE AUTOMATIZACIÓN

NO quiero depender exclusivamente de un agente genérico.

Usa esta jerarquía:

LEVEL 1 — deterministic adapter
LEVEL 2 — semantic DOM automation
LEVEL 3 — Laya/Jev agent
LEVEL 4 — optional vision fallback
LEVEL 5 — human takeover

Crea adapters específicos para ATS importantes.

Como mínimo:

- Greenhouse
- Lever
- Ashby
- Workday
- SmartRecruiters
- Workable
- Teamtailor
- iCIMS
- Taleo

Y adapters/source handling para:

- LinkedIn
- Indeed
- direct company career pages

La arquitectura debe permitir añadir nuevos adapters fácilmente.

Por ejemplo:

src/automation/adapters/
    greenhouse.py
    lever.py
    ashby.py
    workday.py
    linkedin.py
    indeed.py
    generic.py

Cada adapter debe implementar una interface común.

# 6. SESIONES DEL NAVEGADOR

Quiero poder iniciar sesión manualmente una sola vez.

Usa un persistent browser profile específico de la aplicación.

Ejemplo conceptual:

~/Library/Application Support/JobAgent/browser-profile/

Nunca almacenes passwords directamente en SQLite.

Cookies/sessions deben permanecer localmente.

Para MFA:

- detener;
- abrir ventana visible;
- pedirme completar MFA;
- detectar cuándo termina;
- continuar.

Para CAPTCHA:

NO intentar romper ni evadir CAPTCHAs.

Pausar automáticamente, mostrar:

"Human verification required"

permitirme tomar control del navegador y continuar después.

Igualmente, no intentes ocultar automatización mediante técnicas diseñadas para eludir sistemas anti-bot.

# 7. MODOS DE APLICACIÓN

Debe haber tres modos:

## MANUAL

Encuentra y prepara aplicaciones pero no rellena ni envía nada.

## REVIEW

Rellena automáticamente toda la aplicación.

Antes del último Submit:

- pausa;
- muestra los datos introducidos;
- muestra documentos utilizados;
- muestra respuestas generadas;
- muestra preguntas sensibles;
- deja que pulse Approve.

Debe ser el modo inicial recomendado.

## AUTO

Puede completar y enviar una aplicación automáticamente.

Pero sólo:

- si job match >= threshold;
- si no hay preguntas sin verificar;
- si no hay CAPTCHA;
- si no hay preguntas consideradas sensibles;
- si existe suficiente confianza;
- si el dominio/plataforma está aprobado en la whitelist;
- si no existe una aplicación previa al mismo puesto;
- si cumple todas mis reglas de búsqueda.

AUTO debe poder habilitarse:

globalmente
por fuente
por empresa
por dominio
por tipo de trabajo

Siempre almacenar exactamente qué ocurrió.

# 8. NO VIOLAR RESTRICCIONES DE LAS PLATAFORMAS

No quiero un sistema basado en bypassing, stealth evasion o abuso de plataformas.

Si una plataforma bloquea explícitamente automatización, presenta una alternativa:

- descubrir el puesto;
- preparar todos los datos;
- abrir el formulario;
- rellenar lo permitido;
- pedirme confirmar/continuar manualmente.

Nunca implementes:

- CAPTCHA bypass;
- credential theft;
- rate-limit evasion;
- fingerprint spoofing para saltarse bloqueos;
- métodos diseñados expresamente para eludir sistemas de seguridad.

La automatización debe ser suficientemente conservadora como para no poner innecesariamente mis cuentas en riesgo.

# 9. JOB DISCOVERY ENGINE

La plataforma debe poder descubrir oportunidades continuamente desde múltiples fuentes.

Sources:

- LinkedIn
- Indeed
- Google/search engine results cuando sea viable
- company career pages
- Greenhouse boards
- Lever boards
- Ashby boards
- Workday career pages
- SmartRecruiters
- Workable
- Teamtailor
- GitHub repositories que agreguen puestos
- listas de compañías
- URLs manuales
- RSS/feeds/APIs públicas cuando existan

Debe existir:

Sources Manager

donde pueda activar/desactivar fuentes.

# 10. COMPANY WATCHLIST

Quiero una lista específica de empresas que me interesan.

Cada empresa puede tener:

name
careers URL
locations
preferred roles
priority
notes
check frequency
auto apply enabled
salary preference
blacklist keywords

La aplicación comprobará periódicamente las career pages.

Debe ser capaz de detectar puestos nuevos aunque la empresa no aparezca en LinkedIn/Indeed.

# 11. JOB SEARCH PROFILES

Permite crear múltiples search profiles.

Ejemplos:

"London AI/ML"
"London SWE"
"Europe AI"
"Graduate Big Tech"
"Remote ML"
"Startups AI"

Cada perfil tendrá:

roles
keywords
negative keywords
locations
remote/hybrid/onsite
minimum salary
experience levels
companies
excluded companies
industries
technologies
visa requirements
date posted
contract type
application mode
minimum match score

# 12. MATCHING ENGINE

Cada trabajo descubierto debe normalizarse y analizarse.

Extrae:

company
title
location
remote status
salary min/max
currency
description
responsibilities
required skills
preferred skills
experience required
education
visa/sponsorship
technologies
employment type
posting date
source
original URL
application URL
ATS
job ID
company ID

Calcula un Match Score 0-100.

El scoring debe ser transparente.

Ejemplo:

role similarity          25
skills match             20
experience match         15
education match          10
preferred technologies   10
location                 10
salary                    5
work authorization        5

No fijes necesariamente estos pesos: hazlos configurables.

Mostrar:

MATCH 87

con:

Strong matches:
+ Python
+ Machine Learning
+ MSc AI
+ London

Potential gaps:
- Kubernetes
- 2 years production ML

Important:
No rechaces automáticamente una oportunidad simplemente porque falta una skill.

Debe existir un "stretch factor".

# 13. SEMANTIC MATCHING

No uses sólo keywords.

Ejemplo:

"PyTorch experience"

también puede relacionarse con:

deep learning
neural networks
TensorFlow
ML frameworks

Usa embeddings/local semantic matching cuando sea útil.

Mantén explicabilidad.

# 14. DEDUPLICATION

Esto es extremadamente importante.

El mismo puesto puede aparecer en:

LinkedIn
Indeed
company careers
Greenhouse
Google
GitHub

Debe detectarse como UNA oportunidad.

Deduplica utilizando:

company
normalized title
location
job ID
ATS ID
application URL
description fingerprint
semantic similarity

Nunca aplicar dos veces accidentalmente.

# 15. CANDIDATE KNOWLEDGE GRAPH

Además de almacenar mis datos de forma plana, crea relaciones.

Por ejemplo:

Python
  -> used in Project X
  -> used during Internship
  -> used for Machine Learning
  -> years/level
  -> evidence

React
  -> project A
  -> application B

Machine Learning
  -> UCL
  -> projects
  -> modules

Esto ayudará a generar respuestas contextualizadas.

No hace falta una graph database externa.

Puede implementarse sobre SQLite.

# 16. DOCUMENT MANAGER

Mantener:

Base CV
SWE CV
AI/ML CV
Data CV
custom CVs
cover letters
transcripts
portfolio PDFs
other documents

Cada fichero tendrá versioning.

Cuando aplique a un puesto:

almacena exactamente qué versión se utilizó.

# 17. CV TAILORING

La aplicación podrá producir una versión adaptada del CV para un puesto.

Pero:

NO inventar nada.

Sólo puede:

- reordenar;
- seleccionar;
- resumir;
- reformular;
- enfatizar experiencia verdadera;
- elegir proyectos relevantes;
- cambiar wording;
- priorizar skills relevantes.

Nunca cambiar:

- empresas;
- titulaciones;
- fechas;
- technologies realmente utilizadas;
- métricas verificadas;
- job titles sin permiso;
- experiencia real.

Antes de generar el PDF:

mostrar diff respecto al CV base.

Generar CV ATS-friendly.

Considera Typst o LaTeX para generación de PDFs consistente.

# 18. COVER LETTERS

Genera cover letters únicamente cuando sean necesarias o útiles.

Deben incorporar:

- company
- role
- relevant experience
- relevant projects
- why role/company

Evitar cartas genéricas.

Mantenerlas breves salvo que el formulario exija más.

Nunca inventar motivos personales.

# 19. APPLICATION QUESTION ENGINE

Clasifica cada pregunta de una aplicación.

Tipos:

identity
contact
education
experience
skills
work authorization
visa
location
salary
availability
demographic optional
company-specific
motivation
free text
legal certification
unknown

Respuestas simples:

rellenar desde Knowledge Base.

Respuestas abiertas:

construir usando únicamente hechos verificados.

Ejemplo:

"Why are you interested in this role?"

Generar usando:

job description
company information
candidate experience

Guardar:

question_normalized
raw_question
answer
source facts
confidence
last used
editable

Si encuentro una versión similar:

reutilizar/adaptar.

# 20. SENSITIVE QUESTIONS

Nunca responder automáticamente sin una regla explícita a categorías especialmente delicadas.

Por ejemplo:

disability
ethnicity
gender
medical information
criminal record
demographic surveys

Permitir configurar una política como:

- leave blank
- prefer not to say
- ask me
- custom saved answer

# 21. WORK AUTHORIZATION

No adivinar nunca.

Crear una sección editable específica para:

countries allowed to work in
nationality if I decide almacenarla
current visa/status
sponsorship required
sponsorship preferred
relocation willingness

Las respuestas deben salir exclusivamente de aquí.

# 22. APPLICATION EXECUTION

Cuando se decide aplicar:

STEP 1
guardar snapshot de la oportunidad.

STEP 2
seleccionar CV.

STEP 3
generar documentos necesarios.

STEP 4
abrir application URL.

STEP 5
identificar ATS.

STEP 6
usar adapter específico si existe.

STEP 7
rellenar formulario.

STEP 8
resolver preguntas.

STEP 9
subir documentos.

STEP 10
validar todo.

STEP 11
según modo:
- Manual
- Review
- Auto

STEP 12
enviar.

STEP 13
verificar que existe confirmación real.

No asumir `DONE` simplemente porque se pulsó Submit.

Busca evidencia como:

application received
thank you
success message
confirmation ID
confirmation email

STEP 14
guardar resultado.

# 23. APPLICATION PIPELINE

Statuses:

DISCOVERED
SCORED
SHORTLISTED
IGNORED
PREPARING
NEEDS_REVIEW
APPLYING
APPLIED
CONFIRMED
RECRUITER_SCREEN
ASSESSMENT
TECHNICAL_TEST
INTERVIEW
FINAL_INTERVIEW
OFFER
REJECTED
WITHDRAWN
GHOSTED
ERROR

Cada cambio crea un ApplicationEvent.

Nunca sobrescribas el historial.

# 24. EMAIL MONITOR

Esta es una función central.

Permíteme conectar el email que utilizo para aplicaciones.

Implementar:

Gmail OAuth
Microsoft Graph/Outlook
generic IMAP cuando sea posible

NO guardar la contraseña del correo en SQLite.

Tokens en macOS Keychain.

Monitorizar nuevos emails.

Frecuencia configurable.

Ejemplo:

cada 5 / 10 / 15 / 30 minutos.

# 25. EMAIL CLASSIFICATION

Clasificar emails de recruitment como:

APPLICATION_CONFIRMATION
REJECTION
RECRUITER_MESSAGE
ASSESSMENT_INVITATION
CODING_TEST
INTERVIEW_REQUEST
INTERVIEW_CONFIRMATION
FOLLOW_UP
OFFER
BACKGROUND_CHECK
DOCUMENT_REQUEST
OTHER

Extraer:

company
role
sender
application ID
deadline
test URL
interview date/time
next action
people involved

Relacionar email con la aplicación correcta.

Usa:

sender domain
company
job title
application IDs
ATS
conversation
timestamps
semantic matching

Si no hay suficiente confianza:

pedirme confirmar qué aplicación corresponde.

# 26. AUTOMATIC STATUS UPDATES

Ejemplos:

"Thank you for applying..."
-> CONFIRMED

"We have decided not to progress..."
-> REJECTED

"We would like to invite you..."
-> INTERVIEW

"Complete the following coding challenge..."
-> TECHNICAL_TEST

Cuando cambie el estado:

- actualizar application;
- crear ApplicationEvent;
- mostrar notificación de macOS;
- mostrarlo en Dashboard.

# 27. DEADLINES

Detecta fechas límite en emails.

Ejemplo:

"Please complete the HackerRank within 5 days."

Crear:

Technical Test
Due: 4 October 2026

Mostrar en dashboard:

URGENT
2 days remaining

Opcionalmente añade soporte posterior para calendar integration, pero no es requisito para el MVP si complica innecesariamente la primera versión.

# 28. EMAIL SAFETY

No enviar automáticamente emails importantes.

Puede generar draft.

Yo debo aprobar antes de enviar, a menos que habilite explícitamente reglas específicas.

Nunca responder automáticamente a:

offers
salary negotiation
legal questions
visa questions
recruiters

sin confirmación.

# 29. UI

Quiero una aplicación visual extremadamente limpia, profesional y rápida.

Diseño:

macOS-native feeling
minimal
light/dark mode
responsive dentro de desktop
keyboard shortcuts
command palette

Sidebar:

Dashboard
Discover
Jobs
Applications
Review Queue
Companies
Profile
Documents
Email
Analytics
Automation
Activity
Settings

# 30. DASHBOARD

Mostrar:

Applications this week
Applications total
New matching jobs
Applications requiring attention
Technical tests
Upcoming interviews
Responses
Interview rate
Rejection rate
Average match score
Application sources

Y debajo:

TODAY

8 new high-match jobs
3 applications ready to submit
1 technical test due tomorrow
2 new recruiter emails

# 31. DISCOVER VIEW

Tabla/cards con:

Match
Company
Role
Location
Salary
Source
Posted
Status

Filtros:

Match > X
Location
Role
Company
Source
Date
Salary
Remote
ATS

Quick actions:

Apply
Review
Ignore
Save
Open original

# 32. JOB DETAILS

Panel lateral/detalle:

Company
Role
Match score
Why it matches
Potential gaps
Description
Required skills
Salary
Work authorization
Source
ATS
Documents selected
Similar applications
Company history

Botones:

Prepare Application
Apply
Ignore
Open Job
Add Company to Watchlist

# 33. KANBAN DE APPLICATIONS

Columnas:

Preparing
Applied
Assessment
Interview
Offer
Rejected

Cards:

Company
Role
Date
Match
Last event
Next action

Drag & drop permitido.

Pero cambios detectados automáticamente por email deben aparecer como automated events.

# 34. APPLICATION DETAIL PAGE

Debe ser extremadamente completa.

Tabs:

Overview
Timeline
Job
Answers
Documents
Emails
Automation Run
Notes

Timeline ejemplo:

29 Sep 10:42
Job discovered via LinkedIn

29 Sep 10:43
Match score: 88

29 Sep 10:45
Application started

29 Sep 10:47
CV: ML-v4.pdf

29 Sep 10:48
Application submitted

29 Sep 10:51
Confirmation email received

2 Oct 14:23
Technical assessment invitation

# 35. REVIEW QUEUE

Antes de enviar algo que requiera aprobación:

mostrar una única cola.

Ejemplo:

Google — Software Engineer Graduate

87 Match

CV:
SWE-v3.pdf

Questions:
14 automatic
2 AI generated
1 needs review

[Open]
[Approve & Submit]
[Reject]

# 36. AUTOMATION RUN VIEWER

Quiero poder observar qué está haciendo el agente.

Mostrar:

current URL
current task
DOM elements
selected operation
selected target
confidence
last actions
duration
errors

Opcionalmente screenshot del navegador para debugging.

Ejemplo:

Goal:
Apply to Software Engineer at X.

Step 18

Operation:
TYPE_TEXT

Target:
[12] "Why do you want to work here?"

Confidence:
0.93

Generated answer:
...

Esto será muy útil para debugging.

# 37. ACTIVITY LOG

Guardar logs estructurados.

Eventos:

job discovered
job merged as duplicate
score calculated
document generated
browser action
application submitted
email received
status updated
manual change
error

Filtros por:

application
company
source
severity
date
automation run

# 38. ANALYTICS

Dashboard con:

applications/day
applications/week
applications/source
applications/company
applications/role
average match
response rate
rejection rate
assessment rate
interview rate
offer rate
time to response

Funnel:

Discovered
↓
Applied
↓
Response
↓
Assessment
↓
Interview
↓
Offer

También quiero analizar:

qué fuentes producen más entrevistas;
qué tipos de trabajos generan respuestas;
match score vs response rate;
CV version vs response rate.

No saques conclusiones fuertes con datasets pequeños.

# 39. AUTOMATION SCHEDULER

La app debe poder ejecutar tareas localmente incluso si la interfaz no está abierta cuando sea razonable.

Usa launchd / macOS LaunchAgents si es necesario.

Jobs:

job discovery
company watchlist scan
email sync
status reconciliation
cleanup

Configurable desde Settings.

Ejemplo:

Search new jobs:
every 2 hours

Email:
every 10 minutes

Watchlist:
every 4 hours

No consultes agresivamente páginas.

Aplica rate limits.

# 40. LOCAL LAYA

Configura Laya local como decision backend preferente si la máquina puede ejecutarlo correctamente.

En Apple Silicon:

investiga y utiliza MLX cuando sea apropiado.

Proporcionar:

Laya status
model downloaded
model
runtime
latency
memory
test button

Nunca bloquear la aplicación completa si Laya falla.

Fallback:

Jev
or deterministic/generic agent.

# 41. JEV

Permite utilizar TypeSafe/Jev opcionalmente.

Settings:

Browser Decision Engine

(•) Laya Local
( ) Jev
( ) Hybrid

Hybrid puede hacer:

Laya first
confidence gate
Jev fallback

Mantén el API boundary independiente para poder sustituir los motores fácilmente.

Interface conceptual:

BrowserDecisionEngine.decide(state, actions) -> Decision

Decision:
operation
target
confidence
metadata

# 42. TEXT MODEL

Separar completamente:

DECISION MODEL

de:

TEXT GENERATION MODEL

Laya/Jev decide dónde actuar.

El text model genera únicamente contenidos como:

form answers
cover letters
CV wording

Esto sigue la filosofía de jev-ultrafast.

# 43. COST CONTROL

Dashboard:

AI Usage

local Laya:
$0

Ollama:
$0

Jev:
$X

OpenAI:
$X

Anthropic:
$X

Debe poder establecer presupuesto mensual.

Si puede funcionar localmente, preferir local.

# 44. DATABASE MODEL

Diseña correctamente las entidades.

Como mínimo:

Candidate
CandidateFact
Education
Experience
Project
Skill
CandidateSkill
Preference
SavedAnswer
Document
DocumentVersion

Company
CompanyDomain
CompanyWatchRule

Job
JobSource
JobSkill
JobSnapshot
JobMatch
JobDuplicate

Application
ApplicationEvent
ApplicationAnswer
ApplicationDocument
ApplicationEmail

EmailMessage
EmailClassification

AutomationRun
AutomationStep
HumanTask

SearchProfile
Source
SchedulerTask

Settings
Integration
SecretReference

Normaliza adecuadamente pero evita sobreingeniería.

# 45. SEARCH / DATA FLOW

Arquitectura:

SOURCE
   ↓
INGEST
   ↓
NORMALIZE
   ↓
DEDUPLICATE
   ↓
ENRICH
   ↓
MATCH
   ↓
FILTER
   ↓
SHORTLIST
   ↓
PREPARE
   ↓
APPLY
   ↓
VERIFY
   ↓
TRACK

Cada paso debe ser independiente/reintentable.

# 46. RETRIES

Si falla una aplicación:

no empezar desde cero inmediatamente.

Guardar checkpoints.

Ejemplo:

current page
completed fields
uploaded files
current ATS step
browser trace

Tipos de error:

NETWORK_ERROR
SESSION_EXPIRED
CAPTCHA
MFA
ELEMENT_NOT_FOUND
PAGE_CHANGED
UPLOAD_ERROR
UNKNOWN_QUESTION
SUBMISSION_UNCONFIRMED
SITE_BLOCKED

Cada tipo tiene su estrategia.

# 47. HUMAN TAKEOVER

En cualquier punto quiero poder pulsar:

Take Control

Esto:

- pausa agente;
- me da navegador;
- permite interactuar manualmente;
- cuando pulse Resume Agent:
- toma nuevo snapshot;
- continúa desde estado actual.

# 48. APPLICATION QUALITY GUARD

Antes de Submit ejecuta una validación final:

correct name
correct email
correct phone
correct CV
correct company
correct role
work authorization answers
salary answers
required fields
attachments
no hallucinated facts
no obviously malformed text
no duplicate application

Genera:

Application Quality Score

pero NO uses este score para inventar respuestas.

# 49. COMPANY RESEARCH

Cuando sea útil para una respuesta abierta:

obtener contexto limitado sobre empresa:

product
industry
role
job posting

No necesito un research enorme.

Evita gastar tokens innecesarios.

Cachea información.

# 50. GITHUB JOB SOURCES

Añade posibilidad de registrar repositories GitHub como sources.

Pueden contener:

README job lists
JSON feeds
CSV
Markdown tables
issues tagged hiring/jobs
company lists

Source configuration:

repository
branch
paths
parser
poll interval

Normaliza URLs encontradas y pásalas al pipeline.

# 51. URL INGESTION

Quiero poder copiar cualquier URL de una oferta dentro de la app.

Input:

Paste job URL

Sistema:

open
extract
normalize
score
check duplicate

y añadirla al pipeline.

# 52. BROWSER EXTENSION — OPCIONAL

Si resulta muy útil, crea una pequeña extensión local de Chrome.

Botón:

"Send to Job Agent"

Desde cualquier job posting.

Pero sólo hazlo después de que la aplicación principal funcione correctamente.

# 53. GLOBAL SEARCH

Command-K:

buscar:

jobs
companies
applications
emails
skills
answers
documents

Ejemplo:

Cmd+K
"Google"

-> Google jobs
-> Google applications
-> Google emails

# 54. NOTIFICACIONES DE MACOS

Notificaciones importantes:

New interview
Technical test received
Offer received
Application failed
Human action required
High match job
Deadline approaching

No notificar cada pequeño evento.

# 55. MENU BAR

Añade un pequeño menu bar helper si resulta razonable.

Mostrar:

Job Agent

12 new jobs
3 ready to apply
1 action required

Actions:

Open Dashboard
Run Search
Pause Automation
Resume Automation

# 56. PRIVACY

Local-first.

No telemetry.

No analytics externas.

No datos enviados fuera del equipo salvo:

- sites que necesariamente visites;
- APIs explícitamente configuradas;
- AI providers explícitamente seleccionados.

Mostrar claramente qué provider recibe qué información.

# 57. SECURITY

Nunca:

hardcode API keys;
commit secrets;
guardar passwords plaintext;
loguear auth headers;
guardar cookies en logs;
guardar tokens en traces.

Secrets -> macOS Keychain.

Sanitizar logs.

`.env` sólo para desarrollo.

# 58. BACKUPS

Permitir:

Export encrypted backup

Que contenga:

database
settings
documents
knowledge base

pero NO browser credentials ni Keychain secrets.

Restore backup.

# 59. TESTING

Quiero tests reales.

Unit tests:
matching
deduplication
classification
normalization
answer lookup

Integration tests:
SQLite
email parsing
ATS adapters
document generation

Browser E2E tests:

crea páginas mock locales imitando:

Greenhouse
Lever
Workday
generic application form

Prueba:

text fields
dropdown
checkbox
radio
upload
multi-step
validation
confirmation

Nunca utilices una aplicación de trabajo real para automated CI tests.

# 60. EMAIL TESTS

Fixtures reales pero anonimizadas:

confirmation
rejection
assessment
interview
offer
generic recruiter message

Verificar linking correcto a application.

# 61. OBSERVABILITY

Implementa structured logging.

Development mode:

verbose.

Production:

normal.

Cada automation run tendrá:

run ID
application ID
steps
duration
engine
confidence
tokens/cost
result
error

# 62. DEVELOPMENT EXPERIENCE

Quiero:

README completo
CONTRIBUTING
ARCHITECTURE.md
SECURITY.md

Commands simples:

make setup
make dev
make test
make lint
make build

o equivalentes.

Incluye script bootstrap para macOS.

Detectar:

Homebrew
Python
uv
Node/pnpm
Rust
Chrome
Ollama

e instalar/guiar dependencias faltantes.

# 63. PACKAGING

Resultado final:

macOS `.app`

y preferiblemente `.dmg`.

Que pueda instalarse como aplicación normal.

Si code signing/notarization requiere developer certificate que no tengo:

documenta el proceso;
permite unsigned local build.

# 64. DATABASE LOCATION

Utilizar paths estándar macOS.

Ejemplo conceptual:

~/Library/Application Support/<AppName>/

Separar:

database
documents
browser-profile
cache
logs
backups
models

# 65. NOMBRE

Escoge temporalmente un nombre profesional para el proyecto.

Evita nombres genéricos tipo AutoApply.

Puede cambiarse posteriormente.

Mantén el nombre encapsulado/configurable.

# 66. UX DE PRIMER ARRANQUE

Wizard:

Welcome

Step 1
Import CV

Step 2
Review profile

Step 3
Job preferences

Step 4
Connect browser accounts

Step 5
Connect application email

Step 6
Configure AI

Step 7
Automation settings

Step 8
Run first search

No exigir todas las integrations para arrancar.

# 67. LINKEDIN / INDEED LOGIN

La app debe abrir el browser profile.

Mostrar:

"Log into LinkedIn"

Yo inicio sesión manualmente.

Después:

Test session

Lo mismo para Indeed u otras plataformas.

No solicitar directamente password dentro de nuestra aplicación salvo que exista una integración oficial que realmente lo requiera.

# 68. SOURCE HEALTH

Mostrar estado:

LinkedIn
Connected
Last checked: 12:30
18 jobs found

Indeed
Connected
Last checked: 12:24
7 jobs found

Company Watchlist
28/28 OK

Email
Connected
Last sync: 12:33

# 69. RULE ENGINE

Quiero reglas configurables.

Ejemplo:

IF
match >= 85
AND location == London
AND role contains "Machine Learning"
AND company not blacklisted
THEN
prepare_application

Otro:

IF
match >= 92
AND ATS == Greenhouse
AND no_unknown_questions
THEN
auto_apply

Implementa rule builder sencillo.

# 70. APPLICATION RATE LIMITS

Configurar:

max applications/day
max applications/company
minimum interval

Valores iniciales conservadores.

No quiero un spam bot.

Debe priorizar calidad sobre volumen.

# 71. JOB PRIORITY

Priority score separado de match.

Puede combinar:

match
company priority
freshness
application complexity
competition indicators if known
salary
user preferences

Mostrar ambos:

Match 91
Priority High

# 72. BLACKLISTS

Configurables:

companies
roles
keywords
industries
locations

Ejemplo:

Senior
Staff
Principal

si estoy buscando graduate/junior.

# 73. FEEDBACK LOOP

Después de revisar una recomendación:

Interested
Not interested

Permitir opcionalmente:

Why?

wrong role
wrong location
too senior
salary
company
technology

Usar feedback para mejorar ranking.

No modificar hechos del Candidate Profile a partir de este feedback.

# 74. REJECTION LEARNING

Analizar estadísticas agregadas de mis aplicaciones.

NO asumir la causa real de un rechazo si la empresa no la comunica.

Puede decir:

"Applications con característica X han tenido menor response rate"

pero nunca:

"Te rechazaron por X"

sin evidencia.

# 75. SCALABILITY

Aunque sea personal:

diseña para almacenar decenas de miles de trabajos y miles de aplicaciones.

Usa:

indexes
pagination
lazy loading
background tasks

No cargar toda DB en memoria.

# 76. OFFLINE BEHAVIOR

Sin internet:

la UI sigue funcionando;
puedo revisar aplicaciones;
editar profile;
ver analytics;
editar documents.

Las funciones online quedan marcadas Offline.

# 77. NO PLACEHOLDERS

No quiero botones falsos.

No quiero:

TODO
mock implementation
fake backend
hardcoded fake data
"coming soon"

excepto features claramente clasificadas como optional/future.

Todo lo presente en la UI final debe estar conectado a funcionalidad real.

# 78. IMPLEMENTATION ORDER

Trabaja en fases, pero completa cada una correctamente.

PHASE 1
foundation
Tauri
React
Python backend
SQLite
migrations
settings
logging

PHASE 2
Candidate Knowledge Base
CV import
profile editor
documents

PHASE 3
job model
sources
normalization
dedupe
matching

PHASE 4
discovery engine
company watchlists
manual URLs

PHASE 5
browser harness
DOM snapshots
actions
Laya integration
Jev adapter

PHASE 6
ATS adapters
form filling
document upload
application verification

PHASE 7
application tracking
timeline
Kanban

PHASE 8
email integrations
email classification
automatic status updates

PHASE 9
scheduler
notifications
analytics

PHASE 10
testing
performance
packaging
documentation

NO abandones fases anteriores parcialmente implementadas para crear muchas features superficiales.

# 79. GIT WORKFLOW

Inicializa Git si aún no existe.

Trabaja con commits pequeños y descriptivos.

Ejemplo:

feat: initialize desktop application
feat: add candidate knowledge base
feat: implement job normalization pipeline
feat: add semantic matching
feat: integrate local Laya decision backend
feat: add Greenhouse application adapter
feat: implement email application tracking

No uses:

Co-authored-by

No te pongas como co-author de ningún commit.

No reescribas historial innecesariamente.

# 80. INSPECCIÓN DE DEPENDENCIAS

Antes de integrar un proyecto externo:

- revisar licencia;
- revisar mantenimiento;
- revisar arquitectura;
- revisar API;
- revisar seguridad;
- evitar copiar grandes fragmentos innecesariamente.

Respeta licencias.

Documenta dependencias importantes.

# 81. JEV-ULTRAFAST

Del proyecto jev-ultrafast me interesan especialmente estas ideas:

- indexed element tables;
- structured DOM observation;
- one browser snapshot;
- dynamic valid action space;
- target validation;
- stale state checking;
- DOM-node references;
- separation between decision and text generation;
- confidence;
- independent verification before declaring DONE;
- fast browser loop.

Reimplementa/adapta estas ideas correctamente para job applications.

# 82. LAYA

Usa Laya/localdecide donde sea apropiado para:

operation selection
target selection
confidence scoring

Especialmente porque quiero que la aplicación pueda funcionar de forma privada y barata en mi Mac.

Añade benchmark local:

page
elements
decision latency
correct target
memory

para poder comparar:

Laya
Jev
hybrid

desde Settings > AI > Browser Engine.

# 83. IMPORTANTES LIMITACIONES DEL AGENTE BASE

El proyecto debe solucionar explícitamente necesidades que un browser-agent genérico puede no gestionar bien:

file uploads
iframes
popup tabs
multi-step forms
date pickers
autocomplete
custom dropdowns
radio groups
checkbox groups
validation errors
dynamic Workday forms
resume parsing pages
repeated questions
session timeouts

Implementa soporte específico donde sea necesario.

# 84. INSPECTOR

Development mode debe tener:

Browser Agent Inspector

Observation
Actions
Decision
Execution
Verification

Observation:

URL
page title
elements
visible text

Decision:

engine
operation
target
confidence

Execution:

success
timing

Verification:

changed state
goal progress
errors

# 85. SAFE FORM ANSWER GENERATION

Para cualquier answer generado:

la función recibirá algo similar a:

question
job
company
relevant_verified_candidate_facts
previous_verified_answers

No pasar todo mi Knowledge Base innecesariamente.

El output deberá incluir:

answer
facts_used
confidence

Antes de utilizarlo:

validar que facts_used realmente existen.

# 86. PROVENANCE

Toda respuesta automática tiene que poder explicar:

"¿Por qué pusiste esto?"

Ejemplo:

Answer:
"Yes, I have experience with Python."

Evidence:
- Project X
- Experience Y
- Candidate skill: Python

Esto debe aparecer en la Application Detail cuando haga clic sobre una respuesta.

# 87. SEARCH PRIORITY PARA MI PERFIL

Los search profiles iniciales pueden incluir de forma editable:

Software Engineer
Graduate Software Engineer
Junior Software Engineer
AI Engineer
Machine Learning Engineer
Graduate ML Engineer
Data Engineer
AI/Data Engineer
Applied AI Engineer
ML Platform Engineer
Research Engineer
Backend Engineer
Cloud Engineer

No hardcodear únicamente estos roles.

Debo poder añadir/eliminar.

# 88. DEFAULT GEOGRAPHY

No hardcodees work authorization basándote en mis estudios.

En onboarding pregúntame/permite configurar:

UK
Spain
EU
Remote
relocation

y después utiliza únicamente la información verificada.

# 89. DAILY EXPERIENCE

Una vez configurada, el flujo ideal debe ser:

Abro el Mac.

La app ya ha encontrado nuevos puestos.

Dashboard:

32 jobs discovered
9 strong matches
5 applications prepared
3 ready for review

Reviso rápidamente.

Apruebo 3.

El agente las envía.

Por la tarde llega un email:

"Invitation to technical assessment"

La app:

detecta email;
lo relaciona con la aplicación;
cambia status;
extrae deadline;
me notifica.

Eso es la experiencia que quiero.

# 90. AUTONOMOUS MODE

Cuando active explicitamente:

Autonomous Job Search

la aplicación puede:

buscar;
evaluar;
preparar;
aplicar;
verificar;
monitorizar;

sin intervención salvo:

CAPTCHA
MFA
unknown information
sensitive questions
low confidence
site restriction
submission uncertainty
rule requiring approval

Debe existir un gran:

PAUSE AUTOMATION

que detenga inmediatamente nuevas acciones.

# 91. CRASH SAFETY

Si la app se cierra durante una aplicación:

al volver a abrir:

mostrar:

"Interrupted automation detected"

y permitir:

Resume
Review
Discard

Nunca asumir submission.

# 92. PERFORMANCE

Quiero una aplicación rápida.

No enviar screenshot a LLM en cada paso.

Preferir:

DOM snapshot
ARIA
structured text
indexed elements

Vision sólo fallback.

Cachear embeddings.

No recalcular job analysis innecesariamente.

# 93. DOCUMENTATION FINAL

README debe explicar:

qué hace
arquitectura
instalación
desarrollo
build
configuration
browser setup
email setup
Laya setup
Jev setup
Ollama setup
security
troubleshooting

ARCHITECTURE.md:

component diagram
data flow
browser flow
email flow
application flow
database model

SECURITY.md:

credentials
sessions
browser data
AI providers
local storage

# 94. ACCEPTANCE TEST

Consideraré el proyecto terminado únicamente cuando pueda hacer este test:

1. Instalo la app en mi Mac.
2. Importo mi CV.
3. Corrijo mi profile.
4. Inicio sesión en una job platform.
5. Conecto mi email.
6. Creo un Search Profile.
7. Pulso Run Search.
8. Aparecen puestos reales.
9. Elijo uno.
10. La app calcula match.
11. Pulso Prepare.
12. Genera documentos/respuestas.
13. Pulso Apply.
14. El navegador completa el formulario.
15. Puedo observar el progreso.
16. Si necesita algo, me lo pregunta.
17. Envía al aprobar.
18. Verifica submission.
19. Aparece en Applied.
20. Recibo confirmation email.
21. La app lo detecta.
22. Actualiza automáticamente Timeline.
23. Días después recibe rejection/interview/test.
24. Detecta el mensaje.
25. Cambia el status correcto.
26. Me notifica.
27. Analytics refleja el proceso.

Todo esto debe ser funcional.

# 95. PRIMER PASO QUE DEBES HACER AHORA

Antes de comenzar a escribir código:

1. inspecciona el entorno actual;
2. inspecciona la estructura del repository si existe;
3. estudia `browser-use/jev-ultrafast`;
4. estudia `ChenneyZhuang/laya-browser-agent`;
5. estudia las interfaces de Laya que realmente estén disponibles;
6. verifica licencias y compatibilidad actual;
7. decide la arquitectura final;
8. crea `ARCHITECTURE.md`;
9. crea un plan de implementación detallado;
10. empieza inmediatamente a implementarlo.

No me preguntes decisiones menores.

Toma decisiones técnicas razonables autónomamente.

Si una librería o API que menciono ha cambiado, investiga su estado actual y adapta la implementación a la versión actual, en vez de bloquearte porque las instrucciones estén desactualizadas.

Prioriza:

correctness
reliability
privacy
maintainability
user control

por encima de construir una demo visual.

# 96. REQUISITO FINAL

No termines simplemente cuando compile.

Ejecuta:

lint
typecheck
unit tests
integration tests
browser E2E tests
production build

Corrige los errores encontrados.

Después realiza una auditoría final del proyecto buscando:

broken buttons
fake data
dead code
unhandled errors
unsafe secret handling
race conditions
duplicate applications
missing database indexes
unverified AI answers
bad retry loops
browser session problems
packaging problems

Corrige todos los problemas razonablemente detectables antes de considerar terminado el trabajo.

El resultado debe sentirse como una aplicación personal que realmente podría utilizar diariamente durante mi búsqueda de trabajo, no como una demo de un agente de navegador.