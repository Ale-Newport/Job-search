# Meridian · identidad visual

El símbolo es una **m ascendente** construida con dos arcos. El segundo arco se abre antes de llegar al punto de orientación. El dibujo funciona en un solo color y conserva su lectura a tamaños pequeños.

La palabra *meridian* utiliza contornos vectoriales de Avenir Next Demi Bold, con espaciado ajustado. Los SVG no requieren tener esa fuente instalada y no incluyen el archivo de la fuente.

## Archivos

- `frontend/public/brand/meridian-logo-on-light.svg`: logotipo completo sobre fondos claros.
- `frontend/public/brand/meridian-logo-on-dark.svg`: logotipo completo sobre fondos oscuros.
- `frontend/public/brand/meridian-logo.png`: logotipo transparente a 1650 px.
- `frontend/public/brand/meridian-symbol.svg`: símbolo sobre fondo claro.
- `frontend/public/brand/meridian-symbol-light.svg`: símbolo sobre fondo oscuro.
- `frontend/public/brand/meridian-symbol-mono.svg`: versión de un solo color.
- `src-tauri/icons/app-icon.svg`: original del icono de macOS, con fondo, margen y sombra.
- `src-tauri/icons/icon.icns`: icono nativo con tamaños de 16 a 1024 px.
- `src-tauri/icons/tray-icon.rgba`: plantilla monocroma de 44 px para la barra de menús Retina.
- `docs/brand/meridian-identity.png`: presentación del conjunto.

## Color y uso

| Color | Valor | Uso |
| --- | --- | --- |
| Bosque | `#173F36` | Marca principal y texto |
| Menta | `#A8E4CE` | Símbolo sobre fondo oscuro |
| Arena | `#D6B782` | Punto de orientación |
| Marfil | `#F3F3EA` | Fondos claros y texto invertido |

Conservar las proporciones y el espacio libre alrededor. Usar la variante monocroma a tamaños muy pequeños. El fondo del icono de macOS pertenece al icono; el símbolo y el logotipo permanecen transparentes. La barra de menús utiliza una plantilla de macOS que adapta el color automáticamente.

## Regeneración

Con Node.js, `sharp` y las herramientas de macOS disponibles:

```sh
node scripts/generate_brand.cjs
iconutil -c icns .build-cache/Meridian.iconset -o src-tauri/icons/icon.icns
npm run build:app
bash scripts/create_dmg.sh
```

`NODE_PATH` puede apuntar a una instalación existente de `sharp`. La geometría del símbolo está en el generador y los contornos del logotipo están en `wordmark-path.json`. Todos los archivos finales están incluidos en el repositorio; el arranque de la app no necesita el generador.
