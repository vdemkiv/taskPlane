"""Render the README walkthrough. Requires Pillow, Node.js and Playwright/Chromium.

Example: NODE_PATH=/path/to/node_modules CHROME_BIN=/path/to/chrome python3 render.py
Set NODE_BIN to select a Node executable; otherwise node is used from PATH.
The render uses an isolated temporary directory and does not touch TaskPlane state.
"""
import os
from pathlib import Path
import subprocess
import tempfile

from PIL import Image

HERE = Path(__file__).resolve().parent
CAPTURE = r"""
const { chromium } = require('playwright');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
(async () => {
  const browser = await chromium.launch({headless:true,
    ...(process.env.CHROME_BIN ? {executablePath:process.env.CHROME_BIN} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1400,height:1080},deviceScaleFactor:1});
    const errors=[]; page.on('pageerror',error=>errors.push(error.message));
    await page.goto(pathToFileURL(process.argv[1]).href);
    await page.evaluate(()=>document.fonts.ready);
    const count=await page.evaluate(()=>window.sceneCount);
    for(let i=0;i<count;i++) {
      await page.evaluate(i=>window.renderScene(i),i);
      const overflow=await page.evaluate(()=>{
        const body=document.querySelector('.body');
        const footer=document.querySelector('.foot').getBoundingClientRect();
        return [...body.children].some(el=>el.getBoundingClientRect().bottom>footer.top-12)
          || document.querySelector('#stage').scrollWidth>1400;
      });
      if(overflow)throw Error('Scene '+i+' overlaps the footer or overflows');
      await page.locator('#stage').screenshot({path:path.join(process.argv[2],i+'.png')});
    }
    await page.getByRole('button',{name:'Next →'}).click();
    if(await page.evaluate(()=>document.querySelector('#counter').textContent.startsWith('1 of'))!==true)
      throw Error('Next does not wrap');
    await page.getByRole('button',{name:'← Previous'}).click();
    if(await page.evaluate(()=>document.querySelector('#counter').textContent.startsWith('10 of'))!==true)
      throw Error('Previous does not wrap');
    await page.setViewportSize({width:700,height:600});
    if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Narrow view overflows');
    if(errors.length)throw Error(errors.join('\n'));
    console.log('Rendered '+count+' scenes; checked layout, navigation and narrow width.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
"""


def main():
    with tempfile.TemporaryDirectory(prefix="taskplane-readme-render-") as tmp:
        subprocess.run([os.environ.get("NODE_BIN", "node"), "-e", CAPTURE,
                        str(HERE / "index.html"), tmp], check=True)
        frames = []
        for i in range(10):
            with Image.open(Path(tmp) / f"{i}.png") as source:
                if i == 0:
                    source.save(HERE.parent / "taskplane-flow-overview.png", optimize=True)
                frames.append(source.convert("RGB").quantize(colors=96))
        output = HERE.parent / "taskplane-run-walkthrough.gif"
        durations = [5000, 5500, 5000, 5500, 5000, 7000, 5500, 7000, 5500, 5000]
        frames[0].save(output, save_all=True, append_images=frames[1:],
                       duration=durations, loop=0, optimize=True, disposal=2)
        with Image.open(output) as result:
            assert result.n_frames == 10 and result.size == (1400, 1000)
            assert sum(result.seek(i) or result.info["duration"]
                       for i in range(result.n_frames)) == sum(durations)
        print(f"GIF: {output.stat().st_size:,} bytes; {sum(durations) / 1000:g} seconds")


if __name__ == "__main__":
    main()
