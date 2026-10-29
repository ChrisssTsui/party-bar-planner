// Optional local renderer: npm install --no-save playwright, use installed Chrome.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
(async () => {
  const [input, chrome] = process.argv.slice(2);
  if (!input || !chrome) throw new Error('Usage: node tools/render_menu.cjs output/menu.html /path/to/chrome');
  const filename = path.resolve(input);
  const browser = await chromium.launch({ executablePath: chrome, headless: true });
  try {
    const page = await browser.newPage({viewport:{width:900,height:1200},deviceScaleFactor:1});
    await page.route('**/*', route => route.request().url().startsWith('file:') ? route.continue() : route.abort());
    await page.goto(pathToFileURL(filename).href);
    await page.evaluate(() => document.fonts.ready);
    const inspect = () => page.evaluate(() => ({
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
      clipped: [...document.querySelectorAll('h1,h2,p,footer')].filter(e => e.scrollWidth > e.clientWidth + 1).length,
      cards: document.querySelectorAll('article').length,
      height: document.querySelector('main').getBoundingClientRect().height,
      fonts: document.fonts.status
    }));
    const screen = await inspect();
    await page.screenshot({path: filename.replace(/\.html$/, '-screen.png'), fullPage:true});
    await page.emulateMedia({media:'print'});
    await page.setViewportSize({width:680,height:1010});
    const print = await inspect();
    if (screen.horizontalOverflow || screen.clipped || print.horizontalOverflow || print.clipped) throw new Error('Detected overflowing content');
    await page.pdf({path:filename.replace(/\.html$/,'.pdf'),format:'A4',preferCSSPageSize:true,printBackground:true,displayHeaderFooter:false});
    fs.writeFileSync(filename.replace(/\.html$/,'-render-check.json'), JSON.stringify({screen, print},null,2)+'\n');
    console.log(JSON.stringify({screen, print}));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exitCode=1; });
