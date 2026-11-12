# sandbox/

Prototypes that are not part of the app yet. Nothing in `metis_toolbox/`
imports from here, and the suite never reads it: the palette guard scans
`metis_toolbox/` only. A prototype graduates by moving into `metis_toolbox/`
(v1 of Pythia's face moved to `panels/pythia_face.py` on 2026-09-01), and from
then on it follows every app rule.

---

## Pythia face v2: full-screen still

The hooded wire-frame construct from `reference/pythia_v2_reference.webp`,
drawn in code with no image assets, and shown **full screen only**. It is
still on purpose: this pass settles the look, and animation comes after.

| File | Job |
|---|---|
| `pythia_face_v2.py` | Builds the scene (pure geometry: no tk, no numpy) and shows it full screen |
| `pythia_v2_raster.py` | The renderer: scene to pixels, with additive light, anti-aliasing and bloom (numpy) |
| `pythia_v2_font.py` | A stroke font, so the HUD text glows like the wire |
| `render_still.py` | Writes the same pixels to `renders/*.png`, headless |
| `reference/` | The poster the look was drawn from |

```bash
# full screen at the monitor's own resolution; Escape or q closes it
python sandbox/pythia_face_v2.py                # reference colourway
python sandbox/pythia_face_v2.py phosphor       # Felhaven's palette.C

# stills: the 1080p pair by default; --all adds 1440p and the poster framing
python sandbox/render_still.py
python sandbox/render_still.py --size 3840x2160
```

Needs numpy, which the app already has: it is in `requirements.txt` for
Calliope. On Windows the window declares itself DPI-aware, so a scaled
display gets native pixels, not a stretched, soft frame.

### How it is built

- **Scene, then pixels.** `build_scene()` is pure geometry: a display list of
  lines, dots, glows and polygons in the poster's 1312×1936 coordinates.
  `render()` turns that into an image. The window and the PNG stills are the
  same pixels; a test grab of the full-screen window matched the still
  exactly.
- **Light adds up.** Lines and dots add into a light buffer, so a bundle of
  strands burns brighter than any one strand, and a hot channel spills into
  the others and burns toward white. Then a five-level bloom. A plain
  `tk.Canvas` can do neither, which is why v2 is no longer drawn as canvas
  items.
- **Fills are opaque.** A fill covers the light drawn before it. That keeps
  the cavity dark in front of the hood and lays a dark fabric band under the
  gold trim, so gold sits on dark instead of washing out to white over the
  blue mesh.
- **Wide screens.** The poster's height fits the screen and the figure
  stays centred. Everything else lays itself out on the visible stage: the
  HUD frame at the screen's edges, schematic and dust across the full width,
  glyphs easing outward, and the cloak falling past the shoulders into arms
  instead of stopping at the poster's edge.
- **The face is a skeleton over a mesh.** A few feature lines catch the
  light harder than the filler mesh: a brow angling down to the nose, the
  bridge, high cheekbones, the lips and a hard jaw. The mesh is built on them
  and stays dim. They are drawn span by span, each a little brighter or
  dimmer, so they read as edges lining up along the bone, not strokes on
  top. A lighting pass does the rest: the forehead sits in the brim's
  shadow, the eyes burn in dark sockets cut out of the mesh, light gathers
  down the nose, cheekbones and mouth, and the cheeks fall into hollows.
  Exactly symmetric: triangulated on the left half and mirrored.
- **Reference-measured.** The hood silhouette was measured off the reference
  row by row, and the colours were sampled region by region.
- **Seeded.** `seed=7` draws the same figure every time; another seed draws a
  different one that is just as valid.

### Cost

One render at launch, while the window shows black. Measured in the sandbox
container:

| Screen | Scene | Render |
|---|---|---|
| 1920×1080 | 1.0 s | 2.0 s |
| 2560×1440 | 0.8 s | 3.4 s |

Animation must not re-render the whole frame each tick. The plan is to keep
this frame as the static layer and re-composite only the regions that move:
the eyes, a few trim traces, and some node twinkle.

### To decide before it moves into the app

1. **Colour.** The reference colourway uses blue and crimson that
   `palette.py` does not have; `palette.py` deliberately folds `blue` into
   green so the app reads monochrome. Either v2 adds keys for them (a visible
   palette decision), or it ships the phosphor colourway, which needs none.
2. **v1's rules said no eyes, no mouth, no text, and no numpy.** v2 has eyes
   and HUD text by design. It uses numpy because a full screen is millions of
   pixels, and pure Python cannot light them in a launch-time budget.
3. **Where it lives.** Full screen means its own window or mode, not the
   Hestia header slot v1 is mounted in.

### Still to improve

From the review of the first still, not yet done:

- volume in the hood: facets shaded as if lit from the front, and fold lines
- the robe as four or five draped folds, not free strands
- a slight head tilt and an uneven hood, so it reads less like a diagram
