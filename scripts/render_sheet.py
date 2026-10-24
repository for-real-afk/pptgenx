import sys
from pathlib import Path
from PIL import Image
from deckgen.qa.soffice import render_deck
spec, out = sys.argv[1], Path(sys.argv[2])
imgs = render_deck(Path(spec), out)
ims = [Image.open(i).resize((800, 450)) for i in imgs]
for k in range(0, len(ims), 4):
    sheet = Image.new("RGB", (1600, 900), "white")
    for j, im in enumerate(ims[k:k+4]): sheet.paste(im, ((j % 2) * 800, (j // 2) * 450))
    sheet.save(out / f"sheet{k//4}.png")
print(len(imgs), "slides")
