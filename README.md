# LED Panel AI Effects

Public animation library for the Nedu41 LED panel. The Cloudflare Worker asks Gemini for code in the existing Python `frame(t, W, H)` format. GitHub Actions safely renders that effect into a 90-frame animated GIF and saves it under `effects/`. The panel browser loads the GIF from this repository, converts its frames to the panel palette, and stores the animation in Pico LittleFS.

Only the rendered animation is published. Prompts and generated Python source are not committed to this repository. Animation files are public and can be viewed or downloaded by anyone.