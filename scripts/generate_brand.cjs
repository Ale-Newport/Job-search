// Rebuild vector brand assets and native icon sizes from the same geometry.
// Requires Node + sharp. Wordmark outlines contain no embedded font software.
const fs = require('node:fs');
const path = require('node:path');
const sharp = require('sharp');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'frontend/public/brand');
const icons = path.join(root, 'src-tauri/icons');
const font = JSON.parse(fs.readFileSync(path.join(root, 'docs/brand/wordmark-path.json')));
const ink = '#173F36', mint = '#A8E4CE', gold = '#D6B782', paper = '#F3F3EA';
const left = 'M19 102V57C19 42 30 31 44 31C58 31 69 42 69 57V102H53V58C53 52 49 47 44 47C39 47 35 52 35 58V102Z';
const right = 'M53 102V44C53 28 65 16 81 16C97 16 109 28 109 44V72H93V44C93 37 88 32 81 32C74 32 69 37 69 44V102Z';
const mark = (color, point = color) => `<path fill="${color}" d="${left}"/><path fill="${color}" d="${right}"/><circle cx="101" cy="94" r="8" fill="${point}"/>`;
const words = color => `<g transform="translate(0 ${font.ascent}) scale(1 -1)"><path fill="${color}" d="${font.path}"/></g>`;
const svg = (width, height, body, title = 'Meridian') => `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${title}"><title>${title}</title>${body}</svg>`;
const write = (name, text) => fs.writeFileSync(path.join(output, name), text + '\n');
fs.mkdirSync(output, {recursive: true});
fs.mkdirSync(icons, {recursive: true});

write('meridian-symbol.svg', svg(128, 128, mark(ink, gold), 'Meridian symbol'));
write('meridian-symbol-light.svg', svg(128, 128, mark(mint, gold), 'Meridian symbol'));
write('meridian-symbol-mono.svg', svg(128, 128, mark('currentColor'), 'Meridian symbol'));
const wordBounds = font.bounds;
const croppedWords = color => svg(wordBounds.width, wordBounds.height, `<g transform="translate(${-wordBounds.x} ${-(font.ascent - wordBounds.y - wordBounds.height)})">${words(color)}</g>`);
write('meridian-wordmark.svg', croppedWords(ink));
write('meridian-wordmark-light.svg', croppedWords(paper));
const lockup = (color, symbol) => svg(550, 144, `<g transform="translate(0 8)">${mark(symbol, gold)}</g><g transform="translate(148 16)">${words(color)}</g>`);
write('meridian-logo-on-light.svg', lockup(ink, ink));
write('meridian-logo-on-dark.svg', lockup(paper, mint));

const appIcon = svg(1024, 1024, `<defs>
  <linearGradient id="plate" x1="0" y1="0" x2=".8" y2="1"><stop stop-color="#28594C"/><stop offset=".48" stop-color="#153E35"/><stop offset="1" stop-color="#0B2723"/></linearGradient>
  <linearGradient id="ribbon" x1="0" y1="0" x2=".85" y2="1"><stop stop-color="#C9F3DB"/><stop offset=".55" stop-color="#A8E4CE"/><stop offset="1" stop-color="#72BFA9"/></linearGradient>
  <linearGradient id="edge" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#FFFFFF" stop-opacity=".2"/><stop offset="1" stop-color="#FFFFFF" stop-opacity=".02"/></linearGradient>
  <filter id="shadow" x="-.3" y="-.3" width="1.6" height="1.6"><feGaussianBlur stdDeviation="13"/></filter>
</defs>
<rect x="82" y="91" width="860" height="860" rx="192" fill="#031411" opacity=".22" filter="url(#shadow)"/>
<rect x="80" y="68" width="864" height="864" rx="192" fill="url(#plate)"/>
<rect x="82" y="70" width="860" height="860" rx="190" fill="none" stroke="url(#edge)" stroke-width="3"/>
<g transform="translate(64 102) scale(7)" opacity=".18">${mark('#001C15')}</g>
<g transform="translate(64 90) scale(7)">${mark('url(#ribbon)', gold)}</g>`, 'Meridian app icon');
fs.writeFileSync(path.join(icons, 'app-icon.svg'), appIcon + '\n');
write('meridian-app-icon.svg', appIcon);

async function build() {
  const source = Buffer.from(appIcon);
  for (const [size, name] of [[1024, 'icon.png'], [32, '32x32.png'], [128, '128x128.png'], [256, '128x128@2x.png']]) {
    await sharp(source).resize(size, size).png().toFile(path.join(icons, name));
  }
  await sharp(source).resize(512, 512).png().toFile(path.join(output, 'meridian-app-icon.png'));
  await sharp(Buffer.from(lockup(ink, ink))).resize(1650, 432).png().toFile(path.join(output, 'meridian-logo.png'));
  const tray = sharp(Buffer.from(svg(128, 128, mark('#000')))).resize(44, 44).ensureAlpha();
  await tray.clone().png().toFile(path.join(icons, 'tray-icon.png'));
  fs.writeFileSync(path.join(icons, 'tray-icon.rgba'), await tray.raw().toBuffer());
  const iconset = path.join(root, '.build-cache/Meridian.iconset');
  fs.mkdirSync(iconset, {recursive: true});
  for (const size of [16, 32, 128, 256, 512]) {
    for (const scale of [1, 2]) {
      await sharp(source).resize(size * scale, size * scale).png().toFile(path.join(iconset, `icon_${size}x${size}${scale === 2 ? '@2x' : ''}.png`));
    }
  }
  const board = svg(1800, 1160, `
    <rect width="1800" height="1160" fill="${paper}"/>
    <text x="100" y="87" fill="${ink}" font-family="Arial, sans-serif" font-size="18" letter-spacing="5">MERIDIAN / VISUAL IDENTITY</text>
    <line x1="100" y1="122" x2="1700" y2="122" stroke="#CBD5C9"/>
    <g transform="translate(100 216) scale(1.52)">${mark(ink, gold)}<g transform="translate(148 8)">${words(ink)}</g></g>
    <text x="111" y="455" fill="#66776D" font-family="Arial, sans-serif" font-size="23">A clearer path forward.</text>
    <rect x="1135" y="185" width="565" height="385" rx="30" fill="#E6E9DE"/>
    <svg x="1230" y="185" width="380" height="380" viewBox="0 0 1024 1024">${appIcon.match(/<title>.*?<\/title>([\s\S]*)<\/svg>/)[1]}</svg>
    <rect x="100" y="638" width="1010" height="352" rx="28" fill="#11362E"/>
    <g transform="translate(212 735) scale(1.25)">${mark(mint, gold)}<g transform="translate(148 8)">${words(paper)}</g></g>
    <text x="1135" y="675" fill="#66776D" font-family="Arial, sans-serif" font-size="16" letter-spacing="3">ONE SYMBOL. EVERY SIZE.</text>
    <g transform="translate(1135 731) scale(1.05)">${mark(ink)}</g>
    <g transform="translate(1305 771) scale(.64)">${mark(ink)}</g>
    <g transform="translate(1430 797) scale(.4)">${mark(ink)}</g>
    <g transform="translate(1520 810) scale(.26)">${mark(ink)}</g>
    <circle cx="1166" cy="942" r="26" fill="${ink}"/><circle cx="1243" cy="942" r="26" fill="${mint}"/><circle cx="1320" cy="942" r="26" fill="${gold}"/>
    <text x="1380" y="948" fill="#66776D" font-family="Arial, sans-serif" font-size="18">Forest · Mint · Sand</text>
    <line x1="100" y1="1047" x2="1700" y2="1047" stroke="#CBD5C9"/>
    <text x="100" y="1096" fill="#66776D" font-family="Arial, sans-serif" font-size="17">An ascending monogram. A point of direction. Space to move forward.</text>
  `, 'Meridian visual identity');
  fs.writeFileSync(path.join(root, 'docs/brand/meridian-identity.svg'), board + '\n');
  await sharp(Buffer.from(board)).png().toFile(path.join(root, 'docs/brand/meridian-identity.png'));
  console.log('Brand vectors, PNGs, native icon sizes and tray template generated.');
}
build().catch(error => { console.error(error); process.exitCode = 1; });
