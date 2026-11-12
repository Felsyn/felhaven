# sandbox/

Prototypes that are not part of the app yet. Nothing in `metis_toolbox/`
imports from here, and the suite never reads it: the palette guard scans
`metis_toolbox/` only. A prototype graduates by moving into `metis_toolbox/`
(v1 of Pythia's face moved to `panels/pythia_face.py` on 2026-09-01), and from
then on it follows every app rule.

---

## Pythia face v2: static prototype

The hooded wire-frame construct from `reference/pythia_v2_reference.webp`,
drawn in code on a `tk.Canvas`: stdlib only, no image assets, no Pillow. It
is still on purpose. This pass settles the look, and animation comes after.

| File | Job |
|---|---|
| `pythia_face_v2.py` | Builds the scene (pure geometry, no tk) and paints it on a canvas. Run it for a live window. |
| `render_still.py` | Paints each framing on a real canvas, grabs it and writes `renders/*.png` |
| `reference/` | The poster the look was drawn from |
| `renders/` | The current stills |

```bash
# a window (Windows or Linux): poster|face, reference|phosphor
python sandbox/pythia_face_v2.py poster reference
python sandbox/pythia_face_v2.py face phosphor

# the PNGs, headless on Linux
xvfb-run -s "-screen 0 1400x2000x24" python3 sandbox/render_still.py
# on Windows (grabs with Pillow; --small if the screen is shorter than 1936 px)
python sandbox\render_still.py --small
```

### How it is built

- **Reference coordinates.** Every landmark is a pixel of the 1312×1936
  poster. The hood silhouette was measured off it row by row, and the colours
  were sampled region by region. A `View` maps the poster onto any canvas, so
  the poster and the 190×160 header framing come from the same geometry.
- **Mesh.** Poisson-disc points plus landmark seeds, Delaunay-triangulated in
  pure Python (Bowyer-Watson). Facet size is set in *output* pixels, so a small
  canvas gets fewer, larger facets instead of the poster's mesh shrunk to mush.
- **Face.** Triangulated on the left half and mirrored, so it is exactly
  symmetric and the hood is not. It has no outline of its own: denser,
  brighter mesh is what separates it from the cavity. The eye sockets are cut
  out of the mesh rather than painted over it.
- **Glow.** Tk has no alpha, so this uses v1's fake-alpha trick, generalised:
  each primitive is a *role* plus an *intensity*, blended from the background
  up to the role colour and then toward burnout white above 1.0. A line drawn
  over a glow has to be brighter than the glow, which is why the face mesh is
  lit by the eyes.
- **Seeded.** `build_scene(..., seed=7)` draws the same figure every time.
  Another seed draws a different figure that is just as valid.

### Renders

| | |
|---|---|
| `pythia_v2_poster.png` | 1312×1936, reference colourway, every layer |
| `pythia_v2_face_380x320.png` | The head, at twice the header slot |
| `pythia_v2_face_190x160.png` | The head, at the size Hestia mounts v1 |
| `pythia_v2_phosphor_*.png` | The same scene in Felhaven's own `palette.C` |

### To decide before it moves into `panels/`

1. **Colour.** The reference colourway uses blue and crimson that
   `palette.py` does not have; `palette.py` deliberately folds `blue` into
   green so the app reads monochrome. Either v2 adds keys for them (a visible
   palette decision), or it ships the phosphor colourway, which needs none.
2. **v1's contract said no eyes, no mouth, no text.** v2 has eyes by design,
   and at header size they carry the whole signal. The text (HUD and footer)
   is poster-only; the face framing draws none.
3. **Animation.** v1 draws 265 items at 190×160 and v2 draws about 1,330. A
   20 fps loop should touch only what moves (the eyes, a few trim traces, some
   node twinkle) and leave the mesh static. Mapping the four states
   (idle / playing / thinking / speaking) onto that is the next step.
4. **Tk limits are real.** Lines are aliased and overlaps occlude instead of
   adding, on Windows too. The renders show what the widget can actually
   draw, not a softened preview.
